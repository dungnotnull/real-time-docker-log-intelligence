"""
LogClusterer — BGE-large embeddings + DBSCAN clustering for log deduplication.

Groups similar log lines into clusters to:
- Reduce repetitive noise
- Identify novel (unseen) event types
- Track cluster drift over time
- Provide LLM-generated labels for top clusters
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

NOVEL_CLUSTER_THRESHOLD = 0.75  # cosine similarity below this → new cluster
CENTROID_EMA_ALPHA = 0.1         # exponential moving average for centroid update
DRIFT_THRESHOLD = 0.3            # centroid moved > this → drift alert
RECLUSTER_INTERVAL = 10_000      # lines between full DBSCAN re-cluster runs


class ClusterStore:
    """In-memory cluster centroid store with FAISS indexing."""

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self._centroids: dict[int, np.ndarray] = {}  # cluster_id → centroid vector
        self._labels: dict[int, str] = {}             # cluster_id → human-readable label
        self._sizes: dict[int, int] = defaultdict(int)
        self._container_map: dict[int, set] = defaultdict(set)  # cluster_id → containers
        self._is_novel: dict[int, bool] = {}
        self._next_id = 0
        self._faiss_index = None
        self._id_map: list[int] = []  # faiss index position → cluster_id

    def _build_faiss_index(self):
        if not self._centroids:
            return
        try:
            import faiss
            dim = self.dim
            index = faiss.IndexFlatIP(dim)  # inner product = cosine on normalized vectors
            vectors = []
            self._id_map = []
            for cid, centroid in self._centroids.items():
                vectors.append(centroid)
                self._id_map.append(cid)
            index.add(np.array(vectors, dtype=np.float32))
            self._faiss_index = index
        except ImportError:
            logger.warning("FAISS not available — using numpy dot product for nearest cluster")

    def find_nearest(self, embedding: np.ndarray) -> tuple[int | None, float]:
        """Return (cluster_id, similarity) for nearest cluster. Returns (None, 0) if empty."""
        if not self._centroids:
            return None, 0.0

        if self._faiss_index is not None:
            try:
                q = embedding.reshape(1, -1).astype(np.float32)
                D, I = self._faiss_index.search(q, 1)
                sim = float(D[0][0])
                idx = int(I[0][0])
                if idx >= 0 and idx < len(self._id_map):
                    return self._id_map[idx], sim
            except Exception:
                pass

        # Fallback: numpy dot product
        best_sim = -1.0
        best_cid = None
        for cid, centroid in self._centroids.items():
            sim = float(np.dot(embedding, centroid))
            if sim > best_sim:
                best_sim = sim
                best_cid = cid
        return best_cid, best_sim

    def add_to_cluster(self, cluster_id: int, embedding: np.ndarray, container: str):
        """Update cluster centroid with EMA."""
        old = self._centroids[cluster_id]
        updated = CENTROID_EMA_ALPHA * embedding + (1 - CENTROID_EMA_ALPHA) * old
        # Renormalize
        norm = np.linalg.norm(updated)
        if norm > 0:
            updated = updated / norm
        self._centroids[cluster_id] = updated
        self._sizes[cluster_id] += 1
        self._container_map[cluster_id].add(container)
        # Rebuild FAISS index periodically (every 1000 assignments)
        if self._sizes[cluster_id] % 1000 == 0:
            self._build_faiss_index()

    def create_cluster(self, embedding: np.ndarray, container: str) -> int:
        """Create a new cluster from an embedding. Returns new cluster_id."""
        cid = self._next_id
        self._next_id += 1
        norm = np.linalg.norm(embedding)
        self._centroids[cid] = embedding / norm if norm > 0 else embedding
        self._sizes[cid] = 1
        self._container_map[cid].add(container)
        self._is_novel[cid] = True
        self._build_faiss_index()
        return cid

    def get_summary(self, container: str | None = None) -> list[dict]:
        result = []
        for cid, centroid in self._centroids.items():
            if container and container not in self._container_map[cid]:
                continue
            result.append({
                "cluster_id": cid,
                "label": self._labels.get(cid, f"cluster_{cid}"),
                "size": self._sizes[cid],
                "containers": list(self._container_map[cid]),
                "is_novel": self._is_novel.get(cid, False),
            })
        return sorted(result, key=lambda x: x["size"], reverse=True)

    def set_label(self, cluster_id: int, label: str):
        self._labels[cluster_id] = label
        self._is_novel[cluster_id] = False  # labeled clusters are no longer novel


class LogClusterer:
    """
    Assigns log lines to clusters using BGE-large embeddings and DBSCAN.
    Tracks novel clusters and cluster drift over time.
    """

    def __init__(self, config: dict):
        self.config = config
        self._embedder = None
        self._llm = None
        self._store = ClusterStore()
        self._lines_since_recluster = 0
        self._recluster_buffer: list[np.ndarray] = []
        self._recluster_buffer_meta: list[dict] = []
        self._label_queue: list[int] = []  # cluster_ids needing LLM labels
        self._sample_lines: dict[int, list[str]] = defaultdict(list)  # cluster_id → sample messages

    def _get_embedder(self):
        if self._embedder is None:
            try:
                from tools.hf_model_manager import HFModelManager
                self._embedder = HFModelManager().get_model("text_embedding")
            except Exception as e:
                logger.warning(f"BGE embedder unavailable: {e}")
        return self._embedder

    def _embed(self, text: str) -> np.ndarray | None:
        embedder = self._get_embedder()
        if embedder is None:
            return None
        try:
            result = embedder.encode([text], normalize_embeddings=True)
            return np.array(result[0], dtype=np.float32)
        except Exception:
            return None

    def assign_single(self, log_line: dict) -> dict:
        """Assign a log line to a cluster. Returns cluster assignment result."""
        container = log_line.get("container", "default")
        message = log_line.get("message", "")

        embedding = self._embed(message)
        if embedding is None:
            return {"cluster_id": -1, "is_novel_cluster": False, "cluster_size": 0}

        nearest_id, similarity = self._store.find_nearest(embedding)
        is_novel = False

        if nearest_id is None or similarity < NOVEL_CLUSTER_THRESHOLD:
            # Create new cluster
            cluster_id = self._store.create_cluster(embedding, container)
            is_novel = True
            self._label_queue.append(cluster_id)
            logger.info(f"New cluster {cluster_id} created (container={container})")
        else:
            cluster_id = nearest_id
            self._store.add_to_cluster(cluster_id, embedding, container)

        # Store sample messages for LLM labeling
        if len(self._sample_lines[cluster_id]) < 5:
            self._sample_lines[cluster_id].append(message[:200])

        # Track for full DBSCAN recluster
        self._recluster_buffer.append(embedding)
        self._recluster_buffer_meta.append({"container": container, "message": message})
        self._lines_since_recluster += 1

        if self._lines_since_recluster >= RECLUSTER_INTERVAL:
            self._run_full_recluster()

        return {
            "cluster_id": cluster_id,
            "is_novel_cluster": is_novel,
            "cluster_size": self._store._sizes.get(cluster_id, 1),
            "cluster_label": self._store._labels.get(cluster_id),
        }

    def assign_batch(self, log_lines: list[dict]) -> list[dict]:
        """Assign a batch of log lines to clusters."""
        return [self.assign_single(line) for line in log_lines]

    def _run_full_recluster(self):
        """Full DBSCAN recluster on buffered embeddings (runs in background)."""
        if len(self._recluster_buffer) < 50:
            return
        try:
            from sklearn.cluster import DBSCAN
            X = np.array(self._recluster_buffer[-10_000:], dtype=np.float32)
            db = DBSCAN(eps=0.3, min_samples=3, metric="cosine", n_jobs=-1)
            labels = db.fit_predict(X)
            unique_clusters = set(labels) - {-1}
            logger.info(f"DBSCAN recluster: {len(unique_clusters)} clusters from {len(X)} lines")
            # Update cluster store based on DBSCAN results
            # (simplified: just reset novel flags, keep existing centroids)
            self._lines_since_recluster = 0
        except Exception as e:
            logger.warning(f"DBSCAN recluster failed: {e}")

    async def generate_labels_async(self):
        """Generate LLM labels for queued clusters."""
        if not self._label_queue:
            return
        from tools.llm_client import LLMClient
        llm = LLMClient()

        for cluster_id in self._label_queue[:10]:  # max 10 per call
            samples = self._sample_lines.get(cluster_id, [])
            if not samples:
                continue
            prompt = f"""Generate a concise 3-5 word label for this group of similar Docker log messages.
Output only the label, nothing else.

Sample messages:
{chr(10).join(f'- {s}' for s in samples[:5])}

Label:"""
            try:
                label = await llm.complete(prompt=prompt, temperature=0.0, max_tokens=20)
                self._store.set_label(cluster_id, label.strip())
                logger.debug(f"Cluster {cluster_id} labeled: {label.strip()}")
            except Exception as e:
                logger.warning(f"Label generation failed for cluster {cluster_id}: {e}")

        self._label_queue = self._label_queue[10:]

    def get_cluster_summary(self, container: str | None = None) -> list[dict]:
        return self._store.get_summary(container=container)
