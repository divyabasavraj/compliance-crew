"""Print the results tables (markdown) from eval/results/*.json, for the README and the log.

    uv run python eval/summarize.py
"""

import json
from pathlib import Path

R = Path(__file__).parent / "results"


def load(name: str) -> dict | None:
    p = R / name
    return json.loads(p.read_text()) if p.exists() else None


def review_table() -> str:
    rows = ["| Version | Split | System | Recall | Precision | False alarms | Clean-doc alarms | Valid citations | Sec/doc | Tokens/doc |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for tag in ("v1", "v2", "k8b", "k14b", "k8b3", "k14b3"):
        for split in ("dev", "test", "all"):
            d = load(f"review.{tag}.{split}.json")
            if not d:
                continue
            for system, m in d["results"].items():
                rows.append(f"| {tag} | {split} ({d['docs']} docs) | {system} | {m['recall']} ({m['caught']}/{m['planted']}) | "
                            f"{m['precision']} | {m['false_alarms']} | {m['clean_doc_false_alarms']} | "
                            f"{m['citation_validity']} | {m['sec_per_doc']} | {m['tokens_per_doc']} |")
    return "\n".join(rows)


def security_table() -> str:
    s = load("security.guardian.json") or load("security.json")
    if not s:
        return ""
    i, h, p, m, o = s["input"], s["input"]["heldout"], s["pii"], s["message"], s["output"]
    return "\n".join([
        "| Check | Result |", "|---|---|",
        f"| Input: attacks blocked (seen set) | {i['blocked_final']}/{i['attacks']} ({i['blocked_by_rules']} by rules) |",
        f"| Input: attacks blocked (held-out set) | {h['blocked']}/{h['attacks']} ({h['blocked_by_rules']} by rules) |",
        f"| Input: benign docs falsely blocked | {i['false_blocks']}/{i['benign_docs']} seen + {h['false_blocks']}/{h['benign']} held-out |",
        f"| PII masking recall (Indian IDs) | {p['masked']}/{p['planted']}, look-alikes masked {p['decoys_masked']}/{p['decoys']} |",
        f"| Agent messages: poisoned blocked / real dropped | {m['poisoned_blocked']}/{m['poisoned']} / {m['false_drops']}/{m['real_messages']} |",
        f"| Output: false redactions on real reports | {o['false_redactions_on_real_reports']}/{o['real_reports']} |",
    ])


def e2e_table() -> str:
    e = load("attacks_e2e.json")
    if not e:
        return ""
    rows = ["| Attack | Technique | Violations caught: clean doc → attacked (no guard) | Attack worked without guard? | Guarded crew |",
            "|---|---|---|---|---|"]
    for r in e["attacks"]:
        rows.append(f"| {r['id']} | {r['technique']} | {r['base_caught']} → {r['unguarded_caught']} | "
                    f"{'yes' if r['attack_succeeded_unguarded'] else 'no'} | {r['guarded_status']} |")
    rows += ["", "| PII doc | Caught (unmasked base → guarded) | Planted PII values in output |", "|---|---|---|"]
    for r in e["pii"]:
        rows.append(f"| {r['id']} | {r['base_caught']} → {r['guarded_caught']} | {len(r['pii_values_in_output'])} |")
    return "\n".join(rows)


if __name__ == "__main__":
    for t in (review_table(), security_table(), e2e_table()):
        if t:
            print(t, "\n")
