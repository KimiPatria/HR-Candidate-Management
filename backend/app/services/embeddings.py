"""Local, offline text embeddings for the document vector store.

Runs on fastembed (ONNX runtime) rather than sentence-transformers/torch, since nothing
here needs a GPU and the smaller dependency matters more than raw throughput for
per-interview document sets. The model weights download from Hugging Face once and are
cached under `data/models/fastembed` - deliberately NOT the OS temp directory (fastembed's
own default), which Windows purges without warning and would otherwise force a surprise
re-download the next time a document is indexed.
"""

import logging

import numpy as np
from fastembed import TextEmbedding

from app.core.config import settings

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
# BGE models are tuned to take an instruction prefix on the query side only; passages are
# embedded plain. Per the model card, skipping this still works, just with slightly worse
# recall - so a missing/changed prefix is not a correctness bug, only a quality one.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        cache_dir = settings.upload_path.parent / "models" / "fastembed"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(cache_dir))
    return _model


def warm_up() -> None:
    """Force the model to load (and download, if not yet cached) once, at startup,
    rather than surprising the first document upload with the delay - or worse, a
    failure with no network, discovered only when someone is waiting on it."""
    next(_get_model().embed(["warm up"]))


def embed_passages(texts: list[str]) -> list[np.ndarray]:
    return [np.asarray(v, dtype=np.float32) for v in _get_model().embed(texts)]


def embed_query(text: str) -> np.ndarray:
    vec = next(_get_model().embed([_QUERY_PREFIX + text]))
    return np.asarray(vec, dtype=np.float32)


def to_bytes(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)
