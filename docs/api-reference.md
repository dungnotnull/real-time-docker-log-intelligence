# API Reference

## Base URL

```
http://localhost:8766
```

## Authentication

Currently, the API does not require authentication. For production, reverse behind an authenticating proxy like nginx or OAuth2-proxy.

---

## Endpoints

### GET /health

Health check endpoint.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "2026-06-10T14:32:15.123Z"
}
```

---

### POST /query

Execute a natural language query against logs.

**Request Body:**

```json
{
  "query": "show redis errors last 2 hours",
  "limit": 100
}
```

**Response:**

```json
{
  "query": "show redis errors last 2 hours",
  "results": [
    {
      "id": 12345,
      "container": "redis",
      "image": "redis:7-alpine",
      "timestamp": "2026-06-10T14:32:15.123Z",
      "message": "ERROR Connection timeout to server",
      "log_level": "ERROR",
      "stream": "stdout",
      "anomaly_score": -0.75
    }
  ],
  "count": 23
}
```

**Query Examples:**

- "show nginx errors last 2 hours"
- "database connection errors today"
- "all critical logs from the past 15 minutes"
- "timeout errors in redis"

---

### GET /anomalies

Get recent anomaly events.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| container | string | null | Filter by container name (partial match) |
| hours | int | 1 | Look-back window in hours (1-168) |
| limit | int | 50 | Max results (1-500) |

**Request:**

```
GET /anomalies?container=nginx&hours=1&limit=50
```

**Response:**

```json
{
  "anomalies": [
    {
      "id": 789,
      "container": "nginx",
      "timestamp": "2026-06-10T14:32:15.123Z",
      "message": "ERROR Connection timeout to upstream",
      "anomaly_score": -0.85,
      "seq_anomaly_score": 0.0,
      "cluster_id": 42,
      "severity": "HIGH",
      "explanation": {
        "error_type": "ConnectionTimeout",
        "severity": "HIGH",
        "description": "Connection timed out, possibly due to network issues or slow dependency",
        "root_cause": "Downstream service unreachable or responding slowly",
        "fix_steps": [
          "Check health of dependent services",
          "Increase timeout values if load is legitimately high",
          "Add circuit breaker pattern"
        ],
        "docs_links": []
      }
    }
  ],
  "count": 15
}
```

---

### GET /clusters

Get log cluster summary.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| container | string | null | Filter by container name |

**Request:**

```
GET /clusters?container=redis
```

**Response:**

```json
{
  "clusters": [
    {
      "cluster_id": 1,
      "label": "Cache hit messages",
      "size": 15234,
      "containers": ["redis", "api-server"],
      "is_novel": false
    },
    {
      "cluster_id": 42,
      "label": "Connection timeout errors",
      "size": 423,
      "containers": ["redis"],
      "is_novel": true
    }
  ]
}
```

---

### POST /explain

Generate AI explanation for a batch of log lines.

**Request Body:**

```json
{
  "container_name": "postgres",
  "log_lines": [
    "ERROR: out of memory",
    "FATAL: database is shutting down",
    "HINT: Increase max_connections"
  ]
}
```

**Response:**

```json
{
  "container": "postgres",
  "explanation": {
    "error_type": "OutOfMemoryError",
    "severity": "CRITICAL",
    "description": "Database cannot accept new connections due to memory exhaustion",
    "root_cause": "Connection pool exceeded max_connections limit",
    "fix_steps": [
      "Increase max_connections in postgresql.conf",
      "Configure pgbouncer for connection pooling",
      "Review application connection retention logic"
    ],
    "docs_links": [
      "https://www.postgresql.org/docs/current/runtime-config-connection.html"
    ]
  }
}
```

---

### GET /stats

Get agent statistics.

**Response:**

```json
{
  "lines_processed": 152341,
  "anomalies_detected": 423,
  "clusters_created": 87,
  "llm_calls": 152,
  "start_time": "2026-06-10T08:00:00.000Z",
  "memory_stats": {
    "total_log_lines": 152341,
    "total_anomaly_events": 423,
    "total_llm_cost_usd": 0.0765,
    "total_tokens_used": 450230
  },
  "uptime_seconds": 22415
}
```

---

### GET /anomalies/stream

Server-Sent Events (SSE) stream of real-time anomaly events.

**Request:**

```
GET /anomalies/stream
```

**Response:**

```
Content-Type: text/event-stream

data: {"container":"nginx","timestamp":"2026-06-10T14:32:15.123Z","anomaly_count":5,"explanation":{...}}

data: {"heartbeat":true,"timestamp":"2026-06-10T14:32:45.123Z"}

data: {"container":"postgres","timestamp":"2026-06-10T14:33:15.123Z","anomaly_count":3,"explanation":{...}}
```

---

### GET /metrics

Prometheus metrics endpoint for monitoring.

**Response:**

```
# HELP dozzle_agent_log_lines_processed_total Total number of log lines processed
# TYPE dozzle_agent_log_lines_processed_total counter
dozzle_agent_log_lines_processed_total{container="nginx"} 15234
dozzle_agent_log_lines_processed_total{container="redis"} 45231

# HELP dozzle_agent_anomalies_detected_total Total number of anomalies detected
# TYPE dozzle_agent_anomalies_detected_total counter
dozzle_agent_anomalies_detected_total{container="nginx",severity="HIGH"} 45
dozzle_agent_anomalies_detected_total{container="redis",severity="MEDIUM"} 23

# HELP dozzle_agent_anomaly_score Current anomaly scores by container
# TYPE dozzle_agent_anomaly_score gauge
dozzle_agent_anomaly_score{container="nginx"} -0.65
dozzle_agent_anomaly_score{container="redis"} -0.23
```

---

## Error Responses

All endpoints may return error responses:

**400 Bad Request**

```json
{
  "detail": "Invalid query parameter: limit must be between 1 and 500"
}
```

**500 Internal Server Error**

```json
{
  "detail": "LLM provider unavailable: all providers failed"
}
```

---

## Rate Limiting

Currently, there are no rate limits. For production, implement rate limiting via reverse proxy.

---

## CORS

CORS is enabled by default. To restrict origins, modify `agent/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```
