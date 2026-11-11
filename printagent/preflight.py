"""Artwork preflight: the checks a prepress operator does by hand, as code.

Deterministic checks (geometry, resolution, colour, fonts, bleed, safe zone)
run here. Semantic checks (typos, a wrong phone number, a cropped logo) are
left to the LLM that calls `render_proof`, which sees the proof image.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .catalog import Product, get

MM_PER_IN = 25.4
PT_PER_MM = 72 / MM_PER_IN
ASPECT_TOL = 0.015      # 1.5 % for raster files
PDF_SIZE_TOL_MM = 0.6   # PDFs are exact, allow rounding only
MAX_WORK_PX = 2000


@dataclass
class Check:
    id: str
    severity: str          # "ok" | "warning" | "error"
    title: str
    detail: str
    fix: str = ""
    auto_fixable: bool = False


@dataclass
class Artwork:
    kind: str                       # "raster" | "pdf"
    image: Image.Image              # RGB working image covering the full page
    product: Product
    trim_mm: tuple[float, float]    # oriented to match the artwork
    geometry: str                   # "with_bleed" | "trim_only" | "mismatch"
    size_mm: tuple[float, float]    # physical size the artwork maps to
    effective_dpi: float | None
    native: dict


# --------------------------------------------------------------------- loading

def _orient(product: Product, w: float, h: float) -> tuple[float, float]:
    tw, th = product.trim_mm
    if tw != th and w != h and (w > h) != (tw > th):
        return th, tw
    return tw, th


def _match_geometry(product: Product, trim: tuple[float, float], w: float, h: float,
                    *, exact_mm: bool) -> str:
    b = product.bleed_mm
    canvas = (trim[0] + 2 * b, trim[1] + 2 * b)
    if exact_mm:
        def close(a, c):
            return abs(a[0] - c[0]) <= PDF_SIZE_TOL_MM and abs(a[1] - c[1]) <= PDF_SIZE_TOL_MM
    else:
        def close(a, c):
            return abs((a[0] / a[1]) / (c[0] / c[1]) - 1) <= ASPECT_TOL
    if close((w, h), canvas):
        return "with_bleed"
    if close((w, h), trim):
        return "trim_only"
    return "mismatch"


def _work_scale(size_mm: tuple[float, float]) -> float:
    return min(10.0, MAX_WORK_PX / max(size_mm))   # px per mm


def load(path: str | Path, product_id: str) -> tuple[Artwork, list[Check]]:
    path = Path(path)
    product = get(product_id)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() == ".pdf":
        return _load_pdf(path, product)
    return _load_raster(path, product)


def _load_raster(path: Path, product: Product) -> tuple[Artwork, list[Check]]:
    checks: list[Check] = []
    with Image.open(path) as img:
        img.load()
    w_px, h_px = img.size
    trim = _orient(product, w_px, h_px)
    geometry = _match_geometry(product, trim, w_px, h_px, exact_mm=False)
    b = product.bleed_mm
    if geometry == "with_bleed":
        size_mm = (trim[0] + 2 * b, trim[1] + 2 * b)
    elif geometry == "trim_only":
        size_mm = trim
    else:  # wrong proportions: keep the file's own aspect so the proof isn't distorted
        size_mm = (trim[0], trim[0] * h_px / w_px)
    eff_dpi = w_px / (size_mm[0] / MM_PER_IN) if geometry != "mismatch" else None

    # colour mode
    mode = img.mode
    if mode == "CMYK":
        checks.append(Check("color", "ok", "Colour mode", "Artwork is already CMYK."))
    else:
        checks.append(Check(
            "color", "warning", "RGB colours",
            f"File is {mode}. Printers use CMYK, so very bright blues, greens and "
            "oranges can print noticeably duller than on screen.",
            "Export as CMYK (e.g. FOGRA39) or accept automatic conversion.", True))

    # transparency
    has_alpha = "A" in img.getbands() or (mode == "P" and "transparency" in img.info)
    if has_alpha:
        alpha = np.asarray(img.convert("RGBA").getchannel("A"))
        if (alpha < 255).any():
            pct = 100 * (alpha < 255).mean()
            checks.append(Check(
                "transparency", "warning", "Transparent areas",
                f"{pct:.0f}% of the image is (semi-)transparent; it will print on white paper.",
                "Flatten onto the intended background colour.", True))

    rgb = _flatten(img)
    scale = _work_scale(size_mm)
    work = rgb.resize((round(size_mm[0] * scale), round(size_mm[1] * scale)), Image.LANCZOS)
    art = Artwork("raster", work, product, trim, geometry, size_mm, eff_dpi,
                  {"pixels": f"{w_px} x {h_px}", "mode": mode,
                   "metadata_dpi": [round(float(v)) for v in img.info["dpi"]] if "dpi" in img.info else None})
    return art, checks


def _load_pdf(path: Path, product: Product) -> tuple[Artwork, list[Check]]:
    with pymupdf.open(path) as doc:
        return _load_pdf_doc(doc, product)


def _load_pdf_doc(doc: pymupdf.Document, product: Product) -> tuple[Artwork, list[Check]]:
    checks: list[Check] = []
    if doc.page_count == 0:
        raise ValueError("PDF has no pages.")
    if doc.page_count > 2:
        checks.append(Check("pages", "warning", "Page count",
                            f"PDF has {doc.page_count} pages; this product prints front + back "
                            "(max 2). Only the first two pages would be used.",
                            "Upload a PDF with 1 (front) or 2 (front, back) pages."))
    page = doc[0]
    w_mm, h_mm = page.rect.width / PT_PER_MM, page.rect.height / PT_PER_MM
    trim = _orient(product, w_mm, h_mm)
    geometry = _match_geometry(product, trim, w_mm, h_mm, exact_mm=True)

    # fonts: ext 'n/a' means the font programme is not embedded
    missing, seen = [], set()
    for pg in list(doc)[:2]:
        for f in pg.get_fonts(full=True):
            name = f[3].split("+")[-1]
            if name in seen:
                continue
            seen.add(name)
            if f[1] == "n/a":
                missing.append(name)
    if missing:
        checks.append(Check(
            "fonts", "error", "Fonts not embedded",
            f"Not embedded: {', '.join(missing)}. The print server will substitute "
            "another font, which can shift or reflow text.",
            "Re-export with 'embed all fonts' or convert text to outlines."))
    elif seen:
        checks.append(Check("fonts", "ok", "Fonts", f"All {len(seen)} fonts are embedded."))

    # placed images: effective resolution and colour space
    worst_dpi, rgb_images = None, 0
    for img in page.get_images(full=True):
        xref, iw, ih, cs = img[0], img[2], img[3], img[5]
        if "RGB" in cs:
            rgb_images += 1
        for r in page.get_image_rects(xref):
            if r.width <= 0 or r.height <= 0:
                continue
            dpi = min(iw / (r.width / 72), ih / (r.height / 72))
            worst_dpi = dpi if worst_dpi is None else min(worst_dpi, dpi)
    if rgb_images:
        checks.append(Check(
            "color", "warning", "RGB images",
            f"{rgb_images} placed image(s) are RGB; colours may shift when converted to CMYK.",
            "Convert images to CMYK before export, or accept automatic conversion.", True))
    else:
        checks.append(Check("color", "ok", "Colour mode", "No RGB images found."))

    b = product.bleed_mm
    size_mm = (trim[0] + 2 * b, trim[1] + 2 * b) if geometry == "with_bleed" else trim
    if geometry == "mismatch":
        size_mm = (w_mm, h_mm)
    scale = _work_scale(size_mm)
    pix = page.get_pixmap(dpi=max(36, round(scale * MM_PER_IN)), alpha=False)
    work = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    work = work.resize((round(size_mm[0] * scale), round(size_mm[1] * scale)), Image.LANCZOS)
    art = Artwork("pdf", work, product, trim, geometry, size_mm, worst_dpi,
                  {"page_size_mm": f"{w_mm:.1f} x {h_mm:.1f}", "pages": doc.page_count,
                   "images": len(page.get_images())})
    return art, checks


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode == "CMYK":
        return img.convert("RGB")
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, rgba).convert("RGB")


# ------------------------------------------------------------------- geometry

def _boxes(art: Artwork) -> dict[str, tuple[int, int, int, int]]:
    """Pixel boxes (l, t, r, b) on the working image."""
    W, H = art.image.size
    s = W / art.size_mm[0]
    bleed = art.product.bleed_mm if art.geometry == "with_bleed" else 0
    bp = round(bleed * s)
    sp = round(art.product.safe_mm * s)
    return {
        "canvas": (0, 0, W, H),
        "trim": (bp, bp, W - bp, H - bp),
        "safe": (bp + sp, bp + sp, W - bp - sp, H - bp - sp),
        "bleed_px": bp, "safe_px": sp, "px_per_mm": s,
    }


def _geometry_checks(art: Artwork) -> list[Check]:
    p = art.product
    tw, th = art.trim_mm
    cw, ch = tw + 2 * p.bleed_mm, th + 2 * p.bleed_mm
    got = art.native.get("page_size_mm") or art.native.get("pixels")
    if art.geometry == "with_bleed":
        return [Check("size", "ok", "Size and bleed",
                      f"Matches {cw:g} x {ch:g} mm ({tw:g} x {th:g} mm + {p.bleed_mm:g} mm bleed).")]
    if art.geometry == "trim_only":
        return [Check("bleed", "error", "No bleed",
                      f"Artwork is exactly the finished size ({tw:g} x {th:g} mm) with no bleed. "
                      "Cutting tolerance is ~1 mm, so thin white edges can appear.",
                      f"Extend the background {p.bleed_mm:g} mm past every edge "
                      f"(document size {cw:g} x {ch:g} mm).", True)]
    return [Check("size", "error", "Wrong size or proportions",
                  f"Artwork ({got}) does not match {p.name} ({tw:g} x {th:g} mm, or "
                  f"{cw:g} x {ch:g} mm with bleed). Printing it would crop or distort the design.",
                  f"Resize the document to {cw:g} x {ch:g} mm and re-place the design.")]


def _resolution_check(art: Artwork) -> Check | None:
    dpi, need = art.effective_dpi, art.product.min_dpi
    if dpi is None:
        return None
    if dpi >= need * 0.98:   # allow rounding of page size
        return Check("resolution", "ok", "Resolution", f"Effective resolution {dpi:.0f} dpi.")
    sev = "error" if dpi < need * 0.75 else "warning"
    return Check("resolution", sev, "Low resolution",
                 f"Effective resolution is {dpi:.0f} dpi; {need} dpi is needed for sharp print. "
                 "Text and logos may look blurry or pixelated.",
                 "Use the original high-resolution file or vector logo. "
                 "Upscaling the same file does not add detail.")


def _luma(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _empty_bleed_check(art: Artwork, bx: dict) -> tuple[Check | None, list[str]]:
    if art.geometry != "with_bleed" or bx["bleed_px"] < 2:
        return None, []
    a = _luma(np.asarray(art.image, dtype=np.float32))
    bp = bx["bleed_px"]
    H, W = a.shape
    strips = {
        "top": (a[:bp, bp:W - bp], a[bp:2 * bp, bp:W - bp]),
        "bottom": (a[H - bp:, bp:W - bp], a[H - 2 * bp:H - bp, bp:W - bp]),
        "left": (a[bp:H - bp, :bp], a[bp:H - bp, bp:2 * bp]),
        "right": (a[bp:H - bp, W - bp:], a[bp:H - bp, W - 2 * bp:W - bp]),
    }
    bad = []
    for side, (outer, inner) in strips.items():
        # Per position along the edge: bleed is white while the artwork right
        # inside the trim is not -> background stops at the cut line.
        axis = 0 if side in ("top", "bottom") else 1
        o, i = outer.mean(axis=axis), inner.mean(axis=axis)
        frac = ((o > 245) & (i < 230)).mean()
        if frac > 0.15:
            bad.append(side)
    if not bad:
        return Check("bleed_fill", "ok", "Bleed filled", "Background extends into the bleed."), []
    return Check("bleed_fill", "error", "Bleed area is empty",
                 f"The background stops at the cut line on the {', '.join(bad)} edge(s); "
                 "the bleed is white. After cutting this shows as a thin white line.",
                 "Extend the background into the bleed.", True), bad


def _safe_zone_check(art: Artwork, bx: dict) -> tuple[Check, list[str]]:
    g = art.image.convert("L").filter(ImageFilter.FIND_EDGES)
    e = np.asarray(g) > 70
    tl, tt, tr, tb = bx["trim"]
    sl, st, sr, sb = bx["safe"]
    pad = 3  # ignore the trim line itself (bleed->trim transitions)
    bands = {
        "top": e[tt + pad:st, sl:sr],
        "bottom": e[sb:tb - pad, sl:sr],
        "left": e[st:sb, tl + pad:sl],
        "right": e[st:sb, sr:tr - pad],
    }
    hits = [side for side, band in bands.items() if band.size and band.mean() > 0.01]
    mm = art.product.safe_mm
    if not hits:
        return Check("safe_zone", "ok", "Safe zone",
                     f"No text or detail within {mm:g} mm of the cut line."), []
    return Check("safe_zone", "warning", "Content close to the cut",
                 f"Text or detail sits within {mm:g} mm of the cut line on the "
                 f"{', '.join(hits)} edge(s). It may be trimmed or look off-centre.",
                 f"Move text and logos at least {mm:g} mm inside the finished edge. "
                 "Ignore this if it is a background image meant to run off the edge."), hits


def _blank_check(art: Artwork) -> Check | None:
    a = np.asarray(art.image.convert("L"), dtype=np.float32)
    if a.std() < 2:
        return Check("blank", "error", "Artwork looks blank",
                     "The page is a single flat colour. Was the right file uploaded?",
                     "Check the file and upload the final design.")
    return None


# ----------------------------------------------------------------------- API

SEVERITY_ORDER = {"error": 0, "warning": 1, "ok": 2}


def preflight(path: str | Path, product_id: str) -> dict:
    art, checks = load(path, product_id)
    checks += _geometry_checks(art)
    if (c := _resolution_check(art)):
        checks.append(c)
    if (c := _blank_check(art)):
        checks.append(c)
    flagged: dict[str, list[str]] = {}
    if art.geometry != "mismatch":
        bx = _boxes(art)
        c, sides = _empty_bleed_check(art, bx)
        if c:
            checks.append(c)
            flagged["bleed"] = sides
        c, sides = _safe_zone_check(art, bx)
        checks.append(c)
        flagged["safe_zone"] = sides

    checks.sort(key=lambda c: SEVERITY_ORDER[c.severity])
    errors = [c for c in checks if c.severity == "error"]
    warnings = [c for c in checks if c.severity == "warning"]
    status = "fail" if errors else ("pass_with_warnings" if warnings else "pass")
    return {
        "file": str(path),
        "product_id": art.product.id,
        "status": status,
        "printable": not errors,
        "auto_fixable": [c.id for c in errors + warnings if c.auto_fixable],
        "customer_message": _customer_message(status, errors, warnings),
        "checks": [asdict(c) for c in checks],
        "artwork": {"kind": art.kind, "geometry": art.geometry,
                    "effective_dpi": round(art.effective_dpi) if art.effective_dpi else None,
                    **art.native},
        "_flagged_sides": flagged,
    }


def _customer_message(status: str, errors: list[Check], warnings: list[Check]) -> str:
    if status == "pass":
        return "Your artwork is print-ready."
    parts = []
    if errors:
        parts.append("Before we can print: " + " ".join(f"{c.title} – {c.fix}" for c in errors))
    if warnings:
        parts.append("Worth checking: " + "; ".join(c.title for c in warnings) + ".")
    fixable = [c for c in errors + warnings if c.auto_fixable]
    if fixable:
        parts.append("I can fix automatically: " + ", ".join(c.title for c in fixable) + ".")
    return " ".join(parts)


# --------------------------------------------------------------------- proof

def render_proof(path: str | Path, product_id: str, out: str | Path, legend: bool = True) -> dict:
    """Proof image with cut line (solid), safe zone (dashed) and bleed shaded."""
    report = preflight(path, product_id)
    art, _ = load(path, product_id)
    img = art.image.convert("RGBA")
    if art.geometry == "mismatch":
        img.convert("RGB").save(out)
        return {"proof": str(out), "report": report}
    bx = _boxes(art)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    W, H = img.size
    tl, tt, tr, tb = bx["trim"]
    if bx["bleed_px"]:
        shade = (20, 20, 20, 120)
        d.rectangle([0, 0, W, tt], fill=shade)
        d.rectangle([0, tb, W, H], fill=shade)
        d.rectangle([0, tt, tl, tb], fill=shade)
        d.rectangle([tr, tt, W, tb], fill=shade)
    lw = max(2, round(bx["px_per_mm"] * 0.3))
    d.rectangle([tl, tt, tr - 1, tb - 1], outline=(230, 0, 60, 255), width=lw)
    _dashed_rect(d, bx["safe"], (0, 150, 255, 255), lw, dash=round(bx["px_per_mm"] * 2))
    for side in report["_flagged_sides"].get("safe_zone", []):
        _highlight(d, bx, side, (255, 235, 0, 150))
    proof = Image.alpha_composite(img, overlay).convert("RGB")
    if not legend:
        proof.save(out)
        return {"proof": str(out), "report": report}
    legend_h = 40
    canvas = Image.new("RGB", (W, H + legend_h), "white")
    canvas.paste(proof, (0, 0))
    ld = ImageDraw.Draw(canvas)
    ld.text((10, H + 10), "red = cut line   blue dashes = safe zone   dark = trimmed off   "
            "yellow = content near the cut", fill=(40, 40, 40), font=ImageFont.load_default(18))
    canvas.save(out)
    return {"proof": str(out), "report": report}


def _dashed_rect(d, box, color, width, dash):
    l, t, r, b = box
    for x in range(l, r, dash * 2):
        d.line([x, t, min(x + dash, r), t], fill=color, width=width)
        d.line([x, b, min(x + dash, r), b], fill=color, width=width)
    for y in range(t, b, dash * 2):
        d.line([l, y, l, min(y + dash, b)], fill=color, width=width)
        d.line([r, y, r, min(y + dash, b)], fill=color, width=width)


def _highlight(d, bx, side, color):
    tl, tt, tr, tb = bx["trim"]
    sl, st, sr, sb = bx["safe"]
    box = {"top": (tl, tt, tr, st), "bottom": (tl, sb, tr, tb),
           "left": (tl, tt, sl, tb), "right": (sr, tt, tr, tb)}[side]
    d.rectangle(box, fill=color)
