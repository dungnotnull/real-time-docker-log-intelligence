"""
DozzleOrchestrator — core agent decision loop for Dozzle-Enhanced AI Log Analysis Agent.

Responsibilities:
- Stream log lines from Docker Engine / Dozzle API
- Dispatch to anomaly detector, clusterer, and error explainer (async)
- Manage anomaly event queue and webhook notifications
- Handle NL log queries
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

logger = logging.getLogger(__name__)


class DozzleOrchestrator:
    def __init__(self, config_path: str = "config/agent_config.yaml"):
        self.config = self._load_config(config_path)
        self._anomaly_detector = None
        self._nl_query = None
        self._log_clusterer = None
        self._error_explainer = None
        self._memory = None
        self._dozzle_client = None
        self._anomaly_event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._running = False
        # Per-container pending anomaly batches for explainer (60-second window)
        self._pending_anomalies: dict[str, list[dict]] = defaultdict(list)
        self._last_explain_flush: dict[str, float] = defaultdict(float)
        self._stats: dict[str, Any] = {
            "lines_processed": 0,
            "anomalies_detected": 0,
            "clusters_created": 0,
            "llm_calls": 0,
            "start_time": datetime.utcnow().isoformat(),
        }

    def _load_config(self, config_path: str) -> dict:
        p = Path(config_path)
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
        return {}

    # ── Lazy module accessors ────────────────────────────────────────────────

    @property
    def anomaly_detector(self):
        if self._anomaly_detector is None:
            from agent.modules.log_anomaly_detector import LogAnomalyDetector
            self._anomaly_detector = LogAnomalyDetector(self.config)
        return self._anomaly_detector

    @property
    def nl_query(self):
        if self._nl_query is None:
            from agent.modules.nl_log_query import NLLogQuery
            self._nl_query = NLLogQuery(self.config)
        return self._nl_query

    @property
    def log_clusterer(self):
        if self._log_clusterer is None:
            from agent.modules.log_clusterer import LogClusterer
            self._log_clusterer = LogClusterer(self.config)
        return self._log_clusterer

    @property
    def error_explainer(self):
        if self._error_explainer is None:
            from agent.modules.error_explainer import ErrorExplainer
            self._error_explainer = ErrorExplainer(self.config)
        return self._error_explainer

    @property
    def memory(self):
        if self._memory is None:
            from agent.memory.memory_manager import MemoryManager
            self._memory = MemoryManager(self.config)
        return self._memory

    @property
    def dozzle_client(self):
        if self._dozzle_client is None:
            from agent.tools.dozzle_client import DozzleClient
            self._dozzle_client = DozzleClient(self.config)
        return self._dozzle_client

    # ── Core monitoring loop ─────────────────────────────────────────────────

    async def run_monitoring_loop(self):
        """Main agent loop: stream logs → analyze → alert."""
        self._running = True
        logger.info("DozzleOrchestrator starting monitoring loop")

        # Start background tasks
        explain_task = asyncio.create_task(self._explain_flush_loop())
        ingest_task = asyncio.create_task(self._log_ingest_loop())

        try:
            await asyncio.gather(ingest_task, explain_task)
        except asyncio.CancelledError:
            logger.info("Monitoring loop cancelled")
        finally:
            self._running = False

    async def _log_ingest_loop(self):
        """Stream logs from Docker / Dozzle and process each line."""
        batch: list[dict] = []
        batch_timeout = 0.1  # 100ms flush interval
        last_flush = time.monotonic()

        async for log_line in self.dozzle_client.stream_logs():
            batch.append(log_line)
            now = time.monotonic()

            if len(batch) >= 50 or (now - last_flush) >= batch_timeout:
                await self._process_batch(batch)
                batch = []
                last_flush = now

        if batch:
            await self._process_batch(batch)

    async def _process_batch(self, batch: list[dict]):
        """Process a batch of log lines through the full pipeline."""
        if not batch:
            return

        # Store in SQLite
        self.memory.store_log_lines(batch)
        self._stats["lines_processed"] += len(batch)

        # Update Prometheus metrics
        try:
            from agent.main import log_lines_processed
            for line in batch:
                container = line.get("container", "unknown")
                log_lines_processed.labels(container=container).inc()
        except Exception:
            pass  # Metrics unavailable in non-server mode

        # Parallel: anomaly detection + clustering
        results = await asyncio.gather(
            self._run_anomaly_detection(batch),
            self._run_clustering(batch),
            return_exceptions=True,
        )

        anomaly_results = results[0] if not isinstance(results[0], Exception) else []
        cluster_results = results[1] if not isinstance(results[1], Exception) else []

        # Identify lines needing explanation
        for i, log_line in enumerate(batch):
            a_result = anomaly_results[i] if i < len(anomaly_results) else {}
            c_result = cluster_results[i] if i < len(cluster_results) else {}

            is_anomaly = a_result.get("is_anomaly", False) or a_result.get("is_seq_anomaly", False)
            is_novel_cluster = c_result.get("is_novel_cluster", False)

            if is_anomaly or is_novel_cluster:
                container = log_line.get("container", "unknown")
                self._pending_anomalies[container].append({
                    "log_line": log_line,
                    "anomaly_score": a_result.get("anomaly_score", 0.0),
                    "cluster_id": c_result.get("cluster_id"),
                    "is_novel_cluster": is_novel_cluster,
                })
                self._stats["anomalies_detected"] += 1

                # Update Prometheus metrics
                try:
                    from agent.main import anomalies_detected_total, novel_clusters_total
                    severity = "HIGH" if abs(a_result.get("anomaly_score", 0)) > 0.5 else "MEDIUM"
                    anomalies_detected_total.labels(container=container, severity=severity).inc()
                    if is_novel_cluster:
                        novel_clusters_total.labels(container=container).inc()
                except Exception:
                    pass  # Metrics unavailable in non-server mode

                # Store anomaly event
                self.memory.store_anomaly_event({
                    "container": container,
                    "timestamp": log_line.get("timestamp", datetime.utcnow().isoformat()),
                    "message": log_line.get("message", ""),
                    "anomaly_score": a_result.get("anomaly_score", 0.0),
                    "seq_anomaly_score": a_result.get("seq_anomaly_score", 0.0),
                    "cluster_id": c_result.get("cluster_id"),
                })

    async def _run_anomaly_detection(self, batch: list[dict]) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.anomaly_detector.score_batch, batch)

    async def _run_clustering(self, batch: list[dict]) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.log_clusterer.assign_batch, batch)

    async def _explain_flush_loop(self):
        """Every 60 seconds, flush pending anomalies to the error explainer."""
        while self._running:
            await asyncio.sleep(60)
            now = time.monotonic()

            for container, pending in list(self._pending_anomalies.items()):
                if not pending:
                    continue
                last_flush = self._last_explain_flush[container]
                if now - last_flush < 60:
                    continue

                batch_to_explain = pending[:10]  # max 10 per container per flush
                self._pending_anomalies[container] = pending[10:]
                self._last_explain_flush[container] = now

                asyncio.create_task(self._explain_and_store(container, batch_to_explain))

    async def _explain_and_store(self, container: str, anomaly_batch: list[dict]):
        try:
            log_lines = [a["log_line"]["message"] for a in anomaly_batch]
            explanation = await self.error_explainer.explain(container, log_lines)
            self._stats["llm_calls"] += 1

            # Update stored anomaly events with explanation
            self.memory.update_anomaly_explanations(container, log_lines, explanation)

            # Push to anomaly event queue for SSE stream
            event = {
                "container": container,
                "timestamp": datetime.utcnow().isoformat(),
                "anomaly_count": len(anomaly_batch),
                "explanation": explanation,
            }
            try:
                self._anomaly_event_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop if queue full — non-blocking

            # Webhook alert if configured
            webhook_url = self.config.get("alerts", {}).get("webhook_url")
            if webhook_url and explanation.get("severity") in ("CRITICAL", "HIGH"):
                await self._send_webhook(webhook_url, event)

        except Exception as e:
            logger.error(f"Error explaining logs for {container}: {e}")

    async def _send_webhook(self, url: str, payload: dict):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10))
        except Exception as e:
            logger.warning(f"Webhook delivery failed: {e}")

    # ── Public API methods ───────────────────────────────────────────────────

    async def handle_nl_query(self, user_query: str, limit: int = 100) -> list[dict]:
        """Parse NL query and return matching log lines."""
        return await self.nl_query.execute(user_query, limit)

    async def get_recent_anomalies(
        self, container: str | None = None, hours: int = 1, limit: int = 50
    ) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        return self.memory.get_anomaly_events(
            container=container, since=since.isoformat(), limit=limit
        )

    async def get_cluster_summary(self, container: str | None = None) -> list[dict]:
        return self.log_clusterer.get_cluster_summary(container=container)

    async def explain_log_batch(self, container: str, log_lines: list[str]) -> dict:
        return await self.error_explainer.explain(container, log_lines)

    async def get_recent_lines(self, container: str, n: int = 20) -> list[str]:
        rows = self.memory.get_recent_log_lines(container=container, limit=n)
        return [r.get("message", "") for r in rows]

    async def stream_anomaly_events(self) -> AsyncIterator[dict]:
        while True:
            try:
                event = await asyncio.wait_for(self._anomaly_event_queue.get(), timeout=30)
                yield event
            except asyncio.TimeoutError:
                yield {"heartbeat": True, "timestamp": datetime.utcnow().isoformat()}

    async def get_stats(self) -> dict:
        return {
            **self._stats,
            "memory_stats": self.memory.get_stats(),
            "uptime_seconds": (
                datetime.utcnow() - datetime.fromisoformat(self._stats["start_time"])
            ).total_seconds(),
        }

    def stop(self):
        self._running = False
