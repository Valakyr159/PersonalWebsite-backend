"""
Stubs out fastembed with a lightweight bag-of-words embedder so the test
suite never downloads the real ONNX model or spins up onnxruntime. This
module-level code runs before pytest imports any test file, which is what
lets it intercept `from fastembed import TextEmbedding` inside
src/mcp_server/session_manager.py.
"""
import sys
import types
import zlib

import numpy as np

_EMBED_DIM = 32


def _bag_of_words_vector(text: str) -> np.ndarray:
    vector = np.zeros(_EMBED_DIM)
    for word in text.lower().split():
        # crc32, not hash(): str hashes are randomized per process, which made retrieval tests flaky.
        vector[zlib.crc32(word.encode()) % _EMBED_DIM] += 1
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


class FakeTextEmbedding:
    """Deterministic stand-in for fastembed.TextEmbedding."""

    def __init__(self, *_args, **_kwargs):
        pass

    def embed(self, texts):
        for text in texts:
            yield _bag_of_words_vector(text)


fake_module = types.ModuleType("fastembed")
fake_module.TextEmbedding = FakeTextEmbedding
sys.modules.setdefault("fastembed", fake_module)
