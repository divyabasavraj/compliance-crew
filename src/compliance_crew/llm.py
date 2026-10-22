"""Thin LLM client: structured JSON output plus a per-call trace.

Every call records which agent made it, tokens in/out and wall time, so the evaluation
can report cost and latency per review without a paid API.

Provider (CC_LLM): "ollama" (default, local, used for every measured result) or "gemini"
(free-tier API for the hosted demo, which has no GPU; only synthetic or PII-masked text is sent).
"""

import os
import time
from dataclasses import asdict, dataclass, field
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

PROVIDER = os.getenv("CC_LLM", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL = os.getenv("CC_MODEL", "gemini-2.5-flash" if PROVIDER == "gemini" else "qwen3:8b")
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
    call = _gemini if PROVIDER == "gemini" else _ollama
    last_err: Exception | None = None
    for _ in range(2):
        start = time.perf_counter()
        content, prompt_tokens, output_tokens = call(system, user, schema, model)
        if tracer is not None:
            tracer.calls.append(Call(agent, prompt_tokens, output_tokens, time.perf_counter() - start))
        try:
            return schema.model_validate_json(content)
        except ValidationError as err:
            last_err = err
    raise RuntimeError(f"{agent}: model returned invalid JSON twice") from last_err


def _ollama(system: str, user: str, schema: type[BaseModel], model: str) -> tuple[str, int, int]:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema.model_json_schema(),
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": 0, "num_ctx": NUM_CTX},
    }
    resp = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=900)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"], data.get("prompt_eval_count", 0), data.get("eval_count", 0)


def inline_refs(schema: dict) -> dict:
    """Resolve pydantic's $defs/$ref into one nested schema (the plainest form for structured output)."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _gemini(system: str, user: str, schema: type[BaseModel], model: str) -> tuple[str, int, int]:
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": inline_refs(schema.model_json_schema()),
        },
    }
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}
    for attempt in range(6):
        resp = httpx.post(GEMINI_URL.format(model=model), json=payload, headers=headers, timeout=120)
        if resp.status_code not in (429, 500, 503) or attempt == 5:
            break
        # free tier: a few requests per minute, so wait instead of failing the review
        time.sleep(float(resp.headers.get("retry-after", 0)) or min(60, 5 * 2**attempt))
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usageMetadata", {})
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)
