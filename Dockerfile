FROM python:3.11-slim

# Install system dependencies for PyMuPDF
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download SentenceTransformer model to avoid downloading on each start
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy source code
COPY . .

# Set default env vars
ENV PORT=7860
ENV PYTHONPATH=/app

# Expose port (HuggingFace Spaces uses 7860 by default)
EXPOSE 7860

# Run the server
CMD ["python", "-m", "src.mcp_server.server"]
