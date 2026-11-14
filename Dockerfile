# Gardener service + web UI. Model weights and name caches live in the volumes compose.yaml mounts, not in the image.
# In a container on a Mac there is no GPU: analysis runs on the CPU, several times slower than a native install.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy GARDENER_HOME=/data GARDENER_CONTAINER=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY gardener ./gardener
COPY data/names ./data/names
COPY data/avilist ./data/avilist
COPY data/mdd ./data/mdd
COPY data/aesthetic ./data/aesthetic
COPY tests/models ./tests/models
COPY scripts/names ./scripts/names
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
RUN uv sync --frozen --no-dev
EXPOSE 8770
ENTRYPOINT ["sh", "scripts/docker-entrypoint.sh"]
