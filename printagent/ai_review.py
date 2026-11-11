"""AI proofreading: the part rules can't do.

Rules check geometry. A vision model reads the proof like a person would and
flags typos, odd contact details, cut-off logos and hard-to-read text.
Enabled when ANTHROPIC_API_KEY is set (e.g. in a .env file next to the project).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

from PIL import Image

from .catalog import get
from .preflight import load

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_SIDE_PX = 1568

PROMPT = """You are a careful prepress proofreader at an online print shop.
The image is the artwork a customer uploaded for: {product} ({size} mm).

Report only problems the customer would clearly want fixed before printing:
- spelling or grammar mistakes, including misspelled place or street names
- contact details that look wrong or inconsistent (phone format, email, URL)
- logos or images that look cut off, pixelated or stretched
- text that is hard to read (too small for print, low contrast)
Do not comment on design taste. Do not invent problems: if everything looks
fine, return an empty list.

Answer with JSON only, no other text:
{{"issues": [{{"kind": "typo|contact|logo|readability|other",
              "severity": "warning|error",
              "text": "what is wrong, quoting the exact words",
              "suggestion": "how to fix it"}}],
  "transcript": "all the text you can read, in reading order"}}"""

TITLES = {"typo": "Possible typo", "contact": "Check contact details",
          "logo": "Logo or image problem", "readability": "Hard to read",
          "other": "Worth checking"}


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=value lines) so Windows users need no setup."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _png_b64(path: str | Path, product_id: str) -> str:
    art, _ = load(path, product_id)
    img = art.image.copy()
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"issues": [], "transcript": ""}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"issues": [], "transcript": ""}
    issues = [i for i in data.get("issues", []) if isinstance(i, dict) and i.get("text")]
    return {"issues": issues, "transcript": str(data.get("transcript", ""))}


def review(path: str | Path, product_id: str, client=None) -> dict:
    """Ask a vision model to proofread the artwork. Returns checks in the same
    shape as preflight checks (source='ai'), plus the text the model read."""
    product = get(product_id)
    w, h = product.trim_mm
    model = os.environ.get("PRINTAGENT_AI_MODEL", DEFAULT_MODEL)
    if client is None:
        from anthropic import Anthropic
        client = Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=1200,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": _png_b64(path, product_id)}},
            {"type": "text", "text": PROMPT.format(product=product.name, size=f"{w:g} x {h:g}")},
        ]}],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    data = _parse(text)
    checks = [{
        "id": f"ai_{n}",
        "severity": "error" if i.get("severity") == "error" else "warning",
        "title": TITLES.get(i.get("kind"), TITLES["other"]),
        "detail": str(i["text"]),
        "fix": str(i.get("suggestion", "")),
        "auto_fixable": False,
        "source": "ai",
    } for n, i in enumerate(data["issues"])]
    return {"model": model, "checks": checks, "transcript": data["transcript"]}
