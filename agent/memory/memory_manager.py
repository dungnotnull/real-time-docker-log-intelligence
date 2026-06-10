"""
MemoryManager — SQLite-based persistent memory for Dozzle-Enhanced Agent.

Tables:
- log_lines: circular buffer (7-day, 10M row max)
- anomaly_events: detected anomalies with scores and explanations
- cluster_centroids: serialized cluster centroids
- nl_query_cache: NL query results (5-minute TTL)
- llm_cost_log: per-request LLM token usage
- knowledge_hashes: deduplication for SECOND-KNOWLEDGE-BRAIN.md entries
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH_DEFAULT = "data/dozzle_agent.db"
MAX_LOG_ROWS = 10_000_000
RETENTION_DAYS = 7
NL_CACHE_TTL = 300  # 5 minutes


class MemoryManager:
    def __init__(self, config: dict):
        db_path = config.get("memory", {}).get("db_path", DB_PATH_DEFAULT)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup_schema()
        self._setup_indexes()

    def _setup_schema(self):
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS log_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    container TEXT NOT NULL,
                    image TEXT,
                    timestamp TEXT NOT NULL,
                    message TEXT NOT NULL,
                    log_level TEXT,
                    stream TEXT DEFAULT 'stdout',
                    cluster_id INTEGER,
                    anomaly_score REAL,
                    ingested_at REAL DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS anomaly_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    container TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    message TEXT NOT NULL,
                    anomaly_score REAL,
                    seq_anomaly_score REAL,
                    cluster_id INTEGER,
                    severity TEXT,
                    explanation_json TEXT,
                    detected_at REAL DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS cluster_centroids (
                    cluster_id INTEGER PRIMARY KEY,
                    label TEXT,
                    size INTEGER DEFAULT 0,
                    centroid_blob BLOB,
                    containers_json TEXT,
                    created_at REAL DEFAULT (strftime('%s','now')),
                    updated_at REAL DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS nl_query_cache (
                    query_hash TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    cached_at REAL DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS llm_cost_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT,
                    model TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    cost_usd REAL,
                    task_type TEXT,
                    logged_at REAL DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS knowledge_hashes (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    source TEXT,
                    added_at TEXT
                );
            """)

    def _setup_indexes(self):
        with self._conn:
            self._conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_log_container_ts
                    ON log_lines(container, timestamp);
                CREATE INDEX IF NOT EXISTS idx_log_level
                    ON log_lines(log_level);
                CREATE INDEX IF NOT EXISTS idx_log_ingested
                    ON log_lines(ingested_at);
                CREATE INDEX IF NOT EXISTS idx_anomaly_container_ts
                    ON anomaly_events(container, detected_at);
                CREATE INDEX IF NOT EXISTS idx_anomaly_severity
                    ON anomaly_events(severity);
            """)

    # ── Log lines ────────────────────────────────────────────────────────────

    def store_log_lines(self, log_lines: list[dict]):
        rows = []
        for line in log_lines:
            rows.append((
                line.get("container", "unknown"),
                line.get("image"),
                line.get("timestamp", datetime.utcnow().isoformat()),
                line.get("message", ""),
                line.get("log_level"),
                line.get("stream", "stdout"),
                line.get("cluster_id"),
                line.get("anomaly_score"),
            ))
        with self._conn:
            self._conn.executemany(
                """INSERT INTO log_lines
                   (container, image, timestamp, message, log_level, stream, cluster_id, anomaly_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        self._enforce_retention()

    def _enforce_retention(self):
        cutoff = time.time() - (RETENTION_DAYS * 86400)
        with self._conn:
            self._conn.execute("DELETE FROM log_lines WHERE ingested_at < ?", (cutoff,))
            # Also enforce row cap
            row_count = self._conn.execute("SELECT COUNT(*) FROM log_lines").fetchone()[0]
            if row_count > MAX_LOG_ROWS:
                excess = row_count - MAX_LOG_ROWS
                self._conn.execute(
                    "DELETE FROM log_lines WHERE id IN (SELECT id FROM log_lines ORDER BY id ASC LIMIT ?)",
                    (excess,),
                )

    def get_recent_log_lines(self, container: str | None = None, limit: int = 100) -> list[dict]:
        if container:
            rows = self._conn.execute(
                "SELECT * FROM log_lines WHERE container=? ORDER BY id DESC LIMIT ?",
                (container, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM log_lines ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def query_log_lines(self, parsed_filter: dict) -> list[dict]:
        """Execute structured filter against log_lines table."""
        conditions = []
        params = []

        container = parsed_filter.get("container_name")
        if container:
            conditions.append("container LIKE ?")
            params.append(f"%{container}%")

        time_range = parsed_filter.get("time_range_minutes", 60)
        cutoff_ts = time.time() - (time_range * 60)
        conditions.append("ingested_at >= ?")
        params.append(cutoff_ts)

        level = parsed_filter.get("log_level")
        if level:
            conditions.append("log_level = ?")
            params.append(level.upper())

        keywords = parsed_filter.get("keywords", [])
        for kw in keywords:
            conditions.append("message LIKE ?")
            params.append(f"%{kw}%")

        where = " AND ".join(conditions) if conditions else "1=1"
        limit = min(parsed_filter.get("limit", 100), 500)
        sort = "DESC" if parsed_filter.get("sort", "desc") == "desc" else "ASC"

        sql = f"SELECT * FROM log_lines WHERE {where} ORDER BY id {sort} LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Anomaly events ───────────────────────────────────────────────────────

    def store_anomaly_event(self, event: dict):
        with self._conn:
            self._conn.execute(
                """INSERT INTO anomaly_events
                   (container, timestamp, message, anomaly_score, seq_anomaly_score, cluster_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.get("container"),
                    event.get("timestamp"),
                    event.get("message"),
                    event.get("anomaly_score"),
                    event.get("seq_anomaly_score"),
                    event.get("cluster_id"),
                ),
            )

    def update_anomaly_explanations(self, container: str, log_lines: list[str], explanation: dict):
        severity = explanation.get("severity", "MEDIUM")
        expl_json = json.dumps(explanation)
        with self._conn:
            # Update the most recent unresolved anomalies for this container
            self._conn.execute(
                """UPDATE anomaly_events
                   SET severity=?, explanation_json=?
                   WHERE container=? AND explanation_json IS NULL
                   ORDER BY id DESC LIMIT ?""",
                (severity, expl_json, container, len(log_lines)),
            )

    def get_anomaly_events(
        self, container: str | None = None, since: str | None = None, limit: int = 50
    ) -> list[dict]:
        conditions = []
        params = []
        if container:
            conditions.append("container LIKE ?")
            params.append(f"%{container}%")
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM anomaly_events WHERE {where} ORDER BY detected_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("explanation_json"):
                try:
                    d["explanation"] = json.loads(d["explanation_json"])
                except Exception:
                    d["explanation"] = None
            results.append(d)
        return results

    # ── NL query cache ───────────────────────────────────────────────────────

    def _query_hash(self, query: str) -> str:
        import hashlib
        return hashlib.md5(query.strip().lower().encode()).hexdigest()

    def get_nl_query_cache(self, query: str) -> list[dict] | None:
        key = self._query_hash(query)
        row = self._conn.execute(
            "SELECT results_json, cached_at FROM nl_query_cache WHERE query_hash=?", (key,)
        ).fetchone()
        if row and (time.time() - row["cached_at"]) < NL_CACHE_TTL:
            return json.loads(row["results_json"])
        return None

    def store_nl_query_cache(self, query: str, results: list[dict]):
        key = self._query_hash(query)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO nl_query_cache (query_hash, query_text, results_json) VALUES (?,?,?)",
                (key, query, json.dumps(results)),
            )

    # ── LLM cost tracking ────────────────────────────────────────────────────

    def log_llm_cost(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int,
        cost_usd: float, task_type: str = ""
    ):
        with self._conn:
            self._conn.execute(
                """INSERT INTO llm_cost_log
                   (provider, model, prompt_tokens, completion_tokens, cost_usd, task_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (provider, model, prompt_tokens, completion_tokens, cost_usd, task_type),
            )

    # ── Knowledge hash dedup ─────────────────────────────────────────────────

    def is_known_entry(self, url_or_doi: str) -> bool:
        import hashlib
        h = hashlib.md5(url_or_doi.encode()).hexdigest()
        row = self._conn.execute(
            "SELECT 1 FROM knowledge_hashes WHERE url_hash=?", (h,)
        ).fetchone()
        return row is not None

    def add_knowledge_entry(self, url_or_doi: str, title: str, source: str):
        import hashlib
        h = hashlib.md5(url_or_doi.encode()).hexdigest()
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO knowledge_hashes (url_hash, url, title, source, added_at) VALUES (?,?,?,?,?)",
                (h, url_or_doi, title, source, datetime.utcnow().isoformat()),
            )

    # ── Stats ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        rows = self._conn.execute("SELECT COUNT(*) as n FROM log_lines").fetchone()
        anomaly_rows = self._conn.execute("SELECT COUNT(*) as n FROM anomaly_events").fetchone()
        cost_rows = self._conn.execute(
            "SELECT SUM(cost_usd) as total, SUM(prompt_tokens+completion_tokens) as tokens FROM llm_cost_log"
        ).fetchone()
        return {
            "total_log_lines": rows["n"] if rows else 0,
            "total_anomaly_events": anomaly_rows["n"] if anomaly_rows else 0,
            "total_llm_cost_usd": round(cost_rows["total"] or 0.0, 4),
            "total_tokens_used": cost_rows["tokens"] or 0,
        }

    def close(self):
        self._conn.close()
