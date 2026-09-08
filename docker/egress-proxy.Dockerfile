# 本文件构建默认拒绝的 Egress Proxy 容器镜像。
# 安装 Squid、复制默认拒绝配置并以非 root 用户启动代理。
FROM alpine:3.21

RUN apk add --no-cache squid
COPY docker/egress-proxy/squid.conf /etc/squid/squid.conf

RUN mkdir -p /var/cache/squid /var/log/squid \
    && chown -R squid:squid /var/cache/squid /var/log/squid /etc/squid

USER squid
EXPOSE 3128
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
