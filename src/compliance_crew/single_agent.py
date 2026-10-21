"""Milestone 3 baseline: one agent reviews the whole product doc in a single pass.

It gets the same model and the same retriever as the crew. Retrieval uses each doc
section as a query; the agent then sees the doc plus the retrieved paragraphs once.
"""

import time

from pydantic import BaseModel

from .llm import Tracer, chat
from .product_doc import format_paragraphs, split_sections
from .schemas import Finding, Review
from .tools import Toolbox, normalize_rule_id

MAX_PARAGRAPHS = 20

SYSTEM = """You are a compliance reviewer for Indian fintech lending products.
You check a product document against Reserve Bank of India (RBI) regulation paragraphs.
Report only clear conflicts between what the product does and what a paragraph requires.
Do not report features that comply.
Do not report something only because the document does not mention it; report only what the document says the product does.
Every finding must cite exactly one paragraph id from the
list you are given, written exactly as shown in square brackets, e.g. DLD2025:12(i).
Quote the product document word for word as evidence. Reply in JSON."""


class SingleOutput(BaseModel):
    findings: list[Finding]


def review(doc_id: str, doc: str, toolbox: Toolbox) -> Review:
    start = time.perf_counter()
    tracer = Tracer()

    paragraphs, seen = [], set()
    for sec in split_sections(doc):
        for chunk in toolbox.call("single_agent", "search_corpus", query=f"{sec.heading}. {sec.text}", k=5):
            if chunk.id not in seen:
                seen.add(chunk.id)
                paragraphs.append(chunk)
    paragraphs = paragraphs[:MAX_PARAGRAPHS]

    user = (
        f"RBI paragraphs:\n\n{format_paragraphs(paragraphs)}\n\n"
        f"Product document:\n\n{doc}\n\n"
        "List every compliance violation. For each give: feature (section name), rule_id, issue, "
        "doc_evidence (verbatim quote), severity (high/medium/low) and fix."
    )
    out = chat(SYSTEM, user, SingleOutput, agent="single_agent", tracer=tracer)

    known = set(toolbox.retriever.by_id)
    findings = [f.model_copy(update={"rule_id": normalize_rule_id(f.rule_id, known)}) for f in out.findings]
    return Review(
        system="single",
        doc_id=doc_id,
        findings=findings,
        trace=tracer.summary(),
        seconds=round(time.perf_counter() - start, 1),
    )
