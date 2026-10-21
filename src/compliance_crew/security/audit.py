"""Checkpoint 5: an append-only audit trail of every security check, pass or fail."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Event:
    checkpoint: str  # input | pii | message | output | access
    agent: str
    decision: str  # pass | flag | block | mask | drop | redact
    reason: str = ""
    ts: float = field(default_factory=time.time)


@dataclass
class AuditLog:
    doc_id: str
    caller: str = "local"
    events: list[Event] = field(default_factory=list)

    def add(self, checkpoint: str, agent: str, decision: str, reason: str = "") -> None:
        self.events.append(Event(checkpoint, agent, decision, reason))

    def blocked(self) -> bool:
        return any(e.decision == "block" for e in self.events)

    def to_list(self) -> list[dict]:
        return [asdict(e) | {"doc_id": self.doc_id, "caller": self.caller} for e in self.events]

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            for row in self.to_list():
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
