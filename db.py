# -*- coding: utf-8 -*-
"""SQLite 数据层：商品主档 / SKU 字典 / 库存快照 / 变更事件 / 尺码订阅 / 设置"""
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  code          TEXT PRIMARY KEY,
  product_code  TEXT NOT NULL,
  name          TEXT,
  origin_price  REAL,
  cur_price     REAL,
  store_count   INTEGER,
  limited_begin INTEGER,
  limited_end   INTEGER,
  target_price  REAL,
  hist_low_price REAL,
  target_hit    INTEGER DEFAULT 0,
  main_pic      TEXT,
  color_pics    TEXT,
  enabled       INTEGER DEFAULT 1,
  created_at    INTEGER,
  updated_at    INTEGER
);
CREATE TABLE IF NOT EXISTS skus (
  sku_id       TEXT PRIMARY KEY,
  product_code TEXT NOT NULL,
  size         TEXT,
  size_no      TEXT,
  color        TEXT,
  color_no     TEXT,
  sub_code     TEXT,
  price        REAL,
  updated_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skus_pc ON skus(product_code);
CREATE TABLE IF NOT EXISTS stock_snapshots (
  sku_id  TEXT NOT NULL,
  ts      INTEGER NOT NULL,
  express INTEGER, bpl INTEGER, dc INTEGER, transit INTEGER,
  PRIMARY KEY(sku_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON stock_snapshots(ts);
CREATE TABLE IF NOT EXISTS price_snapshots (
  code  TEXT NOT NULL,
  ts    INTEGER NOT NULL,
  price REAL,
  PRIMARY KEY(code, ts)
);
CREATE INDEX IF NOT EXISTS idx_price_ts ON price_snapshots(ts);
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  product_code TEXT,
  sku_id       TEXT,
  kind         TEXT,
  title        TEXT,
  detail       TEXT,
  old_val      TEXT,
  new_val      TEXT,
  ts           INTEGER,
  notified     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS watches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  product_code TEXT NOT NULL,
  size         TEXT,
  color        TEXT,
  low_threshold INTEGER DEFAULT 2,
  enabled      INTEGER DEFAULT 1,
  created_at   INTEGER,
  UNIQUE(product_code, size, color)
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS collect_logs (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      INTEGER,
  ok      INTEGER,
  message TEXT
);
"""

DEFAULT_SETTINGS = {
    "stock_interval_min": 30,     # 库存采集间隔（分钟）
    "price_interval_min": 180,    # 价格采集间隔（分钟）
    "sku_refresh_hour": 4,        # SKU 字典每日刷新时刻
    "low_threshold": 2,           # 低库存阈值（件）
    "notify_enabled": 0,
    "notify_channel": "ntfy",     # ntfy / wecom
    "ntfy_topic": "",
    "ntfy_server": "https://ntfy.sh",
    "wecom_webhook": "",
    "pp_token": "",
    "quiet_hours": "23:00-08:00",
}


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = sqlite3.Row  # placeholder
        import threading
        self._mutex = threading.Lock()
        self._init()

    def _init(self):
        with self._mutex:
            self.conn.executescript(SCHEMA)
            # 老库迁移：补列
            for tbl, col, ddl in [
                ("skus", "sub_code", "TEXT"),
                ("products", "target_price", "REAL"),
                ("products", "hist_low_price", "REAL"),
                ("products", "target_hit", "INTEGER DEFAULT 0"),
                ("products", "main_pic", "TEXT"),
                ("products", "color_pics", "TEXT"),
            ]:
                cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(%s)" % tbl)}
                if col not in cols:
                    self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tbl, col, ddl))
            self.conn.commit()
        for k, v in DEFAULT_SETTINGS.items():
            if self.get_setting(k) is None:
                self.set_setting(k, v)

    # ---------- settings ----------
    def get_setting(self, key: str, default=None):
        with self._mutex:
            cur = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cur.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_setting(self, key: str, value: Any):
        with self._mutex:
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))
            self.conn.commit()

    def all_settings(self) -> dict:
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute("SELECT key,value FROM settings")]
        out = dict(DEFAULT_SETTINGS)
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except Exception:
                out[row["key"]] = row["value"]
        return out

    # ---------- products ----------
    def upsert_product(self, p: dict):
        now = int(time.time())
        vals = dict(p)
        vals.setdefault("target_price", None)
        vals.setdefault("hist_low_price", None)
        vals.setdefault("target_hit", 0)
        vals.setdefault("main_pic", None)
        vals.setdefault("color_pics", None)
        vals["now"] = now
        with self._mutex:
            self.conn.execute(
                """INSERT INTO products(code,product_code,name,origin_price,cur_price,
                     store_count,limited_begin,limited_end,target_price,hist_low_price,target_hit,
                     main_pic,color_pics,enabled,created_at,updated_at)
                   VALUES(:code,:product_code,:name,:origin_price,:cur_price,
                     :store_count,:limited_begin,:limited_end,:target_price,:hist_low_price,
                     COALESCE(:target_hit,0),:main_pic,:color_pics,1,:now,:now)
                   ON CONFLICT(code) DO UPDATE SET
                     product_code=excluded.product_code, name=excluded.name,
                     origin_price=excluded.origin_price, cur_price=excluded.cur_price,
                     store_count=excluded.store_count,
                     limited_begin=excluded.limited_begin, limited_end=excluded.limited_end,
                     target_price=excluded.target_price,
                     hist_low_price=excluded.hist_low_price,
                     target_hit=excluded.target_hit,
                     main_pic=excluded.main_pic,
                     color_pics=excluded.color_pics,
                     updated_at=excluded.updated_at""",
                vals)
            self.conn.commit()

    def set_target_price(self, code: str, target_price):
        """设置/清除预期价。设置时若现价已达标则直接标记 hit，不再推送。"""
        p = self.get_product(code)
        if not p:
            return False
        hit = 0
        if target_price is not None and p.get("cur_price") is not None \
                and p["cur_price"] <= target_price:
            hit = 1
        with self._mutex:
            self.conn.execute("UPDATE products SET target_price=?, target_hit=? WHERE code=?",
                              (target_price, hit, code))
            self.conn.commit()
        return True

    def list_products(self) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM products ORDER BY created_at DESC")]

    def get_product(self, code: str) -> Optional[dict]:
        with self._mutex:
            r = self.conn.execute("SELECT * FROM products WHERE code=?", (code,)).fetchone()
        return dict(r) if r else None

    def get_product_by_pc(self, product_code: str) -> Optional[dict]:
        with self._mutex:
            r = self.conn.execute("SELECT * FROM products WHERE product_code=?",
                                  (product_code,)).fetchone()
        return dict(r) if r else None

    def delete_product(self, code: str):
        """删除商品主档、SKU 字典与价格历史。

        订阅记录保留——重新添加同商品时自动恢复；
        价格历史一并清除——避免跨监控周期的旧价格污染走势图（史低会随新周期重置）。
        """
        p = self.get_product(code)
        if not p:
            return
        with self._mutex:
            self.conn.execute("DELETE FROM products WHERE code=?", (code,))
            self.conn.execute("DELETE FROM skus WHERE product_code=?", (p["product_code"],))
            self.conn.execute("DELETE FROM price_snapshots WHERE code=?", (code,))
            self.conn.commit()

    def clear_price_history(self, code: str):
        """清除某商品的价格历史（重新添加时保证走势图从新周期开始）。"""
        with self._mutex:
            self.conn.execute("DELETE FROM price_snapshots WHERE code=?", (code,))
            self.conn.commit()

    def set_product_enabled(self, code: str, enabled: int):
        with self._mutex:
            self.conn.execute("UPDATE products SET enabled=? WHERE code=?", (enabled, code))
            self.conn.commit()

    # ---------- skus ----------
    def upsert_skus(self, product_code: str, skus: List[dict]):
        now = int(time.time())
        with self._mutex:
            for s in skus:
                self.conn.execute(
                    """INSERT INTO skus(sku_id,product_code,size,size_no,color,color_no,sub_code,price,updated_at)
                       VALUES(:sku_id,:product_code,:size,:size_no,:color,:color_no,:sub_code,:price,:now)
                       ON CONFLICT(sku_id) DO UPDATE SET
                         size=excluded.size, size_no=excluded.size_no, color=excluded.color,
                         color_no=excluded.color_no, sub_code=excluded.sub_code,
                         price=excluded.price, updated_at=excluded.updated_at""",
                    {**s, "product_code": product_code, "now": now})
            self.conn.commit()

    def list_skus(self, product_code: str) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM skus WHERE product_code=? ORDER BY color_no, size_no",
                (product_code,))]

    def get_sku(self, sku_id: str) -> Optional[dict]:
        with self._mutex:
            r = self.conn.execute("SELECT * FROM skus WHERE sku_id=?", (sku_id,)).fetchone()
        return dict(r) if r else None

    # ---------- snapshots ----------
    def add_snapshots(self, ts: int, rows: List[dict]):
        with self._mutex:
            self.conn.executemany(
                """INSERT OR REPLACE INTO stock_snapshots(sku_id,ts,express,bpl,dc,transit)
                   VALUES(:sku_id,:ts,:express,:bpl,:dc,:transit)""",
                [{**r, "ts": ts} for r in rows])
            self.conn.commit()

    def latest_stock(self, product_code: str) -> Dict[str, dict]:
        """该商品每个 SKU 最近一次的库存。"""
        sql = """
          SELECT s.sku_id, sn.express, sn.bpl, sn.dc, sn.transit, sn.ts
          FROM skus s
          JOIN stock_snapshots sn ON sn.sku_id = s.sku_id
          JOIN (SELECT sku_id, MAX(ts) AS mts FROM stock_snapshots
                WHERE sku_id IN (SELECT sku_id FROM skus WHERE product_code=?)
                GROUP BY sku_id) m ON m.sku_id = sn.sku_id AND m.mts = sn.ts
          WHERE s.product_code = ?
        """
        with self._mutex:
            rows = self.conn.execute(sql, (product_code, product_code)).fetchall()
        return {r["sku_id"]: dict(r) for r in rows}

    def prev_stock(self, product_code: str) -> Dict[str, dict]:
        """上一次的库存（用于 diff）。"""
        sql = """
          SELECT sku_id, express FROM stock_snapshots
          WHERE sku_id IN (SELECT sku_id FROM skus WHERE product_code=?)
          ORDER BY ts DESC
        """
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute(sql, (product_code,))]
        out: Dict[str, dict] = {}
        seen: Dict[str, int] = {}
        for r in rows:
            seen[r["sku_id"]] = seen.get(r["sku_id"], 0) + 1
            if seen[r["sku_id"]] == 2:      # 第二条即上一次
                out[r["sku_id"]] = r
        return out

    def stock_series(self, sku_id: str, limit: int = 200) -> List[dict]:
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT ts, express, transit FROM stock_snapshots WHERE sku_id=? "
                "ORDER BY ts DESC LIMIT ?", (sku_id, limit))]
        return rows[::-1]

    def prune_snapshots(self, keep_days: int = 30):
        cutoff = int(time.time()) - keep_days * 86400
        with self._mutex:
            self.conn.execute("DELETE FROM stock_snapshots WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM price_snapshots WHERE ts < ?", (cutoff,))
            self.conn.commit()

    # ---------- 价格时序 ----------
    def add_price_snapshot(self, code: str, ts: int, price: float):
        with self._mutex:
            self.conn.execute(
                "INSERT OR REPLACE INTO price_snapshots(code,ts,price) VALUES(?,?,?)",
                (code, ts, price))
            self.conn.commit()

    def price_series(self, code: str, limit: int = 400) -> List[dict]:
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT ts, price FROM price_snapshots WHERE code=? "
                "ORDER BY ts DESC LIMIT ?", (code, limit))]
        return rows[::-1]

    # ---------- events ----------
    def add_event(self, ev: dict):
        with self._mutex:
            self.conn.execute(
                """INSERT INTO events(product_code,sku_id,kind,title,detail,old_val,new_val,ts,notified)
                   VALUES(:product_code,:sku_id,:kind,:title,:detail,:old_val,:new_val,:ts,0)""",
                ev)
            self.conn.commit()

    def list_events(self, limit: int = 100) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))]

    def mark_notified(self, ids: List[int]):
        if not ids:
            return
        with self._mutex:
            self.conn.executemany("UPDATE events SET notified=1 WHERE id=?", [(i,) for i in ids])
            self.conn.commit()

    # ---------- watches ----------
    def add_watch(self, product_code: str, size: str, color: str, threshold: int = 2):
        with self._mutex:
            self.conn.execute(
                """INSERT INTO watches(product_code,size,color,low_threshold,enabled,created_at)
                   VALUES(?,?,?,?,1,?)
                   ON CONFLICT(product_code,size,color) DO UPDATE SET
                     low_threshold=excluded.low_threshold, enabled=1""",
                (product_code, size, color, threshold, int(time.time())))
            self.conn.commit()

    def remove_watch(self, watch_id: int):
        with self._mutex:
            self.conn.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            self.conn.commit()

    def list_watches(self) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT w.*, p.name, p.code, p.main_pic, p.cur_price, p.hist_low_price "
                "FROM watches w "
                "LEFT JOIN products p ON p.product_code=w.product_code "
                "WHERE w.enabled=1 ORDER BY w.id DESC")]

    # ---------- logs ----------
    def add_log(self, ok: int, message: str):
        with self._mutex:
            self.conn.execute("INSERT INTO collect_logs(ts,ok,message) VALUES(?,?,?)",
                              (int(time.time()), ok, message))
            self.conn.commit()

    def list_logs(self, limit: int = 20) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM collect_logs ORDER BY id DESC LIMIT ?", (limit,))]
