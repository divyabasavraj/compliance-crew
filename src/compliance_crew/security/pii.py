"""Indian PII detection and masking, deterministic (regex + checksums), no model.

Presidio was the plan, but it needs a spaCy model and its built-in recognizers do not cover
Aadhaar/PAN/UPI/IFSC/GSTIN well. These recognizers are small, testable and run in microseconds.
Each distinct value gets a stable placeholder (<AADHAAR_1>, <PAN_1>, ...), so the agents can still
reason ("the same PAN appears twice") without seeing the value.
"""

import re
from dataclasses import dataclass, field

# Verhoeff tables, used by UIDAI for the Aadhaar check digit
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_ok(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def _aadhaar_ok(m: re.Match) -> bool:
    digits = re.sub(r"\D", "", m.group())
    return digits[0] not in "01" and verhoeff_ok(digits)


# Order matters: longer / more specific patterns first, so a GSTIN is not also masked as a PAN.
RECOGNIZERS: list[tuple[str, re.Pattern, object]] = [
    ("GSTIN", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"), None),
    ("PAN", re.compile(r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b"), None),
    ("AADHAAR", re.compile(r"(?<![\d₹,.])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\d,])"), _aadhaar_ok),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), None),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), None),
    ("UPI", re.compile(r"\b[\w.-]{2,256}@(?:ok)?[a-z]{2,64}\b"), None),
    ("PHONE", re.compile(r"(?<![\d₹,.])(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?![\d,])"), None),
    # bank account numbers only next to an account keyword: a bare 9-18 digit number is too ambiguous
    ("ACCOUNT", re.compile(r"(?i)(?<=a/c[ :.-])\s*\d{9,18}\b|(?<=account no[ :.-])\s*\d{9,18}\b|"
                           r"(?<=account number[ :.-])\s*\d{9,18}\b|(?<=account[ :])\s*\d{9,18}\b"), None),
]


@dataclass
class Masked:
    text: str
    entities: dict[str, str] = field(default_factory=dict)  # placeholder -> original value

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ph in self.entities:
            kind = ph.strip("<>").rsplit("_", 1)[0]
            out[kind] = out.get(kind, 0) + 1
        return out


def find_pii(text: str) -> list[tuple[str, int, int, str]]:
    """All PII spans as (kind, start, end, value), non-overlapping, first recognizer wins."""
    spans: list[tuple[str, int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern, check in RECOGNIZERS:
        for m in pattern.finditer(text):
            s, e = m.start(), m.end()
            value = m.group().strip()
            s += len(m.group()) - len(m.group().lstrip())
            if check and not check(m):
                continue
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            spans.append((kind, s, e, value))
    return sorted(spans, key=lambda x: x[1])


def mask(text: str) -> Masked:
    out = Masked(text)
    by_value: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    pieces, last = [], 0
    for kind, s, e, value in find_pii(text):
        key = (kind, re.sub(r"[\s-]", "", value))
        if key not in by_value:
            counters[kind] = counters.get(kind, 0) + 1
            by_value[key] = f"<{kind}_{counters[kind]}>"
            out.entities[by_value[key]] = value
        pieces += [text[last:s], by_value[key]]
        last = e
    out.text = "".join(pieces) + text[last:]
    return out
