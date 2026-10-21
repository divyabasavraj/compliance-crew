"""Run the single-agent baseline and the crew on the planted-violation benchmark.

Reviews are cached per doc in eval/results/reviews/<tag>/<system>/, so a long run can be stopped
and resumed. Use --force to rerun. The crew run also yields the "crew without Verifier"
numbers from its pre-verification candidates. The tag names a code version (v1 = prompts frozen
on 2026-10-20, v2 = fixes from the v1 failure analysis), so versions can be compared on the same docs.

Docs P01-P12 are the dev split (looked at while tuning), P13-P32 the held-out test split.

    uv run python eval/eval_review.py --tag v2 --split test --systems single crew
"""

import argparse
import json
import sys
from pathlib import Path

from compliance_crew import crew, single_agent
from compliance_crew.llm import MODEL
from compliance_crew.retrieval import Retriever
from compliance_crew.schemas import Review
from compliance_crew.scoring import score
from compliance_crew.tools import Toolbox

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "bench" / "docs"
KEY = json.loads((ROOT / "bench" / "answer_key.json").read_text())
KEY.pop("_note", None)
OUT = Path(__file__).parent / "results"
SYSTEMS = {"single": single_agent.review, "crew": crew.review}
DEV = {d for d in KEY if int(d[1:3]) <= 12}
SPLITS = {"dev": sorted(DEV), "test": sorted(set(KEY) - DEV), "all": sorted(KEY)}


def run(system: str, toolbox: Toolbox, docs: list[str], force: bool, tag: str) -> dict[str, Review]:
    out_dir = OUT / "reviews" / tag / system
    out_dir.mkdir(parents=True, exist_ok=True)
    reviews = {}
    for doc_id in docs:
        path = out_dir / f"{doc_id}.json"
        if path.exists() and not force:
            reviews[doc_id] = Review.model_validate_json(path.read_text())
            continue
        print(f"[{system}] {doc_id} ...", file=sys.stderr, flush=True)
        r = SYSTEMS[system](doc_id, (DOCS / f"{doc_id}.md").read_text(), toolbox)
        path.write_text(r.model_dump_json(indent=2))
        print(f"[{system}] {doc_id}: {len(r.findings)} findings, {r.seconds}s", file=sys.stderr, flush=True)
        reviews[doc_id] = r
    return reviews


def miss_stages(reviews: dict[str, Review], key: dict) -> dict:
    """Per-agent attribution of each planted violation the crew missed (needs trace["retrieved"])."""
    stages = {"not_retrieved": [], "analyst_missed": [], "verifier_rejected": []}
    for doc_id, r in reviews.items():
        retrieved = {i for ids in r.trace.get("retrieved", {}).values() for i in ids}
        cand = {f.rule_id for f in r.candidates}
        final = {f.rule_id for f in r.findings}
        for v in key[doc_id]:
            rules = set(v["rules"])
            if rules & final:
                continue
            stage = ("verifier_rejected" if rules & cand else "analyst_missed" if rules & retrieved else "not_retrieved")
            stages[stage].append(v["id"])
    return stages


def cost(reviews: dict[str, Review]) -> dict:
    n = len(reviews)
    return {
        "sec_per_doc": round(sum(r.seconds for r in reviews.values()) / n, 1),
        "llm_calls_per_doc": round(sum(r.trace["llm_calls"] for r in reviews.values()) / n, 1),
        "tokens_per_doc": round(
            sum(r.trace["prompt_tokens"] + r.trace["output_tokens"] for r in reviews.values()) / n
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", nargs="+", default=list(SYSTEMS), choices=list(SYSTEMS))
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--split", choices=list(SPLITS), default="all")
    ap.add_argument("--docs", nargs="*")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    docs = args.docs or SPLITS[args.split]

    toolbox = Toolbox(Retriever(use_reranker=False))
    known = set(toolbox.retriever.by_id)
    key = {d: KEY[d] for d in docs}
    results = {}
    for system in args.systems:
        reviews = run(system, toolbox, docs, args.force, args.tag)
        c = cost(reviews)
        if system == "crew":
            results["crew_no_verifier"] = score({d: r.candidates for d, r in reviews.items()}, key, known) | c
            results["crew"] = score({d: r.findings for d, r in reviews.items()}, key, known) | c
            if all("retrieved" in r.trace for r in reviews.values()):
                results["crew"]["miss_stages"] = miss_stages(reviews, key)
        else:
            results[system] = score({d: r.findings for d, r in reviews.items()}, key, known) | c

    cols = ["recall", "precision", "false_alarms", "clean_doc_false_alarms", "citation_validity",
            "sec_per_doc", "llm_calls_per_doc", "tokens_per_doc"]
    print("| System | " + " | ".join(cols) + " |")
    print("|" + "---|" * (len(cols) + 1))
    for name, row in results.items():
        print(f"| {name} | " + " | ".join(str(row[c]) for c in cols) + " |")

    name = f"review.{args.tag}.{args.split if not args.docs else 'custom'}.json"
    (OUT / name).write_text(
        json.dumps({"model": MODEL, "tag": args.tag, "split": args.split, "docs": len(key), "results": results}, indent=2)
    )
    print(f"\n{len(key)} docs, model {MODEL} -> {OUT / name}", file=sys.stderr)


if __name__ == "__main__":
    main()
