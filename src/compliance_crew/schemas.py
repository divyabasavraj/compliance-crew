"""Data shapes shared by the single-agent baseline, the crew and the evaluation."""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]


class Finding(BaseModel):
    feature: str
    rule_id: str = Field(description="Paragraph id exactly as given, e.g. DLD2025:12(i)")
    issue: str = Field(description="How the product conflicts with the rule, one or two sentences")
    doc_evidence: str = Field(description="Short verbatim quote from the product document")
    severity: Severity = "medium"
    fix: str = ""
    verifier_note: str = ""


class Review(BaseModel):
    system: str
    doc_id: str
    status: str = "reviewed"  # reviewed | quarantined (blocked by the input check)
    findings: list[Finding]
    candidates: list[Finding] = []  # crew only: findings before the Verifier
    summary: str = ""
    trace: dict = {}
    seconds: float = 0.0
