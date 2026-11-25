# Load the node registry from config/nodes.json and config/nodes/*.json
#The loader — merges config/nodes.json with every config/nodes/*.json into one registry. This is what makes per-node files work.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_nodes(config_dir: str | Path = "config") -> dict[str, Any]:
    """Return one merged registry: global keys + all field nodes."""
    root = Path(config_dir)
    cfg = _read(root / "nodes.json")

    nodes: list[dict[str, Any]] = []
    for path in sorted((root / "nodes").glob("*.json")):
        node = _read(path)
        name = node.get("name")
        if not name:
            raise ValueError(f"{path}: missing 'name'")
        if name != path.stem:
            raise ValueError(f"{path}: name '{name}' does not match filename")
        nodes.append(node)

    names = [n["name"] for n in nodes]
    if len(names) != len(set(names)):
        raise ValueError("duplicate node names in config/nodes/")

    cfg["nodes"] = nodes
    return cfg


def all_nodes(config_dir="config", control=False, bench=False):
    """Convenience: field nodes plus optional control/bench nodes."""
    cfg = load_nodes(config_dir)
    out = list(cfg["nodes"])
    if control:
        out += cfg.get("control_nodes", [])
    if bench:
        out += cfg.get("bench_nodes", [])
    return out