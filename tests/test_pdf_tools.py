import base64

import fitz  # PyMuPDF
import pytest

from src.mcp_server.pdf_tools import chunk_text, extract_text_from_pdf_base64


def _make_pdf_base64(text: str) -> str:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return base64.b64encode(pdf_bytes).decode("utf-8")


def test_extract_text_from_pdf_base64_reads_the_page_content():
    pdf_base64 = _make_pdf_base64("Hello from a test PDF")
    text = extract_text_from_pdf_base64(pdf_base64)
    assert "Hello from a test PDF" in text


def test_chunk_text_splits_long_text_with_overlap():
    text = "x" * 2500
    chunks = chunk_text(text, chunk_size=1000, overlap=200)

    assert len(chunks) == 4
    assert all(len(c) <= 1000 for c in chunks)
    # consecutive chunks overlap by `overlap` characters
    assert chunks[0][-200:] == chunks[1][:200]


def test_chunk_text_returns_single_chunk_for_short_text():
    chunks = chunk_text("short text", chunk_size=1000, overlap=200)
    assert chunks == ["short text"]


def test_chunk_text_empty_string_returns_no_chunks():
    assert chunk_text("", chunk_size=1000, overlap=200) == []
