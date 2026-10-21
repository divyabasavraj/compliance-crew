"""Compare retrieval modes on hand-labelled questions: Recall@k and MRR."""

import json
import sys
from pathlib import Path

from compliance_crew.retrieval import Retriever

QUESTIONS = Path(__file__).parent / "retrieval_questions.jsonl"
MODES = ["bm25", "dense", "hybrid", "hybrid_rerank"]
KS = [1, 3, 5]


def main() -> None:
    qs = [json.loads(line) for line in QUESTIONS.open()]
    retriever = Retriever()
    results = {}
    for mode in MODES:
        recall = {k: 0.0 for k in KS}
        mrr = 0.0
        for item in qs:
            ids = [h.chunk.id for h in retriever.search(item["q"], k=max(KS), mode=mode)]
            relevant = set(item["relevant"])
            for k in KS:
                recall[k] += len(relevant & set(ids[:k])) / len(relevant)
            mrr += next((1 / (i + 1) for i, cid in enumerate(ids) if cid in relevant), 0.0)
        results[mode] = {f"recall@{k}": round(recall[k] / len(qs), 3) for k in KS} | {"mrr": round(mrr / len(qs), 3)}

    header = "| Mode | " + " | ".join(results["bm25"]) + " |"
    print(header)
    print("|" + "---|" * (len(results["bm25"]) + 1))
    for mode, row in results.items():
        print(f"| {mode} | " + " | ".join(f"{v:.3f}" for v in row.values()) + " |")
    out = Path(__file__).parent / "results" / "retrieval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"n_questions": len(qs), "results": results}, indent=2))
    print(f"\n{len(qs)} questions -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
