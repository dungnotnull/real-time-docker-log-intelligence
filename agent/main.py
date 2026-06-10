"""
Dozzle-Enhanced AI Log Analysis Agent — Entry Point
CLI + FastAPI REST API server
"""

import asyncio
import json
import sys
import os
from pathlib import Path
from datetime import datetime

import click
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY
from prometheus_client import CONTENT_TYPE_LATEST

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.orchestrator import DozzleOrchestrator
from tools.knowledge_updater import KnowledgeUpdater
from tools.llm_client import LLMClient

# ── Prometheus Metrics ─────────────────────────────────────────────────────────

# Log processing metrics
log_lines_processed = Counter(
    "dozzle_agent_log_lines_processed_total",
    "Total number of log lines processed",
    ["container"]
)

log_ingestion_rate = Gauge(
    "dozzle_agent_log_ingestion_rate_lines_per_second",
    "Current log ingestion rate",
)

# Anomaly detection metrics
anomalies_detected_total = Counter(
    "dozzle_agent_anomalies_detected_total",
    "Total number of anomalies detected",
    ["container", "severity"]
)

anomaly_score = Gauge(
    "dozzle_agent_anomaly_score",
    "Current anomaly scores by container",
    ["container"]
)

# Clustering metrics
clusters_total = Gauge(
    "dozzle_agent_clusters_total",
    "Total number of log clusters",
    ["container"]
)

novel_clusters_total = Counter(
    "dozzle_agent_novel_clusters_total",
    "Total number of novel (new) clusters created",
    ["container"]
)

# LLM metrics
llm_requests_total = Counter(
    "dozzle_agent_llm_requests_total",
    "Total number of LLM API requests",
    ["provider", "model", "task_type"]
)

llm_latency_seconds = Histogram(
    "dozzle_agent_llm_latency_seconds",
    "LLM request latency in seconds",
    ["provider", "model"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

llm_cost_usd_total = Counter(
    "dozzle_agent_llm_cost_usd_total",
    "Total LLM API cost in USD",
    ["provider", "model"]
)

# Query metrics
nl_query_total = Counter(
    "dozzle_agent_nl_query_total",
    "Total number of natural language queries",
    ["cache_hit"]
)

nl_query_latency_seconds = Histogram(
    "dozzle_agent_nl_query_latency_seconds",
    "Natural language query latency in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0]
)

# System health metrics
agent_uptime_seconds = Gauge(
    "dozzle_agent_uptime_seconds",
    "Agent uptime in seconds"
)

memory_usage_bytes = Gauge(
    "dozzle_agent_memory_usage_bytes",
    "Memory usage by component",
    ["component"]
)

docker_containers_total = Gauge(
    "dozzle_agent_docker_containers_total",
    "Number of Docker containers being monitored"
)

api_request_duration_seconds = Histogram(
    "dozzle_agent_api_request_duration_seconds",
    "API request duration in seconds",
    ["endpoint", "method"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0]
)

app = FastAPI(
    title="Dozzle-Enhanced AI Log Analysis Agent",
    description="ML anomaly detection, NL log queries, and AI-powered error explanations for Docker logs",
    version="1.0.0",
)

_orchestrator: DozzleOrchestrator | None = None


def get_orchestrator() -> DozzleOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = DozzleOrchestrator()
    return _orchestrator


# ── REST API models ─────────────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    query: str
    limit: int = 100


class ExplainRequest(BaseModel):
    container_name: str
    log_lines: list[str]


class AnomalyResponse(BaseModel):
    container: str
    timestamp: str
    message: str
    anomaly_score: float
    explanation: str | None


# ── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/query")
async def nl_query(req: NLQueryRequest):
    orch = get_orchestrator()
    results = await orch.handle_nl_query(req.query, req.limit)
    return {"query": req.query, "results": results, "count": len(results)}


@app.get("/anomalies")
async def get_anomalies(
    container: str | None = Query(None),
    hours: int = Query(1, ge=1, le=168),
    limit: int = Query(50, ge=1, le=500),
):
    orch = get_orchestrator()
    anomalies = await orch.get_recent_anomalies(container=container, hours=hours, limit=limit)
    return {"anomalies": anomalies, "count": len(anomalies)}


@app.get("/clusters")
async def get_clusters(container: str | None = Query(None)):
    orch = get_orchestrator()
    clusters = await orch.get_cluster_summary(container=container)
    return {"clusters": clusters}


@app.post("/explain")
async def explain_logs(req: ExplainRequest):
    orch = get_orchestrator()
    explanation = await orch.explain_log_batch(req.container_name, req.log_lines)
    return {"container": req.container_name, "explanation": explanation}


@app.get("/stats")
async def get_stats():
    orch = get_orchestrator()
    return await orch.get_stats()


@app.get("/anomalies/stream")
async def stream_anomalies():
    """Server-sent events stream of real-time anomaly events."""
    orch = get_orchestrator()

    async def event_generator():
        async for event in orch.stream_anomaly_events():
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint for monitoring and alerting."""
    orch = get_orchestrator()

    # Update dynamic metrics before serving
    stats = await orch.get_stats()

    # Update uptime metric
    uptime = stats.get("uptime_seconds", 0)
    agent_uptime_seconds.set(uptime)

    # Update memory metrics
    memory_stats = stats.get("memory_stats", {})
    memory_usage_bytes.labels(component="total").set(memory_stats.get("total_log_lines", 0))
    memory_usage_bytes.labels(component="anomalies").set(memory_stats.get("total_anomaly_events", 0))

    # Update anomaly scores from recent anomalies
    recent_anomalies = await orch.get_recent_anomalies(limit=100)
    container_scores = {}
    for anomaly in recent_anomalies:
        container = anomaly.get("container", "unknown")
        score = anomaly.get("anomaly_score", 0)
        if container not in container_scores or abs(score) > abs(container_scores[container]):
            container_scores[container] = score

    for container, score in container_scores.items():
        anomaly_score.labels(container=container).set(score)

    # Update cluster counts
    cluster_summary = await orch.get_cluster_summary()
    container_cluster_count = {}
    for cluster in cluster_summary:
        for c in cluster.get("containers", []):
            container_cluster_count[c] = container_cluster_count.get(c, 0) + 1

    for container, count in container_cluster_count.items():
        clusters_total.labels(container=container).set(count)

    # Update container count
    try:
        containers = await orch.dozzle_client.get_containers()
        docker_containers_total.set(len(containers))
    except Exception:
        pass

    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


from fastapi import Response


# ── CLI ──────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Dozzle-Enhanced AI Log Analysis Agent"""
    pass


@cli.command()
@click.option("--host", default="0.0.0.0", help="REST API host")
@click.option("--port", default=8766, help="REST API port")
@click.option("--daemon", is_flag=True, help="Run background monitoring loop only (no API)")
@click.option("--config", default="config/agent_config.yaml", help="Config file path")
def monitor(host: str, port: int, daemon: bool, config: str):
    """Start continuous log monitoring with real-time anomaly detection."""
    click.echo(f"[dozzle-enhanced] Starting log monitor (port={port}, daemon={daemon})")
    orch = DozzleOrchestrator(config_path=config)

    if daemon:
        asyncio.run(orch.run_monitoring_loop())
    else:
        async def run_both():
            monitor_task = asyncio.create_task(orch.run_monitoring_loop())
            server_config = uvicorn.Config(app, host=host, port=port, log_level="info")
            server = uvicorn.Server(server_config)
            await asyncio.gather(monitor_task, server.serve())

        asyncio.run(run_both())


@cli.command()
@click.argument("query_text")
@click.option("--limit", default=100, help="Max results to return")
@click.option("--output", default="table", type=click.Choice(["table", "json"]))
def query(query_text: str, limit: int, output: str):
    """Query logs with natural language. Example: 'show redis errors last 2 hours'"""
    orch = DozzleOrchestrator()

    async def run():
        results = await orch.handle_nl_query(query_text, limit)
        if output == "json":
            click.echo(json.dumps(results, indent=2))
        else:
            click.echo(f"\nQuery: {query_text}")
            click.echo(f"Found {len(results)} matching log lines:\n")
            for r in results[:limit]:
                ts = r.get("timestamp", "")[:19]
                container = r.get("container", "")[:20]
                msg = r.get("message", "")[:120]
                click.echo(f"  [{ts}] {container:20s} | {msg}")

    asyncio.run(run())


@cli.command()
@click.option("--container", default=None, help="Filter by container name")
@click.option("--hours", default=1, help="Look-back window in hours")
@click.option("--limit", default=20, help="Max anomalies to display")
def anomalies(container: str | None, hours: int, limit: int):
    """Show recent anomaly events detected by the agent."""
    orch = DozzleOrchestrator()

    async def run():
        events = await orch.get_recent_anomalies(container=container, hours=hours, limit=limit)
        click.echo(f"\nAnomalies (last {hours}h, container={container or 'all'}):\n")
        for e in events:
            sev = e.get("severity", "?")
            ts = e.get("timestamp", "")[:19]
            cname = e.get("container", "")[:20]
            msg = e.get("message", "")[:80]
            score = e.get("anomaly_score", 0)
            click.echo(f"  [{sev:8s}] [{ts}] {cname:20s} (score={score:.3f})")
            click.echo(f"           {msg}")
            expl = e.get("explanation")
            if expl:
                click.echo(f"           → {expl[:120]}")
            click.echo()

    asyncio.run(run())


@cli.command()
@click.argument("container_name")
@click.option("--lines", default=20, help="Number of recent log lines to analyze")
def explain(container_name: str, lines: int):
    """Explain recent anomalous logs for a container using Claude API."""
    orch = DozzleOrchestrator()

    async def run():
        log_lines = await orch.get_recent_lines(container_name, lines)
        if not log_lines:
            click.echo(f"No recent logs found for container: {container_name}")
            return
        explanation = await orch.explain_log_batch(container_name, log_lines)
        click.echo(f"\nExplanation for {container_name}:\n")
        if isinstance(explanation, dict):
            click.echo(f"  Type:        {explanation.get('error_type', 'N/A')}")
            click.echo(f"  Severity:    {explanation.get('severity', 'N/A')}")
            click.echo(f"  Description: {explanation.get('description', 'N/A')}")
            click.echo(f"  Root cause:  {explanation.get('root_cause', 'N/A')}")
            steps = explanation.get("fix_steps", [])
            if steps:
                click.echo("  Fix steps:")
                for i, step in enumerate(steps, 1):
                    click.echo(f"    {i}. {step}")
        else:
            click.echo(str(explanation))

    asyncio.run(run())


@cli.command()
@click.option("--container", default=None, help="Filter by container name")
def clusters(container: str | None):
    """Show log cluster summary (deduplication overview)."""
    orch = DozzleOrchestrator()

    async def run():
        summary = await orch.get_cluster_summary(container=container)
        click.echo(f"\nLog clusters (container={container or 'all'}):\n")
        for c in summary:
            cid = c.get("cluster_id", "?")
            label = c.get("label", "unlabeled")
            size = c.get("size", 0)
            is_novel = "NEW" if c.get("is_novel") else ""
            click.echo(f"  Cluster {cid:4d}: {label:40s} | {size:6d} lines {is_novel}")

    asyncio.run(run())


@cli.command()
@click.option("--force", is_flag=True, help="Force crawl even if not scheduled")
def update_knowledge(force: bool):
    """Run the research paper crawler to update SECOND-KNOWLEDGE-BRAIN.md."""
    click.echo("[dozzle-enhanced] Running knowledge update crawl...")
    updater = KnowledgeUpdater()

    async def run():
        result = await updater.run_crawl(force=force)
        click.echo(f"  Added {result['new_entries']} new entries to SECOND-KNOWLEDGE-BRAIN.md")
        click.echo(f"  Sources crawled: {', '.join(result['sources'])}")
        click.echo(f"  Next scheduled: {result['next_run']}")

    asyncio.run(run())


@cli.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8766)
def serve(host: str, port: int):
    """Start REST API server only (without monitoring loop)."""
    click.echo(f"[dozzle-enhanced] Starting REST API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
