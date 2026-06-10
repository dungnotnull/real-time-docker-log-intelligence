# Deployment Guide

## Production Deployment

### Prerequisites

- Docker Engine 20.10+
- 4GB+ RAM available
- Python 3.12+
- ANTHROPIC_API_KEY or OPENAI_API_KEY

### Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Optional
DOZZLE_BASE_URL=http://dozzle:8080
OLLAMA_BASE_URL=http://ollama:11434
LOG_LEVEL=INFO
HF_CACHE_DIR=/app/models
DATA_DIR=/app/data
```

### Docker Compose Production

```yaml
version: "3.9"

services:
  dozzle-ai-agent:
    image: dozzle-enhanced:latest
    container_name: dozzle-ai-agent
    restart: unless-stopped
    ports:
      - "8766:8766"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - agent-data:/app/data
      - model-cache:/app/models
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DOZZLE_BASE_URL=http://dozzle:8080
    deploy:
      resources:
        limits:
          memory: 6G
        reservations:
          memory: 2G
```

### Health Checks

```bash
curl http://localhost:8766/health
```

Expected response:

```json
{"status": "ok", "timestamp": "2026-06-10T14:32:15.123Z"}
```

---

## Kubernetes Deployment

### Deployment Manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dozzle-enhanced
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: dozzle-enhanced
  template:
    metadata:
      labels:
        app: dozzle-enhanced
    spec:
      containers:
      - name: agent
        image: dozzle-enhanced:latest
        ports:
        - containerPort: 8766
        env:
        - name: ANTHROPIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-keys
              key: anthropic-key
        - name: DOZZLE_BASE_URL
          value: "http://dozzle:8080"
        volumeMounts:
        - name: docker-sock
          mountPath: /var/run/docker.sock
          readOnly: true
        - name: data
          mountPath: /app/data
        - name: models
          mountPath: /app/models
        resources:
          requests:
            memory: "2Gi"
          limits:
            memory: "6Gi"
      volumes:
      - name: docker-sock
        hostPath:
          path: /var/run/docker.sock
      - name: data
        persistentVolumeClaim:
          claimName: dozzle-agent-data
      - name: models
        persistentVolumeClaim:
          claimName: dozzle-model-cache
```

---

## Monitoring

### Prometheus Configuration

```yaml
scrape_configs:
  - job_name: 'dozzle-enhanced'
    static_configs:
      - targets: ['dozzle-ai-agent:8766']
    metrics_path: '/metrics'
    scrape_interval: 30s
```

### Alert Rules

```yaml
groups:
  - name: dozzle_anomalies
    rules:
      - alert: HighAnomalyRate
        expr: rate(dozzle_agent_anomalies_detected_total[5m]) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High anomaly detection rate in {{ $labels.container }}"
```

---

## Scaling

### Horizontal Scaling

For high-volume environments (100k+ log lines/second):

1. Deploy multiple agent instances
2. Configure Dozzle agents for multi-host
3. Use shared SQLite via NFS or external database
4. Load balance REST API requests

### Vertical Scaling

For better ML performance:

1. Increase memory limit to 8GB+
2. Enable GPU for LSTM autoencoder
3. Preload models on startup

---

## Security

### API Key Protection

Never commit API keys to git. Use secrets:

```bash
# Docker Compose
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
echo ".env" >> .gitignore

# Kubernetes
kubectl create secret generic api-keys \
  --from-literal=anthropic-key=sk-ant-...
```

### Network Security

- Restrict API access via network policies
- Enable TLS for production
- Use webhook authentication

### Data Privacy

For sensitive logs, use Ollama (offline mode):

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    profiles:
      - offline
```

---

## Troubleshooting

### High Memory Usage

- Reduce model cache: set `IDLE_UNLOAD_SECONDS=300`
- Disable LSTM autoencoder in config
- Limit containers monitored

### LLM Rate Limits

- Configure provider fallback order
- Add exponential backoff
- Use Ollama for offline mode

### Missing Anomalies

- Check contamination threshold (default: 0.05)
- Increase bootstrap_size for better training
- Verify log_level parsing

---

## Backup and Recovery

### SQLite Backup

```bash
# Backup
docker exec dozzle-ai-agent cp /app/data/dozzle_agent.db /tmp/backup.db
docker cp dozzle-ai-agent:/tmp/backup.db ./backup_$(date +%Y%m%d).db

# Restore
docker cp ./backup_20260610.db dozzle-ai-agent:/tmp/restore.db
docker exec dozzle-ai-agent cp /tmp/restore.db /app/data/dozzle_agent.db
```

### Model Cache Backup

```bash
docker exec dozzle-ai-agent tar czf /tmp/models.tar.gz /app/models
docker cp dozzle-ai-agent:/tmp/models.tar.gz ./models_backup.tar.gz
```
