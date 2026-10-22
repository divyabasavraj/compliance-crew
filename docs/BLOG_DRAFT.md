# What a held-out split taught me about multi-agent systems

*Draft. Numbers come from `docs/PROJECT_LOG.md`; v3 retrieval results are still pending (marked TODO).*

Fintech teams in India check every new product feature against RBI directions by hand. The open-source tools I found for this are Q&A chatbots: you ask about a circular and get an answer. I wanted something that reads a product document before launch and lists the paragraphs it breaks, with citations. That became ComplianceCrew, a six-agent review crew running on Qwen3 8B on my laptop, at a cost of ₹0.

Demo: https://huggingface.co/spaces/Divya52/compliance-crew. Code: https://github.com/divyabasavraj/compliance-crew.

## The setup

An Orchestrator splits the product doc into features. For each feature, a Researcher searches the RBI corpus (the Digital Lending Directions 2025 and the 2024 KFS circular), a Gap Analyst flags possible violations, and a Verifier checks each one against the cited paragraph. A Risk Assessor scores what survives, and a Report Writer summarises it.

To measure any of this, I wrote 32 synthetic product docs with 68 planted violations and 5 fully compliant docs. Every doc also has features that look suspicious but are fine, such as disbursal to a dealer or an Aadhaar eKYC through a licensed agency. I tuned on P01–P12 and kept P13–P32 as a test split I did not look at while changing prompts.

## The first result was wrong

On the dev docs, the first crew caught 85% of planted violations against 67% for a single agent with the same model and retriever. On the held-out docs it caught 61%, below the single agent's 63%.

Two things explained most of it. A Verifier rule I had added ("an exception applies only if the doc says its condition is met") taught the small model that anything the doc did not mention was a violation, so it approved findings like "the feature does not mention a legally enforceable contract". And the Orchestrator had merged six sections about a default loss guarantee into one feature, so one search query lost the rules for five of them, and the Gap Analyst returned one finding for six sections.

After fixing both, using dev docs only, the crew reached 0.78 recall and 0.45 precision on the held-out split, against 0.71 and 0.27 for the single agent. The multi-agent case turned out to be mostly about precision: half the false alarms (39 vs 80), at about 3.7× the time.

## A bigger model moved the bottleneck

Most remaining misses came from the Gap Analyst, so I ran the same crew with Qwen3 14B on Kaggle's free T4 GPUs, with an 8B control on the same hardware. Precision went from 0.46 to 0.62 and false alarms from 36 to 20. Recall went up by only 2 violations, which is about run-to-run noise on 41.

The miss attribution showed why. Gap Analyst misses fell from 6 to 1, but retrieval misses rose from 3 to 6. The Researcher also ran on 14B, wrote different search queries, and lost rules the 8B queries had found. One of those losses was a plain bug: a cap of 10 paragraphs that two section queries filled on their own, so every model-written query result was cut whenever the Orchestrator put two sections in one feature. I had written that cap before adding per-section queries.

The fix was to make retrieval depend less on the model. The fixed per-section searches (no LLM involved) reach 34 of 41 test rules in their top 5 and 39 in their top 10. I raised that k and merged the pool in two tiers so the cap can only cut the deeper section hits. TODO: v3 results for 8B and 14B.

## Security for agents, measured

Uploaded product docs can carry instructions aimed at the reviewer, and they often contain Aadhaar, PAN and UPI IDs. I reused the guard design from my earlier project PatchPilot (rules first, an LLM judge last) and added what agents need: checks on every message between agents, tool allow-lists enforced in code, Indian PII masking with the Aadhaar checksum, and an audit log.

My regex rules blocked 17 of the 18 attacks I wrote them against and 1 of 8 held-out attacks. Adding a small Guardian model raised the held-out result to 6 of 8. After a fix driven by those two misses, a second held-out set written before the fix went from 3 of 8 blocked to 7 of 8, with 0 false blocks on 52 benign docs. The remaining miss is an instruction written backwards.

## Shipping on ₹0

I planned a server-backed demo on a free Hugging Face Space, and the Space creation failed: Docker Spaces on free CPU now need a paid plan. Static Spaces are still free. The input check and PII masker are pure-stdlib Python, so the demo page loads those two files into Pyodide and runs them in the visitor's browser. The security check on the demo is the project's own code, and nothing gets uploaded. The benchmark reviews on the page are the real crew output, with every finding marked caught, false alarm or missed.

## What I would tell myself at the start

Keep a held-out split from day one, and write the held-out attack set before you describe the fix to anyone. When you add a new source to a pool, check what an old cap now cuts.
