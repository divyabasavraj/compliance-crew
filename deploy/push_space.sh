#!/usr/bin/env bash
# Stage the static demo and upload it to a (free) static Hugging Face Space.
# The page runs the real guard modules in the browser, so they are copied unchanged.
#   deploy/push_space.sh [owner/space]     (default Divya52/compliance-crew)
set -euo pipefail
SPACE="${1:-Divya52/compliance-crew}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cd "$ROOT"
mkdir -p "$STAGE/py"
cp web/index.html web/demo.json "$STAGE/"
cp src/compliance_crew/security/input_guard.py src/compliance_crew/security/pii.py "$STAGE/py/"
cp deploy/SPACE_README.md "$STAGE/README.md"
uv run hf upload "$SPACE" "$STAGE" . --repo-type space --commit-message "Deploy $(git rev-parse --short HEAD)"
