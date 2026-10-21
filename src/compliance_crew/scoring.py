"""Score reviews against the planted-violation answer key.

Findings are deduplicated by (doc, rule_id) first, so repeating the same citation neither
helps recall nor hurts precision twice. A finding is a true positive if its rule_id is one of
the acceptable rules for a planted violation in that doc.
"""

from .schemas import Finding


def score(findings_by_doc: dict[str, list[Finding]], answer_key: dict[str, list[dict]], known_ids: set[str]) -> dict:
    planted = caught = flagged = correct = clean_alarms = valid = total = 0
    missed: list[str] = []
    for doc_id, violations in answer_key.items():
        findings = findings_by_doc.get(doc_id, [])
        total += len(findings)
        valid += sum(f.rule_id in known_ids for f in findings)
        cited = {f.rule_id for f in findings}
        acceptable = {r for v in violations for r in v["rules"]}

        planted += len(violations)
        for v in violations:
            if cited & set(v["rules"]):
                caught += 1
            else:
                missed.append(v["id"])
        flagged += len(cited)
        correct += len(cited & acceptable)
        if not violations:
            clean_alarms += len(cited)

    return {
        "recall": round(caught / planted, 3) if planted else None,
        "precision": round(correct / flagged, 3) if flagged else None,
        "caught": caught,
        "planted": planted,
        "unique_flags": flagged,
        "false_alarms": flagged - correct,
        "clean_doc_false_alarms": clean_alarms,
        "citation_validity": round(valid / total, 3) if total else None,
        "missed": missed,
    }
