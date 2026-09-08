# 本文件构建受限 Tool Gateway 容器镜像；以非 root 用户运行最小 FastAPI 服务。
FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY services/api-server/pyproject.toml services/api-server/pyproject.toml
COPY services/agent-server/pyproject.toml services/agent-server/pyproject.toml
COPY services/tool-gateway/pyproject.toml services/tool-gateway/pyproject.toml
COPY packages/sensitive-masker packages/sensitive-masker
RUN uv sync --frozen --no-dev --package travel-agent-tool-gateway --no-install-project

COPY services/tool-gateway/src services/tool-gateway/src
RUN uv sync --frozen --no-dev --package travel-agent-tool-gateway \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin toolgateway \
    && chown -R toolgateway:toolgateway /workspace

ENV PATH="/workspace/.venv/bin:${PATH}"
EXPOSE 8002
USER toolgateway
CMD ["uvicorn", "travel_agent_tool_gateway.main:app", "--host", "0.0.0.0", "--port", "8002"]
