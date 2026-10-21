"""Thin client for a local Ollama model: structured JSON output plus a per-call trace.

Every call records which agent made it, tokens in/out and wall time, so the evaluation
can report cost and latency per review without a paid API.
"""

import os
import time
from dataclasses import asdict, dataclass, field
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("CC_MODEL", "qwen3:8b")
NUM_CTX = 8192

T = TypeVar("T", bound=BaseModel)


@dataclass
class Call:
    agent: str
    prompt_tokens: int
    output_tokens: int
    seconds: float


@dataclass
class Tracer:
    calls: list[Call] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "llm_calls": len(self.calls),
            "prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "llm_seconds": round(sum(c.seconds for c in self.calls), 1),
            "by_agent": {
                a: sum(c.agent == a for c in self.calls) for a in dict.fromkeys(c.agent for c in self.calls)
            },
        }

    def to_list(self) -> list[dict]:
        return [asdict(c) for c in self.calls]


def chat(system: str, user: str, schema: type[T], agent: str, tracer: Tracer | None = None, model: str = MODEL) -> T:
    """Ask the model for a JSON object matching ``schema``. Retries once on invalid JSON."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema.model_json_schema(),
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": 0, "num_ctx": NUM_CTX},
    }
    last_err: Exception | None = None
    for _ in range(2):
        start = time.perf_counter()
        resp = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=900)
        resp.raise_for_status()
        data = resp.json()
        if tracer is not None:
            tracer.calls.append(
                Call(agent, data.get("prompt_eval_count", 0), data.get("eval_count", 0), time.perf_counter() - start)
            )
        try:
            return schema.model_validate_json(data["message"]["content"])
        except ValidationError as err:
            last_err = err
    raise RuntimeError(f"{agent}: model returned invalid JSON twice") from last_err
