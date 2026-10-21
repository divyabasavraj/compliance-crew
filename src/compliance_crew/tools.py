"""Tools the agents may call, with per-agent allow-lists enforced in code (not in prompts).

Also holds the deterministic checks the Verifier runs before asking the LLM anything.
"""

import re

from .ingest import Chunk
from .retrieval import Retriever

# Least privilege: an agent can only call the tools listed for its role.
AGENT_TOOLS: dict[str, set[str]] = {
    "orchestrator": set(),
    "researcher": {"search_corpus"},
    "gap_analyst": set(),
    "verifier": {"get_paragraph"},
    "risk_assessor": set(),
    "report_writer": set(),
    "single_agent": {"search_corpus"},
}


class ToolNotAllowed(PermissionError):
    pass


# Paragraphs that cannot be broken by a product: preamble, title, applicability, definitions,
# illustrations, annex tables, repeal lists. They crowded the top results for DLG queries
# (definition 4(ii) and Annex II ranked above 20(i) and 22(i)), so agent search skips them.
# They stay in the corpus, so get_paragraph and citation checks still see them.
NON_NORMATIVE = re.compile(
    r"^(DLD2025:(1|2|3|4|28|30)(\(|#|$)|DLD2025:Annex|KFS2024:(1|2|3|11|12|13)(\(|$))"
)


def is_normative(rule_id: str) -> bool:
    return not NON_NORMATIVE.match(rule_id)


class Toolbox:
    def __init__(self, retriever: Retriever):
        self.retriever = retriever
        self.log: list[dict] = []

    def call(self, agent: str, tool: str, **kwargs):
        allowed = tool in AGENT_TOOLS.get(agent, set())
        self.log.append({"agent": agent, "tool": tool, "allowed": allowed})
        if not allowed:
            raise ToolNotAllowed(f"{agent} may not call {tool}")
        return getattr(self, f"_{tool}")(**kwargs)

    def _search_corpus(self, query: str, k: int = 5) -> list[Chunk]:
        # plain hybrid beat hybrid+rerank on our retrieval eval (recall@5 0.825 vs 0.762)
        hits = self.retriever.search(query, k=k + 15, mode="hybrid")
        return [h.chunk for h in hits if is_normative(h.chunk.id)][:k]

    def _get_paragraph(self, rule_id: str) -> Chunk | None:
        return self.retriever.by_id.get(rule_id)


def normalize_rule_id(raw: str, known_ids: set[str]) -> str:
    """Map model output like '[DLD2025 12(i)]', 'Para 12(i)' or 'KFS2024: 5' onto a real corpus id.

    Returns the cleaned string unchanged if nothing matches, so invalid citations stay visible.
    """
    def compact(text: str) -> str:
        text = re.sub(r"para(?:graph)?\.?", "", text.lower())
        return re.sub(r"[\s\[\]:,\-]+", "", text)

    s = raw.strip().strip("[]").strip()
    key = compact(s)
    full = {compact(i): i for i in known_ids}
    if key in full:
        return full[key]
    bare = {}  # ids without the doc prefix, kept only when unambiguous
    for i in known_ids:
        bare.setdefault(compact(i.split(":", 1)[1]), []).append(i)
    if len(bare.get(key, [])) == 1:
        return bare[key][0]
    return s


def _norm_text(text: str) -> list[str]:
    return re.findall(r"[a-z0-9₹%]+", text.lower())


def best_matching_sentence(text: str, source: str) -> str:
    """The sentence of ``source`` sharing the most words with ``text`` (used to re-anchor evidence)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", source) if s.strip()]
    words = set(_norm_text(text))
    return max(sentences, key=lambda s: len(words & set(_norm_text(s))), default="")


def evidence_in_doc(quote: str, doc: str, min_overlap: float = 0.6) -> bool:
    """True if the quoted evidence really comes from the document (exact or near-exact)."""
    q = _norm_text(quote)
    if not q:
        return False
    d = _norm_text(doc)
    if " ".join(q) in " ".join(d):
        return True
    return len(set(q) & set(d)) / len(set(q)) >= min_overlap
