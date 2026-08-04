import base64
import os
import fitz  # PyMuPDF
from typing import List

MAX_PDF_SIZE_MB = int(os.getenv("MAX_PDF_SIZE_MB", "20"))

def extract_text_from_pdf_base64(pdf_base64: str) -> str:
    pdf_bytes = base64.b64decode(pdf_base64)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text() + "\n\n"
    return text

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
        
    return chunks
