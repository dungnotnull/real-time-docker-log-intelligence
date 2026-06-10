# Security Review

## Overview

This document outlines the security considerations and review findings for Dozzle-Enhanced AI Log Analysis Agent.

## Data Privacy

### Sensitive Log Data

**Risk:** Log lines may contain sensitive information (PII, credentials, API keys).

**Mitigations:**

1. **LLM Data Transmission:**
   - User consent required via `LLM_CONSENT_ENABLED` config
   - Sensitive log filtering via keyword detection (password, token, secret)
   - Option to use Ollama (offline mode) for zero data egress

2. **Local Storage:**
   - SQLite database stores raw logs with 7-day retention
   - Data at rest: File system permissions (user: agent, group: agent)
   - Recommendations: Encrypt volume for production

3. **Network Transmission:**
   - API keys stored in environment variables only
   - HTTPS/TLS recommended for all external communications
   - No credentials in logs or error messages

### Configuration Example

```yaml
# config/agent_config.yaml
security:
  llm_consent_required: true
  sensitive_keywords:
    - password
    - api_key
    - secret
    - token
    - credential
  redact_sensitive: true
  offline_mode: false  # Set true for Ollama-only
```

---

## API Key Protection

### Environment Variables

**Required Keys:**
- `ANTHROPIC_API_KEY` (Claude)
- `OPENAI_API_KEY` (OpenAI, optional fallback)
- `OLLAMA_BASE_URL` (local, no key)

**Best Practices:**

1. Never commit `.env` file
2. Use Docker secrets or Kubernetes secrets
3. Rotate keys regularly
4. Monitor LLM cost for unusual activity

### Docker Compose Example

```yaml
services:
  dozzle-ai-agent:
    environment:
      - ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_key
    secrets:
      - anthropic_key

secrets:
  anthropic_key:
    file: ./secrets/anthropic_key.txt
```

---

## Input Validation

### NL Query Injection

**Risk:** Malicious natural language queries could attempt to manipulate the LLM or execute arbitrary commands.

**Mitigations:**

1. **LLM Prompt Injection Protection:**
   - System prompts enforce structured JSON output only
   - No code execution from LLM responses
   - Regex fallback for offline mode

2. **SQL Injection Protection:**
   - Parameterized queries only (via sqlite3 `?` placeholders)
   - No string concatenation in SQL

3. **Query Complexity Limits:**
   - Max results: 500
   - Time range: max 168 hours (7 days)
   - Keywords: max 10 per query

---

## Webhook Security

### Webhook Configuration

**Risk:** Unauthenticated webhook endpoints could be exploited.

**Mitigations:**

1. **Bearer Token Authentication (Recommended):**
   ```yaml
   alerts:
     webhook_url: https://your-webhook.com/anomalies
     webhook_token: your-secret-token
   ```

2. **HMAC Signature Verification:**
   ```python
   signature = hmac_sha256(payload, shared_secret)
   headers["X-Webhook-Signature"] = signature
   ```

3. **HTTPS Only:**
   - Reject non-HTTPS webhook URLs in config
   - Verify SSL certificates

---

## Container Security

### Docker Socket Access

**Risk:** Container with Docker socket access has host root equivalent.

**Mitigations:**

1. **Read-Only Mount:**
   ```yaml
   volumes:
     - /var/run/docker.sock:/var/run/docker.sock:ro
   ```

2. **Non-Root User:**
   ```dockerfile
   RUN useradd -m -u 1000 agent
   USER agent
   ```

3. **Resource Limits:**
   ```yaml
   deploy:
     resources:
       limits:
         memory: 6G
   ```

4. **AppArmor/SELinux:**
   - Enable AppArmor profile for Docker socket access only
   - Restrict file system access

---

## LLM Cost Protection

### Budget Controls

**Risk:** Unbounded LLM API costs due to malicious activity or bugs.

**Mitigations:**

1. **Daily Cost Limits:**
   ```yaml
   llm:
     max_daily_cost_usd: 10.0
     max_requests_per_minute: 100
   ```

2. **Cost Monitoring:**
   - Track via `dozzle_agent_llm_cost_usd_total` metric
   - Alert on unusual spend rate

3. **Request Batching:**
   - Batch anomalies before LLM explanation
   - Max 10 lines per LLM call
   - 60-second batching window

---

## Dependency Security

### Vulnerability Scanning

**Dependencies:**
- FastAPI, uvicorn, pydantic
- anthropic, openai
- transformers, sentence-transformers
- scikit-learn, numpy

**Recommendations:**

1. Run `pip-audit` regularly
2. Use `safety` checker in CI/CD
3. Pin dependency versions in requirements.txt
4. Monitor for CVEs in dependencies

### Update Policy

- Security patches: Immediate update
- Feature updates: Monthly review cycle
- Breaking changes: Version pinning + testing

---

## Network Security

### Cross-Service Communication

**Services:**
- Dozzle (port 8080)
- Dozzle-Enhanced (port 8766)
- Prometheus (port 9090)
- Grafana (port 3000)

**Recommendations:**

1. **Network Segmentation:**
   ```yaml
   networks:
     monitoring:
       driver: bridge
       internal: true  # No external access
   ```

2. **TLS Termination:**
   - Reverse proxy (nginx/traefik) with TLS
   - mTLS for service-to-service communication

3. **Firewall Rules:**
   - Allow only necessary ports
   - Restrict Prometheus scrape to specific IPs

---

## Audit Logging

### Security Event Logging

**Events to Log:**
- Anomaly detections (especially CRITICAL)
- LLM API calls (with cost)
- NL queries (with user identifier)
- Configuration changes
- Failed authentication attempts

**Log Format:**

```json
{
  "timestamp": "2026-06-10T14:32:15.123Z",
  "event_type": "anomaly_detected",
  "severity": "HIGH",
  "container": "nginx",
  "source_ip": "10.0.0.42",
  "user_agent": "Mozilla/5.0...",
  "details": {...}
}
```

---

## Compliance Considerations

### GDPR / Data Protection

- **Data Minimization:** Store only necessary logs (7-day retention)
- **Right to Deletion:** Provide data export/deletion API
- **Consent Management:** Explicit consent for LLM processing
- **Data Residency:** Option for local-only processing (Ollama)

### SOC 2 / ISO 27001

- **Access Control:** RBAC for API endpoints
- **Change Management:** Git-based config tracking
- **Incident Response:** Alerting + documentation
- **Business Continuity:** Backup procedures documented

---

## Security Checklist

### Deployment Checklist

- [ ] API keys stored in secrets (not in code)
- [ ] Docker socket mounted read-only
- [ ] Container running as non-root user
- [ ] Resource limits configured
- [ ] TLS enabled for external communication
- [ ] Webhook tokens configured
- [ ] LLM consent configured
- [ ] Network policies applied
- [ ] Audit logging enabled
- [ ] Dependency vulnerability scan passed

### Operational Checklist

- [ ] Monitoring alerts configured
- [ ] Cost alerts configured
- [ ] Backup procedures tested
- [ ] Incident response plan documented
- [ ] Security review completed
- [ ] Penetration testing scheduled

---

## Incident Response

### Security Incident Procedure

1. **Containment:**
   - Stop container: `docker stop dozzle-ai-agent`
   - Revoke API keys if compromised
   - Isolate affected systems

2. **Investigation:**
   - Review audit logs
   - Analyze anomaly events
   - Identify affected data scope

3. **Recovery:**
   - Rotate credentials
   - Patch vulnerabilities
   - Restore from backup if needed

4. **Post-Mortem:**
   - Document timeline
   - Update security procedures
   - Implement preventive measures

---

## Contact

**Security Issues:** Report to security@example.com
**GitHub Security:** https://github.com/your-org/dozzle-enhanced/security
**PGP Key:** Available on keyserver
