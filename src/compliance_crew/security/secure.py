"""The guarded review: the crew wrapped in the five checkpoints.

    input check -> (Guardian on grey zone) -> PII masking -> crew with message checks
    -> output check -> audit log
"""

import hashlib
import time
from pathlib import Path

from .. import crew
from ..ingest import DATA_DIR
from ..llm import Tracer
from ..schemas import Review
from ..tools import Toolbox
from . import guardian, input_guard, output_guard, pii
from .audit import AuditLog
from .message_guard import MessageGuard


def corpus_intact() -> bool:
    """Checkpoint 3 (integrity half): the corpus must match the hash pinned at ingest time."""
    pinned = (DATA_DIR / "corpus.sha256").read_text().strip()
    return hashlib.sha256((DATA_DIR / "corpus.jsonl").read_bytes()).hexdigest() == pinned


def _quarantine(doc_id: str, reason: str, seconds: float, tracer: Tracer) -> Review:
    return Review(system="crew+guard", doc_id=doc_id, status="quarantined", findings=[],
                  summary=f"Quarantined for human review: {reason}", trace=tracer.summary(), seconds=seconds)


def screen(doc_id: str, doc: str, audit: AuditLog, use_guardian: bool = True, tracer: Tracer | None = None):
    """Checkpoint 1 + PII masking. Returns (masked doc or None if blocked, input result, masked)."""
    res = input_guard.check(doc)
    for kind, text in res.stripped:
        audit.add("input", "input_guard", "strip", f"{kind}: {text[:120]}")
    if res.action != "block" and use_guardian:
        j = guardian.judge(res.text, tracer)
        audit.add("input", "guardian", "block" if j.is_attack else "pass", j.reason)
        if j.is_attack:
            res.action = "block"
    if res.action == "block":
        audit.add("input", "input_guard", "block", ",".join(res.reasons))
        return None, res, None
    audit.add("input", "input_guard", "pass", ",".join(res.reasons))
    masked = pii.mask(res.text)
    if masked.entities:
        audit.add("pii", "pii_masker", "mask", str(masked.counts()))
    return masked.text, res, masked


def secure_review(doc_id: str, doc: str, toolbox: Toolbox, audit_path: Path | None = None,
                  use_guardian: bool = True, caller: str = "local") -> tuple[Review, AuditLog]:
    start = time.perf_counter()
    audit, tracer = AuditLog(doc_id, caller), Tracer()
    if not corpus_intact():
        audit.add("retrieval", "toolbox", "block", "corpus hash mismatch")
        review = _quarantine(doc_id, "RBI corpus failed its integrity check", 0.0, tracer)
    else:
        text, res, _ = screen(doc_id, doc, audit, use_guardian, tracer)
        if text is None:
            review = _quarantine(doc_id, "the document contains instructions aimed at the AI reviewer ("
                                 + ", ".join(res.reasons) + ")", round(time.perf_counter() - start, 1), tracer)
        else:
            guard = MessageGuard(audit)
            review = crew.review(doc_id, text, toolbox, guard=guard)
            review = output_guard.check_review(review, set(toolbox.retriever.by_id), audit)
            review = review.model_copy(update={
                "system": "crew+guard",
                "seconds": round(time.perf_counter() - start, 1),
                "trace": review.trace | {"guardian_calls": len(tracer.calls), "messages_dropped": guard.dropped},
            })
    if audit_path:
        audit.write(audit_path)
    return review, audit
