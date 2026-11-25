"""Physically motivated water-quality process model for one JalDrishti node.

The model runs on a process clock: t_proc_min = (t_wall_s - t0_s) * compression / 60.
With compression 60, one real second is one process minute. Timestamps sent to
the cloud are always the real wall-clock acquisition time in Unix epoch ms.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List

T_FLOOD_NTU = 1000.0
S_FLOOD_MGL = 700.0
PH_FLOOR = 6.0
E_PH_NTU = 400.0


@dataclass
class RainEvent:
    start_min: float
    peak_ntu: float
    rise_min: float = 10.0
    washout_min: float = 90.0

    def excess(self, t_min: float) -> float:
        dt = t_min - self.start_min
        if dt < 0:
            return 0.0
        if dt < self.rise_min:
            x = dt / self.rise_min
            return self.peak_ntu * x * x * (3.0 - 2.0 * x)
        return self.peak_ntu * math.exp(-(dt - self.rise_min) / self.washout_min)


@dataclass
class Interruption:
    start_min: float
    end_min: float

    def active(self, t_min: float) -> bool:
        return self.start_min <= t_min < self.end_min


@dataclass
class NodeModel:
    node: str
    lat: float
    lon: float
    seed: int
    ph0: float = 7.2
    turb0: float = 4.0
    tds0: float = 180.0
    temp_mean: float = 20.0
    temp_amp: float = 3.5
    flow0: float = 13.0
    rssi0: float = -85.0
    start_hour: float = 6.0
    events: List[RainEvent] = field(default_factory=list)
    interruptions: List[Interruption] = field(default_factory=list)
    rssi_penalty_db: float = 0.0
    manual_values: dict | None = None

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self.k_s = (S_FLOOD_MGL - self.tds0) / (T_FLOOD_NTU - self.turb0)

    def add_rain(self, start_min, peak_ntu, rise_min, washout_min):
        self.events.append(RainEvent(start_min, peak_ntu, rise_min, washout_min))

    def add_interruption(self, start_min, duration_min):
        self.interruptions.append(Interruption(start_min, start_min + duration_min))

    def set_manual(self, values: dict) -> None:
        """Force every future sample to these operator-chosen values."""
        def finite(key: str, baseline: float) -> float:
            try:
                candidate = float(values.get(key, baseline))
            except (TypeError, ValueError, OverflowError):
                return baseline
            return candidate if math.isfinite(candidate) else baseline

        self.manual_values = {
            "ph": round(finite("ph", self.ph0), 2),
            "turbidity_ntu": round(finite("turbidity_ntu", self.turb0), 1),
            "tds_ppm": int(round(finite("tds_ppm", self.tds0))),
            "water_temp_c": round(finite("water_temp_c", self.temp_mean), 1),
            "flow_lpm": round(finite("flow_lpm", self.flow0), 1),
            "rssi_dbm": int(round(finite("rssi_dbm", self.rssi0))),
        }

    def clear_manual(self) -> None:
        """Return to the existing random physics model."""
        self.manual_values = None

    def excess_ntu(self, t_min: float) -> float:
        return sum(e.excess(t_min) for e in self.events)

    def sample(self, t_min: float) -> dict:
        manual = self.manual_values
        if manual is not None:
            return {
                "ph": manual["ph"],
                "turbidity_ntu": manual["turbidity_ntu"],
                "tds_ppm": manual["tds_ppm"],
                "water_temp_c": manual["water_temp_c"],
                "flow_lpm": manual["flow_lpm"],
                "latitude": self.lat,
                "longitude": self.lon,
                "rssi_dbm": manual["rssi_dbm"],
            }
        g = self.rng.gauss
        E = self.excess_ntu(t_min)

        turb = max(0.1, self.turb0 * (1.0 + g(0, 0.04)) + E * (1.0 + g(0, 0.02)))
        turb = min(turb, 1000.0)

        tds = self.tds0 + self.k_s * E + g(0, 2.5)

        ph = PH_FLOOR + (self.ph0 - PH_FLOOR) * math.exp(-E / E_PH_NTU) + g(0, 0.02)

        hour = (self.start_hour + t_min / 60.0) % 24.0
        temp = (self.temp_mean
                + self.temp_amp * math.sin(2 * math.pi * (hour - 9.0) / 24.0)
                - min(2.0, 0.003 * E) + g(0, 0.05))

        flow = self.flow0 * (1.0 + g(0, 0.03))
        for e in self.events:
            dt = t_min - e.start_min
            if 0 <= dt < 60:
                flow += 4.0 * math.exp(-dt / 12.0) * math.sin(2 * math.pi * dt / 8.0)
        if any(i.active(t_min) for i in self.interruptions):
            flow = 0.0
        flow = max(0.0, flow)

        rssi = self.rssi0 - self.rssi_penalty_db + g(0, 2.0)

        return {
            "ph": round(ph, 2),
            "turbidity_ntu": round(turb, 1),
            "tds_ppm": int(round(tds)),
            "water_temp_c": round(temp, 1),
            "flow_lpm": round(flow, 1),
            "latitude": self.lat,
            "longitude": self.lon,
            "rssi_dbm": int(round(rssi)),
        }


def is_breach(v: dict) -> bool:
    """Edge-side evidence tag only; cloud alarms are separate."""
    return (v["turbidity_ntu"] > 50.0 or v["ph"] < 6.5 or v["ph"] > 8.5
            or v["tds_ppm"] > 500 or v["flow_lpm"] == 0.0)