"""
Stubs out sentence-transformers with a lightweight bag-of-words embedder so
the test suite never downloads the real ~90MB all-MiniLM-L6-v2 model. This
module-level code runs before pytest imports any test file, which is what
lets it intercept `from sentence_transformers import SentenceTransformer`
inside src/mcp_server/session_manager.py.
"""
import sys
import types

import numpy as np

_EMBED_DIM = 32


def _bag_of_words_vector(text: str) -> np.ndarray:
    vector = np.zeros(_EMBED_DIM)
    for word in text.lower().split():
        vector[hash(word) % _EMBED_DIM] += 1
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


class FakeSentenceTransformer:
    """Deterministic stand-in for sentence_transformers.SentenceTransformer."""

    def __init__(self, *_args, **_kwargs):
        pass

    def encode(self, texts, convert_to_numpy: bool = True):
        if isinstance(texts, str):
            return _bag_of_words_vector(texts)
        return np.array([_bag_of_words_vector(t) for t in texts])


fake_module = types.ModuleType("sentence_transformers")
fake_module.SentenceTransformer = FakeSentenceTransformer
sys.modules.setdefault("sentence_transformers", fake_module)
