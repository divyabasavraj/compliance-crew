"""Kaggle job: Qwen3 14B vs Qwen3 8B (same hardware), crew only, all 32 docs.

Run 1 (2026-10-21, commit 74072f9, tags k8b/k14b): v2 code on the test split.
Run 2 (2026-10-22, tags k8b3/k14b3): v3 retrieval (section k=10, two-tier cap of 24).
Run 3 (2026-10-29, tags k8b4s/k8b4m): 8B only, at smaller retrieval depths (v3 pools diluted its Gap Analyst).
Run 4 (2026-11-08, tags k14b5r0, k14b5s1..s5): error bars for the headline 14B v3 config. r0 repeats
k14b3 exactly (greedy) to check determinism; s1..s5 sample at temperature 0.7 with seeds 1..5.

Two T4 GPUs, one Ollama server per GPU; each GPU works through its own queue of runs. Results land in
/kaggle/working/results (download with: kaggle kernels output divyabasavraj25/compliance-crew-14b).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = "https://github.com/divyabasavraj/compliance-crew.git"
COMMIT = "d8ca013b5aceb8b6420ee9f1ba1df75c38957939"  # pinned: v3 code + sampling overrides
WORK = Path("/kaggle/working")
SRC = Path("/tmp/compliance-crew")
OUT = WORK / "results"
MODEL = "qwen3:14b"
# one queue per GPU: (port, gpu, [(tag, temperature, seed), ...]); v3 retrieval depth (code default)
QUEUES = [
    (11434, "0", [("k14b5r0", 0, 0), ("k14b5s1", 0.7, 1), ("k14b5s2", 0.7, 2)]),
    (11435, "1", [("k14b5s3", 0.7, 3), ("k14b5s4", 0.7, 4), ("k14b5s5", 0.7, 5)]),
]
TAGS = [t for _, _, runs in QUEUES for t, *_ in runs]


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
    for port, gpu, _ in QUEUES:
        env = os.environ | {"OLLAMA_HOST": f"127.0.0.1:{port}", "CUDA_VISIBLE_DEVICES": gpu}
        log = open(WORK / f"ollama_{port}.log", "w")
        servers.append(subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=log))
    time.sleep(10)
    for port, *_ in QUEUES:
        sh(f"OLLAMA_HOST=127.0.0.1:{port} ollama pull {MODEL} > /dev/null 2>&1")

    sh(f"cd {SRC} && uv sync -q")
    OUT.mkdir(exist_ok=True)
    (SRC / "eval" / "results" / "reviews").mkdir(parents=True, exist_ok=True)
    # each GPU runs its queue as one shell chain, so the two queues run in parallel
    procs = []
    for port, _, runs in QUEUES:
        steps = []
        for tag, temp, seed in runs:
            # each tag writes its own cache dir; link it into /kaggle/working so partial results survive a timeout
            target = OUT / "reviews" / tag
            target.mkdir(parents=True, exist_ok=True)
            os.symlink(target, SRC / "eval" / "results" / "reviews" / tag)
            steps.append(
                f"OLLAMA_URL=http://127.0.0.1:{port} CC_MODEL={MODEL} CC_TEMPERATURE={temp} CC_SEED={seed} "
                f"uv run python eval/eval_review.py --tag {tag} --split all --systems crew "
                f"> {WORK}/{tag}.log 2>&1; cp eval/results/review.{tag}.all.json {OUT}/ || true"
            )
        procs.append(subprocess.Popen(" ; ".join(steps), shell=True, cwd=SRC))
    codes = [p.wait() for p in procs]
    for s in servers:
        s.terminate()
    print("exit codes", codes, flush=True)
    for tag in TAGS:
        print(f"--- {tag}", open(WORK / f"{tag}.log").read()[-800:], flush=True)
    missing = [t for t in TAGS if not (OUT / f"review.{t}.all.json").exists()]
    print("missing results:", missing, flush=True)
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
