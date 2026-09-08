# 本文件构建 Web 开发镜像；启用固定 pnpm 版本并启动 Vite 开发服务器。

FROM node:20.20.2-alpine

WORKDIR /workspace
RUN corepack enable && corepack prepare pnpm@10.34.5 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @travel-agent/web...

COPY apps/web apps/web
EXPOSE 5173
CMD ["pnpm", "--filter", "@travel-agent/web", "dev", "--host", "0.0.0.0"]
