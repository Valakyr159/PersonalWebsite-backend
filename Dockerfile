FROM python:3.11-slim

# Install system dependencies for PyMuPDF
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the fastembed ONNX model to avoid downloading it on each start
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# Copy source code
COPY . .

# Set default env vars
ENV PORT=8000
ENV PYTHONPATH=/app

# Expose port (the host platform injects its own PORT at runtime and overrides
# this default — Render, HF Spaces, etc. all set PORT dynamically)
EXPOSE 8000

# Run the server
CMD ["python", "-m", "src.mcp_server.server"]
