"""Checkpoint 1: the uploaded product document, before any agent sees it.

Step 1 strips content a human reviewer would not see but a model would: HTML comments, hidden or
white-on-white spans, zero-width characters, front-matter metadata. Anything stripped is kept
for the audit log, and hidden text that reads like an instruction blocks the doc on its own.
Step 2 scans the visible text with instruction-override rules. Rules are deterministic, so the
guard cannot itself be prompt-injected; the LLM Guardian (guardian.py) only sees grey-zone text.
"""

import re
import unicodedata
from dataclasses import dataclass, field

ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")
HIDDEN_PATTERNS = [
    ("html_comment", re.compile(r"<!--.*?-->", re.S)),
    ("hidden_html", re.compile(
        r"<(\w+)[^>]*style\s*=\s*['\"][^'\"]*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|"
        r"color\s*:\s*(white|#fff\b|#ffffff|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\)))[^'\"]*['\"][^>]*>.*?</\1>",
        re.S | re.I)),
    ("front_matter", re.compile(r"\A---\n.*?\n---\n", re.S)),
    ("md_link_title", re.compile(r"\[[^\]]*\]\([^)\s]*\s+\"([^\"]+)\"\)")),
]

# Instruction-override patterns. Each is (name, weight, regex). Weight 2 blocks alone, weight 1
# needs a second hit (or the Guardian) to block: a tricky benign doc may say "ignore" once.
RULES: list[tuple[str, int, re.Pattern]] = [
    # the target must be the verb's object ("ignore all previous instructions"), not just nearby:
    # "customers who forget their PIN ... previous instructions sent by SMS" is benign (B08)
    ("override", 2, re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\s+(?:(?:all|any|the|of|previous|prior|above|earlier|your|these|those|"
        r"system|rbi|rbi's|regulatory|compliance|review|reviewer's)\s+){0,4}"
        r"(instructions?|rules?|guidelines?|prompts?|directions?|checks?|regulations?)\b", re.I)),
    ("verdict_steer", 2, re.compile(
        r"\b(mark|treat|report|classify|rate|label|declare)\b[^.\n]{0,40}\b(as|is)\s+(fully\s+)?(compliant|no\s+violations?|clean|approved|safe)\b",
        re.I)),
    ("suppress", 2, re.compile(
        r"\b(do\s+not|don't|never)\s+(report|flag|mention|list|include)\b[^.\n]{0,40}\b(violations?|findings?|issues?|gaps?|problems?)\b",
        re.I)),
    ("empty_output", 2, re.compile(r"\b(return|output|reply with|respond with)\b[^.\n]{0,30}\b(empty|no)\s+(list|findings|array)\b", re.I)),
    ("addressed_to_ai", 1, re.compile(
        r"\b(note|message|instruction|attention)s?\s+(to|for)\s+(the\s+)?(ai|llm|model|assistant|reviewer|agent|verifier|analyst|compliance\s+(bot|agent|ai))\b"
        r"|\b(dear|hey|attention)\s+(ai|llm|assistant|model|agent)\b", re.I)),
    ("role_play", 2, re.compile(r"\byou\s+are\s+(now|no\s+longer)\b|\bact\s+as\s+(an?\s+)?(different|new|unrestricted)\b|\bnew\s+role\b", re.I)),
    ("prompt_extraction", 2, re.compile(
        r"\b(reveal|print|show|output|repeat|disclose|leak)\b[^.\n]{0,30}\b(system|hidden|internal|initial)\s+(prompts?|instructions?|config(uration)?)\b",
        re.I)),
    ("chat_markup", 2, re.compile(r"<\|(im_start|im_end|system|user|assistant)\|>|\[/?INST\]|^\s*#{2,3}\s*(system|instruction)s?\s*:?\s*$|^\s*(system|assistant)\s*:", re.I | re.M)),
    ("authority_claim", 1, re.compile(
        r"\b(rbi|reserve bank|regulator|compliance team|legal)\b[^.\n]{0,40}\b(has|have)\s+(pre-?)?(approved|cleared|exempted|waived)\b[^.\n]{0,40}\b(this|all|the)\s+(product|features?|review|checks?)\b",
        re.I)),
    # an agent's turn or a schema field written into the document (A15)
    ("fake_agent_turn", 2, re.compile(
        r"\b(verifier|analyst|orchestrator|researcher|assistant|reviewer)\s*:\s*[^\n]{0,80}\b(violates\s*=|no\s+violations|compliant)\b",
        re.I)),
    ("schema_echo", 2, re.compile(r"\b(violates|is_attack)\s*=\s*(true|false)\b", re.I)),
    ("prompt_disclosure", 1, re.compile(r"\b(my|your|the)\s+(system|internal|hidden)\s+(prompts?|instructions)\b", re.I)),
    ("hindi_override", 2, re.compile(r"(निर्देशों|नियमों)\s*(को)?\s*(अनदेखा|नज़रअंदाज़|नजरअंदाज)|अनुपालन\s*(के\s*रूप\s*में)?\s*चिह्नित")),
]
BLOCK_SCORE = 2


@dataclass
class InputResult:
    action: str  # pass | grey | block
    text: str  # cleaned text (hidden content removed)
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    stripped: list[tuple[str, str]] = field(default_factory=list)


def _normalise(text: str) -> str:
    # NFKC folds full-width and other look-alike letters; zero-width chars are removed so
    # "ig​nore" cannot slip past the rules
    return ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))


def _despace(text: str) -> str:
    # "i g n o r e  a l l  r u l e s" -> "ignore all rules" (letter-spaced obfuscation)
    return re.sub(r"\b(?:\w ){2,}\w\b", lambda m: m.group().replace(" ", ""), text)


def scan(text: str) -> tuple[int, list[str]]:
    score, reasons = 0, []
    variants = {text, _despace(text)}
    for name, weight, rx in RULES:
        if any(rx.search(v) for v in variants):
            score += weight
            reasons.append(name)
    return score, reasons


def check(doc: str) -> InputResult:
    text = _normalise(doc)
    stripped: list[tuple[str, str]] = []
    for name, rx in HIDDEN_PATTERNS:
        for m in rx.finditer(text):
            stripped.append((name, m.group()))
        text = rx.sub("" if name != "md_link_title" else lambda m: m.group().split('"')[0].rstrip() + ")", text)
    if ZERO_WIDTH.search(doc):
        stripped.append(("zero_width", f"{len(ZERO_WIDTH.findall(doc))} chars"))

    score, reasons = scan(text)
    hidden_score, hidden_reasons = scan("\n".join(s for _, s in stripped))
    # instructions hidden from the human reader are an attack even at weight 1
    if hidden_reasons:
        score += max(hidden_score, BLOCK_SCORE)
        reasons += [f"hidden:{r}" for r in hidden_reasons]
    action = "block" if score >= BLOCK_SCORE else "grey" if score > 0 else "pass"
    return InputResult(action, text.strip() + "\n", score, reasons, stripped)
