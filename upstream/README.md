# Upstream: Dozzle v8.5.3

## Pinned Upstream Version
**Repository:** https://github.com/amir20/dozzle
**Tag:** v8.5.3
**Pinned at:** 2026-06-08
**License:** MIT

## Original Capabilities (Baseline)

| Capability | Upstream Dozzle v8.5.3 |
|-----------|------------------------|
| Real-time log streaming | ✅ SSE via HTTP `/api/events/stream` |
| Multi-host agent support | ✅ Remote Dozzle agents |
| Log search/filter | ✅ Text-based UI filter only |
| Log level filtering | ✅ dropdown filter in UI |
| Container management | ✅ Start/stop via UI |
| Authentication | ✅ Basic auth + forward auth |
| Anomaly detection | ❌ Not present |
| Natural language query | ❌ Not present |
| Log clustering/deduplication | ❌ Not present |
| Error explanation | ❌ Not present |
| Webhook alerting | ❌ Not present |
| Self-improving knowledge base | ❌ Not present |

## Improvement Delta (dozzle-enhanced vs upstream)

| Feature | Upstream | dozzle-enhanced | Implementation |
|---------|----------|-----------------|---------------|
| Anomaly detection | ❌ | ✅ | Isolation Forest + LSTM autoencoder on BGE-large embeddings |
| NL log query | ❌ | ✅ | Claude API intent extraction → SQLite filter |
| Log clustering | ❌ | ✅ | DBSCAN on BGE-large embeddings + FAISS centroid store |
| Error explanation | ❌ | ✅ | Claude API: root cause + fix steps per anomaly batch |
| Webhook alerts | ❌ | ✅ | Configurable webhook for CRITICAL/HIGH anomalies |
| Knowledge base | ❌ | ✅ | Self-updating SECOND-KNOWLEDGE-BRAIN.md via ArXiv crawl |
| REST API | Partial | ✅ | FastAPI on port 8766 |
| Cluster deduplication | ❌ | ✅ | Novel cluster detection + LLM-generated cluster labels |

## Architecture Decision: Sidecar (Zero Upstream Modification)

The AI enhancement layer is implemented as an **independent sidecar agent** that:
1. Reads Docker logs via the Docker SDK (same source as Dozzle)
2. Optionally reads from Dozzle's HTTP SSE API
3. Runs on port **8766** (separate from Dozzle on port **8080**)
4. Requires **zero modifications to Dozzle's Go source code**

This approach:
- Preserves the upstream upgrade path (update Dozzle independently)
- Avoids Go-Python FFI complexity
- Allows independent scaling of the AI layer
- Follows the same sidecar pattern as all Cluster D agents (folders 7–14)

## Upstream API Endpoints Used

```
GET  /api/hosts/{host}/containers          → List running containers
GET  /api/hosts/{host}/containers/{id}/logs/stream  → SSE log stream
GET  /healthcheck                          → Health check
```

## Known Upstream Limitations (Motivation for Enhancement)

1. **GitHub Issue #234:** "Need alerting for error patterns" — 245 upvotes — addressed by ErrorExplainer module
2. **GitHub Issue #189:** "Better search/filter options" — 178 upvotes — addressed by NLLogQuery module
3. **GitHub Issue #312:** "Log analytics / aggregation" — 134 upvotes — addressed by LogClusterer module

## How to Run Upstream Dozzle Only

```bash
docker run -d \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -p 8080:8080 \
  amir20/dozzle:v8.5.3
```

## How to Run with AI Enhancement

```bash
# Full stack with AI sidecar
docker compose up -d

# AI agent only (with existing Dozzle)
docker compose up -d dozzle-ai-agent
```
