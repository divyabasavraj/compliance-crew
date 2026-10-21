"""Checkpoint 2: every message passed from one agent to the next.

Agents talk through pydantic schemas, so the attack surface is the free-text fields (feature
names, search queries, issues, quotes, verifier reasons, fixes, the summary). Each field is
scanned with the same instruction rules as the input; a flagged item is dropped and logged.
A compromised upstream agent therefore cannot pass instructions to the next one.
"""

from .audit import AuditLog
from .input_guard import BLOCK_SCORE, scan

MAX_QUERY_CHARS = 200


class MessageGuard:
    def __init__(self, audit: AuditLog):
        self.audit = audit
        self.dropped = 0

    def allow(self, agent: str, field: str, text: str) -> bool:
        score, reasons = scan(text)
        if field == "query" and len(text) > MAX_QUERY_CHARS:
            score, reasons = BLOCK_SCORE, reasons + ["query_too_long"]
        if score >= 1:  # stricter than the input: agent output has no reason to address an AI
            self.dropped += 1
            self.audit.add("message", agent, "drop", f"{field}: {','.join(reasons)}: {text[:120]}")
            return False
        return True


class NoGuard:
    """Default for unguarded runs (the v1/v2 benchmark numbers)."""

    dropped = 0

    def allow(self, agent: str, field: str, text: str) -> bool:
        return True
