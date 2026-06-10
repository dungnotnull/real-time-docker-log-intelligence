# Dozzle AI Integration Guide

## Overview

This document describes how to integrate Dozzle-Enhanced AI capabilities with upstream Dozzle (v8.5.3).

## Integration Methods

### Method 1: Sidecar Deployment (Recommended)

Run Dozzle-Enhanced as a sidecar container alongside Dozzle.

```yaml
version: "3.9"

services:
  dozzle:
    image: amir20/dozzle:v8.5.3
    container_name: dozzle
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - DOZZLE_LEVEL=info

  dozzle-ai-agent:
    image: dozzle-enhanced:latest
    container_name: dozzle-ai-agent
    ports:
      - "8766:8766"
    environment:
      - DOZZLE_BASE_URL=http://dozzle:8080
    depends_on:
      - dozzle
```

### Method 2: Embedded Mode

For future upstream integration, add AI endpoints directly to Dozzle Go codebase.

## API Endpoints

### Dozzle Endpoints (v8.5.3)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/events/stream` | GET | SSE log stream |
| `/api/hosts/{host}/containers` | GET | List containers |
| `/api/hosts/{host}/containers/{id}/logs` | GET | Container logs |

### Dozzle-Enhanced Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/query` | POST | Natural language log query |
| `/anomalies` | GET | Recent anomaly events |
| `/anomalies/stream` | GET | SSE anomaly stream |
| `/explain` | POST | AI error explanation |
| `/clusters` | GET | Log cluster summary |
| `/metrics` | GET | Prometheus metrics |

## Log Stream Format

### Dozzle SSE Format

```
data: {"name":"nginx","image":"nginx:latest","time":"2026-06-10T14:32:15.123Z","message":"INFO Request processed","stream":"stdout"}

data: {"name":"redis","image":"redis:7-alpine","time":"2026-06-10T14:32:16.456Z","message":"ERROR Connection timeout","stream":"stderr"}
```

### Dozzle-Enhanced Processing

1. Ingests Dozzle SSE stream
2. Adds metadata: `log_level`, `anomaly_score`, `cluster_id`
3. Stores in SQLite with 7-day retention
4. Returns enriched log lines via `/query`

## Configuration

### Dozzle Configuration

```yaml
# dozzle.yml
level: info
hostname: myserver
filter:
  - container: nginx
    files:
      - /var/log/nginx/*.log
```

### Dozzle-Enhanced Configuration

```yaml
# config/agent_config.yaml
dozzle:
  base_url: http://localhost:8080
  docker_socket: /var/run/docker.sock

anomaly_detection:
  contamination: 0.05
  bootstrap_size: 500

llm:
  provider_priority: [claude, openai, ollama]
```

## Pull Request Recommendations

For upstream integration, consider:

1. Add optional AI module to Dozzle
2. Include feature flag: `DOZZLE_AI_ENABLED`
3. Expose AI endpoints under `/api/ai/` prefix
4. Make AI dependencies optional (python:slim variant)

## Future Enhancements

- Embedded Python runtime in Dozzle binary
- Shared WebSocket connection
- Unified configuration file
- Combined UI with AI insights panel

## Compatibility Matrix

| Dozzle Version | Dozzle-Enhanced Version | Compatible |
|----------------|-------------------------|------------|
| v8.5.0         | 1.0.0                   | ✅         |
| v8.5.1         | 1.0.0                   | ✅         |
| v8.5.2         | 1.0.0                   | ✅         |
| v8.5.3         | 1.0.0                   | ✅         |

## Migration Guide

From standalone Dozzle to Dozzle-Enhanced:

1. Deploy Dozzle-Enhanced sidecar
2. Verify log ingestion via `/stats`
3. Test NL query: `POST /query`
4. Enable Prometheus metrics scraping
5. Import Grafana dashboard
6. Configure alert webhooks

## Rollback Plan

If issues occur:

1. Stop Dozzle-Enhanced container
2. Dozzle continues operating independently
3. No data loss (SQLite persists)
4. Restart Dozzle-Enhanced when ready

## Performance Impact

Dozzle-Enhanced runs independently with minimal impact on Dozzle:

- Zero network overhead (reads from same Docker socket)
- Separate resource limits
- No Dozzle configuration changes required
- Can be scaled independently

## Support

- Dozzle issues: https://github.com/amir20/dozzle/issues
- Dozzle-Enhanced issues: https://github.com/your-org/dozzle-enhanced/issues
