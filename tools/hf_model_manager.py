"""
HFModelManager — lazy-loading HuggingFace model registry for Dozzle-Enhanced Agent.

Models:
- text_embedding: BAAI/bge-large-en-v1.5 (log line embedding, anomaly detection, clustering)
- sentence_similarity: sentence-transformers/all-MiniLM-L6-v2 (NL query template retrieval)
- summarization: facebook/bart-large-cnn (long log batch summarization)
- code_detection: huggingface/CodeBERTa-small-v1 (detect code/SQL in logs)

All models are lazy-loaded on first use and cached in ./models/.
Idle models are unloaded after 600 seconds to free memory.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL_CACHE_DIR = Path(os.getenv("HF_CACHE_DIR", "./models"))
IDLE_UNLOAD_SECONDS = 600

MODEL_REGISTRY = {
    "text_embedding": {
        "model_id": "BAAI/bge-large-en-v1.5",
        "type": "sentence_transformer",
        "description": "Log line embedding for anomaly detection and clustering",
        "memory_gb": 1.3,
    },
    "sentence_similarity": {
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "type": "sentence_transformer",
        "description": "Fast sentence embedding for NL query template retrieval",
        "memory_gb": 0.09,
    },
    "summarization": {
        "model_id": "facebook/bart-large-cnn",
        "type": "pipeline_summarization",
        "description": "Abstractive summarization of long log batches",
        "memory_gb": 1.6,
    },
    "code_detection": {
        "model_id": "huggingface/CodeBERTa-small-v1",
        "type": "pipeline_text_classification",
        "description": "Detect code/SQL snippets in log lines",
        "memory_gb": 0.12,
    },
}


class HFModelManager:
    """
    Singleton-safe lazy model loader.
    Thread-safe: each model has its own lock.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._models: dict[str, Any] = {}
        self._last_used: dict[str, float] = {}
        self._model_locks: dict[str, threading.Lock] = {
            name: threading.Lock() for name in MODEL_REGISTRY
        }
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._initialized = True
        # Start idle unloader thread
        t = threading.Thread(target=self._idle_unloader, daemon=True)
        t.start()

    def get_model(self, model_name: str) -> Any:
        """Get a model by logical name. Loads on first access."""
        if model_name not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(MODEL_REGISTRY)}")

        with self._model_locks[model_name]:
            if model_name not in self._models:
                self._load_model(model_name)
            self._last_used[model_name] = time.time()
            return self._models.get(model_name)

    def _load_model(self, model_name: str):
        spec = MODEL_REGISTRY[model_name]
        model_id = spec["model_id"]
        model_type = spec["type"]
        logger.info(f"Loading HuggingFace model: {model_id} ({spec['memory_gb']}GB)")
        start = time.time()

        try:
            model = None
            cache_dir = str(MODEL_CACHE_DIR / model_name)

            if model_type == "sentence_transformer":
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(model_id, cache_folder=cache_dir)

            elif model_type == "pipeline_summarization":
                from transformers import pipeline
                device = self._get_device()
                model = pipeline(
                    "summarization",
                    model=model_id,
                    device=device,
                    model_kwargs={"cache_dir": cache_dir},
                )

            elif model_type == "pipeline_text_classification":
                from transformers import pipeline
                device = self._get_device()
                model = pipeline(
                    "text-classification",
                    model=model_id,
                    device=device,
                    model_kwargs={"cache_dir": cache_dir},
                )

            self._models[model_name] = model
            elapsed = time.time() - start
            logger.info(f"Model {model_id} loaded in {elapsed:.1f}s")

        except Exception as e:
            logger.error(f"Failed to load model {model_id}: {e}")
            self._models[model_name] = None

    def _get_device(self) -> int:
        """Return CUDA device index (0) or -1 for CPU."""
        try:
            import torch
            if torch.cuda.is_available():
                return 0
        except ImportError:
            pass
        return -1

    def _idle_unloader(self):
        """Background thread: unload models idle > IDLE_UNLOAD_SECONDS."""
        while True:
            time.sleep(60)
            now = time.time()
            for name in list(self._models):
                last_used = self._last_used.get(name, 0)
                if name in self._models and (now - last_used) > IDLE_UNLOAD_SECONDS:
                    with self._model_locks[name]:
                        if name in self._models and (now - self._last_used.get(name, 0)) > IDLE_UNLOAD_SECONDS:
                            logger.info(f"Unloading idle model: {MODEL_REGISTRY[name]['model_id']}")
                            del self._models[name]
                            try:
                                import gc
                                import torch
                                gc.collect()
                                if torch.cuda.is_available():
                                    torch.cuda.empty_cache()
                            except Exception:
                                pass

    def is_loaded(self, model_name: str) -> bool:
        return model_name in self._models and self._models[model_name] is not None

    def list_loaded(self) -> list[str]:
        return [name for name, model in self._models.items() if model is not None]

    def preload(self, model_names: list[str]):
        """Eagerly load specified models."""
        for name in model_names:
            try:
                self.get_model(name)
            except Exception as e:
                logger.warning(f"Preload failed for {name}: {e}")
