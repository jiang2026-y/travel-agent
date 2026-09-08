# 本文件构建 Agent Server 开发镜像；使用 uv 同步锁定依赖，并启动 travel_agent_agent.main:app。

FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY services/api-server/pyproject.toml services/api-server/pyproject.toml
COPY services/agent-server/pyproject.toml services/agent-server/pyproject.toml
COPY services/tool-gateway/pyproject.toml services/tool-gateway/pyproject.toml
COPY packages/sensitive-masker packages/sensitive-masker
RUN uv sync --frozen --no-dev --package travel-agent-agent --no-install-project

COPY services/agent-server/src services/agent-server/src
RUN uv sync --frozen --no-dev --package travel-agent-agent

ENV PATH="/workspace/.venv/bin:${PATH}"
EXPOSE 8001
CMD ["uvicorn", "travel_agent_agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
