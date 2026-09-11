# -*- coding: utf-8 -*-
"""优衣库（中国区）尺码与价格监控 · 本地服务

启动：python app.py  （默认 http://127.0.0.1:8811）
"""
import json
import os
import re
import sys
import threading
import time
from typing import List, Optional
import uvicorn
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from collector import Collector
from db import DB
from uq_client import UQClient, UQError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(DATA_DIR, exist_ok=True)

db = DB(os.path.join(DATA_DIR, "monitor.db"))
client = UQClient(min_interval=1.0)
collector = Collector(db, client)

# 手动刷新冷却（防高频请求触发风控）：单商品 30 秒
_collect_lock = threading.Lock()
_last_collect: dict = {}
COLLECT_COOLDOWN = 30

app = FastAPI(title="优衣库尺码库存监控")


# ---------------- 数据模型 ----------------
class AddProductIn(BaseModel):
    code: str


class WatchIn(BaseModel):
    product_code: str
    size: str = ""
    color: str = ""
    low_threshold: int = 2


class SettingsIn(BaseModel):
    stock_interval_min: Optional[int] = None
    price_interval_min: Optional[int] = None
    low_threshold: Optional[int] = None
    notify_enabled: Optional[int] = None
    notify_channel: Optional[str] = None
    ntfy_topic: Optional[str] = None
    ntfy_server: Optional[str] = None
    wecom_webhook: Optional[str] = None
    pp_token: Optional[str] = None
    quiet_hours: Optional[str] = None


class TargetIn(BaseModel):
    target_price: Optional[float] = None


# ---------------- 商品 ----------------
@app.get("/api/products")
def api_products():
    out = []
    for p in db.list_products():
        latest = db.latest_stock(p["product_code"])
        skus = db.list_skus(p["product_code"])
        in_stock = sum(1 for s in skus
                       if (latest.get(s["sku_id"]) or {}).get("express") or 0 > 0)
        out.append({**p, "sku_count": len(skus), "in_stock_count": in_stock,
                    "last_ts": max([v.get("ts", 0) for v in latest.values()] or [0])})
    return out


@app.get("/api/search")
def api_search(q: str):
    """按商品编号或关键词搜索（真实接口）。"""
    if not q.strip():
        return []
    try:
        items = client.search(q.strip(), page_size=20)
    except UQError as e:
        raise HTTPException(502, str(e))
    return [{"code": i.get("code"), "name": i.get("name"),
             "product_code": i.get("productCode"),
             "price": i.get("minPrice"), "origin_price": i.get("originPrice"),
             "gender": i.get("gender4zhCN") or i.get("sex4zhCN")}
            for i in items if i.get("code")]


@app.post("/api/products")
def api_add_product(body: AddProductIn):
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "商品编号为空")
    if db.get_product(code):
        raise HTTPException(409, "该商品已在监控列表中")
    try:
        item = client.resolve(code)
    except UQError as e:
        raise HTTPException(502, str(e))

    # 清理可能残留的旧价格历史，确保走势图从本次监控周期开始
    db.clear_price_history(item.get("code") or code)
    db.upsert_product({
        "code": item.get("code") or code,
        "product_code": item["productCode"],
        "name": item.get("name") or "",
        "origin_price": item.get("originPrice"),
        "cur_price": item.get("minPrice"),
        "store_count": len(item.get("stores") or []),
        "limited_begin": item.get("timeLimitedBegin"),
        "limited_end": item.get("timeLimitedEnd"),
        "main_pic": item.get("mainPic") or None,
        "color_pics": json.dumps(item.get("colorPic") or [], ensure_ascii=False)
            if item.get("colorPic") else None,
    })
    r = collector.collect_product(code)      # 首次采集建立基线
    return {"ok": True, "product": db.get_product(code), "collect": r}


@app.delete("/api/products/{code}")
def api_delete_product(code: str):
    db.delete_product(code)
    return {"ok": True}


@app.post("/api/products/{code}/toggle")
def api_toggle(code: str):
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在")
    db.set_product_enabled(code, 0 if p.get("enabled") else 1)
    return {"ok": True, "enabled": db.get_product(code)["enabled"]}


@app.post("/api/products/{code}/target")
def api_set_target(code: str, t: TargetIn):
    """设置/清除预期价。传 null 清除。"""
    if not db.set_target_price(code, t.target_price):
        raise HTTPException(404, "商品不存在")
    return {"ok": True, "product": db.get_product(code)}


@app.get("/api/products/{code}/matrix")
def api_matrix(code: str):
    """颜色 × 尺码 库存矩阵。同色名多商品码时显示为 颜色(商品码)。"""
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在")
    skus = db.list_skus(p["product_code"])
    latest = db.latest_stock(p["product_code"])

    color_cnt: dict = {}
    for s in skus:
        if s.get("color"):
            color_cnt[s["color"]] = color_cnt.get(s["color"], 0) + 1

    def _disp(s):
        c = s.get("color") or ""
        if c and color_cnt.get(c, 0) > 1 and s.get("sub_code"):
            return "%s(%s)" % (c, s["sub_code"])
        return c

    rows = []
    for s in skus:
        st = latest.get(s["sku_id"]) or {}
        rows.append({**s, "display_color": _disp(s),
                     "express": st.get("express"), "bpl": st.get("bpl"),
                     "dc": st.get("dc"), "transit": st.get("transit"), "ts": st.get("ts")})
    sizes, colors = [], []
    for r in rows:
        if r["size"] and r["size"] not in sizes:
            sizes.append(r["size"])
        if r["display_color"] and r["display_color"] not in colors:
            colors.append(r["display_color"])

    # 色号 -> 色卡图（colorPic 文件名含 COLxx）
    pic_map = {}
    try:
        pics = json.loads(p.get("color_pics") or "[]") or []
    except Exception:
        pics = []
    for pic in pics:
        m = re.search(r"COL(\d+)", pic or "")
        if m:
            pic_map.setdefault("COL" + m.group(1), pic)
    return {"product": p, "rows": rows, "sizes": sizes, "colors": colors,
            "color_pic_map": pic_map}


@app.get("/api/sku/{sku_id}/series")
def api_series(sku_id: str, limit: int = 200):
    return db.stock_series(sku_id, limit)


@app.get("/api/products/{code}/price-series")
def api_price_series(code: str):
    """价格时序（仅价格变化时落点）。"""
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在")
    return {"code": code, "origin_price": p.get("origin_price"),
            "cur_price": p.get("cur_price"), "hist_low_price": p.get("hist_low_price"),
            "target_price": p.get("target_price"),
            "points": db.price_series(code)}


import re as _re

@app.get("/img/{path:path}")
def api_img(path: str):
    """商品图片代理：本地转发官网图片，自动升级到最高清版本。

    官网图片 URL 中 /first/{size}/ 和 /sku/{size}/ 的数字是分辨率档位：
      225 = 480×640（缩略）、561 = 1200×1600（中）、1000 = 2100×2800（高清原图）
    统一替换为 1000 拿原图，浏览器端展示时 CSS 缩放即可。
    """
    path = path.lstrip("/")
    if not path.startswith(("hmall/", "public/", "cms/")):
        raise HTTPException(403, "非法图片路径")
    # 自动升级到高清：/first/N/ → /first/1000/，/sku/N/ → /sku/1000/
    path = _re.sub(r"/(first|sku)/\d+/", r"/\1/1000/", path)
    try:
        r = httpx.get("https://www.uniqlo.cn/" + path, timeout=20,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                               "Referer": "https://www.uniqlo.cn/",
                               "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"})
    except Exception:                       # noqa: BLE001
        raise HTTPException(502, "图片获取失败")
    if r.status_code != 200:
        raise HTTPException(404, "图片不存在")
    return Response(content=r.content,
                    media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------------- 订阅 ----------------
@app.get("/api/watches")
def api_watches():
    return db.list_watches()


@app.post("/api/watches")
def api_add_watch(w: WatchIn):
    db.add_watch(w.product_code, w.size, w.color, w.low_threshold)
    return {"ok": True}


@app.delete("/api/watches/{wid}")
def api_del_watch(wid: int):
    db.remove_watch(wid)
    return {"ok": True}


# ---------------- 事件 / 日志 ----------------
@app.get("/api/events")
def api_events(limit: int = 100):
    return db.list_events(limit)


@app.get("/api/logs")
def api_logs(limit: int = 20):
    return db.list_logs(limit)


# ---------------- 采集 ----------------
@app.post("/api/collect")
def api_collect_all():
    return collector.collect_all()


@app.post("/api/collect/{code}")
def api_collect_one(code: str):
    with _collect_lock:
        now = time.time()
        last = _last_collect.get(code, 0)
        if now - last < COLLECT_COOLDOWN:
            raise HTTPException(429, "刷新过于频繁，请 %d 秒后再试"
                                      % (int(COLLECT_COOLDOWN - (now - last)) + 1))
        _last_collect[code] = now
    return collector.collect_product(code)


# ---------------- 设置 ----------------
@app.get("/api/settings")
def api_get_settings():
    return db.all_settings()


@app.post("/api/settings")
def api_set_settings(s: SettingsIn):
    for k, v in s.dict(exclude_none=True).items():
        db.set_setting(k, v)
    return db.all_settings()


# ---------------- 前端 ----------------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------------- 后台调度 ----------------
def _scheduler():
    while True:
        try:
            interval = max(5, int(db.all_settings().get("stock_interval_min") or 30))
        except Exception:
            interval = 30
        time.sleep(interval * 60)
        try:
            if [p for p in db.list_products() if p.get("enabled")]:
                collector.collect_all()
        except Exception as e:                       # noqa: BLE001
            db.add_log(0, "定时采集异常: %s" % e)


def main():
    host = os.environ.get("UQ_HOST", "127.0.0.1")   # 容器部署设为 0.0.0.0
    port = int(os.environ.get("UQ_PORT", "8811"))
    t = threading.Thread(target=_scheduler, daemon=True)
    t.start()
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("优衣库监控已启动: http://%s:%d（监听 %s）" % (shown, port, host))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
