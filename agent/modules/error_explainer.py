"""
ErrorExplainer — LLM-powered error pattern explanation using Claude API.

Batches anomalous log lines per container (60-second window, max 10 lines),
generates structured explanations with root cause and fix steps.
Uses BART-CNN to summarize long log batches before sending to LLM.
Caches explanations at cluster level to reduce API cost.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

SUMMARIZE_THRESHOLD = 2_000  # chars — use BART-CNN above this
MAX_BATCH_LINES = 10
CACHE_TTL_SECONDS = 3600  # 1-hour cluster-level cache

EXPLANATION_SCHEMA = {
    "error_type": str,
    "severity": str,
    "description": str,
    "root_cause": str,
    "fix_steps": list,
    "docs_links": list,
}

FALLBACK_EXPLANATIONS = {
    "OOM": {
        "error_type": "OutOfMemoryKill",
        "severity": "CRITICAL",
        "description": "Container was killed by the OS OOM killer due to memory exhaustion.",
        "root_cause": "Memory limit exceeded — container allocated more RAM than its configured limit.",
        "fix_steps": [
            "Increase container memory limit in docker-compose.yml (deploy.resources.limits.memory)",
            "Profile application for memory leaks using heap dumps",
            "Add memory-efficient caching (LRU eviction)",
        ],
        "docs_links": ["https://docs.docker.com/config/containers/resource_constraints/"],
    },
    "TIMEOUT": {
        "error_type": "ConnectionTimeout",
        "severity": "HIGH",
        "description": "Connection or operation timed out, possibly due to network issues or slow dependency.",
        "root_cause": "Downstream service unreachable or responding slowly beyond configured timeout.",
        "fix_steps": [
            "Check health of dependent services",
            "Increase timeout values if load is legitimately high",
            "Add circuit breaker pattern to prevent cascade failures",
        ],
        "docs_links": [],
    },
    "REFUSED": {
        "error_type": "ConnectionRefused",
        "severity": "HIGH",
        "description": "Connection refused — target service is not listening or not ready.",
        "root_cause": "Service not started, crashed, or port misconfigured.",
        "fix_steps": [
            "Check if target service container is running: docker ps",
            "Verify port mapping configuration",
            "Check service startup logs for initialization errors",
        ],
        "docs_links": [],
    },
}


class ExplanationCache:
    """Simple in-memory LRU cache for cluster-level explanations."""

    def __init__(self, max_size: int = 500):
        self._cache: dict[str, tuple[dict, float]] = {}
        self._max_size = max_size

    def _hash(self, container: str, lines: list[str]) -> str:
        content = container + "|".join(lines[:5])
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, container: str, lines: list[str]) -> dict | None:
        import time
        key = self._hash(container, lines)
        if key in self._cache:
            value, ts = self._cache[key]
            if time.time() - ts < CACHE_TTL_SECONDS:
                return value
            del self._cache[key]
        return None

    def set(self, container: str, lines: list[str], explanation: dict):
        import time
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        key = self._hash(container, lines)
        self._cache[key] = (explanation, time.time())


class ErrorExplainer:
    """
    Generates LLM-powered explanations for anomalous log batches.

    Pipeline:
    1. Optionally summarize with BART-CNN if logs are too long
    2. Build LLM prompt with container context + anomalous lines
    3. LLM (Claude) returns structured JSON explanation
    4. Cache result at cluster level
    5. Return explanation dict
    """

    def __init__(self, config: dict):
        self.config = config
        self._llm = None
        self._summarizer = None
        self._cache = ExplanationCache()

    def _get_llm(self):
        if self._llm is None:
            try:
                from tools.llm_client import LLMClient
                self._llm = LLMClient()
            except Exception as e:
                logger.warning(f"LLM client unavailable: {e}")
        return self._llm

    def _get_summarizer(self):
        if self._summarizer is None:
            try:
                from tools.hf_model_manager import HFModelManager
                self._summarizer = HFModelManager().get_model("summarization")
            except Exception as e:
                logger.warning(f"BART-CNN unavailable: {e}")
        return self._summarizer

    def _keyword_fallback(self, lines: list[str]) -> dict:
        """Return a keyword-matched fallback explanation without LLM."""
        combined = " ".join(lines).upper()
        for keyword, explanation in FALLBACK_EXPLANATIONS.items():
            if keyword in combined:
                return explanation
        return {
            "error_type": "UnknownAnomaly",
            "severity": "MEDIUM",
            "description": "Anomalous log pattern detected. Manual review recommended.",
            "root_cause": "Pattern deviates significantly from normal log baseline.",
            "fix_steps": ["Review full container logs", "Check recent deployments"],
            "docs_links": [],
        }

    def _maybe_summarize(self, log_text: str) -> str:
        """Summarize with BART-CNN if log text exceeds threshold."""
        if len(log_text) <= SUMMARIZE_THRESHOLD:
            return log_text
        summarizer = self._get_summarizer()
        if summarizer is None:
            return log_text[:SUMMARIZE_THRESHOLD] + "... [truncated]"
        try:
            result = summarizer(
                log_text,
                max_length=300,
                min_length=50,
                do_sample=False,
            )
            return result[0]["summary_text"]
        except Exception as e:
            logger.warning(f"BART summarization failed: {e}")
            return log_text[:SUMMARIZE_THRESHOLD] + "... [truncated]"

    def _build_prompt(self, container: str, log_lines: list[str]) -> str:
        log_text = "\n".join(log_lines[:MAX_BATCH_LINES])
        summarized = self._maybe_summarize(log_text)

        return f"""Analyze these anomalous Docker container log lines and provide a structured explanation.

Container: {container}
Anomalous log lines:
{summarized}

Respond with ONLY valid JSON matching this exact structure:
{{
  "error_type": "specific error category",
  "severity": "CRITICAL|HIGH|MEDIUM|LOW",
  "description": "plain English description of what happened",
  "root_cause": "likely underlying cause with confidence level",
  "fix_steps": ["step 1", "step 2", "step 3"],
  "docs_links": ["url1"]
}}"""

    async def explain(self, container: str, log_lines: list[str]) -> dict:
        """Generate explanation for a batch of anomalous log lines."""
        if not log_lines:
            return self._keyword_fallback([])

        # Check cache
        cached = self._cache.get(container, log_lines)
        if cached:
            return cached

        llm = self._get_llm()
        if llm is None:
            return self._keyword_fallback(log_lines)

        prompt = self._build_prompt(container, log_lines)
        system = (
            "You are a senior DevOps and SRE expert. Analyze Docker container logs and explain errors "
            "clearly for software engineers. Always output valid JSON only."
        )

        try:
            response = await llm.complete(
                prompt=prompt,
                system=system,
                temperature=0.2,
                max_tokens=600,
            )
            # Extract JSON
            json_match = re.search(r'\{.*\}', response.strip(), re.DOTALL)
            if json_match:
                explanation = json.loads(json_match.group())
                # Validate required fields
                for field in EXPLANATION_SCHEMA:
                    if field not in explanation:
                        explanation[field] = "" if isinstance(EXPLANATION_SCHEMA[field], str) else []
                self._cache.set(container, log_lines, explanation)
                return explanation
        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON: {e}")
        except Exception as e:
            logger.error(f"LLM explanation failed for {container}: {e}")

        fallback = self._keyword_fallback(log_lines)
        self._cache.set(container, log_lines, fallback)
        return fallback

    def explain_sync(self, container: str, log_lines: list[str]) -> dict:
        """Synchronous version for testing."""
        return self._keyword_fallback(log_lines)
