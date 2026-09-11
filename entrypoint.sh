#!/bin/sh
set -e

# NAS 上用 bind mount 挂载的 /app/data 常为 root 所有，
# 而主进程以非 root 用户(appuser, uid 1000)运行，无写权限会导致 SQLite 建库失败：
#   sqlite3.OperationalError: unable to open database file
# 启动时若目录存在，先修正所有权，再降权运行主进程。
if [ -d /app/data ]; then
    chown -R appuser:appuser /app/data 2>/dev/null || true
fi

exec setpriv --reuid=1000 --regid=1000 --init-groups python app.py