"""Safe automatic fixes. Rule: only fix what can be fixed without changing
the design or hiding a problem (e.g. we never upscale to fake resolution)."""
from __future__ import annotations

from pathlib import Path

import os

import numpy as np
import pymupdf
from PIL import Image, ImageCms

from .preflight import MM_PER_IN, _flatten, load, preflight

PDF_RASTER_DPI = 300

# A real press profile (e.g. FOGRA39 for European offset) gives far more
# faithful colour than Pillow's naive RGB->CMYK formula. Point
# PRINTAGENT_CMYK_ICC at one; common install locations are tried too.
_ICC_CANDIDATES = [
    os.environ.get("PRINTAGENT_CMYK_ICC", ""),
    "/usr/share/texlive/texmf-dist/tex/generic/colorprofiles/FOGRA39L_coated.icc",
    "/usr/share/color/icc/ISOcoated_v2_eci.icc",
    "/Library/Application Support/Adobe/Color/Profiles/Recommended/CoatedFOGRA39.icc",
    "C:/Windows/System32/spool/drivers/color/CoatedFOGRA39.icc",
]


def to_cmyk(img: Image.Image) -> tuple[Image.Image, str]:
    for path in filter(None, _ICC_CANDIDATES):
        if Path(path).exists():
            srgb = ImageCms.createProfile("sRGB")
            cmyk = ImageCms.getOpenProfile(path)
            out = ImageCms.profileToProfile(
                img, srgb, cmyk, renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="CMYK")
            return out, f"Converted to CMYK using {Path(path).stem} (perceptual)."
    return img.convert("CMYK"), ("Converted to CMYK with a generic formula "
                                 "(set PRINTAGENT_CMYK_ICC to a press profile for accurate colour).")


def fix(path: str | Path, product_id: str, out_dir: str | Path) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = preflight(path, product_id)
    art, _ = load(path, product_id)
    p = art.product

    if art.geometry == "mismatch":
        return {"fixed": False, "reason": "Size/proportions don't match the product; "
                "fixing would mean cropping or distorting the design, so a human should decide.",
                "before": _strip(before)}

    changes: list[str] = []
    if art.kind == "pdf":
        with pymupdf.open(path) as doc:
            pix = doc[0].get_pixmap(dpi=PDF_RASTER_DPI, alpha=False)
        src = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        dpi = PDF_RASTER_DPI
        changes.append(f"Rasterised page 1 at {PDF_RASTER_DPI} dpi (vector text becomes pixels).")
    else:
        with Image.open(path) as raw:
            raw.load()
        if raw.mode != "CMYK" and ("A" in raw.getbands() or "transparency" in raw.info):
            changes.append("Flattened transparency onto white.")
        src = _flatten(raw)
        dpi = min(art.effective_dpi or PDF_RASTER_DPI, PDF_RASTER_DPI)
        if art.effective_dpi and art.effective_dpi > PDF_RASTER_DPI * 1.02:
            changes.append(f"Downsampled from {art.effective_dpi:.0f} to {PDF_RASTER_DPI} dpi.")

    def px(mm: float) -> int:
        return round(mm / MM_PER_IN * dpi)

    tw, th = art.trim_mm
    bp = px(p.bleed_mm)

    if art.geometry == "trim_only":
        img = src.resize((px(tw), px(th)), Image.LANCZOS)
        arr = np.pad(np.asarray(img), ((bp, bp), (bp, bp), (0, 0)), mode="symmetric")
        changes.append(f"Added {p.bleed_mm:g} mm bleed by mirroring the edges of the design.")
    else:
        img = src.resize((px(tw) + 2 * bp, px(th) + 2 * bp), Image.LANCZOS)
        arr = np.asarray(img).copy()
        bad = before["_flagged_sides"].get("bleed", [])
        if bad:
            inner = arr[bp:-bp, bp:-bp]
            mirrored = np.pad(inner, ((bp, bp), (bp, bp), (0, 0)), mode="symmetric")
            sl = {"top": np.s_[:bp, :], "bottom": np.s_[-bp:, :],
                  "left": np.s_[:, :bp], "right": np.s_[:, -bp:]}
            for side in bad:
                arr[sl[side]] = mirrored[sl[side]]
            changes.append(f"Filled empty bleed ({', '.join(bad)}) by mirroring the edge.")

    out_img, note = to_cmyk(Image.fromarray(arr))
    changes.append(note)
    out = out_dir / f"{path.stem}_print-ready.pdf"
    out_img.save(out, "PDF", resolution=dpi)

    after = preflight(out, product_id)
    still = [c["title"] for c in after["checks"] if c["severity"] != "ok"]
    return {
        "fixed": True,
        "output_file": str(out),
        "changes": changes,
        "not_fixed": still,
        "status_before": before["status"],
        "status_after": after["status"],
        "after": _strip(after),
    }


def _strip(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}
