from types import SimpleNamespace

import pytest

from src.mcp_server import rag_tools
from src.mcp_server.session_manager import session_manager


def _fake_groq_response(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture(autouse=True)
def clean_session():
    yield
    session_manager.clear_session("rag-test-session")


def test_generate_rag_response_calls_groq_with_context_and_returns_reply(monkeypatch):
    session_manager.add_document(
        "rag-test-session", ["Python is a programming language."], "doc.pdf"
    )

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_groq_response("Python is used for scripting and AI.")

    monkeypatch.setattr(rag_tools.client.chat.completions, "create", fake_create)

    reply = rag_tools.generate_rag_response("rag-test-session", "What is Python?")

    assert reply == "Python is used for scripting and AI."
    assert captured["model"] == "llama-3.1-8b-instant"
    system_message = captured["messages"][0]
    assert system_message["role"] == "system"
    assert "Python is a programming language." in system_message["content"]


def test_generate_rag_response_appends_to_session_history(monkeypatch):
    monkeypatch.setattr(
        rag_tools.client.chat.completions,
        "create",
        lambda **_kwargs: _fake_groq_response("some answer"),
    )

    rag_tools.generate_rag_response("rag-test-session", "a question")

    history = session_manager.get_session("rag-test-session").history
    assert history[-2] == {"role": "user", "content": "a question"}
    assert history[-1] == {"role": "assistant", "content": "some answer"}


def test_generate_rag_response_returns_friendly_message_on_groq_error(monkeypatch):
    def raise_error(**_kwargs):
        raise RuntimeError("Groq is down")

    monkeypatch.setattr(rag_tools.client.chat.completions, "create", raise_error)

    reply = rag_tools.generate_rag_response("rag-test-session", "a question")

    assert "error" in reply.lower()
