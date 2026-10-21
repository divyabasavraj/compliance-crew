"""Hybrid retrieval over the RBI corpus: BM25 + dense embeddings, fused with RRF, then reranked."""

import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi

from .ingest import DATA_DIR, Chunk, load_corpus

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "BAAI/bge-reranker-base"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
RRF_K = 60


@dataclass
class Hit:
    chunk: Chunk
    score: float


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class Retriever:
    def __init__(
        self,
        chunks: list[Chunk] | None = None,
        use_reranker: bool = True,
        embed_model: str = EMBED_MODEL,
        rerank_model: str = RERANK_MODEL,
    ):
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self.embed_model = embed_model
        self.chunks = chunks or load_corpus()
        self.by_id = {c.id: c for c in self.chunks}
        self.bm25 = BM25Okapi([_tokenize(c.search_text()) for c in self.chunks])
        self.embedder = SentenceTransformer(embed_model)
        self.doc_vecs = self._load_or_embed()
        self.reranker = CrossEncoder(rerank_model) if use_reranker else None

    def _load_or_embed(self) -> np.ndarray:
        slug = self.embed_model.replace("/", "__")
        path = DATA_DIR / f"embeddings.{slug}.npy"
        ids_path = DATA_DIR / f"embeddings.{slug}.ids"
        ids = [c.id for c in self.chunks]
        if path.exists() and ids_path.exists() and ids_path.read_text().split("\n") == ids:
            return np.load(path)
        vecs = self.embedder.encode([c.search_text() for c in self.chunks], normalize_embeddings=True)
        np.save(path, vecs)
        ids_path.write_text("\n".join(ids))
        return vecs

    def bm25_ranking(self, query: str) -> list[int]:
        return list(np.argsort(-self.bm25.get_scores(_tokenize(query))))

    def dense_ranking(self, query: str) -> list[int]:
        q = self.embedder.encode([QUERY_PREFIX + query], normalize_embeddings=True)[0]
        return list(np.argsort(-(self.doc_vecs @ q)))

    def search(self, query: str, k: int = 6, mode: str = "hybrid_rerank", pool: int = 25) -> list[Hit]:
        """mode: bm25 | dense | hybrid | hybrid_rerank"""
        if mode == "bm25":
            return [Hit(self.chunks[i], 0.0) for i in self.bm25_ranking(query)[:k]]
        if mode == "dense":
            return [Hit(self.chunks[i], 0.0) for i in self.dense_ranking(query)[:k]]

        fused: dict[int, float] = {}
        for ranking in (self.bm25_ranking(query), self.dense_ranking(query)):
            for rank, i in enumerate(ranking[:pool]):
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        candidates = sorted(fused, key=fused.get, reverse=True)[:pool]

        if mode == "hybrid" or self.reranker is None:
            return [Hit(self.chunks[i], fused[i]) for i in candidates[:k]]

        scores = self.reranker.predict([(query, self.chunks[i].search_text()) for i in candidates])
        order = np.argsort(-np.asarray(scores))[:k]
        return [Hit(self.chunks[candidates[j]], float(scores[j])) for j in order]


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    return Retriever()
