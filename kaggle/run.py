"""Kaggle job: Qwen3 14B vs Qwen3 8B (same hardware), crew only, all 32 docs.

Run 1 (2026-10-21, commit 74072f9, tags k8b/k14b): v2 code on the test split.
Run 2 (2026-10-22, tags k8b3/k14b3): v3 retrieval (section k=10, two-tier cap of 24).
Run 3 (2026-10-29, tags k8b4s/k8b4m): 8B only, at smaller retrieval depths (v3 pools diluted its Gap Analyst).

Two T4 GPUs, one Ollama server per GPU, both runs in parallel. Results land in
/kaggle/working/results (download with: kaggle kernels output divyabasavraj25/compliance-crew-14b).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = "https://github.com/divyabasavraj/compliance-crew.git"
COMMIT = "42834d49a922c6f9455ea0c9d4aa7a41e44dacce"  # pinned: v3 code + depth overrides
WORK = Path("/kaggle/working")
SRC = Path("/tmp/compliance-crew")
OUT = WORK / "results"
# (tag, model, port, gpu, section k, paragraph cap): v2 depth, and a middle depth
RUNS = [("k8b4s", "qwen3:8b", 11434, "0", 5, 10), ("k8b4m", "qwen3:8b", 11435, "1", 10, 14)]


def sh(cmd: str, **kw) -> None:
    print(f"$ {cmd}", flush=True)
    subprocess.run(cmd, shell=True, check=True, **kw)


def main() -> None:
    sh("nvidia-smi --query-gpu=name,memory.total --format=csv")
    SRC.parent.mkdir(parents=True, exist_ok=True)
    sh(f"git clone -q {REPO} {SRC} && cd {SRC} && git checkout -q {COMMIT}")
    # the installer needs zstd (releases ship as .tar.zst); keep its output visible
    sh("apt-get update -qq && apt-get install -y -qq zstd pciutils lshw > /dev/null")
    sh("curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -5")
    sh("ollama --version || true")
    sh("pip install -q uv")

    servers = []
    for _, _, port, gpu, *_ in RUNS:
        env = os.environ | {"OLLAMA_HOST": f"127.0.0.1:{port}", "CUDA_VISIBLE_DEVICES": gpu}
        log = open(WORK / f"ollama_{port}.log", "w")
        servers.append(subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=log))
    time.sleep(10)
    for _, model, port, *_ in RUNS:
        sh(f"OLLAMA_HOST=127.0.0.1:{port} ollama pull {model} > /dev/null 2>&1")

    sh(f"cd {SRC} && uv sync -q")
    OUT.mkdir(exist_ok=True)
    (SRC / "eval" / "results" / "reviews").mkdir(parents=True, exist_ok=True)
    procs = []
    for tag, model, port, _, k, cap in RUNS:
        # each tag writes its own cache dir; link it into /kaggle/working so partial results survive a timeout
        target = OUT / "reviews" / tag
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, SRC / "eval" / "results" / "reviews" / tag)
        env = os.environ | {"OLLAMA_URL": f"http://127.0.0.1:{port}", "CC_MODEL": model,
                            "CC_SECTION_K": str(k), "CC_MAX_PARAGRAPHS": str(cap)}
        log = open(WORK / f"{tag}.log", "w")
        procs.append(subprocess.Popen(
            ["uv", "run", "python", "eval/eval_review.py", "--tag", tag, "--split", "all", "--systems", "crew"],
            cwd=SRC, env=env, stdout=log, stderr=subprocess.STDOUT))
    codes = [p.wait() for p in procs]
    for f in (SRC / "eval" / "results").glob("review.k*.json"):
        sh(f"cp '{f}' {OUT}/")
    for s in servers:
        s.terminate()
    print("exit codes", codes, flush=True)
    for tag, *_ in RUNS:
        print(open(WORK / f"{tag}.log").read()[-1500:], flush=True)
    sys.exit(max(codes))


if __name__ == "__main__":
    main()
