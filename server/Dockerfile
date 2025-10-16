FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN uv pip install --system -e .

COPY src/ ./src/

COPY .env* ./

RUN mkdir -p src/data

EXPOSE 5000

CMD ["python", "-m", "src.main"]
