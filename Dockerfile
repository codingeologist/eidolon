FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package with the API extra (fastapi, uvicorn, psycopg-pool).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api]"

# index.html is served from "/" by the app; it is not part of the installed package.
COPY index.html /app/index.html

ENV INDEX_HTML=/app/index.html \
    HOST=0.0.0.0 \
    PORT=8000

RUN useradd --system --no-create-home app
USER app

EXPOSE 8000
CMD ["eidolon-api"]
