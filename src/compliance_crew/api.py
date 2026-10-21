"""HTTP API and MCP tool (PLAN.md 8.7): ComplianceCrew callable by people and by outside agents.

Both routes sit behind the same API-key check, and every call goes through secure_review(), so
an outside agent gets exactly the checkpoints an uploaded doc gets (input check, PII masking,
message checks, output check) and is logged in the audit trail under its key id.

Keys: CC_API_KEYS="key_id:sha256(secret),..." (only hashes are stored; `compliance-crew keygen`).

    uv run compliance-crew serve            # REST: POST /review   MCP: /mcp (streamable HTTP)
"""

import contextlib
import contextvars
import hashlib
import hmac
import os
import secrets
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import crew
from .schemas import Review
from .security import output_guard
from .security.audit import AuditLog

MAX_DOC_CHARS = 50_000
AUDIT_PATH = Path(os.getenv("CC_AUDIT_LOG", "logs/audit.jsonl"))
_caller: contextvars.ContextVar[str] = contextvars.ContextVar("caller", default="unknown")


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_key(key_id: str) -> tuple[str, str]:
    """Returns (secret to hand to the caller, line to add to CC_API_KEYS)."""
    secret = "cc_" + secrets.token_urlsafe(24)
    return secret, f"{key_id}:{hash_key(secret)}"


def _keys() -> dict[str, str]:
    pairs = [p.split(":", 1) for p in os.getenv("CC_API_KEYS", "").split(",") if ":" in p]
    return {h.strip(): kid.strip() for kid, h in pairs}


def caller_for(api_key: str | None) -> str | None:
    if not api_key:
        return None
    h = hash_key(api_key)
    for known, kid in _keys().items():
        if hmac.compare_digest(h, known):
            return kid
    return None


def _deny(reason: str, where: str) -> None:
    a = AuditLog(doc_id="-", caller="rejected")
    a.add("access", where, "block", reason)
    a.write(AUDIT_PATH)


@lru_cache(maxsize=1)
def _toolbox():
    from .retrieval import Retriever
    from .tools import Toolbox

    return Toolbox(Retriever(use_reranker=False))


def run_review(doc_id: str, text: str, caller: str) -> tuple[Review, str]:
    from .security.secure import secure_review

    if len(text) > MAX_DOC_CHARS:
        raise ValueError(f"document longer than {MAX_DOC_CHARS} characters")
    review, audit = secure_review(doc_id, text, _toolbox(), caller=caller)
    md = output_guard.check_markdown(crew.render_markdown(review, _toolbox()), crew.DISCLAIMER, audit)
    audit.write(AUDIT_PATH)
    return review, md


# ---------- MCP ----------

def build_mcp():
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("ComplianceCrew", instructions=(
        "Reviews an Indian digital-lending product document against RBI Digital Lending Directions 2025 "
        "and the KFS circular. Returns cited findings. Decision support only, not legal advice."))

    @mcp.tool(description="Review a fintech product document (markdown) for RBI digital lending compliance gaps.")
    def review_product_doc(text: str, doc_id: str = "mcp-doc") -> str:
        _, md = run_review(doc_id, text, caller=f"mcp:{_caller.get()}")
        return md

    return mcp


class APIKeyMiddleware:
    """Pure-ASGI key check for the mounted MCP app (FastAPI dependencies do not reach mounts)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            key = headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()
            caller = caller_for(key)
            if caller is None:
                _deny("unknown or missing API key", "mcp")
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"detail":"invalid API key"}'})
                return
            token = _caller.set(caller)
            try:
                return await self.app(scope, receive, send)
            finally:
                _caller.reset(token)
        return await self.app(scope, receive, send)


# ---------- REST ----------

class ReviewRequest(BaseModel):
    text: str = Field(description="Product document in markdown")
    doc_id: str = "api-doc"


class ReviewResponse(BaseModel):
    review: Review
    report_markdown: str


def require_key(x_api_key: str | None = Header(default=None)) -> str:
    caller = caller_for(x_api_key)
    if caller is None:
        _deny("unknown or missing API key", "rest")
        raise HTTPException(status_code=401, detail="invalid API key")
    return caller


def create_app() -> FastAPI:
    mcp = build_mcp()
    mcp_app = mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(_):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="ComplianceCrew", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/review", response_model=ReviewResponse)
    def review(req: ReviewRequest, caller: str = Depends(require_key)) -> ReviewResponse:
        try:
            r, md = run_review(req.doc_id, req.text, caller=f"rest:{caller}")
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))
        return ReviewResponse(review=r, report_markdown=md)

    app.mount("/mcp", APIKeyMiddleware(mcp_app))
    return app
