import time
import numpy as np
from typing import Dict, List, Any
from fastembed import TextEmbedding

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # ONNX Runtime, ~130MB — no PyTorch, fits Render's free 512MB tier

class SessionData:
    def __init__(self):
        self.chunks: List[str] = []
        self.embeddings: np.ndarray | None = None
        self.filename: str = ""
        self.last_accessed: float = time.time()
        self.history: List[Dict[str, str]] = []

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, SessionData] = {}
        # Pre-load embedding model
        # threads=1: onnxruntime otherwise sizes its thread pool/memory arena to
        # os.cpu_count(), which inflates RSS well past Render's free 512MB tier
        # on hosts that report many cores.
        self.embedder = TextEmbedding(model_name=EMBEDDING_MODEL, threads=1)
        self.ttl = 3600  # 1 hour TTL

    def _embed(self, texts: List[str]) -> np.ndarray:
        return np.array(list(self.embedder.embed(texts)))

    def get_session(self, session_id: str) -> SessionData:
        self._cleanup()
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionData()
        self.sessions[session_id].last_accessed = time.time()
        return self.sessions[session_id]

    def clear_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def add_document(self, session_id: str, chunks: List[str], filename: str):
        session = self.get_session(session_id)
        session.chunks = chunks
        session.filename = filename
        
        # Compute embeddings for all chunks
        if chunks:
            session.embeddings = self._embed(chunks)
        else:
            session.embeddings = None

    def retrieve_context(self, session_id: str, query: str, top_k: int = 3) -> str:
        session = self.get_session(session_id)
        if not session.chunks or session.embeddings is None:
            return ""

        query_embedding = self._embed([query])[0]
        
        # Compute cosine similarity
        similarities = np.dot(session.embeddings, query_embedding) / (
            np.linalg.norm(session.embeddings, axis=1) * np.linalg.norm(query_embedding)
        )
        
        # Get top K indices
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        
        # Extract corresponding chunks
        context_chunks = [session.chunks[i] for i in top_indices if similarities[i] > 0.1]
        
        return "\n\n---\n\n".join(context_chunks)

    def _cleanup(self):
        now = time.time()
        expired = [sid for sid, sess in self.sessions.items() if now - sess.last_accessed > self.ttl]
        for sid in expired:
            del self.sessions[sid]

# Global singleton
session_manager = SessionManager()
