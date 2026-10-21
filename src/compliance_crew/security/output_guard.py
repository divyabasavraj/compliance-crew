"""Checkpoint 4: the final report, before it leaves the system.

- every finding must cite a paragraph that exists in the corpus (otherwise dropped)
- no PII in any text field or the rendered report (redacted to the placeholder kind)
- no leaked system prompt: any 8-word run copied from an agent prompt is redacted
- the disclaimer must be present
"""

import re

from ..schemas import Review
from .audit import AuditLog
from .pii import find_pii

SHINGLE = 8
# phrases the prompts ask the model to output; they are not a leak (false redaction on P12)
EXPECTED_OUTPUT = ["no gaps were found against the reviewed rbi rules"]


def _shingles(text: str) -> set[str]:
    text = " ".join(text.lower().split())  # prompts wrap lines mid-phrase
    for phrase in EXPECTED_OUTPUT:
        text = text.replace(phrase, " | ")
    w = re.findall(r"[a-z0-9|]+", text)
    return {" ".join(w[i : i + SHINGLE]) for i in range(len(w) - SHINGLE + 1) if "|" not in w[i : i + SHINGLE]}


def _prompts() -> list[str]:
    from .. import crew, single_agent
    from . import guardian

    return [crew.ORCH_SYSTEM, crew.RESEARCH_SYSTEM, crew.GAP_SYSTEM, crew.VERIFY_SYSTEM, crew.RISK_SYSTEM,
            crew.REPORT_SYSTEM, single_agent.SYSTEM, guardian.SYSTEM]


def leaks_prompt(text: str) -> bool:
    out = _shingles(text)
    return any(out & _shingles(p) for p in _prompts())


def redact_pii(text: str) -> tuple[str, int]:
    spans = find_pii(text)
    for kind, s, e, _ in reversed(spans):
        text = text[:s] + f"<{kind}>" + text[e:]
    return text, len(spans)


def check_review(review: Review, known_ids: set[str], audit: AuditLog) -> Review:
    kept = []
    for f in review.findings:
        if f.rule_id not in known_ids:
            audit.add("output", "report_writer", "drop", f"uncited finding: {f.rule_id}")
            continue
        updates = {}
        for name in ("issue", "doc_evidence", "fix", "feature"):
            value = getattr(f, name)
            clean, n = redact_pii(value)
            if leaks_prompt(clean):
                clean, n = "[removed: matched an internal prompt]", n + 1
            if n:
                updates[name] = clean
                audit.add("output", "report_writer", "redact", f"{name} of {f.rule_id}")
        kept.append(f.model_copy(update=updates))
    summary, n = redact_pii(review.summary)
    if leaks_prompt(summary):
        summary, n = "Summary withheld by the output check; see findings.", n + 1
    if n:
        audit.add("output", "report_writer", "redact", "summary")
    return review.model_copy(update={"findings": kept, "summary": summary})


def check_markdown(md: str, disclaimer: str, audit: AuditLog) -> str:
    md, n = redact_pii(md)
    if n:
        audit.add("output", "report_writer", "redact", f"{n} PII spans in rendered report")
    if disclaimer not in md:
        md += f"\n_{disclaimer}_\n"
        audit.add("output", "report_writer", "fix", "disclaimer added")
    audit.add("output", "report_writer", "pass", "final report checked")
    return md
