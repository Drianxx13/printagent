"""Web front-end for the same preflight engine the MCP server uses.

    python -m printagent.web          # opens http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import ai_review, catalog, pricing
from .fixer import _strip, fix
from .preflight import render_proof

ROOT = Path(__file__).parent
SAMPLES = ROOT.parent / "samples"
WORK = Path(os.environ.get("PRINTAGENT_WORK", Path.home() / ".printagent" / "web"))
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MAX_BYTES = 50 * 1024 * 1024

app = FastAPI(title="printagent preflight")
JOB_TTL_S = 24 * 3600


def _cleanup_old_jobs() -> None:
    """Uploads are scratch data: drop jobs older than a day."""
    if not WORK.exists():
        return
    now = time.time()
    for d in WORK.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > JOB_TTL_S:
            shutil.rmtree(d, ignore_errors=True)


def _job_dir(job: str) -> Path:
    if not job.isalnum():
        raise HTTPException(400, "bad job id")
    d = WORK / job
    if not d.exists():
        raise HTTPException(404, "job not found")
    return d


def _run(job: str, src: Path, product_id: str, label: str) -> dict:
    d = WORK / job
    proof = d / f"proof_{label}.png"
    res = render_proof(src, product_id, proof, legend=False)
    report = _strip(res["report"])
    report["file"] = src.name
    return {"job": job, "label": label, "report": report,
            "proof_url": f"/files/{job}/{proof.name}"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
def config() -> dict:
    return {"ai_review": ai_review.enabled()}


class AIReq(BaseModel):
    job: str
    product_id: str
    view: str = "original"


@app.post("/api/ai-review")
def ai_review_job(req: AIReq) -> dict:
    if not ai_review.enabled():
        raise HTTPException(400, "AI review is off. Add ANTHROPIC_API_KEY to the .env file.")
    d = _job_dir(req.job)
    if req.view == "fixed":
        src = next((p for p in d.iterdir() if p.stem.endswith("_print-ready")), None)
    else:
        src = next((p for p in d.iterdir() if p.suffix.lower() in ALLOWED
                    and "_print-ready" not in p.stem and not p.stem.startswith("proof_")), None)
    if src is None:
        raise HTTPException(404, "file not found")
    try:
        return ai_review.review(src, req.product_id)
    except Exception as e:  # network, auth, rate limit
        raise HTTPException(502, f"AI review failed: {e}") from e


@app.get("/api/products")
def products() -> list[dict]:
    return [p.summary() for p in catalog.PRODUCTS.values()]


@app.get("/api/samples")
def samples() -> list[str]:
    return sorted(p.name for p in SAMPLES.iterdir()
                  if p.suffix.lower() in ALLOWED and "_print-ready" not in p.stem)


@app.post("/api/preflight")
async def preflight_upload(product_id: str = Form(...), file: UploadFile = File(...)) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Use PDF, PNG, JPG or TIFF.")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File larger than 50 MB.")
    job = uuid.uuid4().hex[:12]
    d = WORK / job
    d.mkdir(parents=True)
    stem = "".join(c for c in Path(file.filename).stem if c.isalnum() or c in "-_")[:60] or "artwork"
    if stem.startswith("proof_"):  # reserved for generated proofs
        stem = "file_" + stem
    src = d / f"{stem}{ext}"
    src.write_bytes(data)
    return _safe_run(job, src, product_id)


class SampleReq(BaseModel):
    name: str
    product_id: str = "business-cards"


@app.post("/api/preflight-sample")
def preflight_sample(req: SampleReq) -> dict:
    if req.name not in samples():
        raise HTTPException(404, "unknown sample")
    job = uuid.uuid4().hex[:12]
    d = WORK / job
    d.mkdir(parents=True)
    src = d / req.name
    shutil.copy(SAMPLES / req.name, src)
    return _safe_run(job, src, req.product_id)


def _safe_run(job: str, src: Path, product_id: str) -> dict:
    try:
        catalog.get(product_id)
        return _run(job, src, product_id, "original")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # unreadable / corrupt file
        raise HTTPException(422, f"Could not read this file: {e}") from e


class FixReq(BaseModel):
    job: str
    product_id: str


@app.post("/api/fix")
def fix_job(req: FixReq) -> dict:
    d = _job_dir(req.job)
    src = next(p for p in d.iterdir() if p.suffix.lower() in ALLOWED
               and "_print-ready" not in p.stem and not p.stem.startswith("proof_"))
    result = fix(src, req.product_id, d)
    if not result["fixed"]:
        return {"fixed": False, "reason": result["reason"]}
    out = Path(result["output_file"])
    after = _run(req.job, out, req.product_id, "fixed")
    return {"fixed": True, "changes": result["changes"], "not_fixed": result["not_fixed"],
            "after": after, "download_url": f"/files/{req.job}/{out.name}"}


class QuoteReq(BaseModel):
    product_id: str
    quantity: int
    paper: str | None = None
    finish: str = "none"
    express: bool = False


@app.post("/api/quote")
def quote(req: QuoteReq) -> dict:
    try:
        return pricing.quote(req.product_id, req.quantity, req.paper, req.finish, req.express)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/files/{job}/{name}")
def files(job: str, name: str) -> FileResponse:
    d = _job_dir(job)
    p = (d / name).resolve()
    if p.parent != d.resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=name if p.suffix == ".pdf" else None)


def main() -> None:
    import uvicorn
    ai_review.load_dotenv(ROOT.parent / ".env")
    port = int(os.environ.get("PRINTAGENT_PORT", 8765))
    url = f"http://127.0.0.1:{port}"
    _cleanup_old_jobs()
    if not os.environ.get("PRINTAGENT_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"printagent preflight running at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
