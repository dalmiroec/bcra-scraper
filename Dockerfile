FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY scraper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper/ .

RUN mkdir -p /app/data /app/logs

CMD ["python", "bcra_scraper.py"]
