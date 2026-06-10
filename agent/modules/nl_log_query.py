"""
NLLogQuery — natural language log query interface.

Converts user queries like "show redis errors last 2 hours" into structured
filters, then executes them against the SQLite log store.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Pre-defined query templates for MiniLM template retrieval
QUERY_TEMPLATES = [
    {"text": "show errors from {container} last {N} hours", "level": "ERROR", "time": True},
    {"text": "show critical logs from {container}", "level": "CRITICAL", "time": False},
    {"text": "show warnings in the last {N} minutes", "level": "WARNING", "time": True},
    {"text": "find {keyword} errors", "level": "ERROR", "keywords": ["{keyword}"]},
    {"text": "all logs from {container}", "level": None, "time": False},
    {"text": "database connection errors last {N} hours", "keywords": ["connection", "database"]},
    {"text": "memory errors or OOM kills", "keywords": ["OOM", "memory", "killed"]},
    {"text": "authentication failures today", "keywords": ["auth", "unauthorized", "403", "401"]},
    {"text": "timeout errors", "keywords": ["timeout", "timed out", "deadline"]},
    {"text": "startup and initialization logs", "keywords": ["starting", "initialized", "ready"]},
]

FALLBACK_PARSE_PATTERNS = [
    (r"container[:\s]+(\w[\w-]*)", "container_name"),
    (r"last\s+(\d+)\s+hour", "time_range_hours"),
    (r"last\s+(\d+)\s+minute", "time_range_minutes"),
    (r"\b(ERROR|WARN(?:ING)?|INFO|DEBUG|CRITICAL|FATAL)\b", "log_level"),
]


class NLLogQuery:
    """
    NL → structured log filter → SQLite results.

    Uses:
    1. MiniLM template retrieval for speed (no LLM needed for cached queries)
    2. LLM (Claude/OpenAI/Ollama) for novel query intent extraction
    3. Regex fallback for offline mode
    """

    def __init__(self, config: dict):
        self.config = config
        self._embedder = None
        self._llm = None
        self._memory = None
        self._template_embeddings = None

    def _get_embedder(self):
        if self._embedder is None:
            try:
                from tools.hf_model_manager import HFModelManager
                self._embedder = HFModelManager().get_model("sentence_similarity")
            except Exception as e:
                logger.warning(f"MiniLM unavailable: {e}")
        return self._embedder

    def _get_llm(self):
        if self._llm is None:
            try:
                from tools.llm_client import LLMClient
                self._llm = LLMClient()
            except Exception as e:
                logger.warning(f"LLM client unavailable: {e}")
        return self._llm

    def _get_memory(self):
        if self._memory is None:
            from agent.memory.memory_manager import MemoryManager
            self._memory = MemoryManager(self.config)
        return self._memory

    def _get_template_embeddings(self):
        if self._template_embeddings is None:
            embedder = self._get_embedder()
            if embedder:
                try:
                    import numpy as np
                    texts = [t["text"] for t in QUERY_TEMPLATES]
                    self._template_embeddings = embedder.encode(texts, normalize_embeddings=True)
                except Exception:
                    pass
        return self._template_embeddings

    def _similarity_to_templates(self, query: str) -> float:
        """Return max cosine similarity to any template."""
        embedder = self._get_embedder()
        template_embs = self._get_template_embeddings()
        if embedder is None or template_embs is None:
            return 0.0
        try:
            import numpy as np
            q_emb = embedder.encode([query], normalize_embeddings=True)[0]
            sims = template_embs @ q_emb
            return float(np.max(sims))
        except Exception:
            return 0.0

    async def _parse_with_llm(self, query: str) -> dict:
        """Use LLM to extract structured filter from NL query."""
        llm = self._get_llm()
        if llm is None:
            return self._parse_with_regex(query)

        prompt = f"""Extract a structured JSON filter from this Docker log query.

Query: "{query}"

Examples:
Query: "show redis errors last 2 hours"
Output: {{"container_name": "redis", "time_range_minutes": 120, "log_level": "ERROR", "keywords": [], "limit": 100, "sort": "desc"}}

Query: "nginx 404 errors today"
Output: {{"container_name": "nginx", "time_range_minutes": 1440, "log_level": null, "keywords": ["404"], "limit": 200, "sort": "desc"}}

Query: "all critical logs from the past 15 minutes"
Output: {{"container_name": null, "time_range_minutes": 15, "log_level": "CRITICAL", "keywords": [], "limit": 500, "sort": "desc"}}

Output only valid JSON, nothing else."""

        try:
            response = await llm.complete(
                prompt=prompt,
                system="You are a log query filter extractor. Output only valid JSON.",
                temperature=0.0,
                max_tokens=256,
            )
            text = response.strip()
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"LLM query parse failed: {e}")

        return self._parse_with_regex(query)

    def _parse_with_regex(self, query: str) -> dict:
        """Fallback regex-based query parser."""
        result = {
            "container_name": None,
            "time_range_minutes": 60,
            "log_level": None,
            "keywords": [],
            "limit": 100,
            "sort": "desc",
        }
        q_lower = query.lower()

        # Time range extraction
        hours_match = re.search(r"last\s+(\d+)\s+hour", q_lower)
        minutes_match = re.search(r"last\s+(\d+)\s+minute", q_lower)
        if hours_match:
            result["time_range_minutes"] = int(hours_match.group(1)) * 60
        elif minutes_match:
            result["time_range_minutes"] = int(minutes_match.group(1))
        elif "today" in q_lower:
            result["time_range_minutes"] = 1440
        elif "yesterday" in q_lower:
            result["time_range_minutes"] = 2880

        # Log level
        level_match = re.search(r"\b(ERROR|WARN(?:ING)?|INFO|DEBUG|CRITICAL|FATAL)\b", query, re.I)
        if level_match:
            level = level_match.group(1).upper()
            result["log_level"] = "WARNING" if level == "WARN" else level

        # Keywords from query terms (remove stop words)
        stop_words = {"show", "me", "the", "all", "from", "last", "in", "of", "for", "a", "an",
                      "logs", "log", "lines", "hours", "minutes", "hour", "minute", "get", "find"}
        words = re.findall(r"\w+", q_lower)
        keywords = [w for w in words if w not in stop_words and len(w) > 3
                    and not w.isdigit()]
        result["keywords"] = keywords[:5]

        return result

    async def execute(self, user_query: str, limit: int = 100) -> list[dict]:
        """Parse NL query and execute against log store."""
        # Check cache
        memory = self._get_memory()
        cached = memory.get_nl_query_cache(user_query)
        if cached:
            logger.debug(f"NL query cache hit: {user_query[:50]}")
            return cached

        # Parse query
        parsed_filter = await self._parse_with_llm(user_query)
        parsed_filter["limit"] = min(limit, parsed_filter.get("limit", 100))

        # Execute against SQLite
        results = memory.query_log_lines(parsed_filter)

        # Cache result (5-minute TTL)
        memory.store_nl_query_cache(user_query, results)

        return results

    def parse_filter_sync(self, query: str) -> dict:
        """Synchronous regex-only parse — for testing."""
        return self._parse_with_regex(query)
