import os
from dotenv import load_dotenv

# Load env before other imports
load_dotenv()

from typing import Any
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware.cors import CORSMiddleware
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

from .pdf_tools import extract_text_from_pdf_base64, chunk_text, MAX_PDF_SIZE_MB
from .session_manager import session_manager
from .rag_tools import generate_rag_response

# Initialize MCP Server
app = Server("portfolio-mcp-server")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="upload_pdf",
            description="Uploads a PDF for a specific session to be used as context",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "pdf_base64": {"type": "string"},
                    "filename": {"type": "string"}
                },
                "required": ["session_id", "pdf_base64", "filename"]
            }
        ),
        Tool(
            name="query_rag",
            description="Query the RAG chatbot using the uploaded PDF context",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "query": {"type": "string"}
                },
                "required": ["session_id", "query"]
            }
        ),
        Tool(
            name="clear_session",
            description="Clears the session data",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"}
                },
                "required": ["session_id"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    if not arguments:
        raise ValueError("Missing arguments")
        
    session_id = arguments.get("session_id")
    if not session_id:
        raise ValueError("session_id is required")

    if name == "upload_pdf":
        pdf_base64 = arguments.get("pdf_base64")
        filename = arguments.get("filename")
        if not pdf_base64 or not filename:
            raise ValueError("pdf_base64 and filename are required")
            
        pdf_size_mb = len(pdf_base64) * 3 / 4 / (1024 * 1024)
        if pdf_size_mb > MAX_PDF_SIZE_MB:
            return [TextContent(type="text", text=f"El PDF supera el límite de {MAX_PDF_SIZE_MB}MB.")]

        try:
            text = extract_text_from_pdf_base64(pdf_base64)
            chunks = chunk_text(text)
            session_manager.add_document(session_id, chunks, filename)
            return [TextContent(type="text", text=f"PDF {filename} processed successfully. {len(chunks)} chunks created.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error parsing PDF: {str(e)}")]

    elif name == "query_rag":
        query = arguments.get("query")
        if not query:
            raise ValueError("query is required")
            
        try:
            response = generate_rag_response(session_id, query)
            return [TextContent(type="text", text=response)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error generating response: {str(e)}")]

    elif name == "clear_session":
        session_manager.clear_session(session_id)
        return [TextContent(type="text", text=f"Session {session_id} cleared.")]
        
    else:
        raise ValueError(f"Unknown tool: {name}")

# Global transport instance
sse = SseServerTransport("/messages")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())

async def handle_messages(request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

async def handle_health(request):
    return JSONResponse({"status": "ok", "sessions": len(session_manager.sessions)})

# Starlette app
starlette_app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health),
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"])
    ]
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "https://valakyr159.github.io").split(",")
    if origin.strip()
]

starlette_app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting MCP Server on port {port}")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)
