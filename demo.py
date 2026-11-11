"""End-to-end demo over real MCP (stdio), exactly as Claude Desktop would call it.

    python demo.py samples/03_no_bleed.png
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def show(title: str, result) -> dict | None:
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))
    data = None
    for block in result.content:
        if block.type == "text":
            try:
                data = json.loads(block.text)
                print(json.dumps(data, indent=2)[:1400])
            except json.JSONDecodeError:
                print(block.text[:1400])
        elif block.type == "image":
            print(f"[proof image, {len(block.data) * 3 // 4 // 1024} KB {block.mimeType}]")
    return data


async def main(artwork: str) -> None:
    artwork = str(Path(artwork).resolve())
    params = StdioServerParameters(command=sys.executable, args=["-m", "printagent.server"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))

        call = s.call_tool
        show("search_products('business cards for my bakery')",
             await call("search_products", {"query": "business cards for my bakery"}))
        show("get_quote(500, soft-touch)", await call("get_quote", {
            "product_id": "business-cards", "quantity": 500, "finish": "soft-touch"}))
        pf = show("preflight_artwork", await call("preflight_artwork", {
            "file_path": artwork, "product_id": "business-cards"}))
        pf_id = pf["preflight_id"]

        if pf["status"] == "fail" and pf["auto_fixable"]:
            fx = show("fix_artwork", await call("fix_artwork", {
                "file_path": artwork, "product_id": "business-cards"}))
            if fx.get("fixed") and fx["status_after"] != "fail":
                pf_id, artwork = fx["after"]["preflight_id"], fx["output_file"]

        show("render_proof", await call("render_proof", {
            "file_path": artwork, "product_id": "business-cards"}))
        show("create_draft_order", await call("create_draft_order", {
            "product_id": "business-cards", "quantity": 500, "finish": "soft-touch",
            "front_preflight_id": pf_id}))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "samples/03_no_bleed.png"))
