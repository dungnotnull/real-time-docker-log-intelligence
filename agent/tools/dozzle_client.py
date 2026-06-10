"""
DozzleClient — streams log lines from Docker Engine / Dozzle HTTP API.

Priority:
1. Dozzle HTTP SSE API (if DOZZLE_BASE_URL configured)
2. Docker SDK direct (docker.sock)
3. Mock/test mode (DOZZLE_MOCK=true)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import AsyncIterator

logger = logging.getLogger(__name__)

LOG_LEVEL_PATTERNS = {
    "ERROR": ["error", "err ", "exception", "traceback", "fatal", "critical"],
    "WARNING": ["warn", "warning"],
    "INFO": ["info", " i "],
    "DEBUG": ["debug", " d "],
}


def _detect_log_level(message: str) -> str:
    msg_lower = message.lower()
    for level, patterns in LOG_LEVEL_PATTERNS.items():
        if any(p in msg_lower for p in patterns):
            return level
    return "INFO"


class DozzleClient:
    """
    Unified Docker log client.
    Streams structured LogLine dicts: {container, image, timestamp, message, log_level, stream}
    """

    def __init__(self, config: dict):
        self.config = config
        self._dozzle_base_url = (
            os.getenv("DOZZLE_BASE_URL") or
            config.get("dozzle", {}).get("base_url", "http://localhost:8080")
        )
        self._mock_mode = os.getenv("DOZZLE_MOCK", "false").lower() == "true"
        self._docker_socket = config.get("dozzle", {}).get("docker_socket", "/var/run/docker.sock")

    async def stream_logs(self) -> AsyncIterator[dict]:
        """Main log stream entry point."""
        if self._mock_mode:
            async for line in self._mock_stream():
                yield line
        else:
            try:
                async for line in self._stream_via_docker_sdk():
                    yield line
            except Exception as e:
                logger.warning(f"Docker SDK stream failed: {e} — falling back to Dozzle HTTP")
                try:
                    async for line in self._stream_via_dozzle_http():
                        yield line
                except Exception as e2:
                    logger.error(f"All log sources failed: {e2}")
                    await asyncio.sleep(30)

    async def _stream_via_docker_sdk(self) -> AsyncIterator[dict]:
        """Stream logs from all containers via docker-py SDK."""
        import docker  # type: ignore
        client = docker.from_env()
        containers = client.containers.list()
        logger.info(f"Streaming logs from {len(containers)} containers via Docker SDK")

        # Create per-container async generators
        queues: dict[str, asyncio.Queue] = {}
        tasks = []
        loop = asyncio.get_event_loop()
        master_queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)

        def _stream_container(container):
            for log_bytes in container.logs(stream=True, follow=True, timestamps=True):
                line = log_bytes.decode("utf-8", errors="replace").rstrip()
                log_line = self._parse_docker_log_line(line, container.name, container.image.tags)
                asyncio.run_coroutine_threadsafe(master_queue.put(log_line), loop)

        for container in containers:
            import threading
            t = threading.Thread(target=_stream_container, args=(container,), daemon=True)
            t.start()

        while True:
            try:
                item = await asyncio.wait_for(master_queue.get(), timeout=60)
                yield item
            except asyncio.TimeoutError:
                # Check for new containers every 60 seconds
                try:
                    new_containers = client.containers.list()
                    new_ids = {c.id for c in new_containers} - {c.id for c in containers}
                    for cid in new_ids:
                        new_c = client.containers.get(cid)
                        t = threading.Thread(target=_stream_container, args=(new_c,), daemon=True)
                        t.start()
                    containers = new_containers
                except Exception:
                    pass

    async def _stream_via_dozzle_http(self) -> AsyncIterator[dict]:
        """Stream logs via Dozzle HTTP SSE endpoint."""
        import aiohttp
        url = f"{self._dozzle_base_url}/api/events/stream"
        logger.info(f"Streaming logs via Dozzle HTTP SSE: {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(sock_read=3600)) as resp:
                async for line in resp.content:
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text or text.startswith(":"):
                        continue
                    if text.startswith("data:"):
                        try:
                            data = json.loads(text[5:].strip())
                            yield {
                                "container": data.get("name", "unknown"),
                                "image": data.get("image", ""),
                                "timestamp": data.get("time", datetime.utcnow().isoformat()),
                                "message": data.get("message", ""),
                                "log_level": _detect_log_level(data.get("message", "")),
                                "stream": data.get("stream", "stdout"),
                            }
                        except json.JSONDecodeError:
                            pass

    async def _mock_stream(self) -> AsyncIterator[dict]:
        """Mock log stream for testing — generates synthetic Docker log lines."""
        import random
        containers = ["nginx", "redis", "postgres", "api-server", "worker"]
        normal_messages = [
            "INFO  Request processed in 12ms",
            "INFO  Cache hit for key user:123",
            "INFO  Database connection pool: 5/20 used",
            "DEBUG Health check passed",
            "INFO  Worker job completed successfully",
        ]
        anomalous_messages = [
            "ERROR  Connection timeout to postgres after 5000ms",
            "CRITICAL  Out of memory: Kill process 1234 (node) total-vm:4096MB",
            "ERROR  FATAL: database 'app_db' does not exist",
            "ERROR  Unhandled exception: NullPointerException at line 42",
            "WARNING  High memory usage: 95% of limit",
        ]

        i = 0
        while True:
            container = random.choice(containers)
            is_anomaly = random.random() < 0.05  # 5% anomaly rate
            message = random.choice(anomalous_messages if is_anomaly else normal_messages)

            yield {
                "container": container,
                "image": f"{container}:latest",
                "timestamp": datetime.utcnow().isoformat(),
                "message": message,
                "log_level": _detect_log_level(message),
                "stream": "stdout",
            }

            i += 1
            await asyncio.sleep(0.01)  # 100 lines/second mock rate

    def _parse_docker_log_line(self, raw: str, container_name: str, image_tags: list) -> dict:
        """Parse raw Docker log line (with optional RFC3339 timestamp prefix)."""
        import re
        timestamp = datetime.utcnow().isoformat()
        message = raw

        ts_match = re.match(r'^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s+(.*)', raw)
        if ts_match:
            timestamp = ts_match.group(1)
            message = ts_match.group(2)

        return {
            "container": container_name,
            "image": image_tags[0] if image_tags else "",
            "timestamp": timestamp,
            "message": message,
            "log_level": _detect_log_level(message),
            "stream": "stdout",
        }

    async def get_containers(self) -> list[dict]:
        """List all running containers."""
        try:
            import docker
            client = docker.from_env()
            containers = client.containers.list()
            return [
                {
                    "id": c.id[:12],
                    "name": c.name,
                    "image": c.image.tags[0] if c.image.tags else "",
                    "status": c.status,
                }
                for c in containers
            ]
        except Exception as e:
            logger.error(f"Failed to list containers: {e}")
            return []
