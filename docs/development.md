# Development Guide

## Getting Started

### Prerequisites

- Python 3.12+
- Docker Engine 20.10+
- Git

### Clone Repository

```bash
git clone https://github.com/your-org/dozzle-enhanced.git
cd dozzle-enhanced
```

### Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Project Structure

```
dozzle-enhanced/
├── agent/
│   ├── modules/
│   │   ├── log_anomaly_detector.py    # Isolation Forest + LSTM
│   │   ├── nl_log_query.py            # Natural language queries
│   │   ├── log_clusterer.py           # DBSCAN clustering
│   │   └── error_explainer.py         # LLM explanations
│   ├── tools/
│   │   └── dozzle_client.py           # Docker/Dozzle log stream
│   ├── memory/
│   │   └── memory_manager.py         # SQLite persistence
│   ├── orchestrator.py               # Main agent loop
│   └── main.py                       # CLI + REST API
├── tools/
│   ├── llm_client.py                # Claude/OpenAI/Ollama
│   ├── hf_model_manager.py          # HuggingFace models
│   └── knowledge_updater.py        # ArXiv crawler
├── config/
│   └── agent_config.yaml            # Runtime configuration
├── docker/
│   ├── Dockerfile                   # Multi-stage build
│   ├── docker-compose.yml           # Full stack
│   └── grafana-dozzle-enhanced-dashboard.json
├── tests/
│   └── test_agent.py                # Integration tests
├── docs/
│   ├── api-reference.md
│   ├── deployment.md
│   └── security-review.md
├── SECOND-KNOWLEDGE-BRAIN.md         # Self-updating knowledge base
└── requirements.txt
```

---

## Development Workflow

### Running Tests

```bash
# Unit tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=agent --cov=tools --cov-report=html

# Specific module
pytest tests/test_agent.py::test_anomaly_detector
```

### Running the Agent

```bash
# Mock mode (no Docker required)
export DOZZLE_MOCK=true
python -m agent.main monitor --daemon

# With Docker
python -m agent.main monitor --host 0.0.0.0 --port 8766

# CLI query
python -m agent.main query "show redis errors last 2 hours"
```

### Docker Development

```bash
# Build image
docker build -f docker/Dockerfile -t dozzle-enhanced:dev .

# Run with hot reload
docker-compose -f docker/docker-compose.yml up --build

# Logs
docker-compose -f docker/docker-compose.yml logs -f dozzle-ai-agent
```

---

## Module Development

### Adding a New Module

1. Create file in `agent/modules/`
2. Inherit from base pattern:

```python
from __future__ import annotations

class NewModule:
    def __init__(self, config: dict):
        self.config = config
        self _model = None

    def process(self, log_line: dict) -> dict:
        # Process log line
        return result
```

3. Add to orchestrator:

```python
# agent/orchestrator.py
@property
def new_module(self):
    if self._new_module is None:
        from agent.modules.new_module import NewModule
        self._new_module = NewModule(self.config)
    return self._new_module
```

### Testing Modules

```python
# tests/test_new_module.py
import pytest
from agent.modules.new_module import NewModule

def test_new_module():
    config = {}
    module = NewModule(config)
    result = module.process({"message": "test"})
    assert result["processed"] is True
```

---

## HuggingFace Models

### Adding a New Model

1. Register in `tools/hf_model_manager.py`:

```python
MODEL_REGISTRY = {
    "new_model": {
        "model_id": "org/model-name",
        "type": "sentence_transformer",
        "description": "Model description",
        "memory_gb": 1.0,
    },
}
```

2. Use in module:

```python
from tools.hf_model_manager import HFModelManager

hf = HFModelManager()
model = hf.get_model("new_model")
result = model.encode(["text"])
```

### Testing Model Locally

```python
# Load model
from tools.hf_model_manager import HFModelManager
hf = HFModelManager()
model = hf.get_model("text_embedding")

# Test inference
import numpy as np
emb = model.encode(["test log message"])
print(f"Embedding shape: {emb.shape}")
```

---

## LLM Integration

### Testing LLM Clients

```python
# Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# Test Claude
python -c "
from tools.llm_client import LLMClient
import asyncio

async def test():
    llm = LLMClient()
    response = await llm.complete('Test prompt')
    print(response)

asyncio.run(test())
"
```

### Adding New LLM Provider

1. Add to `tools/llm_client.py`:

```python
async def _call_new_provider(self, prompt, system, temperature, max_tokens):
    # Implement provider call
    return text, usage
```

2. Register in provider list:

```python
PROVIDER_PRIORITY = ["claude", "openai", "ollama", "new_provider"]
```

---

## Configuration

### Adding New Config Options

Edit `config/agent_config.yaml`:

```yaml
new_feature:
  enabled: true
  setting1: value
  setting2: 42
```

Access in code:

```python
setting = self.config.get("new_feature", {}).get("setting1", default)
```

---

## Documentation

### Updating Docs

1. Edit markdown files in `docs/`
2. Update README.md if needed
3. Update SECOND-KNOWLEDGE-BRAIN.md via knowledge updater

### Building API Docs

```bash
# Generate OpenAPI schema
curl http://localhost:8766/docs > api-docs.html
```

---

## Debugging

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
python -m agent.main monitor
```

### Profile Memory

```python
import tracemalloc
tracemalloc.start()

# Run code...

snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)
```

### Profile Performance

```bash
# Python profiler
python -m cProfile -o profile.stats agent/main.py

# View results
python -c "
import pstats
p = pstats.Stats('profile.stats')
p.sort_stats('cumulative').print_stats(20)
"
```

---

## Release Process

1. Update version in `agent/main.py`
2. Update CHANGELOG.md
3. Run full test suite
4. Build Docker image
5. Tag release: `git tag -a v1.0.0 -m "Release v1.0.0"`
6. Push tag: `git push origin v1.0.0`
7. Create GitHub release

---

## Contributing

### Code Style

- Follow PEP 8
- Use type hints
- Add docstrings for functions
- Keep functions under 50 lines

### Commit Messages

```
feat: add Prometheus metrics endpoint
fix: handle empty log batches gracefully
docs: update API reference
refactor: consolidate LLM calls
```

### Pull Request Process

1. Fork repository
2. Create feature branch
3. Make changes with tests
4. Ensure CI passes
5. Submit PR with description

---

## Troubleshooting

### Import Errors

```bash
# Ensure Python path is set
export PYTHONPATH=/path/to/dozzle-enhanced
```

### Model Download Issues

```bash
# Set HuggingFace cache
export HF_HOME=/path/to/cache
export TRANSFORMERS_CACHE=/path/to/cache
```

### Docker Socket Issues

```bash
# Ensure socket is accessible
ls -l /var/run/docker.sock
sudo chmod 666 /var/run/docker.sock  # if needed
```

---

## Resources

- [Dozzle Documentation](https://dozzle.dev/)
- [Anthropic API Docs](https://docs.anthropic.com/)
- [HuggingFace Models](https://huggingface.co/models)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
