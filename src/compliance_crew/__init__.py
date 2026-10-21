import argparse
from pathlib import Path


def main() -> None:
    """compliance-crew review <product_doc.md> [--system crew|single] [--no-guard] [--out report.md]"""
    ap = argparse.ArgumentParser(prog="compliance-crew")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rv = sub.add_parser("review", help="review a product document against RBI digital lending rules")
    rv.add_argument("doc", type=Path)
    rv.add_argument("--system", choices=["crew", "single"], default="crew")
    rv.add_argument("--no-guard", action="store_true", help="run the crew without the security layer")
    rv.add_argument("--out", type=Path)
    kg = sub.add_parser("keygen", help="create an API key for the HTTP API / MCP endpoint")
    kg.add_argument("key_id")
    sv = sub.add_parser("serve", help="run the HTTP API (POST /review) and the MCP endpoint (/mcp)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.cmd == "keygen":
        from .api import new_key

        secret, line = new_key(args.key_id)
        print(f"secret (give to the caller, shown once): {secret}\nadd to CC_API_KEYS: {line}")
        return
    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return

    from . import crew, single_agent
    from .retrieval import Retriever
    from .security import output_guard
    from .security.secure import secure_review
    from .tools import Toolbox

    toolbox = Toolbox(Retriever(use_reranker=False))
    text = args.doc.read_text()
    if args.system == "crew" and not args.no_guard:
        result, audit = secure_review(args.doc.stem, text, toolbox)
        report = output_guard.check_markdown(crew.render_markdown(result, toolbox), crew.DISCLAIMER, audit)
        audit.write((args.out or Path("report.md")).with_suffix(".audit.jsonl"))  # after the output check
    else:
        run = crew.review if args.system == "crew" else single_agent.review
        result = run(args.doc.stem, text, toolbox)
        report = crew.render_markdown(result, toolbox)
    if args.out:
        args.out.write_text(report)
        args.out.with_suffix(".json").write_text(result.model_dump_json(indent=2))
    print(report)
