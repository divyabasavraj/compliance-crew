"""Deterministic pieces of the review pipeline: no model calls."""

import json
from pathlib import Path

import pytest

from compliance_crew.product_doc import split_sections
from compliance_crew.schemas import Finding
from compliance_crew.scoring import score
from compliance_crew.tools import AGENT_TOOLS, Toolbox, ToolNotAllowed, evidence_in_doc, normalize_rule_id

ROOT = Path(__file__).resolve().parents[1]
KNOWN = {"DLD2025:12(i)", "DLD2025:9(ii)", "DLD2025:Annex I", "KFS2024:5", "KFS2024:3(a)"}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DLD2025:12(i)", "DLD2025:12(i)"),
        ("[DLD2025:12(i)]", "DLD2025:12(i)"),
        ("DLD2025 12(i)", "DLD2025:12(i)"),
        ("Para 12(i)", "DLD2025:12(i)"),
        ("KFS2024: 5", "KFS2024:5"),
        ("dld2025:annex i", "DLD2025:Annex I"),
        ("DLD2025:99(z)", "DLD2025:99(z)"),  # unknown ids are kept so they count as invalid
    ],
)
def test_normalize_rule_id(raw, expected):
    assert normalize_rule_id(raw, KNOWN) == expected


def test_evidence_in_doc():
    doc = "The app reads the phone's contact list and call logs in the background."
    assert evidence_in_doc("reads the phone's contact list and call logs", doc)
    assert not evidence_in_doc("stores fingerprints on servers in Singapore", doc)
    assert not evidence_in_doc("", doc)


def test_tool_allow_list_is_enforced():
    class FakeRetriever:
        by_id = {}

        def search(self, query, k, mode):
            return []

    box = Toolbox(FakeRetriever())
    assert box.call("researcher", "search_corpus", query="x") == []
    with pytest.raises(ToolNotAllowed):
        box.call("report_writer", "search_corpus", query="x")
    with pytest.raises(ToolNotAllowed):
        box.call("gap_analyst", "get_paragraph", rule_id="DLD2025:12(i)")
    assert [e["allowed"] for e in box.log] == [True, False, False]
    assert "search_corpus" not in AGENT_TOOLS["verifier"]


def test_split_sections():
    doc = "# Title\nIntro line\n\n## 1. Fees\nA fee.\n\n## 2. Data\nStored in India."
    secs = split_sections(doc)
    assert [(s.number, s.heading) for s in secs] == [(0, "Overview"), (1, "1. Fees"), (2, "2. Data")]
    assert secs[2].text == "Stored in India."


def _f(rule):
    return Finding(feature="x", rule_id=rule, issue="y", doc_evidence="z")


def test_score_counts_recall_precision_and_clean_doc_alarms():
    key = {
        "A": [{"id": "A1", "rules": ["DLD2025:12(i)"]}, {"id": "A2", "rules": ["DLD2025:9(ii)", "DLD2025:9(iii)"]}],
        "B": [],
    }
    found = {
        "A": [_f("DLD2025:12(i)"), _f("DLD2025:12(i)"), _f("KFS2024:5")],  # duplicate + one false alarm
        "B": [_f("DLD2025:9(ii)"), _f("DLD2025:99(z)")],  # clean doc: two alarms, one invalid id
    }
    s = score(found, key, KNOWN)
    assert s["recall"] == 0.5 and s["missed"] == ["A2"]
    assert s["unique_flags"] == 4 and s["false_alarms"] == 3
    assert s["precision"] == 0.25
    assert s["clean_doc_false_alarms"] == 2
    assert s["citation_validity"] == 0.8


def test_answer_key_cites_real_paragraphs_and_docs_exist():
    corpus_ids = {json.loads(line)["id"] for line in (ROOT / "data" / "corpus.jsonl").open()}
    key = json.loads((ROOT / "bench" / "answer_key.json").read_text())
    key.pop("_note")
    for doc_id, violations in key.items():
        assert (ROOT / "bench" / "docs" / f"{doc_id}.md").exists(), doc_id
        for v in violations:
            assert set(v["rules"]) <= corpus_ids, v["id"]


def test_non_normative_paragraphs_are_skipped_by_search():
    from compliance_crew.tools import is_normative

    for rid in ["DLD2025:4(ii)", "DLD2025:Annex II#2", "DLD2025:1", "KFS2024:3(c)", "DLD2025:30(i)"]:
        assert not is_normative(rid), rid
    for rid in ["DLD2025:10(i)", "DLD2025:12(i)", "DLD2025:20(i)", "DLD2025:22(i)", "KFS2024:4"]:
        assert is_normative(rid), rid


def test_best_matching_sentence_reanchors_evidence():
    from compliance_crew.tools import best_matching_sentence

    feature = "Approved amounts are credited to the QuickCash wallet. The user can move money any time."
    assert best_matching_sentence("disbursal into the LSP wallet not the borrower bank account", feature) == (
        "Approved amounts are credited to the QuickCash wallet."
    )


def test_orchestrator_caps_feature_size_and_covers_every_section(monkeypatch):
    from compliance_crew import crew

    doc = "# T\nctx\n" + "".join(f"\n## {n}. S{n}\ntext {n}\n" for n in range(1, 7))
    secs = split_sections(doc)
    plan = crew.Plan(features=[crew.FeaturePlan(name="DLG", sections=[2, 3, 4, 5, 6])])
    monkeypatch.setattr(crew, "chat", lambda *a, **k: plan)
    features = crew.orchestrate(secs, tracer=None)
    groups = [[s.number for s in part] for _, part in features]
    assert groups == [[2, 3], [4, 5], [6], [1]]
    assert all(len(g) <= crew.MAX_SECTIONS_PER_FEATURE for g in groups)


def test_researcher_cap_never_drops_llm_query_hits(monkeypatch):
    # v2 bug: two section queries filled the old cap of 10, so every LLM-query hit was cut
    from compliance_crew import crew
    from compliance_crew.ingest import Chunk

    class FakeBox:
        def call(self, agent, tool, query, k):
            prefix = "llm" if query.startswith("q") else query.split(":")[0]
            return [Chunk(f"{prefix}-{i}", "DLD2025", "", "", "", "t", "") for i in range(k)]

    monkeypatch.setattr(crew, "chat", lambda *a, **k: crew.Queries(queries=["q1", "q2"]))
    secs = split_sections("# T\nctx\n\n## A\nx\n\n## B\ny\n")[1:]
    ids = [c.id for c in crew.research("ctx", secs, FakeBox(), tracer=None)]
    assert len(ids) <= crew.MAX_PARAGRAPHS
    assert {"llm-0", "llm-1", "llm-2"} <= set(ids)  # both LLM queries return llm-0..2 (deduplicated)
    assert ids[:5] == [f"A-{i}" for i in range(5)] and "B-9" in ids
