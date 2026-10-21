"""Milestone 7: the security layer around the crew (PLAN.md section 8).

Five checkpoints, cheap and deterministic first, LLM last:
1. input_guard   uploaded doc: strip hidden content, block instruction-like text
2. message_guard every message passed between agents
3. retrieval     search results must be real, normative corpus paragraphs (tools.py)
4. output_guard  final report: no PII, no leaked prompts, only valid citations
5. audit         every check, pass or fail, written to a JSON-lines log

PII is masked (pii.py) after checkpoint 1 and before any model sees the document.
"""
