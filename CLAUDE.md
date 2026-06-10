# CLAUDE.md — Dozzle-Enhanced AI Log Analysis Agent

## Agent Identity
**Name:** dozzle-enhanced
**Tagline:** Real-time Docker log intelligence — anomaly detection, natural language queries, and AI-powered error explanations
**Build Phase:** Phase 0 (Architecture & Research)
**Upstream:** Dozzle v8.5.3 (MIT License — https://github.com/amir20/dozzle)
**Cluster:** D — DevOps, Infrastructure & Security Intelligence

---

## Problem Statement
Docker containers generate thousands of log lines per minute. Dozzle provides beautiful real-time log viewing but offers no intelligence layer: engineers must manually scan logs for anomalies, write complex filters, and interpret cryptic error messages. The **dozzle-enhanced** agent adds a production-grade AI sidecar that continuously monitors all container logs, detects anomalies using Isolation Forest and LSTM autoencoder, answers natural language log queries ("show me auth errors from the last hour"), clusters similar log events to eliminate noise, and explains recurring error patterns in plain language. The longer the agent runs, the more accurate its anomaly baselines become.

---

## Agent Architecture

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

**E2E flow (5 steps):**
1. `DozzleClient` streams log lines from all containers via Dozzle HTTP API or Docker SDK
2. `LogAnomalyDetector` scores each log line: BGE embedding → Isolation Forest contamination score; LSTM autoencoder reconstruction error for sequence anomalies
3. `LogClusterer` groups similar log lines using DBSCAN on BGE embeddings — deduplicates noise, surfaces unique event types
4. `ErrorExplainer` batches high-anomaly + high-frequency cluster centroids → LLM (Claude) generates plain-language root cause and fix suggestions
5. `NLLogQuery` accepts user natural language queries → LLM parses intent → structured filter applied to log stream → paginated results returned

---

## Module List (`agent/modules/`)

| File | Description |
|------|-------------|
| `log_anomaly_detector.py` | Isolation Forest on BGE-large embeddings; LSTM autoencoder fallback for temporal sequence anomalies; per-container adaptive thresholds; flags top-N anomalous lines |
| `nl_log_query.py` | Accepts NL user queries ("show Redis errors past 2h"); LLM extracts: container_name, time_range, log_level, keywords, limit; returns filtered log lines |
| `log_clusterer.py` | BGE-large embeddings + DBSCAN clustering; deduplicates repetitive log noise; identifies unique event clusters; tracks cluster drift over time |
| `error_explainer.py` | Collects anomalous + high-frequency cluster centroids; LLM (Claude) generates: error description, likely root cause, fix steps, related docs; batched for cost efficiency |

---

## Tools (`agent/tools/`)

| File | Description |
|------|-------------|
| `dozzle_client.py` | HTTP client for Dozzle REST API + Docker SDK fallback; streams log lines with container metadata (name, image, timestamp, level) |

---

## HuggingFace Models

| Model ID | Task | Why Chosen |
|----------|------|-----------|
| `BAAI/bge-large-en-v1.5` | Log line embedding for anomaly detection and clustering | Top-ranked on MTEB; 1024-dim dense vectors; outperforms OpenAI ada-002 on asymmetric retrieval |
| `facebook/bart-large-cnn` | Summarization of long error log batches before LLM explanation | Strong abstractive summarizer; reduces token cost by 60% for long log sequences |
| `sentence-transformers/all-MiniLM-L6-v2` | Fast similarity scoring for NL query intent matching | 6× faster than BGE-large; acceptable accuracy for query template retrieval |
| `huggingface/CodeBERTa-small-v1` | Code snippet detection in logs (stack traces, SQL queries) | Identifies log lines containing code/SQL for specialized explanation |

---

## LLM API Integration

| Provider | Use Cases |
|----------|-----------|
| Claude (`claude-opus-4-8`) | Error pattern explanation, NL query parsing, incident summary generation |
| OpenAI (`gpt-4o`) | Fallback for all LLM tasks; multimodal if log screenshots provided |
| Ollama (`llama3`) | Privacy mode for sensitive log data; offline deployments |

**Provider priority:** Claude → OpenAI → Ollama

---

## Knowledge Crawl Sources

| Source | Categories/Keywords | Frequency |
|--------|--------------------|-----------| 
| ArXiv | cs.OS, cs.DC — log analysis, anomaly detection | Weekly |
| Papers with Code | LogBERT, DRAIN, Spell, DeepLog, LogAnomaly leaderboards | Weekly |
| USENIX ATC | Log management, distributed systems observability | Weekly |
| Dozzle GitHub | Releases, issues, PRs — upstream changelog | Weekly |
| Loki/Fluentd/Vector | Engineering blogs — log processing patterns | Weekly |

---

## Supporting Tools (`tools/`)

| File | Description |
|------|-------------|
| `knowledge_updater.py` | Crawls ArXiv cs.OS+cs.DC, USENIX ATC, Dozzle releases, log analysis papers; appends to SECOND-KNOWLEDGE-BRAIN.md weekly |
| `llm_client.py` | Unified Claude/OpenAI/Ollama client with streaming, retry, and cost tracking |
| `hf_model_manager.py` | Lazy-loads BGE-large, BART-CNN, MiniLM models; auto-unloads idle models after 600s |

---

## Active Development Tasks

- [ ] Phase 0: Fork Dozzle v8.5.3, document existing API endpoints, record baseline capabilities
- [ ] Phase 1: Implement `log_anomaly_detector.py` with Isolation Forest + BGE embeddings
- [ ] Phase 1: Implement `nl_log_query.py` with LLM intent parsing
- [ ] Phase 2: Implement `log_clusterer.py` with DBSCAN on BGE embeddings
- [ ] Phase 2: Implement `error_explainer.py` with Claude batched explanation
- [ ] Phase 3: Wire `DozzleOrchestrator` with async module pipeline
- [ ] Phase 4: Integrate HuggingFace BGE-large + BART-CNN via `hf_model_manager.py`
- [ ] Phase 5: Implement `knowledge_updater.py` — first crawl run
- [ ] Phase 6: Docker Compose stack + integration tests
- [ ] Phase 7: REST API with FastAPI — real-time anomaly feed endpoint
