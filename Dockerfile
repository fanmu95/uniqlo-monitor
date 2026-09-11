FROM python:3.13-slim

# 时区必须显式设置：slim 镜像默认 UTC，会造成免打扰时段（23:00-08:00）判断错误、日志时间偏 8 小时
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UQ_HOST=0.0.0.0 \
    UQ_PORT=8811

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py db.py collector.py notifier.py uq_client.py ./
COPY static/ ./static/

# 数据目录（挂卷持久化）；以非 root 运行
RUN mkdir -p /app/data \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app

# 启动入口：以 root 修正 bind mount 卷权限（NAS 宿主机目录常为 root 所有，
# 导致非 root 用户无法写 SQLite -> "unable to open database file"），再降权运行
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER root

EXPOSE 8811
VOLUME ["/app/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8811/api/products', timeout=4).status==200 else 1)"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "app.py"]