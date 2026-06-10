"""
LogAnomalyDetector — two-stage log line anomaly detection.

Stage 1: Isolation Forest on BGE-large embeddings (per-container model)
Stage 2: LSTM Autoencoder on 20-line sequences (per-container model)
Fallback: keyword-based severity scorer (no ML required)
"""

from __future__ import annotations

import logging
import os
import pickle
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Keywords mapped to anomaly score (lower = more anomalous, matching IF output convention)
KEYWORD_SCORES = {
    "FATAL": -0.95, "PANIC": -0.95, "CRITICAL": -0.90,
    "ERROR": -0.75, "EXCEPTION": -0.75, "TRACEBACK": -0.75,
    "OOM": -0.85, "KILLED": -0.85, "SEGFAULT": -0.90,
    "TIMEOUT": -0.65, "REFUSED": -0.65, "UNREACHABLE": -0.65,
    "WARNING": -0.30, "WARN": -0.30,
    "INFO": 0.05, "DEBUG": 0.10,
}

BOOTSTRAP_SIZE = 500
RETRAIN_INTERVAL = 10_000
LSTM_WINDOW = 20
THRESHOLD_FACTOR = 2.0  # mean + N * std
ANOMALY_THRESHOLD = -0.3  # IF score below this = anomaly


class IsolationForestModel:
    """Per-container Isolation Forest model with bootstrap training."""

    def __init__(self, contamination: float = 0.05):
        self.contamination = contamination
        self._model = None
        self._fitted = False
        self._buffer: list[np.ndarray] = []
        self._lines_since_retrain = 0

    def add_embedding(self, embedding: np.ndarray):
        self._buffer.append(embedding)
        self._lines_since_retrain += 1

        if not self._fitted and len(self._buffer) >= BOOTSTRAP_SIZE:
            self._fit()
        elif self._fitted and self._lines_since_retrain >= RETRAIN_INTERVAL:
            self._fit()

    def _fit(self):
        from sklearn.ensemble import IsolationForest
        X = np.array(self._buffer[-10_000:])  # keep last 10k for retraining
        self._model = IsolationForest(
            contamination=self.contamination,
            n_estimators=100,
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X)
        self._fitted = True
        self._lines_since_retrain = 0
        logger.debug(f"IsolationForest retrained on {len(X)} samples")

    def score(self, embedding: np.ndarray) -> float:
        """Return anomaly score. Lower (more negative) = more anomalous."""
        if not self._fitted:
            return 0.0  # neutral until trained
        score = self._model.score_samples(embedding.reshape(1, -1))[0]
        return float(score)

    def is_anomaly(self, score: float) -> bool:
        return score < ANOMALY_THRESHOLD


class LSTMAutoencoder:
    """Per-container LSTM autoencoder for sequence-level anomaly detection."""

    def __init__(self, input_dim: int = 1024, hidden_dim: int = 64, window: int = LSTM_WINDOW):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.window = window
        self._model = None
        self._fitted = False
        self._window_buffer: deque = deque(maxlen=window)
        self._errors: deque = deque(maxlen=1000)
        self._threshold = float("inf")

    def add_embedding(self, embedding: np.ndarray):
        self._window_buffer.append(embedding)
        if len(self._window_buffer) == self.window and self._fitted:
            error = self._reconstruction_error()
            self._errors.append(error)
            if len(self._errors) >= 100:
                mean = np.mean(self._errors)
                std = np.std(self._errors)
                self._threshold = mean + THRESHOLD_FACTOR * std

    def _build_model(self):
        try:
            import torch
            import torch.nn as nn

            class Autoencoder(nn.Module):
                def __init__(self, input_dim, hidden_dim, window):
                    super().__init__()
                    self.encoder = nn.LSTM(input_dim, hidden_dim, batch_first=True)
                    self.decoder = nn.LSTM(hidden_dim, input_dim, batch_first=True)

                def forward(self, x):
                    _, (h, _) = self.encoder(x)
                    h_expanded = h.squeeze(0).unsqueeze(1).repeat(1, x.size(1), 1)
                    out, _ = self.decoder(h_expanded)
                    return out

            self._model = Autoencoder(self.input_dim, self.hidden_dim, self.window)
            self._fitted = True
            return True
        except ImportError:
            logger.warning("PyTorch not available — LSTM autoencoder disabled")
            return False

    def _reconstruction_error(self) -> float:
        if self._model is None:
            return 0.0
        try:
            import torch
            seq = np.array(list(self._window_buffer), dtype=np.float32)
            seq_tensor = torch.tensor(seq).unsqueeze(0)
            with torch.no_grad():
                reconstructed = self._model(seq_tensor)
            error = float(torch.mean((seq_tensor - reconstructed) ** 2).item())
            return error
        except Exception:
            return 0.0

    def score(self) -> tuple[float, bool]:
        """Return (reconstruction_error, is_anomaly)."""
        if not self._fitted or len(self._window_buffer) < self.window:
            return 0.0, False
        error = self._reconstruction_error()
        is_anomaly = error > self._threshold and self._threshold != float("inf")
        return error, is_anomaly

    def initialize(self):
        if not self._fitted:
            self._build_model()


class LogAnomalyDetector:
    """
    Two-stage anomaly detector:
    1. Isolation Forest on BGE-large embeddings (per-container)
    2. LSTM Autoencoder on 20-line sequences (per-container)
    3. Keyword fallback if models unavailable
    """

    def __init__(self, config: dict):
        self.config = config
        self._if_models: dict[str, IsolationForestModel] = {}
        self._lstm_models: dict[str, LSTMAutoencoder] = {}
        self._embedder = None
        self._embed_dim = 1024

    def _get_embedder(self):
        if self._embedder is None:
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent.parent))
                from tools.hf_model_manager import HFModelManager
                self._embedder = HFModelManager().get_model("text_embedding")
            except Exception as e:
                logger.warning(f"BGE-large unavailable: {e} — using keyword fallback")
        return self._embedder

    def _embed(self, text: str) -> np.ndarray | None:
        embedder = self._get_embedder()
        if embedder is None:
            return None
        try:
            result = embedder.encode([text], normalize_embeddings=True)
            return np.array(result[0], dtype=np.float32)
        except Exception as e:
            logger.debug(f"Embedding failed: {e}")
            return None

    def _keyword_score(self, message: str) -> float:
        msg_upper = message.upper()
        for keyword, score in KEYWORD_SCORES.items():
            if keyword in msg_upper:
                return score
        return 0.05  # default: normal

    def _get_if_model(self, container: str) -> IsolationForestModel:
        if container not in self._if_models:
            contamination = self.config.get("anomaly_detection", {}).get("contamination", 0.05)
            self._if_models[container] = IsolationForestModel(contamination=contamination)
        return self._if_models[container]

    def _get_lstm_model(self, container: str) -> LSTMAutoencoder:
        if container not in self._lstm_models:
            m = LSTMAutoencoder(input_dim=self._embed_dim)
            m.initialize()
            self._lstm_models[container] = m
        return self._lstm_models[container]

    def score_single(self, log_line: dict) -> dict:
        """Score a single log line. Returns anomaly scoring result dict."""
        container = log_line.get("container", "default")
        message = log_line.get("message", "")

        embedding = self._embed(message)

        if embedding is not None:
            # Stage 1: Isolation Forest
            if_model = self._get_if_model(container)
            if_model.add_embedding(embedding)
            if_score = if_model.score(embedding)
            is_if_anomaly = if_model.is_anomaly(if_score)

            # Stage 2: LSTM Autoencoder
            lstm_model = self._get_lstm_model(container)
            lstm_model.add_embedding(embedding)
            seq_score, is_seq_anomaly = lstm_model.score()
        else:
            # Fallback: keyword-based
            if_score = self._keyword_score(message)
            is_if_anomaly = if_score < ANOMALY_THRESHOLD
            seq_score, is_seq_anomaly = 0.0, False

        return {
            "container": container,
            "anomaly_score": if_score,
            "is_anomaly": is_if_anomaly,
            "seq_anomaly_score": seq_score,
            "is_seq_anomaly": is_seq_anomaly,
            "used_ml": embedding is not None,
        }

    def score_batch(self, log_lines: list[dict]) -> list[dict]:
        """Score a batch of log lines. Used via executor for thread-safe call."""
        return [self.score_single(line) for line in log_lines]
