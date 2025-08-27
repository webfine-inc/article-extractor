FROM python:3.11-slim

# System deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-copy only requirements for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Non-root (optional)
# RUN useradd -ms /bin/bash appuser
# USER appuser

EXPOSE 8080

# Gunicorn config file is included
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
