"""Embedding backends (local e5 or Gemini) and the Chroma vector store."""
from typing import Sequence

from backend import config
from backend.dsa import LRUCache
from backend.utils import get_logger

log = get_logger("embeddings")
_model = None
_query_cache = LRUCache(config.CACHE_SIZE)


def _local_model():
    """Load the sentence-transformers model once. First run downloads ~470 MB."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model (first time can take a few minutes)...")
        _model = SentenceTransformer(config.LOCAL_EMBED_MODEL)
    return _model


def _gemini_embed(texts: Sequence[str], task: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Embed with Gemini (needs API key). Verify the model name in Google docs.
    Sends batches of 50 (the API allows up to 100 per call). `task` tells Gemini if the text is a document or a query."""
    from google.genai import types
    from backend.llm import get_client, _call_with_retry
    client, out = get_client(), []
    for i in range(0, len(texts), 50):
        batch = list(texts[i:i + 50])
        res = _call_with_retry(lambda: client.models.embed_content(
            model=config.GEMINI_EMBED_MODEL, contents=batch, config=types.EmbedContentConfig(task_type=task)))
        out.extend(e.values for e in res.embeddings)
    return out


def embed_passages(texts: Sequence[str], backend: str = "local") -> list[list[float]]:
    """Embed documents. e5 needs the 'passage: ' prefix."""
    if backend == "gemini":
        return _gemini_embed(texts)
    vecs = _local_model().encode([f"passage: {t}" for t in texts], normalize_embeddings=True,
                                 batch_size=32, show_progress_bar=False)
    return vecs.tolist()


def embed_query(text: str, backend: str = "local") -> list[float]:
    """Embed a question (cached). e5 needs the 'query: ' prefix."""
    key = (backend, text)
    hit = _query_cache.get(key)
    if hit is not None:
        return hit
    vec = (_gemini_embed([text], "RETRIEVAL_QUERY")[0] if backend == "gemini" else
           _local_model().encode(f"query: {text}", normalize_embeddings=True).tolist())
    _query_cache.put(key, vec)
    return vec


class VectorStore:
    """Thin wrapper over a persistent ChromaDB. One collection per (chunk config, backend)."""

    def __init__(self) -> None:
        import chromadb
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))

    def collection(self, name: str):
        """Get or create a cosine-distance collection."""
        return self.client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    def add(self, name: str, ids: list[str], vecs: list[list[float]], docs: list[str], metas: list[dict]) -> None:
        self.collection(name).add(ids=ids, embeddings=vecs, documents=docs, metadatas=metas)

    def query(self, name: str, vec: list[float], notebook_id: str, n: int) -> list[tuple[str, float, list[float]]]:
        """Return (chunk_id, similarity, embedding) for the nearest chunks of one notebook."""
        col = self.collection(name)
        if col.count() == 0:
            return []
        res = col.query(query_embeddings=[vec], n_results=min(n, col.count()),
                        where={"notebook_id": notebook_id}, include=["distances", "embeddings"])
        return [(i, 1 - d, e) for i, d, e in zip(res["ids"][0], res["distances"][0], res["embeddings"][0])]

    def delete_source(self, source_id: str) -> None:
        """Delete all vectors of a source from every collection."""
        for col in self.client.list_collections():
            name = col if isinstance(col, str) else col.name
            self.client.get_collection(name).delete(where={"source_id": source_id})

    def drop(self, name: str) -> None:
        """Delete a whole collection (used for temporary Lab runs)."""
        try:
            self.client.delete_collection(name)
        except ValueError:
            pass


_store: VectorStore | None = None


def store() -> VectorStore:
    """Get the shared vector store."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
