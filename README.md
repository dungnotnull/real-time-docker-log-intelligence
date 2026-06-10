# Dozzle-Enhanced AI Log Analysis Agent

> Real-time Docker log intelligence — anomaly detection, natural language queries, and AI-powered error explanations

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Phase](https://img.shields.io/badge/Phase-Production-green.svg)]()

**Upstream:** [Dozzle v8.5.3](https://github.com/amir20/dozzle) | **Cluster:** D — DevOps, Infrastructure & Security Intelligence

---

## Overview

Docker containers generate thousands of log lines per minute. Dozzle provides beautiful real-time log viewing but offers no intelligence layer. **dozzle-enhanced** adds a production-grade AI sidecar that:

- **Continuously monitors** all container logs via Docker Engine API or Dozzle HTTP API
- **Detects anomalies** using Isolation Forest + BGE-large embeddings + LSTM autoencoder
- **Answers natural language queries** like "show Redis errors from the last 2 hours"
- **Clusters similar logs** using DBSCAN to eliminate noise and surface unique event types
- **Explains errors** in plain language using Claude API with cost tracking
- **Self-improves** via weekly research paper crawls from ArXiv, Semantic Scholar, and GitHub

The longer the agent runs, the more accurate its anomaly baselines become.

---

## Architecture

```
Docker Engine / Dozzle REST API
         ↓
┌─────────────────────────────────────────────────────────────┐
│  DozzleOrchestrator (agent/orchestrator.py)                 │
│  ┌───────────────┐  ┌─────────────────┐  ┌───────────────┐ │
│  │  Log Ingestion│→ │ Module Pipeline │→ │ Memory/Context│ │
│  │  (stream loop)│  │  (async gather) │  │  (SQLite+FAISS│ │
│  └───────────────┘  └─────────────────┘  └───────────────┘ │
│       ↕                    ↕                                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Domain Modules                                       │   │
│  │  log_anomaly_detector.py  → Isolation Forest + LSTM  │   │
│  │  nl_log_query.py          → NL → structured filter   │   │
│  │  log_clusterer.py         → BGE embeddings + DBSCAN  │   │
│  │  error_explainer.py       → LLM error explanation    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         ↓              ↓              ↓
    LLM API        HuggingFace    Dozzle/Docker API
  (llm_client)   (hf_model_mgr)   (tools/dozzle_client)
         ↓
   REST API + CLI + Real-time Alerts
```

---

## Features

### Anomaly Detection

- **Stage 1: Isolation Forest** on BGE-large embeddings (per-container model)
- **Stage 2: LSTM Autoencoder** on 20-line sequences for temporal anomalies
- **Keyword fallback** when ML models unavailable
- **Adaptive thresholds** using rolling mean + 2σ (7-day window per container)

### Natural Language Queries

```
$ dozzle-enhanced query "show nginx errors last 2 hours"

Query: show nginx errors last 2 hours
Found 23 matching log lines:

  [2026-06-10 14:32:15] nginx              | [ERROR] Connection timeout to upstream
  [2026-06-10 14:31:22] nginx              | [ERROR] SSL handshake failed for client 10.0.0.42
  ...
```

### Error Explanation

```
$ dozzle-enhanced explain postgres --lines 10

Explanation for postgres:
  Type:        ConnectionError
  Severity:    CRITICAL
  Description: Database unable to accept new connections due to max_connections limit
  Root cause:  Connection pool exhaustion (confidence: HIGH)
  Fix steps:
    1. Increase max_connections in postgresql.conf
    2. Configure pgbouncer for connection pooling
    3. Review application connection retention logic
```

### Real-time Alerts

- Webhook notifications for HIGH/CRITICAL anomalies
- SSE stream endpoint: `/anomalies/stream`
- Prometheus metrics endpoint: `/metrics`
- Integrated with Grafana dashboards

---

## Quick Start

### Docker Compose (Recommended)

```bash
# Clone repository
git clone https://github.com/your-org/dozzle-enhanced.git
cd dozzle-enhanced

# Copy environment template
cp .env.example .env

# Edit .env and add your API keys
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# Start full stack
docker-compose -f docker/docker-compose.yml up -d

# Access services
# - Dozzle UI: http://localhost:8080
# - Agent API: http://localhost:8766
# - Grafana: http://localhost:3000
```

### Python Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ANTHROPIC_API_KEY=sk-ant-...
export DOZZLE_BASE_URL=http://localhost:8080

# Start monitoring
python -m agent.main monitor --host 0.0.0.0 --port 8766
```

---

## CLI Usage

### Monitor Mode

```bash
# Start continuous log monitoring with REST API
python -m agent.main monitor --host 0.0.0.0 --port 8766

# Daemon mode (monitoring only, no API)
python -m agent.main monitor --daemon
```

### Query Logs

```bash
# Natural language query
python -m agent.main query "show redis errors last 2 hours" --limit 50

# JSON output
python -m agent.main query "all critical logs" --output json
```

### View Anomalies

```bash
# Recent anomalies (all containers)
python -m agent.main anomalies --hours 1 --limit 20

# Specific container
python -m agent.main anomalies --container postgres --hours 24
```

### Explain Errors

```bash
# Explain recent anomalous logs
python -m agent.main explain nginx --lines 20
```

### View Clusters

```bash
# Log cluster summary (deduplication overview)
python -m agent.main clusters --container nginx
```

### Update Knowledge Base

```bash
# Run research paper crawler manually
python -m agent.main update-knowledge --force
```

---

## REST API

### Query Logs

```bash
curl -X POST http://localhost:8766/query \
  -H "Content-Type: application/json" \
  -d '{"query": "show redis errors last 2 hours", "limit": 100}'
```

### Get Anomalies

```bash
curl "http://localhost:8766/anomalies?container=nginx&hours=1&limit=50"
```

### Get Clusters

```bash
curl "http://localhost:8766/clusters?container=redis"
```

### Explain Logs

```bash
curl -X POST http://localhost:8766/explain \
  -H "Content-Type: application/json" \
  -d '{"container_name": "postgres", "log_lines": ["ERROR: out of memory", "FATAL: database is shutting down"]}'
```

### Stream Anomalies

```bash
curl -N http://localhost:8766/anomalies/stream
```

### Prometheus Metrics

```bash
curl http://localhost:8766/metrics
```

---

## Configuration

Edit `config/agent_config.yaml`:

```yaml
# Dozzle / Docker connection
dozzle:
  base_url: http://localhost:8080
  docker_socket: /var/run/docker.sock
  mock_mode: false

# Anomaly detection
anomaly_detection:
  contamination: 0.05
  bootstrap_size: 500
  retrain_interval: 10000
  if_score_threshold: -0.3

# LLM API
llm:
  provider_priority: [claude, openai, ollama]
  claude_model: claude-opus-4-8
  openai_model: gpt-4o
  ollama_model: llama3

# Alerting
alerts:
  webhook_url: null  # Set to Slack/Teams webhook URL
  min_severity: HIGH
```

---

## HuggingFace Models

| Model | Task | Size | Why Chosen |
|-------|------|------|------------|
| `BAAI/bge-large-en-v1.5` | Log embedding | 1.3GB | SOTA on MTEB retrieval |
| `facebook/bart-large-cnn` | Summarization | 1.6GB | Strong abstractive summarizer |
| `sentence-transformers/all-MiniLM-L6-v2` | Template retrieval | 90MB | 6× faster than BGE-large |
| `huggingface/CodeBERTa-small-v1` | Code detection | 120MB | Identifies code in logs |

Models are lazy-loaded and cached in `./models/`. Idle models unload after 600 seconds.

---

## Performance

- **Throughput:** 10,000 log lines/second on CPU (anomaly detection + clustering)
- **Memory:** 4GB total (1.3GB BGE-large + 1.6GB BART-CNN + SQLite + FAISS)
- **Latency:** NL query < 3 seconds (including LLM call)
- **Cost:** ~$0.50 per 1000 anomalies explained (Claude Opus)

---

## Cross-Agent Integration

### Prometheus Metrics

Exports metrics for `dockprom-enhanced` integration:

```
dozzle_agent_log_lines_processed_total
dozzle_agent_anomalies_detected_total
dozzle_agent_clusters_total
dozzle_agent_llm_cost_usd_total
```

### Grafana Dashboard

Import `docker/grafana-dozzle-enhanced-dashboard.json` for anomaly visualization.

### Coroot Integration

Share anomaly events via shared SQLite or REST API. See `docs/coroot-integration.md`.

---

## Documentation

- [Architecture](docs/architecture.md)
- [API Reference](docs/api-reference.md)
- [Configuration Guide](docs/configuration.md)
- [Deployment](docs/deployment.md)
- [Development](docs/development.md)
- [Upstream Integration](ai_layer/patches/dozzle_ai_integration.md)

---

## Contributing

This project follows the [Cluster D Development Guidelines](https://github.com/cluster-d/.github).

---

## License

MIT License — See [LICENSE](LICENSE) file.

---

**Upstream:** [Dozzle v8.5.3](https://github.com/amir20/dozzle) (MIT License)
