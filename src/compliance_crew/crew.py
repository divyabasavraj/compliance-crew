"""Milestone 4: the multi-agent crew, as a plain Python pipeline.

Orchestrator -> (per feature) Researcher -> Gap Analyst -> Verifier -> Risk Assessor -> Report Writer.

Plain Python instead of LangGraph: the flow is a fixed pipeline with a per-feature loop,
so a framework would add a dependency without adding control. Each agent is one function
with its own prompt and its own tool allow-list (see tools.AGENT_TOOLS).

The Verifier runs before the Risk Assessor so no LLM calls are spent scoring findings that
get rejected. Candidates before verification are kept, which gives the "multi-agent without
Verifier" result from the same run.
"""

import time

from pydantic import BaseModel, Field

from .ingest import Chunk
from .llm import Tracer, chat
from .product_doc import Section, format_paragraphs, split_sections
from .schemas import Finding, Review, Severity
from .security.message_guard import NoGuard
from .tools import Toolbox, best_matching_sentence, evidence_in_doc, normalize_rule_id

MAX_PARAGRAPHS = 24  # v3 (was 10); paragraphs average ~90 tokens, so 24 fit the 8k context
SECTION_K = 10  # v3 (was 5): model-free section queries reach 39/41 test rules at k=10 vs 34 at k=5
MAX_SECTIONS_PER_FEATURE = 2  # v2: one merged 6-section DLG feature hid 2 of 3 violations in P07
DISCLAIMER = "Decision support only, not legal advice. Findings need review by a compliance officer."


# ---------- Orchestrator ----------

class FeaturePlan(BaseModel):
    name: str
    sections: list[int] = Field(description="Section numbers that describe this feature")


class Plan(BaseModel):
    features: list[FeaturePlan]


ORCH_SYSTEM = """You are the orchestrator of a compliance review team for Indian digital lending products.
Split the product document into features that each need a separate regulatory check
(e.g. disbursal, repayment, fees, data collection, data storage, credit bureau reporting,
grievance redressal, default loss guarantee). Group sections that describe the same feature.
Every section belongs to exactly one feature. Refer to sections only by their numbers. Reply in JSON."""


def orchestrate(sections: list[Section], tracer: Tracer) -> list[tuple[str, list[Section]]]:
    numbered = "\n\n".join(f"Section {s.number}: {s.heading}\n{s.text}" for s in sections)
    plan = chat(ORCH_SYSTEM, numbered, Plan, agent="orchestrator", tracer=tracer)

    by_num = {s.number: s for s in sections}
    features, covered = [], set()
    for f in plan.features:
        secs = [by_num[n] for n in dict.fromkeys(f.sections) if n in by_num and n != 0 and n not in covered]
        covered.update(s.number for s in secs)
        # size cap: a large group is split so each part gets its own retrieval and analysis
        for i in range(0, len(secs), MAX_SECTIONS_PER_FEATURE):
            part = secs[i : i + MAX_SECTIONS_PER_FEATURE]
            features.append((f.name if i == 0 else f"{f.name} ({part[0].heading})", part))
    # coverage guarantee: a section the orchestrator skipped is still reviewed on its own
    for s in sections:
        if s.number != 0 and s.number not in covered:
            features.append((s.heading, [s]))
    return features


# ---------- Researcher ----------

class Queries(BaseModel):
    queries: list[str] = Field(description="Up to 3 short search queries about the regulatory topics")


RESEARCH_SYSTEM = """You are a regulation researcher. Given one feature of a lending product and the product
context, write up to 3 short search queries that would find the RBI digital lending rules that apply
to it (for example "disbursal to third party account", "data stored outside India",
"cap on default loss guarantee"). Consider how the feature combines with the product type in the
context (for example a guarantee on a revolving credit line, or a loan covered by a guarantee scheme).
Reply in JSON."""


def research(context: str, secs: list[Section], toolbox: Toolbox, tracer: Tracer, guard=None) -> list[Chunk]:
    guard = guard or NoGuard()
    feature_text = "\n".join(f"{s.heading}: {s.text}" for s in secs)
    q = chat(RESEARCH_SYSTEM, f"Product context:\n{context}\n\nFeature:\n{feature_text}", Queries,
             agent="researcher", tracer=tracer)
    # one query per section (a merged query loses rules for all but the dominant section), then the LLM queries
    llm_queries = [query for query in q.queries[:3] if guard.allow("researcher", "query", query)]
    section_hits = [toolbox.call("researcher", "search_corpus", query=f"{s.heading}: {s.text}", k=SECTION_K)
                    for s in secs]
    llm_hits = [toolbox.call("researcher", "search_corpus", query=query, k=3) for query in llm_queries]
    # v3: two tiers, so the cap can only cut the deeper section hits. In v2 two section queries
    # filled the old cap of 10 and silently dropped every LLM-query result (lost P25-a with 14B).
    found: dict[str, Chunk] = {}
    tiers = [[h[:5] for h in section_hits] + llm_hits, [h[5:] for h in section_hits]]
    for tier in tiers:
        for hits in tier:
            for chunk in hits:
                found.setdefault(chunk.id, chunk)
    return list(found.values())[:MAX_PARAGRAPHS]


# ---------- Gap Analyst ----------

class Gap(BaseModel):
    rule_id: str
    issue: str
    doc_evidence: str


class Gaps(BaseModel):
    findings: list[Gap]


GAP_SYSTEM = """You are a compliance gap analyst for Indian digital lending.
Compare one product feature against the RBI paragraphs given. Report a finding only when the
feature as described clearly breaks what a paragraph requires. If the feature complies, or the
paragraphs do not apply, return an empty list.
Do not report something only because the document does not mention it; report only what the document says the product does.
Cite exactly one paragraph id per finding, written
exactly as shown in square brackets. Quote the product feature text word for word as doc_evidence
(never quote the RBI paragraph). Reply in JSON."""


def analyse(context: str, name: str, feature_text: str, paragraphs: list[Chunk], tracer: Tracer) -> list[Gap]:
    user = (
        f"Product context:\n{context}\n\nFeature: {name}\n{feature_text}\n\n"
        f"RBI paragraphs:\n\n{format_paragraphs(paragraphs)}"
    )
    return chat(GAP_SYSTEM, user, Gaps, agent="gap_analyst", tracer=tracer).findings


# ---------- Verifier ----------

class Verdict(BaseModel):
    # reason comes first so the model explains before it decides
    reason: str = Field(description="One or two sentences comparing what the feature does with what the paragraph requires")
    violates: bool = Field(description="True only if the feature, as described, breaks this paragraph")


VERIFY_SYSTEM = """You are a strict verifier. You get one RBI paragraph, one product feature and a claimed
violation. Decide whether the feature, as described, really breaks this specific paragraph.
Answer violates=true when the feature does something the paragraph forbids, or fails to do
something the paragraph requires. Answer violates=false if the feature actually complies, if the
paragraph is about a different topic, if the claim relies on facts not in the feature text or on something
the feature does not mention, or if
the paragraph explicitly allows what the feature does.
Something the feature does not mention, state or confirm is never a violation: if the claim rests on
what the feature leaves out, answer violates=false.
When the feature does something the paragraph forbids and the paragraph has an exception for it,
the exception counts only if the feature text says its condition is met.
Your verdict must agree with your reason: if your reason says the feature does what the paragraph
forbids, answer violates=true. Reply in JSON."""


def verify(f: Finding, feature_text: str, doc: str, toolbox: Toolbox, tracer: Tracer) -> tuple[bool, str]:
    para = toolbox.call("verifier", "get_paragraph", rule_id=f.rule_id)
    if para is None:
        return False, "cited paragraph does not exist in the corpus"
    note = ""
    if not evidence_in_doc(f.doc_evidence, doc):
        # v2: re-anchor instead of rejecting. The claim is still judged against the real feature
        # text below, so a made-up fact cannot pass, and the report quotes the document, not the model.
        anchored = best_matching_sentence(f"{f.issue} {f.doc_evidence}", feature_text)
        if not anchored:
            return False, "quoted evidence not found in the product document"
        f.doc_evidence, note = anchored, "[evidence re-anchored to the feature text] "
    user = (
        f"RBI paragraph [{para.id}]:\n{format_paragraphs([para])}\n\n"
        f"Product feature:\n{feature_text}\n\nClaimed violation:\n{f.issue}"
    )
    v = chat(VERIFY_SYSTEM, user, Verdict, agent="verifier", tracer=tracer)
    return v.violates, note + v.reason


# ---------- Risk Assessor ----------

class Risk(BaseModel):
    index: int
    severity: Severity
    fix: str


class Risks(BaseModel):
    risks: list[Risk]


RISK_SYSTEM = """You are a risk assessor at an Indian lender. For each confirmed compliance finding, give a
severity (high = borrower harm, money flow or data breach risk, or likely RBI penalty;
medium = disclosure or process gap; low = documentation detail) and one concrete fix.
Use the finding index given. Reply in JSON."""


def assess(findings: list[Finding], tracer: Tracer, guard=None) -> list[Finding]:
    guard = guard or NoGuard()
    if not findings:
        return findings
    listing = "\n".join(f"{i}. [{f.rule_id}] {f.feature}: {f.issue}" for i, f in enumerate(findings))
    risks = {r.index: r for r in chat(RISK_SYSTEM, listing, Risks, agent="risk_assessor", tracer=tracer).risks}
    risks = {i: r for i, r in risks.items() if guard.allow("risk_assessor", "fix", r.fix)}
    return [
        f.model_copy(update={"severity": risks[i].severity, "fix": risks[i].fix}) if i in risks else f
        for i, f in enumerate(findings)
    ]


# ---------- Report Writer ----------

class Summary(BaseModel):
    summary: str = Field(description="3 to 4 plain sentences for a product manager")


REPORT_SYSTEM = """You write the executive summary of a compliance review for a product manager.
Use only the findings given. Do not add new issues. If there are no findings, say no gaps were
found against the reviewed RBI rules. Reply in JSON."""


def write_summary(product: str, findings: list[Finding], tracer: Tracer) -> str:
    listing = "\n".join(f"- ({f.severity}) [{f.rule_id}] {f.issue}" for f in findings) or "No findings."
    user = f"Product: {product}\n\nFindings:\n{listing}"
    return chat(REPORT_SYSTEM, user, Summary, agent="report_writer", tracer=tracer).summary


def render_markdown(review: Review, toolbox: Toolbox) -> str:
    lines = [f"# Compliance review: {review.doc_id}", "", review.summary, "", f"_{DISCLAIMER}_", ""]
    order = {"high": 0, "medium": 1, "low": 2}
    for f in sorted(review.findings, key=lambda f: order[f.severity]):
        para = toolbox.retriever.by_id.get(f.rule_id)
        cite = f"[{f.rule_id}]({para.url})" if para else f.rule_id
        lines += [
            f"## [{f.severity.upper()}] {f.feature}",
            f"- **Rule:** {cite}",
            f"- **Issue:** {f.issue}",
            f"- **Evidence:** \"{f.doc_evidence}\"",
            f"- **Fix:** {f.fix}",
            "",
        ]
    return "\n".join(lines)


# ---------- Pipeline ----------

def review(doc_id: str, doc: str, toolbox: Toolbox, guard=None) -> Review:
    """``guard`` (security.message_guard.MessageGuard) checks every message between agents; None = off."""
    guard = guard or NoGuard()
    start = time.perf_counter()
    tracer = Tracer()
    log_start = len(toolbox.log)
    known = set(toolbox.retriever.by_id)
    sections = split_sections(doc)
    context = sections[0].text if sections and sections[0].number == 0 else ""

    candidates, kept, retrieved = [], [], {}
    for name, secs in orchestrate(sections, tracer):
        if not guard.allow("orchestrator", "feature", name):
            name = secs[0].heading
        feature_text = "\n".join(f"{s.heading}: {s.text}" for s in secs)
        paragraphs = research(context, secs, toolbox, tracer, guard)
        retrieved[f"{len(retrieved)}:{name}"] = [p.id for p in paragraphs]  # lets the eval tell retrieval misses from analyst misses
        seen = set()
        for gap in analyse(context, name, feature_text, paragraphs, tracer):
            rid = normalize_rule_id(gap.rule_id, known)
            if rid in seen or not guard.allow("gap_analyst", "finding", f"{gap.issue}\n{gap.doc_evidence}"):
                continue
            seen.add(rid)
            f = Finding(feature=name, rule_id=rid, issue=gap.issue, doc_evidence=gap.doc_evidence)
            candidates.append(f)
            ok, reason = verify(f, feature_text, doc, toolbox, tracer)
            if not guard.allow("verifier", "reason", reason):
                # a poisoned verdict would most likely try to suppress a finding, so a blocked
                # Verifier message keeps the finding for a human instead of trusting the verdict
                ok, reason = True, "NEEDS HUMAN REVIEW: verifier message blocked by the security layer"
            f.verifier_note = reason
            if ok:
                kept.append(f.model_copy())

    kept = assess(kept, tracer, guard)
    title = doc.splitlines()[0].lstrip("# ").strip() if doc.strip() else doc_id
    summary = write_summary(title, kept, tracer)
    if not guard.allow("report_writer", "summary", summary):
        summary = f"{len(kept)} findings; the generated summary was withheld by the security layer."
    return Review(
        system="crew",
        doc_id=doc_id,
        findings=kept,
        candidates=candidates,
        summary=summary,
        trace=tracer.summary() | {"tool_calls": len(toolbox.log) - log_start, "retrieved": retrieved},
        seconds=round(time.perf_counter() - start, 1),
    )
