"""
Imports and exercises the actual Starlette app in server.py.

This exists because a bare `mcp[cli]>=1.0.0` pin silently picked up mcp 2.0.0
in production, whose low-level Server class dropped the @app.list_tools() /
@app.call_tool() decorator API this module is written against — server.py
raised AttributeError on import and the container OOM'd during the (broken)
startup. None of the other test files import server.py at all, so CI stayed
green while the actual entrypoint was broken. Keep this test importing
server.py for real, not just session_manager/rag_tools/pdf_tools in
isolation.
"""
import os

os.environ.setdefault("GROQ_API_KEY", "test-key")

from starlette.testclient import TestClient

from src.mcp_server import server


def test_server_module_imports_and_registers_routes():
    paths = {route.path for route in server.starlette_app.routes}
    assert paths == {"/health", "/sse", "/messages", "/genshin/profile/{uid}", "/genshin/meta", "/genshin/chat"}


def test_health_endpoint_returns_ok():
    client = TestClient(server.starlette_app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "sessions" in body


def test_cors_allows_the_configured_origin():
    # allowed_origins is read from ALLOWED_ORIGINS at import time, so this
    # only re-checks the default baked in when the module was imported above.
    assert "https://valakyr159.github.io" in server.allowed_origins
