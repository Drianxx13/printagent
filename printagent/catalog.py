"""Mock product catalog modelled on a typical online print shop.

All dimensions in millimetres. Prices in EUR excl. VAT.
The catalog is intentionally data-only so it can be swapped for a real
product API without touching the agent tools.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    category: str
    trim_mm: tuple[float, float]          # finished size (w, h)
    bleed_mm: float                       # required bleed on every side
    safe_mm: float                        # keep text this far inside the trim
    min_dpi: int
    papers: dict[str, float]              # paper -> price multiplier
    finishes: dict[str, float]            # finish -> add-on per unit
    tiers: list[tuple[int, float]]        # (min qty, unit price)
    production_days: int
    keywords: tuple[str, ...] = field(default_factory=tuple)

    @property
    def canvas_mm(self) -> tuple[float, float]:
        w, h = self.trim_mm
        return (w + 2 * self.bleed_mm, h + 2 * self.bleed_mm)

    def summary(self) -> dict:
        w, h = self.trim_mm
        cw, ch = self.canvas_mm
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "finished_size_mm": f"{w:g} x {h:g}",
            "artwork_size_with_bleed_mm": f"{cw:g} x {ch:g}",
            "bleed_mm": self.bleed_mm,
            "safe_zone_mm": self.safe_mm,
            "min_dpi": self.min_dpi,
            "papers": list(self.papers),
            "finishes": list(self.finishes),
            "quantities_from": self.tiers[0][0],
            "from_price_eur": round(self.tiers[-1][1], 3),
            "production_days": self.production_days,
        }


_STD_PAPERS = {"350gsm silk": 1.0, "400gsm matte": 1.15, "450gsm uncoated": 1.3}
_FLYER_PAPERS = {"135gsm gloss": 1.0, "170gsm silk": 1.12, "250gsm silk": 1.3}

PRODUCTS: dict[str, Product] = {p.id: p for p in [
    Product("business-cards", "Business Cards", "cards", (85, 55), 3, 3, 300,
            _STD_PAPERS, {"none": 0, "soft-touch": 0.02, "spot UV": 0.05},
            [(50, 0.30), (250, 0.11), (500, 0.07), (1000, 0.045), (2500, 0.03)], 3,
            ("visitekaartjes", "cards", "visiting", "contact")),
    Product("business-cards-square", "Square Business Cards", "cards", (55, 55), 3, 3, 300,
            _STD_PAPERS, {"none": 0, "soft-touch": 0.02},
            [(50, 0.34), (250, 0.13), (500, 0.085), (1000, 0.055)], 3,
            ("square", "cards")),
    Product("flyer-a5", "Flyers A5", "flyers", (148, 210), 3, 4, 300,
            _FLYER_PAPERS, {"none": 0, "gloss laminate": 0.03},
            [(100, 0.22), (500, 0.07), (1000, 0.045), (5000, 0.022)], 2,
            ("leaflet", "a5", "handout", "folder")),
    Product("flyer-a4", "Flyers A4", "flyers", (210, 297), 3, 5, 300,
            _FLYER_PAPERS, {"none": 0, "gloss laminate": 0.05},
            [(100, 0.35), (500, 0.12), (1000, 0.08), (5000, 0.04)], 2,
            ("leaflet", "a4", "handout")),
    Product("poster-a2", "Posters A2", "posters", (420, 594), 3, 10, 150,
            {"170gsm silk": 1.0, "200gsm photo": 1.4}, {"none": 0},
            [(1, 9.50), (10, 4.20), (50, 2.10), (250, 1.30)], 3,
            ("poster", "a2", "affiche")),
    Product("sticker-round-50", "Round Stickers 50mm", "stickers", (50, 50), 2, 3, 300,
            {"white vinyl": 1.0, "transparent vinyl": 1.25}, {"none": 0, "matte laminate": 0.01},
            [(50, 0.40), (250, 0.14), (1000, 0.06), (5000, 0.03)], 4,
            ("sticker", "label", "round", "circle")),
    Product("postcard-a6", "Postcards A6", "cards", (148, 105), 3, 4, 300,
            _STD_PAPERS, {"none": 0, "soft-touch": 0.03},
            [(50, 0.32), (250, 0.12), (1000, 0.05)], 3,
            ("postcard", "a6", "invitation", "card")),
]}


def search(query: str, limit: int = 5) -> list[dict]:
    """Very small keyword ranker – good enough for a demo, swappable for embeddings."""
    terms = [t for t in query.lower().replace(",", " ").split() if len(t) > 1]
    scored = []
    for p in PRODUCTS.values():
        hay = " ".join([p.id, p.name, p.category, *p.keywords]).lower()
        score = sum(2 if t in p.name.lower() else 1 for t in terms if t in hay)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p.summary() for _, p in scored[:limit]] or [p.summary() for p in PRODUCTS.values()][:limit]


def get(product_id: str) -> Product:
    try:
        return PRODUCTS[product_id]
    except KeyError:
        raise ValueError(f"Unknown product '{product_id}'. Valid ids: {', '.join(PRODUCTS)}") from None
