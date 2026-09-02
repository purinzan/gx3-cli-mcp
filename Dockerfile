FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app
RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 gx3mcp \
    && chown -R gx3mcp:gx3mcp /app

USER gx3mcp

ENTRYPOINT ["gx3-mcp-server"]
