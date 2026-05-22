FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для pymupdf, Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY assets/ ./assets/

# Volume для SQLite
RUN mkdir -p /app/data

CMD ["python", "-m", "src.bot"]
