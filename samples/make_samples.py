"""Generate test artwork for a fictional bakery's business cards (85 x 55 mm + 3 mm bleed).

Each file reproduces a classic customer mistake that ends up as a support
ticket or a reprint at an online print shop.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
DPI = 300
MM = DPI / 25.4
TRIM = (85, 55)
BLEED = 3
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]
FONT_FILE = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)

BG = (233, 84, 32)      # warm orange
INK = (255, 255, 255)


def font(size_mm: float, dpi: int = DPI):
    px = round(size_mm * dpi / 25.4)
    return ImageFont.truetype(FONT_FILE, px) if FONT_FILE else ImageFont.load_default(px)


def card(*, bleed=True, bleed_filled=True, text_inset_mm=8.0, dpi=DPI,
         city="Rotterdam", mode="RGB", size_mm=None) -> Image.Image:
    mm = dpi / 25.4
    b = BLEED if bleed else 0
    w_mm, h_mm = size_mm or (TRIM[0] + 2 * b, TRIM[1] + 2 * b)
    img = Image.new("RGB", (round(w_mm * mm), round(h_mm * mm)), "white")
    d = ImageDraw.Draw(img)
    if bleed_filled:
        d.rectangle([0, 0, img.width, img.height], fill=BG)
    else:
        d.rectangle([b * mm, b * mm, (w_mm - b) * mm, (h_mm - b) * mm], fill=BG)
    x = (b + text_inset_mm) * mm
    d.text((x, (b + 8) * mm), "Bakkerij Moerman", font=font(5.2, dpi), fill=INK)
    d.text((x, (b + 16) * mm), "Sourdough & pastries since 1987", font=font(2.6, dpi), fill=INK)
    d.text((x, (b + 34) * mm), f"Witte de Withstraat 12, {city}", font=font(2.4, dpi), fill=INK)
    d.text((x, (b + 39) * mm), "+31 10 123 4567  ·  moerman.example", font=font(2.4, dpi), fill=INK)
    d.ellipse([(w_mm - b - 22) * mm, (b + 7) * mm, (w_mm - b - 8) * mm, (b + 21) * mm],
              outline=INK, width=round(0.6 * mm))
    return img.convert(mode) if mode != "RGB" else img


def pdf_card(path: Path, embed: bool) -> None:
    doc = pymupdf.open()
    w, h = (TRIM[0] + 2 * BLEED) * 72 / 25.4, (TRIM[1] + 2 * BLEED) * 72 / 25.4
    page = doc.new_page(width=w, height=h)
    page.draw_rect(page.rect, color=None, fill=[c / 255 for c in BG])
    if embed and FONT_FILE:
        page.insert_font(fontname="brand", fontfile=FONT_FILE)
        fname = "brand"
    else:
        fname = "helv"   # Base-14 Helvetica: referenced, not embedded
    pt = 72 / 25.4
    page.insert_text(((BLEED + 8) * pt, (BLEED + 13) * pt), "Bakkerij Moerman",
                     fontname=fname, fontsize=15, color=(1, 1, 1))
    page.insert_text(((BLEED + 8) * pt, (BLEED + 40) * pt), "+31 10 123 4567",
                     fontname=fname, fontsize=7, color=(1, 1, 1))
    doc.save(path)


def main() -> None:
    card(mode="CMYK").save(OUT / "01_good_card.tif", dpi=(DPI, DPI))
    pdf_card(OUT / "02_good_card.pdf", embed=True)
    card(bleed=False).save(OUT / "03_no_bleed.png", dpi=(DPI, DPI))
    card(bleed_filled=False).save(OUT / "04_empty_bleed.png", dpi=(DPI, DPI))
    card(text_inset_mm=1.0).save(OUT / "05_text_near_cut.png", dpi=(DPI, DPI))
    card(dpi=110).convert("RGB").save(OUT / "06_low_res.jpg", dpi=(110, 110), quality=85)
    card(size_mm=(80, 80)).save(OUT / "07_wrong_size.png")
    pdf_card(OUT / "08_font_not_embedded.pdf", embed=False)
    card(city="Rotterdma", mode="CMYK").save(OUT / "09_typo_passes_checks.tif", dpi=(DPI, DPI))
    print("samples written to", OUT)


if __name__ == "__main__":
    main()
