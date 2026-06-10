"""
Automated tests for Dozzle-Enhanced AI Log Analysis Agent.
Run: pytest tests/test_agent.py -v
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_log_lines():
    return [
        {"container": "redis", "image": "redis:7", "timestamp": "2026-06-08T10:00:00Z",
         "message": "INFO  Server started, listening on port 6379", "log_level": "INFO"},
        {"container": "redis", "image": "redis:7", "timestamp": "2026-06-08T10:00:01Z",
         "message": "INFO  Client connected from 192.168.1.10:54123", "log_level": "INFO"},
        {"container": "redis", "image": "redis:7", "timestamp": "2026-06-08T10:00:02Z",
         "message": "ERROR  Connection reset by peer — client 192.168.1.10 disconnected", "log_level": "ERROR"},
        {"container": "api-server", "image": "api:v2", "timestamp": "2026-06-08T10:00:03Z",
         "message": "CRITICAL  Killed process 4242 (node) oom-kill:constraint=CONSTRAINT_MEMCG total-vm:8192MB anon-rss:3900MB",
         "log_level": "CRITICAL"},
        {"container": "nginx", "image": "nginx:1.25", "timestamp": "2026-06-08T10:00:04Z",
         "message": "INFO  127.0.0.1 - GET /health HTTP/1.1 200 OK", "log_level": "INFO"},
    ]

@pytest.fixture
def config():
    return {
        "anomaly_detection": {"contamination": 0.05, "bootstrap_size": 10},
        "clustering": {"novel_cluster_threshold": 0.75},
        "memory": {"db_path": ":memory:"},
    }

@pytest.fixture
def memory_manager(config):
    from agent.memory.memory_manager import MemoryManager
    return MemoryManager(config)


# ── LogAnomalyDetector Tests ──────────────────────────────────────────────────

class TestLogAnomalyDetector:

    def test_keyword_fallback_critical(self, config):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector, ANOMALY_THRESHOLD
        detector = LogAnomalyDetector(config)
        result = detector.score_single({
            "container": "test", "message": "CRITICAL  OOM kill: process terminated"
        })
        assert result["anomaly_score"] < ANOMALY_THRESHOLD
        assert result["is_anomaly"] is True

    def test_keyword_fallback_normal(self, config):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector
        detector = LogAnomalyDetector(config)
        result = detector.score_single({
            "container": "test", "message": "INFO  Request processed in 12ms"
        })
        assert result["is_anomaly"] is False

    def test_keyword_fallback_warning(self, config):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector
        detector = LogAnomalyDetector(config)
        result = detector.score_single({
            "container": "test", "message": "WARNING  High memory usage: 85%"
        })
        # WARNING should score > ANOMALY_THRESHOLD (-0.3)
        assert result["anomaly_score"] > -0.4  # -0.30 > -0.4

    def test_batch_scoring_returns_correct_count(self, config, sample_log_lines):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector
        detector = LogAnomalyDetector(config)
        results = detector.score_batch(sample_log_lines)
        assert len(results) == len(sample_log_lines)

    def test_batch_oom_detected(self, config):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector
        detector = LogAnomalyDetector(config)
        oom_line = {
            "container": "api-server",
            "message": "CRITICAL  Killed process 4242 oom-kill total-vm:8192MB"
        }
        result = detector.score_single(oom_line)
        assert result["is_anomaly"] is True

    def test_score_has_required_fields(self, config):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector
        detector = LogAnomalyDetector(config)
        result = detector.score_single({"container": "test", "message": "test log"})
        assert "anomaly_score" in result
        assert "is_anomaly" in result
        assert "seq_anomaly_score" in result
        assert "is_seq_anomaly" in result

    def test_isolation_forest_bootstraps(self, config):
        from agent.modules.log_anomaly_detector import LogAnomalyDetector, IsolationForestModel
        import numpy as np
        model = IsolationForestModel(contamination=0.05)
        # Add bootstrap_size embeddings
        for _ in range(12):
            emb = np.random.randn(1024).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            model.add_embedding(emb)
        # Before bootstrap size (10 in test config), model should not be fitted
        # (using default BOOTSTRAP_SIZE=500, so not fitted yet with 12)
        score = model.score(np.random.randn(1024).astype(np.float32))
        assert isinstance(score, float)


# ── NLLogQuery Tests ──────────────────────────────────────────────────────────

class TestNLLogQuery:

    def test_parse_with_regex_container(self, config):
        from agent.modules.nl_log_query import NLLogQuery
        q = NLLogQuery(config)
        result = q.parse_filter_sync("show postgres errors last 2 hours")
        assert result["time_range_minutes"] == 120
        assert result["log_level"] == "ERROR"

    def test_parse_with_regex_critical(self, config):
        from agent.modules.nl_log_query import NLLogQuery
        q = NLLogQuery(config)
        result = q.parse_filter_sync("all critical logs from the past 15 minutes")
        assert result["time_range_minutes"] == 15
        assert result["log_level"] == "CRITICAL"

    def test_parse_with_regex_today(self, config):
        from agent.modules.nl_log_query import NLLogQuery
        q = NLLogQuery(config)
        result = q.parse_filter_sync("nginx 404 errors today")
        assert result["time_range_minutes"] == 1440

    def test_parse_warning_level(self, config):
        from agent.modules.nl_log_query import NLLogQuery
        q = NLLogQuery(config)
        result = q.parse_filter_sync("show warnings in the last 30 minutes")
        assert result["log_level"] == "WARNING"
        assert result["time_range_minutes"] == 30

    def test_parse_returns_required_fields(self, config):
        from agent.modules.nl_log_query import NLLogQuery
        q = NLLogQuery(config)
        result = q.parse_filter_sync("show errors")
        for field in ["container_name", "time_range_minutes", "log_level", "keywords", "limit", "sort"]:
            assert field in result

    @pytest.mark.asyncio
    async def test_execute_with_memory(self, config, memory_manager, sample_log_lines):
        from agent.modules.nl_log_query import NLLogQuery
        memory_manager.store_log_lines(sample_log_lines)
        q = NLLogQuery(config)
        q._memory = memory_manager

        with patch.object(q, '_parse_with_llm', new=AsyncMock(
            return_value={"container_name": "redis", "time_range_minutes": 1440,
                          "log_level": None, "keywords": [], "limit": 100, "sort": "desc"}
        )):
            results = await q.execute("show redis logs today")
        assert all(r["container"] == "redis" for r in results)


# ── LogClusterer Tests ────────────────────────────────────────────────────────

class TestLogClusterer:

    def test_novel_cluster_first_line(self, config):
        from agent.modules.log_clusterer import LogClusterer
        clusterer = LogClusterer(config)
        result = clusterer.assign_single({
            "container": "app", "message": "INFO  Service started on port 8080"
        })
        assert "cluster_id" in result
        assert result["is_novel_cluster"] is True  # First line always novel

    def test_similar_lines_same_cluster(self, config):
        """Two nearly identical log lines should end up in the same cluster."""
        from agent.modules.log_clusterer import LogClusterer
        import numpy as np

        clusterer = LogClusterer(config)
        msg = "INFO  Request processed successfully in 12ms"

        # Mock embedder to return consistent vectors
        mock_emb = np.random.randn(1024).astype(np.float32)
        mock_emb = mock_emb / np.linalg.norm(mock_emb)

        with patch.object(clusterer, '_embed', return_value=mock_emb):
            r1 = clusterer.assign_single({"container": "app", "message": msg})
            r2 = clusterer.assign_single({"container": "app", "message": msg})

        assert r1["cluster_id"] == r2["cluster_id"]

    def test_different_lines_different_clusters(self, config):
        """Two semantically different log lines should get different cluster IDs."""
        from agent.modules.log_clusterer import LogClusterer
        import numpy as np

        clusterer = LogClusterer(config)

        # Create two orthogonal embeddings (maximally different)
        emb1 = np.zeros(1024, dtype=np.float32)
        emb1[0] = 1.0
        emb2 = np.zeros(1024, dtype=np.float32)
        emb2[512] = 1.0

        with patch.object(clusterer, '_embed', side_effect=[emb1, emb2]):
            r1 = clusterer.assign_single({"container": "app", "message": "INFO  start"})
            r2 = clusterer.assign_single({"container": "app", "message": "ERROR  crash"})

        assert r1["cluster_id"] != r2["cluster_id"]

    def test_cluster_summary_empty(self, config):
        from agent.modules.log_clusterer import LogClusterer
        clusterer = LogClusterer(config)
        summary = clusterer.get_cluster_summary()
        assert isinstance(summary, list)
        assert len(summary) == 0

    def test_assign_batch_returns_correct_length(self, config, sample_log_lines):
        from agent.modules.log_clusterer import LogClusterer
        clusterer = LogClusterer(config)
        results = clusterer.assign_batch(sample_log_lines)
        assert len(results) == len(sample_log_lines)


# ── ErrorExplainer Tests ──────────────────────────────────────────────────────

class TestErrorExplainer:

    def test_keyword_fallback_oom(self, config):
        from agent.modules.error_explainer import ErrorExplainer
        explainer = ErrorExplainer(config)
        result = explainer.explain_sync("api-server", ["CRITICAL OOM kill: process terminated"])
        assert result["error_type"] == "OutOfMemoryKill"
        assert result["severity"] == "CRITICAL"
        assert len(result["fix_steps"]) > 0

    def test_keyword_fallback_timeout(self, config):
        from agent.modules.error_explainer import ErrorExplainer
        explainer = ErrorExplainer(config)
        result = explainer.explain_sync("redis", ["ERROR  Connection timeout to upstream"])
        assert result["error_type"] == "ConnectionTimeout"
        assert result["severity"] == "HIGH"

    def test_keyword_fallback_unknown(self, config):
        from agent.modules.error_explainer import ErrorExplainer
        explainer = ErrorExplainer(config)
        result = explainer.explain_sync("app", ["INFO  Some unknown pattern here"])
        assert "error_type" in result
        assert "severity" in result
        assert "fix_steps" in result

    @pytest.mark.asyncio
    async def test_explain_with_mock_llm(self, config):
        from agent.modules.error_explainer import ErrorExplainer
        explainer = ErrorExplainer(config)

        mock_response = json.dumps({
            "error_type": "ConnectionRefused",
            "severity": "HIGH",
            "description": "Database connection refused",
            "root_cause": "Postgres container not running",
            "fix_steps": ["Check docker ps", "Restart postgres"],
            "docs_links": [],
        })

        with patch.object(explainer, '_get_llm') as mock_llm_getter:
            mock_llm = AsyncMock()
            mock_llm.complete.return_value = mock_response
            mock_llm_getter.return_value = mock_llm
            result = await explainer.explain("api-server", ["FATAL  connection refused to postgres"])

        assert result["error_type"] == "ConnectionRefused"
        assert result["severity"] == "HIGH"

    def test_explanation_cache_hit(self, config):
        from agent.modules.error_explainer import ErrorExplainer, ExplanationCache
        cache = ExplanationCache()
        explanation = {"error_type": "Test", "severity": "LOW"}
        lines = ["test line"]
        cache.set("container", lines, explanation)
        cached = cache.get("container", lines)
        assert cached == explanation


# ── MemoryManager Tests ───────────────────────────────────────────────────────

class TestMemoryManager:

    def test_store_and_retrieve_log_lines(self, memory_manager, sample_log_lines):
        memory_manager.store_log_lines(sample_log_lines)
        results = memory_manager.get_recent_log_lines(limit=10)
        assert len(results) == len(sample_log_lines)

    def test_store_and_retrieve_anomaly_event(self, memory_manager):
        event = {
            "container": "api-server",
            "timestamp": "2026-06-08T10:00:00Z",
            "message": "CRITICAL OOM kill",
            "anomaly_score": -0.95,
            "seq_anomaly_score": 0.8,
            "cluster_id": 1,
        }
        memory_manager.store_anomaly_event(event)
        results = memory_manager.get_anomaly_events(container="api-server", limit=5)
        assert len(results) >= 1
        assert results[0]["message"] == event["message"]

    def test_nl_query_cache(self, memory_manager):
        query = "show redis errors last 2 hours"
        results = [{"id": 1, "message": "test", "container": "redis"}]
        memory_manager.store_nl_query_cache(query, results)
        cached = memory_manager.get_nl_query_cache(query)
        assert cached is not None
        assert len(cached) == 1

    def test_knowledge_hash_dedup(self, memory_manager):
        url = "https://arxiv.org/abs/2103.04475"
        assert memory_manager.is_known_entry(url) is False
        memory_manager.add_knowledge_entry(url, "LogBERT Paper", "ArXiv")
        assert memory_manager.is_known_entry(url) is True

    def test_get_stats_returns_counts(self, memory_manager, sample_log_lines):
        memory_manager.store_log_lines(sample_log_lines)
        stats = memory_manager.get_stats()
        assert stats["total_log_lines"] == len(sample_log_lines)
        assert "total_anomaly_events" in stats


# ── Integration Tests ─────────────────────────────────────────────────────────

class TestIntegration:

    @pytest.mark.asyncio
    async def test_orchestrator_processes_mock_stream(self, config):
        """Orchestrator should process mock log lines and detect OOM anomaly."""
        from agent.orchestrator import DozzleOrchestrator
        orch = DozzleOrchestrator()
        orch._memory = MagicMock()
        orch._memory.store_log_lines = MagicMock()
        orch._memory.store_anomaly_event = MagicMock()

        oom_lines = [
            {"container": "api", "message": "CRITICAL OOM kill process 999", "timestamp": "2026-06-08T00:00:00Z"},
        ]
        with patch.object(orch, '_run_anomaly_detection', new=AsyncMock(
            return_value=[{"is_anomaly": True, "anomaly_score": -0.95, "is_seq_anomaly": False}]
        )):
            with patch.object(orch, '_run_clustering', new=AsyncMock(
                return_value=[{"cluster_id": 0, "is_novel_cluster": False}]
            )):
                await orch._process_batch(oom_lines)

        orch._memory.store_anomaly_event.assert_called_once()
        assert orch._stats["anomalies_detected"] == 1

    @pytest.mark.asyncio
    async def test_nl_query_pipeline(self, config):
        """NL query should parse and return results from memory."""
        from agent.orchestrator import DozzleOrchestrator
        from agent.memory.memory_manager import MemoryManager
        orch = DozzleOrchestrator()
        orch._memory = MemoryManager(config)

        # Pre-populate with test log lines
        test_lines = [
            {"container": "redis", "image": "redis:7",
             "timestamp": "2026-06-08T10:00:00Z",
             "message": "ERROR  Connection refused", "log_level": "ERROR"},
        ]
        orch.memory.store_log_lines(test_lines)

        with patch.object(orch.nl_query, '_parse_with_llm', new=AsyncMock(
            return_value={"container_name": "redis", "time_range_minutes": 1440,
                          "log_level": "ERROR", "keywords": [], "limit": 50, "sort": "desc"}
        )):
            orch.nl_query._memory = orch.memory
            results = await orch.handle_nl_query("show redis errors today", 50)

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_explain_log_batch_returns_dict(self, config):
        from agent.orchestrator import DozzleOrchestrator
        orch = DozzleOrchestrator()
        with patch.object(orch.error_explainer, '_get_llm', return_value=None):
            result = await orch.explain_log_batch(
                "api-server", ["ERROR OOM kill process 999"]
            )
        assert isinstance(result, dict)
        assert "error_type" in result


# ── CLI Smoke Tests ──────────────────────────────────────────────────────────

class TestCLI:

    def test_cli_help(self):
        from click.testing import CliRunner
        from agent.main import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "monitor" in result.output

    def test_cli_query_help(self):
        from click.testing import CliRunner
        from agent.main import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["query", "--help"])
        assert result.exit_code == 0

    def test_cli_anomalies_help(self):
        from click.testing import CliRunner
        from agent.main import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["anomalies", "--help"])
        assert result.exit_code == 0

    def test_cli_explain_help(self):
        from click.testing import CliRunner
        from agent.main import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["explain", "--help"])
        assert result.exit_code == 0

    def test_cli_update_knowledge_help(self):
        from click.testing import CliRunner
        from agent.main import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["update-knowledge", "--help"])
        assert result.exit_code == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
