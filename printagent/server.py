"""MCP server: lets any AI assistant (Claude, ChatGPT, Cursor...) find a print
product, price it, check and fix the artwork, and prepare an order."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from . import catalog, pricing, store
from .fixer import fix, _strip
from .preflight import preflight
from .preflight import render_proof as _render_proof

mcp = FastMCP(
    "printagent",
    instructions=(
        "Agentic ordering for an online print shop. Typical flow: search_products -> "
        "get_quote -> preflight_artwork -> (fix_artwork if needed) -> render_proof "
        "(look at the proof yourself: check spelling, phone numbers, URLs, cropped logos) "
        "-> confirm with the customer -> create_draft_order. Never create an order "
        "without showing the customer the quote and the proof first."
    ),
)


@mcp.tool()
def search_products(query: str) -> list[dict]:
    """Find print products from a natural-language need, e.g. 'cards for my bakery'
    or 'A5 leaflets'. Returns sizes, bleed/safe-zone specs, papers and finishes."""
    return catalog.search(query)


@mcp.tool()
def get_quote(product_id: str, quantity: int, paper: str | None = None,
              finish: str = "none", express: bool = False) -> dict:
    """Price an order (EUR, incl. 21% VAT) with delivery date and a hint for the
    next quantity tier. paper/finish must be options from search_products."""
    return pricing.quote(product_id, quantity, paper, finish, express)


@mcp.tool()
def preflight_artwork(file_path: str, product_id: str) -> dict:
    """Check a customer's artwork (PDF, PNG, JPG, TIFF) against the product's print
    specs: size and bleed, effective resolution, CMYK/RGB, transparency, embedded
    fonts, empty bleed and text too close to the cut. Returns a preflight_id that
    create_draft_order requires, a plain-language customer_message, and which
    issues fix_artwork can repair automatically."""
    report = preflight(file_path, product_id)
    out = _strip(report)
    out["preflight_id"] = store.save_preflight(report)
    return out


@mcp.tool()
def fix_artwork(file_path: str, product_id: str) -> dict:
    """Automatically repair what can be repaired without changing the design:
    add missing bleed (mirrored edges), fill empty bleed, flatten transparency,
    convert to CMYK. Never upscales low-resolution images. Writes
    <name>_print-ready.pdf next to the original and re-runs preflight on it
    (the new file gets its own preflight_id)."""
    result = fix(file_path, product_id, Path(file_path).parent)
    if result.get("fixed"):
        report = preflight(result["output_file"], product_id)
        result["after"]["preflight_id"] = store.save_preflight(report)
    return result


@mcp.tool()
def render_proof(file_path: str, product_id: str) -> list:
    """Render a print proof with the cut line, safe zone and bleed marked. LOOK at
    the image: report typos, wrong contact details, cut-off logos or low-contrast
    text to the customer, since those are things the automatic checks can't see."""
    out = Path(tempfile.gettempdir()) / f"proof_{Path(file_path).stem}.png"
    res = _render_proof(file_path, product_id, out)
    summary = {k: res["report"][k] for k in ("status", "customer_message")}
    return [Image(path=str(out)), json.dumps(summary)]


@mcp.tool()
def create_draft_order(product_id: str, quantity: int, front_preflight_id: str,
                       back_preflight_id: str | None = None, paper: str | None = None,
                       finish: str = "none", express: bool = False,
                       note: str = "") -> dict:
    """Create a DRAFT order (not paid, not sent to production). Refuses if the
    artwork failed preflight, was checked for another product, or changed since
    it was checked. Only call after the customer approved quote and proof."""
    q = pricing.quote(product_id, quantity, paper, finish, express)
    order = store.create_order(product_id, q, front_preflight_id, back_preflight_id, note)
    order["next_step"] = "Customer reviews and pays at checkout (not implemented in this prototype)."
    return order


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Look up a draft order by id."""
    return store.get_order(order_id)


@mcp.prompt()
def order_print(need: str) -> str:
    """Start a guided print order."""
    return (f"I want to order print: {need}. Find the right product, quote it, check my "
            "artwork, fix what you can, show me the proof, and prepare a draft order once I approve.")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
