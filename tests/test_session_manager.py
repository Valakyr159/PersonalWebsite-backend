import time

from src.mcp_server.session_manager import SessionManager


def test_get_session_creates_a_new_session_on_first_access():
    manager = SessionManager()
    session = manager.get_session("session-1")

    assert session.chunks == []
    assert session.embeddings is None
    assert "session-1" in manager.sessions


def test_get_session_returns_the_same_session_on_repeat_access():
    manager = SessionManager()
    first = manager.get_session("session-1")
    second = manager.get_session("session-1")

    assert first is second


def test_clear_session_removes_the_session():
    manager = SessionManager()
    manager.get_session("session-1")

    manager.clear_session("session-1")

    assert "session-1" not in manager.sessions


def test_clear_session_on_unknown_id_does_not_raise():
    manager = SessionManager()
    manager.clear_session("does-not-exist")  # should not raise


def test_add_document_computes_embeddings_for_each_chunk():
    manager = SessionManager()
    chunks = ["The cat sat on the mat", "Quantum physics explains particles"]

    manager.add_document("session-1", chunks, "doc.pdf")
    session = manager.sessions["session-1"]

    assert session.filename == "doc.pdf"
    assert session.chunks == chunks
    assert session.embeddings.shape[0] == 2


def test_retrieve_context_returns_the_most_relevant_chunk():
    manager = SessionManager()
    chunks = [
        "The cat sat on the mat and looked at the dog",
        "Quantum physics explains the behavior of subatomic particles",
    ]
    manager.add_document("session-1", chunks, "doc.pdf")

    context = manager.retrieve_context("session-1", "Tell me about the cat", top_k=1)

    assert context == chunks[0]


def test_retrieve_context_with_no_document_returns_empty_string():
    manager = SessionManager()
    manager.get_session("session-1")

    assert manager.retrieve_context("session-1", "anything") == ""


def test_cleanup_evicts_sessions_past_the_ttl():
    manager = SessionManager()
    manager.ttl = 0.05
    manager.get_session("session-1")

    time.sleep(0.1)
    manager.get_session("session-2")  # triggers _cleanup() as a side effect

    assert "session-1" not in manager.sessions
    assert "session-2" in manager.sessions
