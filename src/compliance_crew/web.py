"""Public demo routes behind the web UI (web/index.html).

- Precomputed: web/demo.json, real crew reviews of the 32 benchmark docs (built by eval/build_demo.py).
- Live security check: input rules + PII masking on an uploaded PDF or markdown. No LLM, so it
  always works and costs nothing.
- Live review: the full guarded crew, only when an LLM is configured (CC_LIVE=1, or CC_LLM=gemini
  with GEMINI_API_KEY). One review at a time, at most CC_LIVE_PER_HOUR per hour across all
  visitors, so a public page cannot burn the free-tier quota. Reviews run as background jobs
  that the page polls, because a crew review takes minutes.

The keyed REST /review and MCP /mcp routes in api.py are unchanged; these routes are for people.
"""

import os
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import llm
from .pdf_text import pdf_to_markdown
from .product_doc import split_sections
from .security import input_guard, pii

WEB = Path(__file__).resolve().parents[2] / "web"
MAX_UPLOAD_BYTES = 5_000_000
LIVE = os.getenv("CC_LIVE", "1" if llm.PROVIDER == "gemini" and os.getenv("GEMINI_API_KEY") else "0") == "1"
LIVE_PER_HOUR = int(os.getenv("CC_LIVE_PER_HOUR", "6"))

router = APIRouter()
_jobs: dict[str, dict] = {}
_recent: deque[float] = deque()
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1)


async def read_upload(file: UploadFile | None, text: str | None) -> str:
    if file is not None:
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file larger than 5 MB")
        if raw[:5] == b"%PDF-":
            try:
                return pdf_to_markdown(raw)
            except Exception as e:  # a broken PDF is the uploader's problem, not a server error
                raise HTTPException(422, f"could not read the PDF: {e}")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "upload a PDF or a UTF-8 markdown/text file")
    if text and text.strip():
        return text
    raise HTTPException(400, "send a file or text")


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@router.get("/demo.json", include_in_schema=False)
def demo() -> FileResponse:
    return FileResponse(WEB / "demo.json", media_type="application/json")


@router.get("/api/config")
def config() -> dict:
    return {"live": LIVE, "model": llm.MODEL if LIVE else None, "live_per_hour": LIVE_PER_HOUR}


@router.post("/api/check")
async def check(file: UploadFile | None = File(default=None), text: str | None = Form(default=None)) -> dict:
    """Checkpoint 1 rules and PII masking only: what the crew would (not) get to see."""
    from .api import MAX_DOC_CHARS

    doc = await read_upload(file, text)
    if len(doc) > MAX_DOC_CHARS:
        raise HTTPException(413, f"document longer than {MAX_DOC_CHARS} characters")
    res = input_guard.check(doc)
    masked = pii.mask(res.text)
    return {
        "markdown": doc,
        "sections": [s.heading for s in split_sections(doc) if s.number],
        "input": {"action": res.action, "reasons": res.reasons,
                  "stripped": [{"kind": k, "text": t[:200]} for k, t in res.stripped]},
        "pii": {"counts": masked.counts(), "masked_text": masked.text},
    }


def _run_job(job_id: str, doc: str) -> None:
    from .api import _run

    job = _jobs[job_id]
    job["status"] = "running"
    try:
        review, md, audit = _run(f"web-{job_id}", doc, caller="web")
        job.update(status="done", review=review.model_dump(), report_markdown=md, audit=audit.to_list())
    except Exception as e:
        job.update(status="error", error=str(e)[:300])


@router.post("/api/review")
async def start_review(file: UploadFile | None = File(default=None), text: str | None = Form(default=None)) -> dict:
    if not LIVE:
        raise HTTPException(503, "live reviews are off on this deployment; browse the precomputed reports")
    doc = await read_upload(file, text)
    with _lock:
        now = time.time()
        while _recent and now - _recent[0] > 3600:
            _recent.popleft()
        if len(_recent) >= LIVE_PER_HOUR:
            raise HTTPException(429, f"the demo allows {LIVE_PER_HOUR} live reviews per hour; try again later")
        _recent.append(now)
        job_id = secrets.token_urlsafe(8)
        _jobs[job_id] = {"status": "queued", "created": now}
    _pool.submit(_run_job, job_id, doc)
    return {"job": job_id}


@router.get("/api/review/{job_id}")
def review_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job
