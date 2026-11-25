# Scripted repeatable scenario timeline (Timing comes from JSON)
# Loads a scenario JSON into Event/Scenario dataclasses and maps real seconds to process minutes.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List


@dataclass
class Event:
    at: float
    type: str
    args: dict = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    duration_s: float
    sample_period_s: float
    compression: float
    start_hour: float
    seed: int
    events: List[Event]
    settle_s: float = 60.0

    @staticmethod
    def load(path: str) -> "Scenario":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        evs = [Event(float(e.pop("at")), e.pop("type"), e)
               for e in d.get("events", [])]
        evs.sort(key=lambda e: e.at)
        return Scenario(
            name=d["name"],
            duration_s=float(d["duration_s"]),
            sample_period_s=float(d.get("sample_period_s", 2.0)),
            compression=float(d.get("compression", 60.0)),
            start_hour=float(d.get("start_hour", 6.0)),
            seed=int(d.get("seed", 7)),
            events=evs,
            settle_s=float(d.get("settle_s", 60.0)),
        )

    def proc_min(self, real_s: float) -> float:
        """Real seconds since start -> process minutes."""
        return real_s * self.compression / 60.0