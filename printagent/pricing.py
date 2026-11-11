from __future__ import annotations

from datetime import date, timedelta

from .catalog import get

VAT_NL = 0.21
EXPRESS_SURCHARGE = 0.35


def _add_business_days(start: date, days: int) -> date:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def quote(product_id: str, quantity: int, paper: str | None = None,
          finish: str = "none", express: bool = False, today: date | None = None) -> dict:
    p = get(product_id)
    if quantity < p.tiers[0][0]:
        raise ValueError(f"Minimum quantity for {p.name} is {p.tiers[0][0]}.")
    paper = paper or next(iter(p.papers))
    if paper not in p.papers:
        raise ValueError(f"Paper '{paper}' not available. Options: {list(p.papers)}")
    if finish not in p.finishes:
        raise ValueError(f"Finish '{finish}' not available. Options: {list(p.finishes)}")

    unit = [price for min_q, price in p.tiers if quantity >= min_q][-1]
    unit = unit * p.papers[paper] + p.finishes[finish]
    subtotal = unit * quantity
    if express:
        subtotal *= 1 + EXPRESS_SURCHARGE
    days = max(1, p.production_days - 2) if express else p.production_days
    ship = _add_business_days(today or date.today(), days + 1)

    # Upsell hint: what does the next tier cost?
    next_tier = next(((q, pr) for q, pr in p.tiers if q > quantity), None)
    hint = None
    if next_tier:
        nq, npr = next_tier
        ntotal = (npr * p.papers[paper] + p.finishes[finish]) * nq * (1 + EXPRESS_SURCHARGE if express else 1)
        hint = (f"{nq} units would cost EUR {ntotal:.2f} "
                f"(EUR {ntotal / nq:.3f}/unit vs EUR {subtotal / quantity:.3f}/unit now).")

    return {
        "product_id": p.id,
        "product": p.name,
        "quantity": quantity,
        "paper": paper,
        "finish": finish,
        "express": express,
        "unit_price_eur": round(subtotal / quantity, 4),
        "subtotal_eur": round(subtotal, 2),
        "vat_eur": round(subtotal * VAT_NL, 2),
        "total_eur": round(subtotal * (1 + VAT_NL), 2),
        "estimated_delivery": ship.isoformat(),
        "next_tier_hint": hint,
    }
