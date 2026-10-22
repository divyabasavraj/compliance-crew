"""Model-free retrieval floor: do the fixed per-section queries alone reach each planted rule?

The Researcher runs one search per section (no LLM) and then up to 3 LLM-written queries.
Whatever the section queries find does not depend on which model runs the crew, so this
measures the part of retrieval a bigger or different model cannot lose. For every planted
violation it reports the best rank of its rule over all sections of the doc.

    uv run python eval/probe_section_retrieval.py
"""

import json
from pathlib import Path

from compliance_crew.product_doc import split_sections
from compliance_crew.retrieval import Retriever
from compliance_crew.tools import Toolbox

ROOT = Path(__file__).resolve().parents[1]
KEY = json.loads((ROOT / "bench" / "answer_key.json").read_text())
KEY.pop("_note", None)
DEPTH = 15
KS = (5, 6, 8, 10)


def best_ranks(toolbox: Toolbox, with_context: bool) -> dict[str, int | None]:
    ranks = {}
    for doc_id, violations in sorted(KEY.items()):
        sections = split_sections((ROOT / "bench" / "docs" / f"{doc_id}.md").read_text())
        context = sections[0].text if sections and sections[0].number == 0 else ""
        results = []
        for s in sections:
            if s.number == 0:
                continue
            query = f"{s.heading}: {s.text}"
            if with_context and context:
                query = f"{context}\n{query}"
            results.append([c.id for c in toolbox.call("researcher", "search_corpus", query=query, k=DEPTH)])
        for v in violations:
            found = [ids.index(r) + 1 for ids in results for r in v["rules"] if r in ids]
            ranks[v["id"]] = min(found) if found else None
    return ranks


def main():
    toolbox = Toolbox(Retriever(use_reranker=False))
    out = {}
    for with_context in (False, True):
        ranks = best_ranks(toolbox, with_context)
        label = "section + product context" if with_context else "section text only"
        print(f"\n{label}")
        print("split | " + " | ".join(f"top-{k}" for k in KS) + f" | not in top-{DEPTH}")
        for split, keep in (("dev", lambda v: int(v[1:3]) <= 12), ("test", lambda v: int(v[1:3]) > 12)):
            sub = {v: r for v, r in ranks.items() if keep(v)}
            hits = [sum(r is not None and r <= k for r in sub.values()) for k in KS]
            missing = sorted(v for v, r in sub.items() if r is None)
            print(f"{split} ({len(sub)}) | " + " | ".join(map(str, hits)) + f" | {', '.join(missing) or '-'}")
        out[label] = ranks
    for v in ("P13-a", "P16-b", "P25-a", "P17-b", "P19-b", "P20-d"):
        print(v, {label: r[v] for label, r in out.items()})
    (ROOT / "eval" / "results" / "section_retrieval.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
