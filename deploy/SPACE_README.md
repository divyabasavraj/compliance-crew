---
title: ComplianceCrew
emoji: 📑
colorFrom: gray
colorTo: yellow
sdk: static
pinned: false
short_description: Multi-agent RBI digital lending compliance review
---

# ComplianceCrew demo

A multi-agent review of Indian fintech product documents against the RBI Digital Lending Directions 2025 and the KFS circular, with a measured security layer.

- **Benchmark reviews:** real Qwen3 crew output on 32 synthetic product docs with planted violations, each finding marked against the answer key.
- **Check your own document:** upload a PDF or markdown. The project's own Python input check and Indian PII masking run in your browser (Pyodide); nothing is uploaded.
- Full live reviews, the REST API and the MCP tool need the crew server (`compliance-crew serve`); see the repository.

Source, evaluation and project log: https://github.com/divyabasavraj/compliance-crew

Decision support only, not legal advice. Use synthetic documents only.
