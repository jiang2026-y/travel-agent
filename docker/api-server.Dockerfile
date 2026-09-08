# 本文件构建 API Server 开发镜像；使用 uv 同步锁定依赖，并启动 travel_agent_api.main:app。

FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY services/api-server/pyproject.toml services/api-server/pyproject.toml
COPY services/agent-server/pyproject.toml services/agent-server/pyproject.toml
COPY services/tool-gateway/pyproject.toml services/tool-gateway/pyproject.toml
COPY packages/sensitive-masker packages/sensitive-masker
COPY migrations/api-server migrations/api-server
RUN uv sync --frozen --no-dev --package travel-agent-api --no-install-project

COPY services/api-server/src services/api-server/src
COPY scripts/manage_users.py scripts/manage_users.py
RUN uv sync --frozen --no-dev --package travel-agent-api

ENV PATH="/workspace/.venv/bin:${PATH}"
EXPOSE 8000
CMD ["uvicorn", "travel_agent_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
