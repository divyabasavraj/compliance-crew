"""Build the precomputed half of the demo: real crew reviews of the 32 benchmark docs, each finding
marked against the answer key, written to web/demo.json for the hosted UI.

Nothing here calls a model; it reads the cached reviews in eval/results/reviews/<tag>/crew/.

    uv run python eval/build_demo.py --run "Qwen3 8B=v2" --run "Qwen3 14B=k14b3"
"""

import argparse
import json
from pathlib import Path

from compliance_crew.ingest import load_corpus
from compliance_crew.schemas import Review

ROOT = Path(__file__).resolve().parents[1]
KEY = json.loads((ROOT / "bench" / "answer_key.json").read_text())
KEY.pop("_note", None)
OUT = ROOT / "web" / "demo.json"


def stage(rules: set[str], r: Review) -> str:
    retrieved = {i for ids in r.trace.get("retrieved", {}).values() for i in ids}
    if rules & {f.rule_id for f in r.candidates}:
        return "rejected by the Verifier"
    return "missed by the Gap Analyst" if rules & retrieved else "rule not retrieved"


def run_view(r: Review, planted: list[dict]) -> dict:
    by_rule = {rule: v["id"] for v in planted for rule in v["rules"]}
    findings = [f.model_dump(exclude={"verifier_note"}) | {"verifier_note": f.verifier_note.removeprefix(
        "[evidence re-anchored to the feature text] "), "match": by_rule.get(f.rule_id)} for f in r.findings]
    caught = {f["match"] for f in findings if f["match"]}
    return {
        "summary": r.summary,
        "seconds": r.seconds,
        "llm_calls": r.trace.get("llm_calls"),
        "findings": findings,
        "missed": [{"id": v["id"], "why": stage(set(v["rules"]), r)} for v in planted if v["id"] not in caught],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, help='"Label=tag", e.g. "Qwen3 8B=v2"')
    args = ap.parse_args()
    runs = [tuple(r.split("=", 1)) for r in args.run]

    paragraphs = {c.id: {"text": c.text, "title": c.section_title, "url": c.url} for c in load_corpus()}
    cited: set[str] = set()
    docs = []
    for doc_id, planted in sorted(KEY.items()):
        md = (ROOT / "bench" / "docs" / f"{doc_id}.md").read_text()
        views = {}
        for label, tag in runs:
            path = ROOT / "eval" / "results" / "reviews" / tag / "crew" / f"{doc_id}.json"
            if path.exists():
                views[label] = run_view(Review.model_validate_json(path.read_text()), planted)
                cited |= {f["rule_id"] for f in views[label]["findings"]}
        cited |= {rule for v in planted for rule in v["rules"]}
        docs.append({
            "id": doc_id,
            "title": md.splitlines()[0].lstrip("# ").strip(),
            "split": "dev" if int(doc_id[1:3]) <= 12 else "test",
            "markdown": md,
            "planted": planted,
            "runs": views,
        })
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "runs": [label for label, _ in runs],
        "tags": dict(runs),
        "docs": docs,
        "paragraphs": {i: paragraphs[i] for i in sorted(cited) if i in paragraphs},
    }, ensure_ascii=False))
    print(f"{OUT.relative_to(ROOT)}: {len(docs)} docs, {len(cited)} paragraphs, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
