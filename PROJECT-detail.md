# PROJECT-detail.md — Dozzle-Enhanced AI Log Analysis Agent

## Executive Summary

Dozzle-Enhanced is a production-grade AI sidecar agent that runs alongside the open-source Dozzle Docker log viewer. While Dozzle provides beautiful real-time log streaming, engineers face three critical gaps: (1) no anomaly detection — critical errors are buried in thousands of lines, (2) no natural language query interface — users must write complex grep filters, and (3) no explanation layer — cryptic stack traces require manual investigation. This agent closes all three gaps using ML-based anomaly detection (Isolation Forest + LSTM autoencoder), NL query parsing via LLM API, BGE-large log clustering for noise reduction, and Claude-powered error explanations.

**Improvement delta over upstream Dozzle v8.5.3:**
| Capability | Upstream Dozzle | Dozzle-Enhanced |
|-----------|----------------|-----------------|
| Anomaly detection | None | Isolation Forest (IF) + LSTM autoencoder |
| Log querying | Manual text filter UI | Natural language ("show auth errors last 2h") |
| Log deduplication | None | DBSCAN clustering on BGE-large embeddings |
| Error explanation | None | Claude API: root cause + fix steps |
| Knowledge base | None | SECOND-KNOWLEDGE-BRAIN.md (self-updating weekly) |

---

## Target Users & Use Cases

**User A — DevOps Engineer:**
> "There's a memory leak somewhere in our 15 microservices. I need to find which container's logs show the pattern."
> → Agent detects anomalous memory-related log sequences across containers, clusters them by service, and explains the likely root cause.

**User B — Developer on Call:**
> "Show me all database connection errors from the payment service in the last 30 minutes."
> → User types natural language; agent parses intent, filters logs, returns structured results with context.

**User C — SRE during Incident:**
> "We have an active incident. Summarize what's happening across all containers."
> → Agent generates cross-container anomaly summary with LLM-synthesized explanation and remediation suggestions.

**User D — Platform Team:**
> "Set up continuous monitoring and alert me on Slack when anomaly rate exceeds threshold."
> → Agent runs as daemon, streams anomalies to configured webhook endpoints.

---

## Agent Architecture (Detailed)

```
[Docker Engine]
     |
     | (log stream via Docker SDK / Dozzle HTTP API)
     ↓
[DozzleClient — agent/tools/dozzle_client.py]
     |
     | LogLine(container, timestamp, message, level, stream)
     ↓
[DozzleOrchestrator — agent/orchestrator.py]
     |
     ├──► [LogAnomalyDetector]
     │         ├── BGE-large: embed log line (1024-dim)
     │         ├── IsolationForest: contamination=0.05
     │         │   → anomaly_score ∈ [-1, 0]  (lower = more anomalous)
     │         ├── LSTMAutoencoder: reconstruction error for sequence-level
     │         └── adaptive_threshold: mean + 2σ per container (7-day window)
     │
     ├──► [LogClusterer]
     │         ├── BGE-large: embed log line
     │         ├── DBSCAN: eps=0.3, min_samples=3
     │         ├── Cluster centroid tracking + drift detection
     │         └── Novel cluster → trigger anomaly flag
     │
     ├──► [ErrorExplainer]  ← triggered when anomaly_score < threshold OR novel cluster
     │         ├── BART-CNN: summarize long error batch (>2000 chars)
     │         ├── LLM (Claude): generate error_description, root_cause, fix_steps, docs_links
     │         └── Batch up to 10 anomalies per LLM call for cost efficiency
     │
     └──► [NLLogQuery]  ← triggered by user query
               ├── MiniLM: retrieve closest query template
               ├── LLM (Claude): extract {container, time_range, level, keywords, limit}
               ├── Apply structured filter to log store
               └── Return paginated results with matching lines
         ↓
[MemoryManager — SQLite + FAISS]
     ├── log_lines: circular buffer (last 7 days, max 10M rows)
     ├── anomaly_events: detected anomalies with scores and explanations
     ├── cluster_centroids: FAISS index of known cluster centroid embeddings
     ├── nl_query_cache: recent NL queries + parsed filters
     ├── llm_cost_log: per-request token usage
     └── knowledge_hashes: dedup for SECOND-KNOWLEDGE-BRAIN.md entries
         ↓
[REST API + CLI + Webhook Alerts]
```

---

## Full Module Catalog

### 1. `agent/modules/log_anomaly_detector.py`
**Responsibility:** Score each incoming log line for anomalousness using a two-stage detector.

**Stage 1 — Isolation Forest (per-container):**
- Input: log line text (string)
- Embed via `BAAI/bge-large-en-v1.5` → 1024-dim vector
- IsolationForest with `contamination=0.05`, `n_estimators=100`
- Bootstrap: train on first 500 log lines per container; retrain every 10,000 new lines
- Output: `anomaly_score` (float, lower = more anomalous); `is_anomaly` (bool)

**Stage 2 — LSTM Autoencoder (sequence-level):**
- Window: 20 consecutive log lines per container → sequence of 20 × 1024-dim vectors
- Autoencoder: encoder (256→64 LSTM), decoder (64→256 LSTM) → reconstructed sequence
- Reconstruction error: mean squared error across sequence
- Threshold: rolling mean + 2σ over last 1000 windows
- Output: `seq_anomaly_score`, `is_seq_anomaly`

**Fallback:** if HuggingFace BGE unavailable → keyword-based scorer (ERROR/CRITICAL/FATAL = 0.9, WARNING = 0.5, INFO = 0.1)

**Quality gate:** precision ≥ 0.7 on synthetic test set (500 injected anomalies)

---

### 2. `agent/modules/nl_log_query.py`
**Responsibility:** Parse natural language log queries into structured filters, execute against log store.

**Flow:**
1. Receive user query string ("show redis errors last 2 hours")
2. MiniLM retrieves closest pre-defined query template (exact match optimization)
3. LLM (Claude) extracts JSON: `{container_name, time_range_minutes, log_level, keywords: [], limit, sort}`
4. Apply structured filter to SQLite `log_lines` table with indexed columns
5. Return: list of matching `LogLine` objects with highlight metadata

**Query schema (LLM output):**
```json
{
  "container_name": "redis|null",
  "time_range_minutes": 120,
  "log_level": "ERROR|WARNING|INFO|null",
  "keywords": ["connection", "timeout"],
  "limit": 100,
  "sort": "desc"
}
```

**LLM prompt template:** 3-shot with diverse query examples; temperature=0; max_tokens=256
**Fallback:** regex-based keyword extractor for offline mode

---

### 3. `agent/modules/log_clusterer.py`
**Responsibility:** Group similar log lines into clusters to deduplicate noise and identify recurring patterns.

**Algorithm:**
1. Embed incoming log line via `BAAI/bge-large-en-v1.5`
2. Compare to existing cluster centroids via cosine similarity (FAISS IndexFlatIP)
3. If max similarity < 0.75 (new cluster threshold): create new cluster; flag as potentially novel
4. Else: assign to nearest cluster; update centroid (exponential moving average α=0.1)
5. DBSCAN full re-cluster every 10,000 new log lines (background thread)

**Output per log line:**
- `cluster_id`: integer cluster identifier
- `cluster_size`: count of lines in cluster
- `is_novel_cluster`: True if first occurrence
- `cluster_label`: LLM-generated label for top-N clusters (run on first occurrence + 10× growth)

**Cluster drift detection:** if cluster centroid moves > 0.3 cosine distance over 1-hour window → alert

---

### 4. `agent/modules/error_explainer.py`
**Responsibility:** Generate plain-language explanations for anomalous log events using Claude API.

**Trigger conditions:**
- `anomaly_score < -0.3` (Isolation Forest)
- `is_seq_anomaly = True` (LSTM)
- `is_novel_cluster = True` (new cluster seen)

**Batching strategy:**
- Collect up to 10 anomalous lines per 60-second window per container
- If log line > 2000 chars: BART-CNN summarize first
- LLM (Claude) prompt: container context + log lines + recent history → structured explanation

**LLM output schema:**
```json
{
  "error_type": "ConnectionTimeout|OOMKill|SegFault|AuthFailure|...",
  "severity": "CRITICAL|HIGH|MEDIUM|LOW",
  "description": "Plain English description of what happened",
  "root_cause": "Likely underlying cause with confidence level",
  "fix_steps": ["Step 1", "Step 2", "Step 3"],
  "docs_links": ["relevant documentation URLs"],
  "related_errors": ["similar_error_1", "similar_error_2"]
}
```

**Cost optimization:** deduplicate explanations for the same cluster within 1-hour window (cache hit rate ~70%)

---

## HuggingFace Model Selection

| Model | Task | Benchmark | Chosen Over |
|-------|------|-----------|-------------|
| `BAAI/bge-large-en-v1.5` | Log line embedding | MTEB: 64.23 (top-1 dense retrieval) | OpenAI ada-002 (cost), MiniLM (accuracy) |
| `facebook/bart-large-cnn` | Long log batch summarization | CNN/DM ROUGE-2: 21.28 | T5-base (quality), PEGASUS (speed) |
| `sentence-transformers/all-MiniLM-L6-v2` | Query template retrieval | MTEB: 56.26 (6× faster than BGE) | BGE-large (overkill for template lookup) |
| `huggingface/CodeBERTa-small-v1` | Code/SQL detection in logs | F1: 0.92 on code classification | Full CodeBERT (4× smaller, sufficient accuracy) |

---

## LLM API Integration Spec

### Provider Chain
```
ANTHROPIC_API_KEY present → Claude claude-opus-4-8
OPENAI_API_KEY present → gpt-4o
OLLAMA_BASE_URL present → llama3
Else → raise ConfigurationError
```

### Prompt Templates

**NL Query Parsing (Claude):**
```
System: You are a log query parser. Extract a structured filter from user queries about Docker container logs.
User: {user_query}
Few-shot examples: [3 diverse examples]
Output: JSON with container_name, time_range_minutes, log_level, keywords, limit
```

**Error Explanation (Claude):**
```
System: You are a DevOps expert. Explain Docker container log errors clearly for engineers.
Container: {container_name} | Image: {image_name} | Recent context: {last_10_normal_lines}
Anomalous log lines:
{anomalous_lines}
Output: JSON with error_type, severity, description, root_cause, fix_steps
```

**Token budgets:** NL query: ~400 tokens; Error explanation: ~1500 tokens; Incident summary: ~3000 tokens

---

## E2E Execution Flow

```
1. START — agent/main.py invoked (CLI or daemon)
2. CONFIG — load agent_config.yaml, validate env vars (API keys, Docker socket)
3. CONNECT — DozzleClient establishes log stream (Dozzle HTTP SSE or Docker SDK)
4. STREAM — for each LogLine received:
   4a. Store in SQLite log_lines (circular 7-day buffer, max 10M rows)
   4b. Embed with BGE-large (async, batched every 100ms or 50 lines)
   4c. Run LogAnomalyDetector → score
   4d. Run LogClusterer → cluster assignment
   4e. IF anomaly OR novel cluster → queue for ErrorExplainer
5. EXPLAIN — ErrorExplainer batch (60s window, max 10 per container):
   5a. Optionally: BART-CNN summarize long logs
   5b. LLM (Claude) → structured explanation JSON
   5c. Store in anomaly_events table
   5d. Send webhook alert if webhook_url configured
6. QUERY — user issues NL query via CLI or REST API:
   6a. MiniLM template retrieval → LLM parses intent
   6b. Structured filter → SQLite query
   6c. Return matching log lines + cluster metadata
7. KNOWLEDGE UPDATE — weekly cron (Sunday 02:00):
   7a. knowledge_updater.py crawls ArXiv cs.OS+cs.DC + USENIX + Dozzle releases
   7b. Appends top-10 scored papers to SECOND-KNOWLEDGE-BRAIN.md
8. LOOP — return to step 4
```

**Error handling:**
- Docker socket unreachable → retry with exponential backoff (max 5 attempts), then graceful shutdown
- LLM API error → log to llm_cost_log, use cached/fallback explanation, continue stream
- HuggingFace model load failure → fallback to keyword-based scorer

---

## SECOND-KNOWLEDGE-BRAIN.md Integration

**Sources:** ArXiv cs.OS+cs.DC, USENIX ATC proceedings, Papers with Code (log analysis leaderboard), Dozzle GitHub releases, Grafana Loki blog, Vector.dev blog

**Crawl config:**
```yaml
sources:
  arxiv:
    categories: [cs.OS, cs.DC]
    keywords: [log analysis, anomaly detection, log clustering, DRAIN, LogBERT, DeepLog]
    max_results: 20
  semantic_scholar:
    query: "docker log anomaly detection"
    fields: [title, abstract, year, citationCount, externalIds]
    max_results: 10
  github_releases:
    repos: [amir20/dozzle, grafana/loki, vectordotdev/vector]
  rss:
    - https://grafana.com/blog/tag/loki/feed.xml
    - https://vector.dev/blog/feed.xml
schedule: weekly_sunday_0200
dedup: url_hash
top_n: 10
```

---

## Quality Gates

1. **Anomaly detector precision ≥ 0.70** on 500-sample synthetic test set (injected ERROR/CRITICAL patterns)
2. **NL query parse accuracy ≥ 0.85** on 20-query eval set (measured: parsed filter matches ground truth)
3. **Cluster deduplication rate ≥ 60%** — at least 60% of repetitive INFO logs grouped in same cluster
4. **LLM explanation latency < 5 seconds** P95 for a batch of 10 log lines
5. **Log ingestion throughput ≥ 10,000 lines/second** without dropped events (queue backpressure)

---

## Test Scenarios

See `tests/test-scenarios.md` for 7 detailed E2E scenarios.

---

## Key Design Decisions

1. **Sidecar architecture:** Agent runs as independent container alongside Dozzle — zero changes to upstream Dozzle Go code. Connects via Dozzle HTTP API or Docker socket directly. Preserves upstream upgrade path.
2. **Isolation Forest chosen over pure LSTM:** IF requires no labeled data, trains on first 500 lines, and provides per-line scores. LSTM autoencoder complements with sequence-level detection. Combined > either alone.
3. **BGE-large over OpenAI embeddings:** BGE-large-en-v1.5 is free, runs locally, no data leaves the system — critical for log data which often contains sensitive information (tokens, IPs, stack traces with internal paths).
4. **Circular buffer with 7-day retention:** Docker logs are high-volume. SQLite circular buffer capped at 10M rows (~2GB) balances history depth vs storage cost.
5. **Batched LLM calls:** Anomaly explanations are batched (up to 10 per 60s window) to minimize LLM API cost. Caching at cluster level cuts cost by ~70% for repetitive errors.
