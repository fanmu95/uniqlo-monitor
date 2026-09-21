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

db = DB(os.environ.get("UQ_DB") or os.path.join(DATA_DIR, "monitor.db"))
client = UQClient(min_interval=1.0)
collector = Collector(db, client)

# 手动刷新冷却（防高频请求触发风控）：单商品 30 秒
_collect_lock = threading.Lock()
_last_collect: dict = {}
_pull_once: dict = {}
COLLECT_COOLDOWN = 30

# 商品库扫描状态（同一时刻只允许一个扫描在跑）
_sweep_lock = threading.Lock()
_sweep_state: dict = {"running": False, "started_ts": 0, "last": None}


def _run_sweep(scope: str = "ALL", with_new: bool = True, with_categories: bool = True) -> dict:
    """全站扫描 + 新品榜 + 分类树（串行，受 _sweep_lock 保护）。"""
    with _sweep_lock:
        if _sweep_state.get("running"):
            return {"ok": False, "message": "扫描进行中"}
        _sweep_state.update(running=True, started_ts=int(time.time()))
    try:
        r = collector.sweep_catalog(scope=scope)
        if with_new:
            collector.sweep_new_arrivals()
        if with_categories:
            collector.sync_categories()
        _sweep_state["last"] = r
        return r
    finally:
        _sweep_state["running"] = False

app = FastAPI(title="优衣库商城 · 商品库与价格监控")


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
    catalog_sweep_min: Optional[int] = None
    catalog_keep_days: Optional[int] = None
    catalog_hide_days: Optional[int] = None
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


class SubscribeIn(BaseModel):
    code: str
    target_price: Optional[float] = None
    size: str = ""            # 空 = 整商品订阅（仅价格）
    color: str = ""


class CatalogSweepIn(BaseModel):
    scope: str = "ALL"


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

IMG_SIZES = {"225": "225", "561": "561", "1000": "1000"}


@app.get("/img/{path:path}")
def api_img(path: str, w: str = "1000"):
    """商品图片代理：本地转发官网图片，可选分辨率档位。

    官网图片 URL 中 /first/{size}/ 和 /sku/{size}/ 的数字是分辨率档位：
      225 = 480×640（缩略）、561 = 1200×1600（中）、1000 = 2100×2800（高清原图）
    w 传 225/561/1000 指定档位；不传默认 1000（原图，供详情大图）。
    """
    path = path.lstrip("/")
    if not path.startswith(("hmall/", "public/", "cms/")):
        raise HTTPException(403, "非法图片路径")
    size = IMG_SIZES.get(str(w).strip(), "1000")
    path = _re.sub(r"/(first|sku)/\d+/", lambda m: "/%s/%s/" % (m.group(1), size), path)
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


# ---------------- 商城：分类 / 商品库 ----------------
def _decode_json_cols(p: dict, cols=("color_pics", "chip_pics", "color_nos", "sizes",
                                     "identity", "style_text")) -> dict:
    for c in cols:
        try:
            p[c] = json.loads(p.get(c) or "[]")
        except Exception:
            p[c] = []
    return p


def _color_options(p: dict) -> list:
    """色号 -> {no, name, pic, chip}。名称来自 styleText（如 486980/32深米色）。"""
    names = {}
    for st in p.get("style_text") or []:
        m = re.match(r"^(\d{6})/(\d{2,3})(.+)$", (st or "").strip())
        if m:
            names.setdefault("COL" + m.group(2).zfill(2), m.group(3).strip())
    pics, chips = {}, {}
    for pic in p.get("color_pics") or []:
        m = re.search(r"COL(\d+)", pic or "")
        if m:
            pics.setdefault("COL" + m.group(1), pic)
    for pic in p.get("chip_pics") or []:
        m = re.search(r"COL(\d+)", pic or "")
        if m:
            chips.setdefault("COL" + m.group(1), pic)
    nos = p.get("color_nos") or []
    out = []
    for no in nos:
        key = no if str(no).startswith("COL") else "COL" + str(no).zfill(2)
        out.append({"no": key, "name": names.get(key, ""),
                    "pic": pics.get(key, ""), "chip": chips.get(key, "")})
    if not out:                                  # 无 colorNums 时回退到图片文件名
        for key, pic in pics.items():
            out.append({"no": key, "name": names.get(key, ""), "pic": pic,
                        "chip": chips.get(key, "")})
    return out


@app.get("/api/categories")
def api_categories():
    """官方分类树（含本地商品数）。数据来自官网筛选面，未抓取时为空。"""
    cats = db.list_categories()
    counts = db.category_counts()
    nodes = [{**c, "count": counts.get(c["code"], 0)} for c in cats]
    by_parent: dict = {}
    for n in nodes:
        by_parent.setdefault(n.get("parent_code") or "", []).append(n)
    return {"nodes": nodes, "counts": counts,
            "roots": by_parent.get("", []), "children": by_parent}


@app.get("/api/catalog")
def api_catalog(q: str = "", category: str = "", sort: str = "overall",
                page: int = 1, page_size: int = 40, all_days: int = 0):
    """商城商品列表（本地商品库）。sort: overall/newest/new/priceAsc/priceDesc/discount"""
    hide_days = None if all_days else int(db.get_setting("catalog_hide_days", 3) or 3)
    r = db.list_catalog(category=category or None, q=q.strip() or None, sort=sort,
                        page=page, page_size=page_size, hide_days=hide_days)
    r["category"] = category or "ALL"
    r["sort"] = sort
    return r


@app.get("/api/catalog/stats")
def api_catalog_stats():
    st = db.catalog_stats()
    st["sweep"] = {"running": _sweep_state.get("running", False),
                   "started_ts": _sweep_state.get("started_ts", 0)}
    st["sweeps"] = db.list_sweeps(3)
    return st


@app.get("/api/catalog/{code}")
def api_catalog_item(code: str):
    """商品详情：商品库字段 + 色卡 + 价格历史（全量监控商品另附 SKU 库存）。"""
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在")
    p = _decode_json_cols(dict(p))
    skus = db.list_skus(p["product_code"])
    latest = db.latest_stock(p["product_code"]) if skus else {}
    subscribed = any(w.get("product_code") == p["product_code"]
                     for w in db.list_watches())
    return {
        "product": p,
        "colors": _color_options(p),
        "skus": [dict(s, **{"express": (latest.get(s["sku_id"]) or {}).get("express")})
                 for s in skus],
        "subscribed": subscribed,
        "price_series": db.price_series(code),
        "has_stock_detail": bool(skus),
    }


@app.post("/api/catalog/sweep")
def api_catalog_sweep(body: CatalogSweepIn):
    """手动触发商品库扫描（后台线程，约 1–2 分钟）。"""
    if _sweep_state.get("running"):
        return {"ok": False, "message": "扫描正在进行中"}
    threading.Thread(target=_run_sweep, kwargs={"scope": body.scope},
                     daemon=True).start()
    return {"ok": True, "message": "已开始扫描 %s（进度见日志/统计）" % body.scope}


@app.get("/api/price-changes")
def api_price_changes(days: int = 3, limit: int = 120, direction: str = ""):
    """变价信息流：窗口内所有商品的价格变动（降价/涨价）。"""
    since = int(time.time()) - max(1, days) * 86400
    rows = db.price_changes_since(since, limit=limit, direction=direction or None)
    return {"since": since, "days": days, "items": rows, "total": len(rows)}


@app.get("/api/new-arrivals")
def api_new_arrivals(days: int = 30, limit: int = 60):
    """新品区：官方「新作商品」榜（rank_new）+ 本地首次发现兜底。"""
    return {"items": db.new_arrivals(limit=limit, days=days)}


@app.post("/api/subscribe")
def api_subscribe(body: SubscribeIn):
    """订阅商品：升级为全量监控（SKU+库存），可附加预期价与尺码订阅。"""
    code = body.code.strip()
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在（请先扫描商品库或添加监控）")
    pc = _canonical_pc(code, p)
    if body.target_price is not None:
        db.set_target_price(code, body.target_price)
    db.set_track_level(code, "full")
    db.add_watch(pc, body.size or "", body.color or "",
                 int(db.get_setting("low_threshold", 2) or 2))
    r = collector.collect_product(code)          # 立即建库存基线
    return {"ok": True, "subscribed": True, "collect": r}


@app.delete("/api/subscribe/{code}")
def api_unsubscribe(code: str):
    """取消商品订阅：移除整商品价格订阅与尺码订阅，降级为仅跟踪价格。"""
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在")
    pc = p["product_code"]
    for w in db.list_watches():
        if w.get("product_code") == pc:
            db.remove_watch(w["id"])
    db.set_track_level(code, "catalog")
    return {"ok": True, "subscribed": False}


def _canonical_pc(code: str, p: dict) -> str:
    """取权威内部 ID：列表接口的 productCode 可能是款式聚合变体（SKU 集合不同），
    搜索接口按 6 位编号精确匹配到的才是详情接口该用的 ID。失败则回退库里现有值。"""
    try:
        it = client.resolve(code)
        pc = (it or {}).get("productCode")
        if pc and pc != p.get("product_code"):
            db.set_product_code(code, pc)
            return pc
    except UQError:
        pass
    return p.get("product_code")


@app.post("/api/catalog/{code}/pull-sizes")
def api_pull_sizes(code: str):
    """按需拉取一次尺码库存（不进入监控；用于未订阅商品看颜色×尺码）。

    实测成本：搜索 1 次（校验权威 ID）+ 详情 1 次 + 库存 1 次上游请求。
    """
    p = db.get_product(code)
    if not p:
        raise HTTPException(404, "商品不存在")
    with _collect_lock:
        now = time.time()
        last = _pull_once.get(code, 0)
        if now - last < COLLECT_COOLDOWN:
            raise HTTPException(429, "拉取过于频繁，请 %d 秒后再试"
                                      % (int(COLLECT_COOLDOWN - (now - last)) + 1))
        _pull_once[code] = now
    pc = _canonical_pc(code, p)
    try:
        snap = client.snapshot(pc)
    except UQError as e:
        raise HTTPException(502, str(e))
    ts = int(time.time())
    db.upsert_skus(pc, [
        {"sku_id": s["sku_id"], "size": s.get("size"), "size_no": s.get("size_no"),
         "color": s.get("color"), "color_no": s.get("color_no"),
         "sub_code": s.get("sub_code"), "price": s.get("price")} for s in snap["skus"]])
    db.add_snapshots(ts, [
        {"sku_id": s["sku_id"], "express": s.get("express"), "bpl": s.get("bpl"),
         "dc": s.get("dc"), "transit": s.get("transit")} for s in snap["skus"]])
    return {"ok": True, "skus": len(snap["skus"]), "ts": ts}



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
    last_sweep = time.time()          # 启动时另起线程扫一次；此处从当前时间起算
    last_prune = 0.0
    while True:
        try:
            cfg = db.all_settings()
            interval = max(5, int(cfg.get("stock_interval_min") or 30))
        except Exception:                             # noqa: BLE001
            cfg, interval = {}, 30
        time.sleep(interval * 60)
        try:
            if [p for p in db.list_products(track_level="full") if p.get("enabled")]:
                collector.collect_all()
        except Exception as e:                        # noqa: BLE001
            db.add_log(0, "定时采集异常: %s" % e)
        try:
            sweep_min = max(30, int(cfg.get("catalog_sweep_min") or 360))
            if not _sweep_state.get("running") and time.time() - last_sweep >= sweep_min * 60:
                last_sweep = time.time()
                _run_sweep()
        except Exception as e:                        # noqa: BLE001
            db.add_log(0, "商品库扫描异常: %s" % e)
        try:
            if time.time() - last_prune > 86400:
                last_prune = time.time()
                db.prune_snapshots(int(cfg.get("catalog_keep_days") or 90))
        except Exception as e:                        # noqa: BLE001
            db.add_log(0, "快照清理异常: %s" % e)


def _startup_sweep():
    """首次启动（或商品库过期）时后台建库，避免空商城。UQ_NO_STARTUP_SWEEP=1 可关闭。"""
    if os.environ.get("UQ_NO_STARTUP_SWEEP"):
        return
    time.sleep(3)
    try:
        st = db.catalog_stats()
        stale = (not st.get("last_sweep_ts")
                 or time.time() - st["last_sweep_ts"] > 6 * 3600)
        if st.get("total", 0) < 10 or stale:
            _run_sweep()
    except Exception as e:                            # noqa: BLE001
        db.add_log(0, "启动建库异常: %s" % e)


def main():
    host = os.environ.get("UQ_HOST", "127.0.0.1")   # 容器部署设为 0.0.0.0
    port = int(os.environ.get("UQ_PORT", "8811"))
    threading.Thread(target=_scheduler, daemon=True).start()
    threading.Thread(target=_startup_sweep, daemon=True).start()
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("优衣库商城/监控已启动: http://%s:%d（监听 %s）" % (shown, port, host))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
