"""SQLite store-and-forward buffer for one JalDrishti node.

Write-first: every sample is committed before any publish. A row is marked
synced only after the MQTT QoS 1 PUBACK for its batch. Eviction is three-tier:
synced rows, then unsynced normal rows, then unsynced breach rows.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class Row:
    seq: int
    ts_ms: int
    payload: str
    breach: int


class Buffer:
    def __init__(self, path: str, max_rows: int, node: str,
                 log: Optional[logging.Logger] = None, events=None):
        self.path = path
        self.max_rows = int(max_rows)
        self.node = node
        self.log = log or logging.getLogger(f"buffer.{node}")
        self.events = events
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._connect() as c:
            with open(SCHEMA, encoding="utf-8") as fh:
                c.executescript(fh.read())
        self.evicted_total = self._scalar("SELECT COALESCE(SUM(rows),0) FROM eviction_log")

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA busy_timeout=5000")
        return c

    def _scalar(self, sql: str, args=()) -> int:
        c = self._connect()
        try:
            r = c.execute(sql, args).fetchone()
            return int(r[0]) if r and r[0] is not None else 0
        finally:
            c.close()

    def _emit(self, **kw):
        if self.events:
            kw.setdefault("node", self.node)
            kw.setdefault("t_ms", now_ms())
            self.events(kw)

    # ---- writer side (sampler thread) -------------------------------------
    def insert(self, ts_ms: int, values: dict, breach: bool) -> int:
        payload = json.dumps(values, separators=(",", ":"))
        with self._lock:
            c = self._connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                total = c.execute("SELECT COUNT(*) FROM telemetry_buffer").fetchone()[0]
                if total >= self.max_rows:
                    self._evict(c, total - self.max_rows + 1)
                c.execute(
                    "INSERT OR IGNORE INTO telemetry_buffer (ts_ms, payload, breach) VALUES (?,?,?)",
                    (int(ts_ms), payload, 1 if breach else 0))
                depth = c.execute(
                    "SELECT COUNT(*) FROM telemetry_buffer WHERE synced=0").fetchone()[0]
                c.execute("COMMIT")
                return depth
            except Exception:
                c.execute("ROLLBACK")
                raise
            finally:
                c.close()

    def _evict(self, c: sqlite3.Connection, need: int) -> None:
        tiers = [
            ("synced", "synced=1"),
            ("unsynced_normal", "synced=0 AND breach=0"),
            ("unsynced_any", "synced=0"),
        ]
        for tier, where in tiers:
            if need <= 0:
                break
            rows = c.execute(
                f"SELECT seq, ts_ms FROM telemetry_buffer WHERE {where} "
                f"ORDER BY ts_ms LIMIT ?", (need,)).fetchall()
            if not rows:
                continue
            c.executemany("DELETE FROM telemetry_buffer WHERE seq=?",
                          [(r[0],) for r in rows])
            depth_after = c.execute(
                "SELECT COUNT(*) FROM telemetry_buffer WHERE synced=0").fetchone()[0]
            c.execute(
                "INSERT INTO eviction_log "
                "(at_ms, tier, rows, oldest_ts, newest_ts, depth_after) "
                "VALUES (?,?,?,?,?,?)",
                (now_ms(), tier, len(rows), rows[0][1], rows[-1][1], depth_after))
            need -= len(rows)
            self.evicted_total += len(rows) if tier != "synced" else 0
            lvl = logging.DEBUG if tier == "synced" else logging.WARNING
            self.log.log(lvl, "EVICT tier=%s rows=%d oldest_ts=%d newest_ts=%d pending_after=%d",
                         tier, len(rows), rows[0][1], rows[-1][1], depth_after)
            self._emit(ev="evict", tier=tier, rows=len(rows), oldest_ts=rows[0][1],
                       newest_ts=rows[-1][1], depth=depth_after)

    # ---- reader side (uplink thread) --------------------------------------
    def pending(self, limit: int, min_ts: int = 0) -> List[Row]:
        c = self._connect()
        try:
            rs = c.execute(
                "SELECT seq, ts_ms, payload, breach FROM telemetry_buffer "
                "WHERE synced=0 AND ts_ms>=? ORDER BY ts_ms LIMIT ?",
                (int(min_ts), int(limit))).fetchall()
            return [Row(*r) for r in rs]
        finally:
            c.close()

    def mark_attempt(self, seqs: List[int], at_ms: int) -> None:
        with self._lock:
            c = self._connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                c.executemany(
                    "UPDATE telemetry_buffer SET attempts=attempts+1, "
                    "first_send_ms=COALESCE(first_send_ms, ?) WHERE seq=?",
                    [(at_ms, s) for s in seqs])
                c.execute("COMMIT")
            finally:
                c.close()

    def mark_synced(self, seqs: List[int], at_ms: int, batch_id: int) -> int:
        """Called only from the PUBACK path. Returns remaining pending depth."""
        with self._lock:
            c = self._connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                c.executemany(
                    "UPDATE telemetry_buffer SET synced=1, synced_ms=?, batch_id=? WHERE seq=?",
                    [(at_ms, batch_id, s) for s in seqs])
                c.execute(
                    "INSERT OR REPLACE INTO node_state (k, v) VALUES ('last_batch_id', ?)",
                    (str(batch_id),))
                depth = c.execute(
                    "SELECT COUNT(*) FROM telemetry_buffer WHERE synced=0").fetchone()[0]
                c.execute("COMMIT")
                return depth
            finally:
                c.close()

    def depth(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM telemetry_buffer WHERE synced=0")

    def total(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM telemetry_buffer")

    def last_batch_id(self) -> int:
        return self._scalar(
            "SELECT CAST(v AS INTEGER) FROM node_state WHERE k='last_batch_id'")

    def journal_mode(self) -> str:
        c = self._connect()
        try:
            return c.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            c.close()