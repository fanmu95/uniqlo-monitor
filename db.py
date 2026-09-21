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
CREATE TABLE IF NOT EXISTS categories (
  code        TEXT PRIMARY KEY,
  name        TEXT,
  parent_code TEXT,
  level       INTEGER DEFAULT 1,
  sort        INTEGER DEFAULT 0,
  product_sum INTEGER,
  updated_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cat_parent ON categories(parent_code);
CREATE TABLE IF NOT EXISTS catalog_categories (
  code          TEXT NOT NULL,
  category_code TEXT NOT NULL,
  PRIMARY KEY(code, category_code)
);
CREATE INDEX IF NOT EXISTS idx_catcat_cat ON catalog_categories(category_code);
CREATE TABLE IF NOT EXISTS catalog_sweeps (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            INTEGER,
  scope         TEXT,
  pages         INTEGER,
  items         INTEGER,
  new_items     INTEGER,
  price_changes INTEGER,
  ok            INTEGER,
  message       TEXT
);
"""

DEFAULT_SETTINGS = {
    "stock_interval_min": 30,     # 库存采集间隔（分钟）
    "price_interval_min": 180,    # 价格采集间隔（分钟）
    "sku_refresh_hour": 4,        # SKU 字典每日刷新时刻
    "catalog_sweep_min": 360,     # 商品库全站扫描间隔（分钟）
    "catalog_keep_days": 90,      # 快照/价格历史保留天数
    "catalog_hide_days": 3,       # 超过 N 天未在扫描中出现的商品视为已下架（列表隐藏）
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
                # 商城化（2026-09-21）：products 从「监控清单」升级为「商品库」
                ("products", "track_level", "TEXT DEFAULT 'full'"),
                ("products", "first_seen_ts", "INTEGER"),
                ("products", "last_seen_ts", "INTEGER"),
                ("products", "gender", "TEXT"),
                ("products", "sizes", "TEXT"),
                ("products", "min_size", "TEXT"),
                ("products", "max_size", "TEXT"),
                ("products", "season", "TEXT"),
                ("products", "material", "TEXT"),
                ("products", "identity", "TEXT"),
                ("products", "sales", "INTEGER"),
                ("products", "evaluation_count", "INTEGER"),
                ("products", "upstream_new_ts", "INTEGER"),
                ("products", "rank_overall", "INTEGER"),
                ("products", "rank_new", "INTEGER"),
                ("products", "stock_flag", "TEXT"),
                ("products", "chip_pics", "TEXT"),
                ("products", "color_nos", "TEXT"),
                ("products", "style_text", "TEXT"),
                # 下架判定（2026-09-21）：唯一「在售/下架」事实，由扫描结果写入
                ("products", "gone_ts", "INTEGER"),
            ]:
                cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(%s)" % tbl)}
                if col not in cols:
                    self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tbl, col, ddl))
            # 存量商品（老监控清单）保持 full 级；新入库商品默认 catalog 级
            self.conn.execute("UPDATE products SET track_level='full' WHERE track_level IS NULL")
            self.conn.execute("UPDATE products SET first_seen_ts=COALESCE(first_seen_ts, created_at)")
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

    def set_target_hit(self, code: str, hit: int):
        """记录预期价达标状态（达标推送过 = 1，价格回升后复位 = 0）。"""
        with self._mutex:
            self.conn.execute("UPDATE products SET target_hit=? WHERE code=?", (hit, code))
            self.conn.commit()

    def list_products(self, track_level: Optional[str] = "full") -> List[dict]:
        """监控清单。track_level='full' 只返回订阅/全量监控商品；None = 全部商品库。"""
        sql = "SELECT * FROM products"
        params: tuple = ()
        if track_level:
            sql += " WHERE track_level=?"
            params = (track_level,)
        sql += " ORDER BY created_at DESC"
        with self._mutex:
            return [dict(r) for r in self.conn.execute(sql, params)]

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

    # ================= 商品库（商城）=================
    _JSON_COLS = ("color_pics", "chip_pics", "color_nos", "sizes", "identity", "style_text")

    def upsert_catalog_item(self, it: dict, rank_overall: Optional[int] = None,
                            ts: Optional[int] = None, update_price: bool = True) -> dict:
        """分类扫描入库（catalog 级）。返回 {inserted, price_changed, old_price, new_price}。

        只写商品库字段；绝不覆盖 track_level / target_price / target_hit，
        hist_low_price 只降不升，enabled / watches 由用户操作决定。

        update_price=False 用于「旁路榜单」（如新品榜）：已存在的商品一律不改价格字段，
        避免同一商品码在不同榜单里价格不同（款式聚合/不同颜色组）把史低带偏。
        """
        ts = ts or int(time.time())
        code = (it.get("code") or "").strip()
        if not code:
            return {}
        vals = {
            "code": code,
            "product_code": it.get("product_code") or "",
            "name": it.get("name") or "",
            "origin_price": it.get("origin_price"),
            "cur_price": it.get("min_price"),
            "store_count": it.get("store_count"),
            "limited_begin": it.get("limited_begin"),
            "limited_end": it.get("limited_end"),
            "main_pic": it.get("main_pic") or "",
            "gender": it.get("gender") or "",
            "min_size": it.get("min_size") or "",
            "max_size": it.get("max_size") or "",
            "season": it.get("season") or "",
            "material": it.get("material") or "",
            "sales": it.get("sales"),
            "evaluation_count": it.get("evaluation_count"),
            "upstream_new_ts": it.get("upstream_new_ts"),
            "stock_flag": it.get("stock_flag") or "",
            "rank_overall": rank_overall,
            "ts": ts,
        }
        for c in self._JSON_COLS:
            vals[c] = json.dumps(it.get(c) or [], ensure_ascii=False)

        with self._mutex:
            row = self.conn.execute(
                "SELECT cur_price, hist_low_price, origin_price, track_level FROM products WHERE code=?",
                (code,)).fetchone()
            inserted = row is None
            old_price = row["cur_price"] if row else None
            new_price = vals["cur_price"]
            hist_low = row["hist_low_price"] if row else None
            # 1) 订阅中（full）商品的现价由详情接口采集维护，扫描不得覆盖；
            # 2) update_price=False 的旁路榜单不改已存在商品的价格。
            # 否则下一轮 collect_product 会因两个来源的价差产生假的价格事件，
            # 或把史低带到另一个聚合条目的价格上。
            keep_price = row is not None and (
                (row["track_level"] or "full") == "full" or not update_price)
            if keep_price:
                vals["cur_price"] = row["cur_price"]
                vals["hist_low_price"] = row["hist_low_price"]
                if row["origin_price"] is not None:
                    vals["origin_price"] = row["origin_price"]
                price_changed = False
            else:
                if hist_low is None or (new_price is not None and new_price < hist_low):
                    hist_low = new_price
                vals["hist_low_price"] = hist_low
                price_changed = (not inserted) and old_price is not None \
                    and new_price is not None and old_price != new_price

            if inserted:
                self.conn.execute(
                    """INSERT INTO products(code,product_code,name,origin_price,cur_price,
                         hist_low_price,store_count,limited_begin,limited_end,main_pic,
                         color_pics,chip_pics,color_nos,gender,sizes,min_size,max_size,season,
                         material,identity,style_text,sales,evaluation_count,upstream_new_ts,
                         stock_flag,rank_overall,track_level,first_seen_ts,last_seen_ts,enabled,
                         created_at,updated_at)
                       VALUES(:code,:product_code,:name,:origin_price,:cur_price,:hist_low_price,
                         :store_count,:limited_begin,:limited_end,:main_pic,:color_pics,:chip_pics,
                         :color_nos,:gender,:sizes,:min_size,:max_size,:season,:material,:identity,
                         :style_text,:sales,:evaluation_count,:upstream_new_ts,:stock_flag,
                         :rank_overall,'catalog',:ts,:ts,1,:ts,:ts)""", vals)
            else:
                self.conn.execute(
                    """UPDATE products SET
                         product_code=CASE WHEN products.product_code IS NULL
                                           OR products.product_code=''
                                      THEN :product_code ELSE products.product_code END,
                         name=:name,
                         origin_price=:origin_price,cur_price=:cur_price,
                         hist_low_price=:hist_low_price,store_count=:store_count,
                         limited_begin=:limited_begin,limited_end=:limited_end,
                         main_pic=:main_pic,color_pics=:color_pics,chip_pics=:chip_pics,
                         color_nos=:color_nos,gender=:gender,sizes=:sizes,min_size=:min_size,
                         max_size=:max_size,season=:season,material=:material,
                         identity=:identity,style_text=:style_text,sales=:sales,
                         evaluation_count=:evaluation_count,upstream_new_ts=:upstream_new_ts,
                         stock_flag=:stock_flag,rank_overall=:rank_overall,
                         gone_ts=NULL,last_seen_ts=:ts,updated_at=:ts
                       WHERE code=:code""", vals)
            self.conn.commit()
        return {"inserted": inserted, "price_changed": price_changed,
                "old_price": old_price, "new_price": new_price}

    def align_hist_low(self) -> int:
        """自愈：把「史低」对齐到价格历史的最低点，返回修正行数。

        不变式 = 史低必须等于我们记录到的最低价（price_snapshots 的最小值）。
        任何旁路写入（榜单价格差异、同秒覆盖等）造成的不一致，扫描后自动纠回。
        """
        with self._mutex:
            cur = self.conn.execute(
                """UPDATE products SET hist_low_price = (
                       SELECT MIN(price) FROM price_snapshots s WHERE s.code = products.code)
                   WHERE hist_low_price IS NOT NULL
                     AND EXISTS (SELECT 1 FROM price_snapshots s WHERE s.code = products.code)
                     AND hist_low_price <> (
                       SELECT MIN(price) FROM price_snapshots s WHERE s.code = products.code)""")
            n = cur.rowcount or 0
            self.conn.commit()
        return n

    def set_rank_new(self, code: str, rank_new: int):
        with self._mutex:
            self.conn.execute("UPDATE products SET rank_new=? WHERE code=?", (rank_new, code))
            self.conn.commit()

    def touch_last_seen(self, codes: List[str], ts: int):
        """批量刷新「本轮扫描见到过」的时间戳。"""
        if not codes:
            return
        with self._mutex:
            self.conn.executemany("UPDATE products SET last_seen_ts=? WHERE code=?",
                                  [(ts, c) for c in codes])
            self.conn.commit()

    def mark_gone(self, hide_days: int = 3) -> List[dict]:
        """把连续 N 天未在扫描中出现的商品标记为已下架（gone_ts），返回本轮新判定的商品。

        只应在「扫描完整成功」后调用；重新出现在扫描结果里的商品由
        upsert_catalog_item 自动清空 gone_ts（即自动恢复在售）。
        """
        now = int(time.time())
        cutoff = now - max(1, int(hide_days)) * 86400
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute(
                """SELECT code, product_code, name, track_level, cur_price, last_seen_ts
                   FROM products
                   WHERE gone_ts IS NULL
                     AND COALESCE(last_seen_ts, created_at) < ?""", (cutoff,))]
            if rows:
                self.conn.executemany("UPDATE products SET gone_ts=? WHERE code=?",
                                      [(now, r["code"]) for r in rows])
                self.conn.commit()
        return rows

    def set_track_level(self, code: str, level: str):
        with self._mutex:
            self.conn.execute("UPDATE products SET track_level=? WHERE code=?", (level, code))
            self.conn.commit()

    def set_product_code(self, code: str, product_code: str):
        """写入权威内部 ID（搜索接口解析结果）。

        官方列表接口返回的 productCode 可能是「款式聚合变体」，与详情接口的
        SKU 集合并不一致（实测 482432：列表 u0000000073746 只有 16 SKU，
        搜索/详情权威 ID u0000000069773 有 24 SKU）。
        """
        with self._mutex:
            self.conn.execute("UPDATE products SET product_code=? WHERE code=?",
                              (product_code, code))
            self.conn.commit()

    def catalog_stats(self) -> dict:
        now = int(time.time())
        with self._mutex:
            total = self.conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
            full = self.conn.execute(
                "SELECT COUNT(*) c FROM products WHERE track_level='full'").fetchone()["c"]
            fresh = self.conn.execute(
                "SELECT COUNT(*) c FROM products WHERE last_seen_ts >= ?",
                (now - 86400,)).fetchone()["c"]
            points = self.conn.execute("SELECT COUNT(*) c FROM price_snapshots").fetchone()["c"]
            cats = self.conn.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"]
            last = self.conn.execute(
                "SELECT ts FROM catalog_sweeps WHERE ok=1 ORDER BY id DESC LIMIT 1").fetchone()
        return {"total": total, "full": full, "fresh_24h": fresh, "price_points": points,
                "categories": cats, "last_sweep_ts": last["ts"] if last else None}

    def list_catalog(self, category: Optional[str] = None, q: Optional[str] = None,
                     sort: str = "overall", page: int = 1, page_size: int = 40,
                     hide_days: Optional[int] = None, with_current: bool = True,
                     only_new: bool = False, only_discount: bool = False,
                     only_stock: bool = False, only_doptimal: bool = False) -> dict:
        """商城商品列表（本地库）。sort: overall/newest/priceAsc/priceDesc/discount/new。

        可选筛选（互相可叠加，都是本地条件，不产生上游请求）：
          only_new      官方「新作商品」榜内（rank_new 非空）
          only_discount 现价 < 原价（有折扣）
          only_stock    官网在售标记为 Y（注：官方列表只收可购商品，实测恒为 Y，预留）
          only_doptimal 官方「限时特优」标识
        """
        where, params = [], []
        if with_current and hide_days:
            where.append("gone_ts IS NULL AND (last_seen_ts IS NULL OR last_seen_ts >= ?)")
            params.append(int(time.time()) - hide_days * 86400)
        if q:
            where.append("(name LIKE ? OR code LIKE ?)")
            params += ["%" + q + "%", "%" + q + "%"]
        if category and category != "ALL":
            where.append("code IN (SELECT code FROM catalog_categories WHERE category_code=?)")
            params.append(category)
        if only_new:
            where.append("rank_new IS NOT NULL")
        if only_discount:
            where.append("origin_price > 0 AND cur_price IS NOT NULL AND cur_price < origin_price")
        if only_stock:
            where.append("stock_flag = 'Y'")
        if only_doptimal:
            where.append("identity LIKE '%time_doptimal%'")
        order = {
            "overall": "rank_overall IS NULL, rank_overall ASC, code DESC",
            "newest": "upstream_new_ts IS NULL, upstream_new_ts DESC, code DESC",
            "new": "rank_new IS NULL, rank_new ASC, first_seen_ts DESC",
            "priceAsc": "cur_price IS NULL, cur_price ASC, code DESC",
            "priceDesc": "cur_price IS NULL, cur_price DESC, code DESC",
            "discount": ("CASE WHEN origin_price>0 AND cur_price IS NOT NULL "
                         "THEN (origin_price-cur_price)/origin_price ELSE -1 END DESC, code DESC"),
        }.get(sort, "rank_overall IS NULL, rank_overall ASC, code DESC")
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        with self._mutex:
            total = self.conn.execute("SELECT COUNT(*) c FROM products" + wsql,
                                      params).fetchone()["c"]
            rows = self.conn.execute(
                """SELECT code, product_code, name, origin_price, cur_price, hist_low_price,
                          main_pic, gender, season, sizes, min_size, max_size, stock_flag,
                          identity, sales, evaluation_count, limited_end, track_level,
                          rank_new, first_seen_ts, last_seen_ts, target_price,
                          CASE WHEN origin_price>0 AND cur_price IS NOT NULL
                               THEN CAST(ROUND((origin_price-cur_price)/origin_price*100) AS INT)
                               END AS discount_pct,
                          EXISTS(SELECT 1 FROM watches w WHERE w.product_code=products.product_code
                                 AND w.enabled=1) AS subscribed
                   FROM products""" + wsql + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                params + [page_size, (page - 1) * page_size]).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            for c in ("sizes", "identity"):
                try:
                    d[c] = json.loads(d.get(c) or "[]")
                except Exception:
                    d[c] = []
            d["is_new"] = d.get("rank_new") is not None
            items.append(d)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def price_changes_since(self, since_ts: int, limit: int = 120,
                            direction: Optional[str] = None,
                            hide_days: Optional[int] = None) -> List[dict]:
        """变价信息：price_snapshots 只在价格变化时落点，用窗口函数取每个点的前价。

        注意：必须在 SQL 里过滤出「确有变化」的点再 LIMIT —— 扫描入库时每件商品
        都会写基线点，先 LIMIT 再过滤会被基线点挤满，真实变价永远取不到。
        已下架商品（gone_ts 有值 / 超过 hide_days 未出现）不出现在变价流里。
        """
        dir_sql = ""
        if direction == "down":
            dir_sql = " AND new_price < old_price"
        elif direction == "up":
            dir_sql = " AND new_price > old_price"
        sale_sql = ""
        params: List = []
        if hide_days:
            sale_sql = (" AND gone_ts IS NULL AND (last_seen_ts IS NULL "
                        "OR last_seen_ts >= ?)")
            params.append(int(time.time()) - hide_days * 86400)
        sql = """
          SELECT * FROM (
            SELECT ps.code, ps.ts, ps.price AS new_price,
                   LAG(ps.price) OVER (PARTITION BY ps.code ORDER BY ps.ts) AS old_price,
                   p.name, p.main_pic, p.origin_price, p.cur_price, p.hist_low_price,
                   p.track_level, p.gender, p.gone_ts, p.last_seen_ts
            FROM price_snapshots ps JOIN products p ON p.code = ps.code
            WHERE p.name IS NOT NULL AND p.name <> ''
          )
          WHERE ts >= ? AND old_price IS NOT NULL AND old_price <> new_price""" \
            + dir_sql + """
          ORDER BY ts DESC LIMIT ?"""
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute(sql, [since_ts, limit])]
        out = []
        for r in rows:
            r["direction"] = "down" if r["new_price"] < r["old_price"] else "up"
            if hide_days:
                cutoff = int(time.time()) - hide_days * 86400
                if r.get("gone_ts") or (r.get("last_seen_ts") and r["last_seen_ts"] < cutoff):
                    continue
            out.append(r)
        return out

    def new_arrivals(self, limit: int = 60, days: int = 30,
                     hide_days: Optional[int] = None) -> List[dict]:
        since = int(time.time()) - days * 86400
        where = ""
        params: List = [since]
        if hide_days:
            where = " AND gone_ts IS NULL AND (last_seen_ts IS NULL OR last_seen_ts >= ?)"
            params.append(int(time.time()) - hide_days * 86400)
        params.append(limit)
        with self._mutex:
            rows = [dict(r) for r in self.conn.execute(
                """SELECT code, product_code, name, origin_price, cur_price, hist_low_price,
                          main_pic, gender, season, stock_flag, rank_new, first_seen_ts,
                          upstream_new_ts, track_level, gone_ts
                   FROM products
                   WHERE (rank_new IS NOT NULL OR first_seen_ts >= ?)""" + where + """
                   ORDER BY rank_new IS NULL, rank_new ASC, first_seen_ts DESC, code DESC
                   LIMIT ?""", params)]
        for r in rows:
            r["is_new"] = True
        return rows

    # ---------- 分类 ----------
    def upsert_categories(self, nodes: List[dict]):
        now = int(time.time())
        with self._mutex:
            for n in nodes:
                self.conn.execute(
                    """INSERT INTO categories(code,name,parent_code,level,sort,updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(code) DO UPDATE SET name=excluded.name,
                         parent_code=excluded.parent_code, level=excluded.level,
                         sort=excluded.sort, updated_at=excluded.updated_at""",
                    (n.get("code"), n.get("name") or "", n.get("parent"),
                     n.get("level") or 1, n.get("sort") or 0, now))
            self.conn.commit()

    def list_categories(self) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM categories ORDER BY level, sort, code")]

    def category_counts(self) -> Dict[str, int]:
        with self._mutex:
            rows = self.conn.execute(
                "SELECT category_code, COUNT(*) c FROM catalog_categories GROUP BY category_code")
            return {r["category_code"]: r["c"] for r in rows}

    def set_item_categories(self, code: str, cat_codes: List[str]):
        with self._mutex:
            self.conn.execute("DELETE FROM catalog_categories WHERE code=?", (code,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO catalog_categories(code,category_code) VALUES(?,?)",
                [(code, c) for c in cat_codes if c])
            self.conn.commit()

    def top_level_codes(self) -> List[str]:
        """商品实际归属到的顶层分类（level=1）之外的候选：短码（非数字叶子码）。"""
        with self._mutex:
            rows = self.conn.execute(
                "SELECT DISTINCT category_code FROM catalog_categories").fetchall()
        return [r["category_code"] for r in rows if not r["category_code"].isdigit()]

    # ---------- 扫描日志 ----------
    def log_sweep(self, scope: str, pages: int, items: int, new_items: int,
                  price_changes: int, ok: int, message: str):
        with self._mutex:
            self.conn.execute(
                """INSERT INTO catalog_sweeps(ts,scope,pages,items,new_items,price_changes,ok,message)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (int(time.time()), scope, pages, items, new_items, price_changes, ok, message))
            self.conn.commit()

    def list_sweeps(self, limit: int = 10) -> List[dict]:
        with self._mutex:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM catalog_sweeps ORDER BY id DESC LIMIT ?", (limit,))]

