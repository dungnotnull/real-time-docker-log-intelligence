# Cross-Agent Integration

## Dockprom-Enhanced Integration

Dozzle-Enhanced exports Prometheus metrics for seamless integration with `dockprom-enhanced`.

### Metrics Exported

```
dozzle_agent_log_lines_processed_total{container}
dozzle_agent_anomalies_detected_total{container, severity}
dozzle_agent_anomaly_score{container}
dozzle_agent_clusters_total{container}
dozzle_agent_llm_cost_usd_total{provider, model}
dozzle_agent_nl_query_total{cache_hit}
```

### Prometheus Configuration

Add to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'dozzle-enhanced'
    static_configs:
      - targets: ['dozzle-ai-agent:8766']
    metrics_path: '/metrics'
    scrape_interval: 30s
```

### Grafana Dashboard

Import `docker/grafana-dozzle-enhanced-dashboard.json` into Grafana.

### Alert Rules

```yaml
groups:
  - name: dozzle_anomalies
    interval: 30s
    rules:
      - alert: HighAnomalyRate
        expr: rate(dozzle_agent_anomalies_detected_total[5m]) > 10
        for: 5m
        labels:
          severity: warning
          service: dozzle-enhanced
        annotations:
          summary: "High anomaly rate in {{ $labels.container }}"
          description: "{{ $value }} anomalies/sec detected"

      - alert: CriticalLogAnomaly
        expr: dozzle_agent_anomalies_detected_total{severity="CRITICAL"} > 0
        for: 1m
        labels:
          severity: critical
          service: dozzle-enhanced
        annotations:
          summary: "Critical anomaly detected in {{ $labels.container }}"
```

---

## Coroot-Enhanced Integration

Share anomaly events with Coroot for application-level correlation.

### REST API Integration

Coroot can poll Dozzle-Enhanced for recent anomalies:

```bash
curl "http://dozzle-ai-agent:8766/anomalies?hours=1&limit=100"
```

### Shared SQLite Integration

For shared storage, mount the same SQLite volume:

```yaml
volumes:
  - shared-data:/app/shared

# In both services
coroot:
  volumes:
    - shared-data:/coroot/data

dozzle-ai-agent:
  environment:
    - SHARED_DB_PATH=/app/shared/anomalies.db
```

### Event Forwarding

Configure Dozzle-Enhanced to forward events to Coroot webhook:

```yaml
# config/agent_config.yaml
alerts:
  webhook_url: http://coroot:8080/api/events/dozzle-anomalies
  min_severity: HIGH
```

---

## Upstream Dozzle Integration

### Installation

Dozzle-Enhanced runs as a sidecar to upstream Dozzle:

```yaml
dozzle:
  image: amir20/dozzle:v8.5.3
  ports:
    - "8080:8080"

dozzle-ai-agent:
  image: dozzle-enhanced:latest
  ports:
    - "8766:8766"
  environment:
    - DOZZLE_BASE_URL=http://dozzle:8080
```

### Capability Comparison

| Feature | Dozzle v8.5.3 | Dozzle-Enhanced |
|---------|---------------|-----------------|
| Real-time log streaming | ✅ | ✅ (via Dozzle API) |
| Multi-container view | ✅ | ✅ |
| Log filtering (basic) | ✅ | ✅ |
| Search (full-text) | ✅ | ✅ |
| **Anomaly detection** | ❌ | ✅ |
| **Natural language queries** | ❌ | ✅ |
| **AI error explanation** | ❌ | ✅ |
| **Log clustering** | ❌ | ✅ |
| **Prometheus metrics** | ❌ | ✅ |
| Alerting | ✅ (webhook) | ✅ (enhanced) |
| | | |

### Configuration

Dozzle-Enhanced reads from Dozzle's HTTP SSE endpoint:

```yaml
dozzle:
  base_url: http://localhost:8080
  docker_socket: /var/run/docker.sock
```

For direct Docker access (bypassing Dozzle), set `docker_socket` path.

---

## Loki Integration

Forward anomalies to Grafana Loki for log aggregation.

### Webhook Configuration

```yaml
alerts:
  webhook_url: http://loki:3100/loki/api/v1/push
```

### Log Format

Anomalies are forwarded as structured JSON logs:

```json
{
  "streams": [
    {
      "stream": {
        "container": "nginx",
        "severity": "HIGH"
      },
      "values": [
        ["1623344567000000000", "{\"message\": \"Anomaly detected\", \"score\": -0.85}"]
      ]
    }
  ]
}
```

---

## VictoriaLogs Integration

Similar to Loki, use webhook forwarding:

```yaml
alerts:
  webhook_url: http://victoria-logs:9428/loki/api/v1/push
```

---

## Vector Integration

Stream anomalies via Vector:

```yaml
sinks:
  dozzle_anomalies:
    type: http
    inputs:
      - anomaly_stream
    uri: http://vector:9000/anomalies
    encoding: json
```

---

## Test Integrations

### Mock Mode

For testing without Docker:

```bash
export DOZZLE_MOCK=true
python -m agent.main monitor --daemon
```

This generates synthetic log lines for testing.

### Test Containers

Include test log generator in docker-compose:

```bash
docker-compose --profile test up
```

---

## Security Considerations

### API Key Protection

When sharing data with external systems:

1. Never include API keys in forwarded events
2. Use authenticated webhooks (bearer tokens)
3. Validate webhook signatures

### Data Privacy

For sensitive logs:

1. Use Ollama (offline mode) for LLM calls
2. Disable LLM features entirely in config
3. Configure data retention policies

### Network Security

1. Restrict cross-agent communication via network policies
2. Use TLS for all webhook communications
3. Implement service mesh (Istio/Linkerd) for mTLS
