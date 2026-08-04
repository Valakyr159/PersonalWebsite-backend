import time
import numpy as np
from typing import Dict, List, Any
from sentence_transformers import SentenceTransformer

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
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
        self.ttl = 3600  # 1 hour TTL

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
            embeddings = self.embedder.encode(chunks, convert_to_numpy=True)
            session.embeddings = embeddings
        else:
            session.embeddings = None

    def retrieve_context(self, session_id: str, query: str, top_k: int = 3) -> str:
        session = self.get_session(session_id)
        if not session.chunks or session.embeddings is None:
            return ""

        query_embedding = self.embedder.encode(query, convert_to_numpy=True)
        
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
