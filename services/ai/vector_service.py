"""
TeamPulse - Vector Embedding Service
=========================================
Semantic search using sentence-transformers + FAISS.
Degrades gracefully if faiss-cpu is not installed.
"""

import os
import json
import pickle
import numpy as np
from pathlib import Path
from loguru import logger
from typing import List, Dict, Optional

VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./vector_store")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# Check availability at import time
try:
    import faiss as _faiss_test
    FAISS_AVAILABLE = True
    logger.info("✅ faiss-cpu available")
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("⚠️  faiss-cpu not installed. Vector search disabled. Run: pip install faiss-cpu")

try:
    from sentence_transformers import SentenceTransformer as _st_test
    ST_AVAILABLE = True
    logger.info("✅ sentence-transformers available")
except ImportError:
    ST_AVAILABLE = False
    logger.warning("⚠️  sentence-transformers not installed. Semantic search disabled.")


class VectorSearchService:
    """
    FAISS vector store with per-team isolation and disk persistence.
    Falls back to keyword search if faiss/sentence-transformers unavailable.
    """

    def __init__(self):
        self.model = None
        self.stores: Dict[str, dict] = {}
        self._ensure_dir()

    def _ensure_dir(self):
        Path(VECTOR_DB_PATH).mkdir(parents=True, exist_ok=True)

    def is_available(self) -> bool:
        return FAISS_AVAILABLE and ST_AVAILABLE

    def _get_model(self):
        if self.model is None:
            if not ST_AVAILABLE:
                raise RuntimeError("sentence-transformers not installed")
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
            self.model = SentenceTransformer(EMBEDDING_MODEL)
            logger.info("✅ Embedding model loaded")
        return self.model

    def _get_store_path(self, team_id: str):
        return Path(VECTOR_DB_PATH) / f"team_{team_id}"

    def _load_store(self, team_id: str):
        if team_id in self.stores:
            return self.stores[team_id]

        store_path = self._get_store_path(team_id)
        index_file = store_path / "index.faiss"
        meta_file = store_path / "metadata.pkl"

        if FAISS_AVAILABLE and index_file.exists() and meta_file.exists():
            try:
                import faiss
                index = faiss.read_index(str(index_file))
                with open(meta_file, "rb") as f:
                    metadata = pickle.load(f)
                self.stores[team_id] = {"index": index, "metadata": metadata}
                logger.info(f"Loaded vector store for team {team_id}: {index.ntotal} vectors")
            except Exception as e:
                logger.error(f"Failed to load vector store: {e}")
                self.stores[team_id] = {"index": None, "metadata": []}
        else:
            self.stores[team_id] = {"index": None, "metadata": []}

        return self.stores[team_id]

    def _save_store(self, team_id: str):
        if not FAISS_AVAILABLE:
            return
        store = self.stores.get(team_id)
        if not store or not store.get("index"):
            return
        store_path = self._get_store_path(team_id)
        store_path.mkdir(parents=True, exist_ok=True)
        try:
            import faiss
            faiss.write_index(store["index"], str(store_path / "index.faiss"))
            with open(store_path / "metadata.pkl", "wb") as f:
                pickle.dump(store["metadata"], f)
        except Exception as e:
            logger.error(f"Failed to save vector store: {e}")

    def add_document(self, team_id: str, doc_id: str, title: str, content: str, doc_type: str = "document") -> bool:
        if not self.is_available():
            logger.warning("Vector store unavailable — document not indexed for semantic search")
            return False

        try:
            import faiss
            model = self._get_model()
            chunks = self._chunk_text(content)
            if not chunks:
                chunks = [content[:512]]

            embeddings = model.encode(chunks, show_progress_bar=False)
            embeddings = np.array(embeddings, dtype=np.float32)
            store = self._load_store(team_id)

            if store["index"] is None:
                dim = embeddings.shape[1]
                store["index"] = faiss.IndexFlatIP(dim)

            faiss.normalize_L2(embeddings)
            start_id = len(store["metadata"])
            store["index"].add(embeddings)

            for i, chunk in enumerate(chunks):
                store["metadata"].append({
                    "doc_id": doc_id,
                    "chunk_id": start_id + i,
                    "title": title,
                    "content": chunk,
                    "doc_type": doc_type
                })

            self._save_store(team_id)
            logger.info(f"✅ Indexed '{title}' ({len(chunks)} chunks)")
            return True

        except Exception as e:
            logger.error(f"Failed to add document: {e}")
            return False

    def search(self, team_id: str, query: str, top_k: int = 5) -> List[Dict]:
        if not self.is_available():
            # Fallback: keyword search over stored metadata
            return self._keyword_search(team_id, query, top_k)

        try:
            import faiss
            model = self._get_model()
            store = self._load_store(team_id)

            if store["index"] is None or store["index"].ntotal == 0:
                return []

            query_embedding = model.encode([query], show_progress_bar=False)
            query_embedding = np.array(query_embedding, dtype=np.float32)
            faiss.normalize_L2(query_embedding)

            scores, indices = store["index"].search(query_embedding, min(top_k * 2, store["index"].ntotal))

            results = []
            seen_docs = set()
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(store["metadata"]):
                    continue
                meta = store["metadata"][idx]
                doc_id = meta["doc_id"]
                if doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
                results.append({
                    "doc_id": doc_id,
                    "title": meta["title"],
                    "content": meta["content"],
                    "doc_type": meta["doc_type"],
                    "score": float(score)
                })
                if len(results) >= top_k:
                    break

            return results

        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return self._keyword_search(team_id, query, top_k)

    def _keyword_search(self, team_id: str, query: str, top_k: int) -> List[Dict]:
        """Simple keyword fallback when FAISS unavailable."""
        store = self._load_store(team_id)
        if not store["metadata"]:
            return []

        query_words = set(query.lower().split())
        scored = []
        seen = set()
        for meta in store["metadata"]:
            doc_id = meta["doc_id"]
            if doc_id in seen:
                continue
            seen.add(doc_id)
            content_words = set(meta["content"].lower().split())
            score = len(query_words & content_words) / max(len(query_words), 1)
            if score > 0:
                scored.append({**meta, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def remove_document(self, team_id: str, doc_id: str):
        store = self._load_store(team_id)
        store["metadata"] = [m for m in store["metadata"] if m["doc_id"] != doc_id]
        logger.info(f"Removed doc {doc_id} from team {team_id}")

    def _chunk_text(self, text: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
        words = text.split()
        if len(words) <= chunk_size:
            return [text]
        chunks = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunks.append(" ".join(words[start:end]))
            if end == len(words):
                break
            start += chunk_size - overlap
        return chunks

    def get_stats(self, team_id: str) -> dict:
        store = self._load_store(team_id)
        return {
            "total_vectors": store["index"].ntotal if store.get("index") and FAISS_AVAILABLE else 0,
            "total_chunks": len(store["metadata"]),
            "unique_documents": len(set(m["doc_id"] for m in store["metadata"])),
            "faiss_available": FAISS_AVAILABLE,
            "embeddings_available": ST_AVAILABLE,
        }


vector_service = VectorSearchService()
