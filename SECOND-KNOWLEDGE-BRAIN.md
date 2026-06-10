# SECOND-KNOWLEDGE-BRAIN.md — Dozzle-Enhanced AI Log Analysis Agent
*Self-updating knowledge base. Updated weekly by `tools/knowledge_updater.py`.*
*Last crawl: 2026-06-08 | Next scheduled: 2026-06-15 02:00*

---

## 1. Core Concepts & Frameworks

### Log Analysis Fundamentals
- **Log parsing:** Converting unstructured log text into structured events. Key challenge: infinite template diversity. DRAIN (2017) and Spell (2016) are the fastest template-mining algorithms.
- **Log anomaly detection:** Identifying log sequences or individual lines that deviate from normal patterns. Two paradigms: (1) unsupervised (Isolation Forest, LSTM autoencoder) and (2) semi-supervised (LogBERT, few labeled anomalies).
- **Log clustering:** Grouping semantically similar log messages to reduce noise. DBSCAN preferred over k-means for logs due to variable cluster counts and noise tolerance.
- **Semantic log embeddings:** Neural embeddings capture meaning beyond keyword matching. BGE-large-en-v1.5 outperforms older TF-IDF and word2vec approaches for log similarity tasks.

### Docker / Container Log Ecosystem
- **Dozzle architecture:** Go HTTP server + Docker SDK; streams logs via Docker Engine API; SSE endpoint `/api/events/stream`; supports multi-host via Dozzle agents
- **Docker log drivers:** json-file (default), syslog, fluentd, journald. Most production deployments use json-file with log rotation.
- **Dozzle REST API:** `GET /api/hosts/{host}/containers/{container}/logs/stream` → SSE; `GET /api/hosts/{host}/containers` → container list
- **Log volume at scale:** A 20-microservice application generates 50,000–500,000 log lines/hour. Manual scanning is impossible at this rate.

---

## 2. Key Research Papers

| Title | Authors | Year | Venue | DOI/Link | Key Finding | Relevance |
|-------|---------|------|-------|----------|-------------|-----------|
| DRAIN: An Online Log Parsing Approach with Fixed Depth Tree | He et al. | 2017 | ICWS | https://arxiv.org/abs/2004.01171 | Fixed-depth parse tree achieves 99% accuracy, 5,800 lines/sec | Core log parsing algorithm for template extraction |
| DeepLog: Anomaly Detection and Diagnosis from System Logs | Du et al. | 2017 | CCS | https://dl.acm.org/doi/10.1145/3133956.3134015 | LSTM on log key sequences achieves 96.3% detection rate; online learning | LSTM autoencoder inspiration for sequence-level detection |
| LogBERT: Log Anomaly Detection via BERT | Guo et al. | 2021 | IJCNN | https://arxiv.org/abs/2103.04475 | BERT pre-training on log data outperforms LSTM; masked log key prediction | Semi-supervised log anomaly detection approach |
| Spell: Streaming Parsing of System Event Logs | Du & Li | 2016 | ICDM | https://arxiv.org/abs/1805.04956 | LCS-based streaming parser; processes logs in real-time | Alternative to DRAIN for streaming environments |
| Log-based Anomaly Detection with Deep Learning: How Far Are We? | Le & Zhang | 2022 | ICSE | https://arxiv.org/abs/2202.04301 | Systematic comparison of 11 deep learning methods; dataset contamination issue | Critical benchmarking guide for anomaly detection implementation |
| NeuralLog: Anomaly Detection Based on Log Key Sequence and Semantic (2021) | Le et al. | 2021 | ArXiv | https://arxiv.org/abs/2110.04038 | Combines log key sequences with semantic content; 98.7% AUC on BGL | Motivation for combining Isolation Forest + BGE semantic embeddings |
| Outpost: Outage Detection Using Logs (2022) | Lim et al. | 2022 | EuroSys | https://dl.acm.org/doi/10.1145/3492321.3527537 | Production outage detection from logs at scale; 87% precision | Real-world validation of log-based outage detection |
| LogCluster: A Data Clustering and Pattern Mining Approach (2016) | Vaarandi & Pihelgas | 2016 | CNSM | https://ieeexplore.ieee.org/document/7828359 | DBSCAN-based log clustering; handles high-volume enterprise logs | Foundational algorithm for LogClusterer module |
| Studying the Characteristics of Logging Practices in Mobile Apps | Chen et al. | 2021 | EMSE | https://arxiv.org/abs/2009.02975 | 23% of log statements are changed in maintenance; log drift analysis | Background on log evolution and cluster drift detection |
| An Empirical Investigation of Log Anomaly Detection (2023) | Landauer et al. | 2023 | ArXiv | https://arxiv.org/abs/2308.03971 | Isolation Forest achieves competitive results vs deep learning with far less data | Validates Isolation Forest as primary anomaly detector |
| BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity | Chen et al. | 2024 | ArXiv | https://arxiv.org/abs/2402.03216 | BGE-large achieves SOTA on 56 retrieval datasets; best open-source dense embedder | Validates BGE-large-en-v1.5 choice for log embeddings |
| Loglizer: A Machine Learning Toolkit for Log-Based Anomaly Detection | He et al. | 2020 | ISSRE | https://github.com/logpai/loglizer | Unified toolkit: PCA, Invariants Mining, DeepLog, LogCluster benchmarks | Implementation reference for comparison baselines |

---

## 3. State-of-the-Art Models

| Model | Task | Benchmark Score | HuggingFace Link | Date Updated |
|-------|------|----------------|-----------------|--------------|
| `BAAI/bge-large-en-v1.5` | Text embedding (log similarity) | MTEB avg: 64.23 | https://huggingface.co/BAAI/bge-large-en-v1.5 | 2024-01 |
| `sentence-transformers/all-MiniLM-L6-v2` | Fast sentence embedding | MTEB avg: 56.26 | https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 | 2022-06 |
| `facebook/bart-large-cnn` | Abstractive summarization | CNN/DM ROUGE-2: 21.28 | https://huggingface.co/facebook/bart-large-cnn | 2020-10 |
| `huggingface/CodeBERTa-small-v1` | Code detection in text | code classification F1: 0.92 | https://huggingface.co/huggingface/CodeBERTa-small-v1 | 2021-03 |
| `deepset/roberta-base-squad2` | QA over log content | F1: 86.5 on SQuAD2 | https://huggingface.co/deepset/roberta-base-squad2 | 2021-08 |

---

## 4. LLM Prompt Patterns

### Pattern 1: NL Log Query Parser
```
System: You are a log query filter extractor for Docker container logs. 
Extract a structured JSON filter from the user's natural language query.
Always output valid JSON only.

User: {user_query}

Examples:
Q: "show me redis errors in the last 2 hours"
A: {"container_name": "redis", "time_range_minutes": 120, "log_level": "ERROR", "keywords": [], "limit": 100, "sort": "desc"}

Q: "nginx 404 errors today"
A: {"container_name": "nginx", "time_range_minutes": 1440, "log_level": null, "keywords": ["404"], "limit": 200, "sort": "desc"}

Q: "all critical logs from the past 15 minutes"
A: {"container_name": null, "time_range_minutes": 15, "log_level": "CRITICAL", "keywords": [], "limit": 500, "sort": "desc"}
```

### Pattern 2: Error Explanation
```
System: You are a senior DevOps engineer. Explain this Docker container log anomaly clearly.
Include: what happened, the likely root cause, and concrete fix steps.
Output valid JSON only.

Container: {container_name} | Image: {image_name}
Recent normal context (last 5 lines):
{normal_context}

Anomalous lines:
{anomalous_lines}

Output format:
{
  "error_type": "string",
  "severity": "CRITICAL|HIGH|MEDIUM|LOW",
  "description": "Plain English description",
  "root_cause": "Likely cause (confidence: HIGH|MEDIUM|LOW)",
  "fix_steps": ["step 1", "step 2"],
  "docs_links": ["url1"]
}
```

### Pattern 3: Cluster Label Generation
```
System: Generate a 3-5 word label for this group of similar log messages.
The label should describe the common theme. Output only the label, nothing else.

Sample log messages from cluster:
{cluster_samples}

Label:
```

### Pattern 4: Incident Summary
```
System: You are an SRE. Summarize the current incident based on anomalous log events.
Be concise. Use bullet points. Include: affected services, anomaly types, timeline, recommended actions.

Anomaly events (last 30 minutes):
{anomaly_events_json}

Summary:
```

---

## 5. Authoritative Data Sources

| Source | Type | URL | Use Case |
|--------|------|-----|----------|
| Dozzle GitHub | Upstream repo | https://github.com/amir20/dozzle | Releases, API docs, issues |
| logpai/logparser | Log parsing tools | https://github.com/logpai/logparser | DRAIN, Spell implementations |
| logpai/loglizer | Anomaly detection | https://github.com/logpai/loglizer | Benchmarks, baselines |
| ArXiv cs.OS | Research papers | https://arxiv.org/list/cs.OS/recent | New log analysis papers |
| ArXiv cs.DC | Research papers | https://arxiv.org/list/cs.DC/recent | Distributed systems logs |
| USENIX ATC | Conference papers | https://www.usenix.org/conferences/byname/131 | Production log systems |
| Grafana Loki Blog | Engineering blog | https://grafana.com/blog/tag/loki/ | Log aggregation patterns |
| Vector.dev Blog | Engineering blog | https://vector.dev/blog/ | Log processing best practices |
| Papers with Code | Benchmarks | https://paperswithcode.com/task/anomaly-detection | SOTA model tracking |
| Docker Engine API | Docker docs | https://docs.docker.com/engine/api/ | Log streaming API reference |

---

## 6. Self-Update Protocol

```yaml
# tools/knowledge_updater.py configuration
sources:
  arxiv:
    base_url: "http://export.arxiv.org/api/query"
    categories: ["cs.OS", "cs.DC"]
    keywords:
      - "log anomaly detection"
      - "log clustering"
      - "log parsing"
      - "DRAIN log"
      - "LogBERT"
      - "DeepLog"
      - "container log analysis"
    max_results: 20
    recency_weight: 1.0   # papers from last 90 days
    older_weight: 0.3

  semantic_scholar:
    base_url: "https://api.semanticscholar.org/graph/v1/paper/search"
    queries:
      - "docker container log anomaly detection machine learning"
      - "log clustering neural embeddings"
    fields: ["title", "abstract", "year", "citationCount", "externalIds", "url"]
    max_results: 10

  github_releases:
    repos:
      - "amir20/dozzle"
      - "grafana/loki"
      - "vectordotdev/vector"
      - "logpai/logparser"
    api_url: "https://api.github.com/repos/{repo}/releases/latest"

  rss_feeds:
    - url: "https://grafana.com/blog/tag/loki/feed.xml"
      keywords: ["anomaly", "analysis", "AI", "ML"]
    - url: "https://vector.dev/blog/feed.xml"
      keywords: ["machine learning", "AI", "anomaly"]

scoring:
  recency_days_max: 90
  relevance_keyword_match_weight: 0.6
  recency_weight: 0.4

output:
  top_n: 10
  append_to: "SECOND-KNOWLEDGE-BRAIN.md"
  section: "## 7. Knowledge Update Log"
  dedup_key: "url_or_doi_hash"
  dedup_table: "knowledge_hashes"  # SQLite table in memory_manager

schedule:
  cron: "0 2 * * 0"  # Sunday 02:00 local time
  fallback_on_failure: true
  retry_count: 3
```

---

## 7. Knowledge Update Log

### 2026-06-08 — Initial Crawl (Manual Seed)
**Source:** Manual research survey
**Entries added:** 12 papers (see Section 2), 5 models (see Section 3), 4 prompt patterns (see Section 4)
**Topics covered:** DRAIN log parsing, DeepLog LSTM anomaly, LogBERT semi-supervised, Spell streaming parser, log clustering with DBSCAN, BGE-large embedding benchmarks, Isolation Forest for log anomaly (Landauer 2023)
**Notes:** Baseline knowledge established. First automated crawl scheduled for 2026-06-15 02:00.

---
*Next automated update: 2026-06-15 02:00 via `tools/knowledge_updater.py`*
*Self-improvement protocol: every weekly crawl appends ≥5 new entries. The agent improves continuously.*
