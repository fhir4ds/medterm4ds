FROM python:3.11-slim

WORKDIR /app

# System deps for DuckDB + faiss
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app + medterm4ds source
COPY app.py .
COPY src/ ./src/
COPY scripts/ ./scripts/

# HF Spaces persistent storage
ENV MEDTERM4DS_DATA_DIR=/data
RUN mkdir -p /data

# HF Spaces listens on 7860
EXPOSE 7860

CMD ["python", "app.py"]
