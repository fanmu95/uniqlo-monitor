# -*- coding: utf-8 -*-
"""采集与变更检测：真实接口取数 -> 入库 -> diff 产出事件 -> 通知"""
import json
import time
from typing import Dict, List, Optional

from db import DB
from notifier import send
from uq_client import PAGE_SIZE_MAX, UQClient, UQError, normalize_item

IMG_BASE = "https://www.uniqlo.cn"

# 官方顶层分类种子（实测存在；其余顶层码从商品 cateSequence 里补）
CATEGORY_SEEDS = ["1111MEN", "1111WOMEN", "1111KIDS", "1111BABY", "UNIQLOTOP", "ALL"]


class Collector:
    def __init__(self, db: DB, client: UQClient):
        self.db = db
        self.client = client

    # ---------- 变更检测 ----------
    def _diff(self, product: dict, prev: Dict[str, dict], snap: dict, ts: int,
              threshold: int) -> List[dict]:
        events = []
        pc = snap["product_code"]
        name = product.get("name") or snap.get("name") or product["code"]

        # 同款聚合时同一颜色名可能来自多个商品码，重名的用 (商品码) 区分
        color_cnt: Dict[str, int] = {}
        for s in snap["skus"]:
            if s.get("color"):
                color_cnt[s["color"]] = color_cnt.get(s["color"], 0) + 1

        def _color_disp(s: dict) -> str:
            c = s.get("color") or ""
            if c and color_cnt.get(c, 0) > 1 and s.get("sub_code"):
                return "%s(%s)" % (c, s["sub_code"])
            return c

        for s in snap["skus"]:
            sku_id = s["sku_id"]
            cur = s.get("express")
            if cur is None:
                continue
            old_row = prev.get(sku_id)
            old = old_row.get("express") if old_row else None
            label = " ".join(filter(None, [name, _color_disp(s), s.get("size") or ""]))

            if old is None:
                continue                      # 首次采集不产生事件

            if old and old > 0 and cur == 0:
                events.append({"product_code": pc, "sku_id": sku_id, "kind": "OUT",
                               "title": "断货：%s" % label,
                               "detail": "库存 %s → 0" % old,
                               "old_val": str(old), "new_val": "0", "ts": ts})
            elif old == 0 and cur and cur > 0:
                events.append({"product_code": pc, "sku_id": sku_id, "kind": "IN",
                               "title": "补货：%s" % label,
                               "detail": "库存 0 → %s 件" % cur,
                               "old_val": "0", "new_val": str(cur), "ts": ts})
            elif cur and 0 < cur <= threshold and (old is None or old != cur):
                events.append({"product_code": pc, "sku_id": sku_id, "kind": "LOW",
                               "title": "低库存：%s" % label,
                               "detail": "仅剩 %s 件" % cur,
                               "old_val": str(old), "new_val": str(cur), "ts": ts})

            # 在途预告：当前无货但有在途
            if cur == 0 and (s.get("transit") or 0) > 0:
                events.append({"product_code": pc, "sku_id": sku_id, "kind": "TRANSIT",
                               "title": "即将补货：%s" % label,
                               "detail": "在途 %s 件" % s["transit"],
                               "old_val": str(old), "new_val": "0", "ts": ts})
        return events

    # ---------- 价格 / 预期价 / 史低 ----------
    def _price_events(self, product: dict, new_price, ts: int):
        """返回 (events, state)。state 含最新 hist_low_price / target_hit / cur_price。

        规则（与流程图一致）：
        - 未设预期价：较上次监控降价 → PRICE_DOWN 推送；涨价 → 仅记录
        - 设预期价：降价未达预期 → PRICE_DOWN 仅记录（静默）；
          跌破预期且未推过 → TARGET_HIT 推送；达标后回升超过预期 → 复位可再次触发
        - 史低：首次采集初始化；现价破史低 → 更新并标记「历史新低」附加到文案
        """
        events = []
        old_price = product.get("cur_price")
        target = product.get("target_price")
        hit = int(product.get("target_hit") or 0)
        hist_low = product.get("hist_low_price")
        origin = product.get("origin_price")
        name = product.get("name") or product["code"]
        new_low = False

        if new_price is not None:
            if hist_low is None:
                hist_low = new_price                     # 初始化，不算新低
            elif new_price < hist_low:
                hist_low = new_price
                new_low = True

        state = {"hist_low_price": hist_low, "target_hit": hit,
                 "cur_price": new_price, "new_low": new_low}

        if old_price is None or new_price is None or new_price == old_price:
            return events, state

        d_last = round(old_price - new_price, 2)                     # 较上次监控
        d_official = (round((origin - new_price), 2)
                      if origin is not None else None)               # 官方较原价
        low_tag = " · 历史新低" if new_low else ""
        off_txt = (" · 官方较原价降 ¥%s" % d_official) if d_official is not None else ""

        if new_price < old_price:                                    # 降价
            if target is None:
                events.append({
                    "product_code": product["product_code"], "sku_id": "",
                    "kind": "PRICE_DOWN", "notify": True,
                    "title": "降价：%s" % name,
                    "detail": "¥%s → ¥%s（较上次监控降 ¥%s%s）%s"
                              % (old_price, new_price, d_last, off_txt, low_tag),
                    "old_val": str(old_price), "new_val": str(new_price), "ts": ts})
            elif new_price <= target:
                if not hit:
                    hit = 1
                    events.append({
                        "product_code": product["product_code"], "sku_id": "",
                        "kind": "TARGET_HIT", "notify": True,
                        "title": "已达到预期价：%s" % name,
                        "detail": "现价 ¥%s ≤ 预期 ¥%s（较上次监控降 ¥%s%s）%s"
                                  % (new_price, target, d_last, off_txt, low_tag),
                        "old_val": str(old_price), "new_val": str(new_price), "ts": ts})
                # 已推过达标，再次降价不重复推送
            else:
                if hit:
                    hit = 0                                          # 回升超过预期，复位
                events.append({
                    "product_code": product["product_code"], "sku_id": "",
                    "kind": "PRICE_DOWN", "notify": False,
                    "title": "降价（未达预期）：%s" % name,
                    "detail": "¥%s → ¥%s（较上次监控降 ¥%s%s，未达预期 ¥%s）"
                              % (old_price, new_price, d_last, off_txt, target),
                    "old_val": str(old_price), "new_val": str(new_price), "ts": ts})
        else:                                                        # 涨价：仅记录
            if hit and target is not None and new_price > target:
                hit = 0                                              # 涨回预期之上，复位
            events.append({
                "product_code": product["product_code"], "sku_id": "",
                "kind": "PRICE_UP", "notify": False,
                "title": "涨价：%s" % name,
                "detail": "¥%s → ¥%s（仅记录）" % (old_price, new_price),
                "old_val": str(old_price), "new_val": str(new_price), "ts": ts})

        state["target_hit"] = hit
        return events, state

    # ---------- 是否通知 ----------
    def _display_color(self, product_code: str, sku: dict) -> str:
        """与前端矩阵一致：同色名多商品码时显示为 颜色(商品码)。"""
        skus = self.db.list_skus(product_code)
        cnt: Dict[str, int] = {}
        for s in skus:
            if s.get("color"):
                cnt[s["color"]] = cnt.get(s["color"], 0) + 1
        c = sku.get("color") or ""
        if c and cnt.get(c, 0) > 1 and sku.get("sub_code"):
            return "%s(%s)" % (c, sku["sub_code"])
        return c

    def _should_notify(self, ev: dict, watches: List[dict]) -> bool:
        if "notify" in ev:                       # 价格类事件自带推送标记
            return bool(ev.get("notify"))
        mine = [w for w in watches if w["product_code"] == ev["product_code"]]
        if not mine:
            return False                          # 无订阅：库存类仅记录不推送
        # 商城「关注降价」写入的是 (颜色='', 尺码='') 的通配订阅 —— 它只订阅价格，
        # 不订阅库存，否则一个商品上百个 SKU 的补货/断货会淹没通知。
        if not any((w.get("size") or w.get("color")) for w in mine):
            return False
        sku = self._find_sku(ev["sku_id"])
        if not sku:
            return False
        disp = self._display_color(ev["product_code"], sku)
        for w in mine:
            if not (w.get("size") or w.get("color")):
                continue
            if w["size"] and sku.get("size") and w["size"] != sku["size"]:
                continue
            if w["color"] and disp and w["color"] != disp:
                continue
            return True
        return False

    def _find_sku(self, sku_id: str) -> Optional[dict]:
        return self.db.get_sku(sku_id)

    # ---------- PushPlus 图文 HTML ----------
    def _rich_html(self, product: dict, events: List[dict], snap: dict) -> str:
        """生成 PushPlus HTML 模板通知：商品图 + 价格 + 事件明细。"""
        name = snap.get("name") or product.get("name") or product["code"]
        cur = snap.get("min_price") or product.get("cur_price")
        origin = snap.get("origin_price") or product.get("origin_price")
        hist = product.get("hist_low_price")
        pic = product.get("main_pic") or ""
        pic_url = (IMG_BASE + pic) if pic else ""
        # 升级到高清
        import re
        pic_url = re.sub(r"/(first|sku)/\d+/", r"/\1/1000/", pic_url)

        kind_meta = {
            "OUT": ("断货", "#999"),
            "IN": ("补货", "#1a7f37"),
            "LOW": ("低库存", "#b25e00"),
            "TRANSIT": ("即将补货", "#2563a8"),
            "PRICE_DOWN": ("降价", "#d0021b"),
            "TARGET_HIT": ("达标", "#1a7f37"),
            "PRICE_UP": ("涨价", "#6b6b6b"),
        }
        rows_html = ""
        for e in events:
            label, color = kind_meta.get(e["kind"], (e["kind"], "#444"))
            rows_html += '<tr><td style="padding:5px 10px;color:%s;font-weight:600">%s</td>' % (color, label)
            rows_html += '<td style="padding:5px 10px">%s<br><span style="color:#888;font-size:12px">%s</span></td></tr>' % (
                e.get("title", ""), e.get("detail", ""))

        price_html = "¥%s" % cur if cur else "-"
        if origin and cur and origin != cur:
            price_html += ' <span style="text-decoration:line-through;color:#999">¥%s</span>' % origin
            price_html += ' <span style="color:#d0021b;font-weight:600">已降 ¥%s</span>' % round(origin - cur, 2)
        if hist and hist == cur:
            price_html += ' <span style="color:#1a7f37;font-weight:600">历史新低</span>'

        html = '<div style="font-family:sans-serif;max-width:600px">'
        if pic_url:
            html += '<img src="%s" style="width:200px;border-radius:8px;margin-bottom:12px">' % pic_url
        html += '<h3 style="margin:0 0 4px">%s</h3>' % name
        html += '<p style="color:#666;margin:0 0 10px">编号 %s · 现价 <b>%s</b>' % (product["code"], price_html)
        if hist:
            html += ' · 史低 ¥%s' % hist
        html += '</p>'
        html += '<table style="border-collapse:collapse;width:100%%;border:1px solid #eee;border-radius:6px">'
        html += rows_html
        html += '</table>'
        html += '<p style="color:#999;font-size:12px;margin-top:10px">%s · 优衣库尺码库存监控</p>' % \
                time.strftime("%m-%d %H:%M")
        html += '</div>'
        return html

    # ---------- 单个商品 ----------
    def collect_product(self, code: str, force: bool = False) -> dict:
        p = self.db.get_product(code)
        if not p:
            return {"ok": False, "message": "商品不存在: %s" % code}
        if p.get("gone_ts") and not force:
            # 已判定下架的商品不再每轮无效请求；重新上架由商品库扫描自动恢复
            return {"ok": False, "message":
                    "%s 已被判定下架（最后在售 %s），已暂停自动采集；"
                    "重新上架后自动恢复（可手动强制刷新一次验证）" % (
                        code, time.strftime("%Y-%m-%d", time.localtime(
                            p.get("last_seen_ts") or p.get("gone_ts"))))}
        cfg = self.db.all_settings()
        threshold = int(cfg.get("low_threshold") or 2)

        try:
            snap = self.client.snapshot(p["product_code"])
        except UQError as e:
            self.db.add_log(0, "%s 采集失败: %s" % (code, e))
            return {"ok": False, "message": str(e)}

        prev = self.db.latest_stock(p["product_code"])
        ts = int(time.time())

        self.db.upsert_skus(p["product_code"], [
            {"sku_id": s["sku_id"], "size": s.get("size"), "size_no": s.get("size_no"),
             "color": s.get("color"), "color_no": s.get("color_no"),
             "sub_code": s.get("sub_code"), "price": s.get("price")}
            for s in snap["skus"]
        ])
        self.db.add_snapshots(ts, [
            {"sku_id": s["sku_id"], "express": s.get("express"), "bpl": s.get("bpl"),
             "dc": s.get("dc"), "transit": s.get("transit")}
            for s in snap["skus"]
        ])

        events = self._diff(p, prev, snap, ts, threshold)
        price_events, state = self._price_events(p, snap.get("min_price"), ts)
        events += price_events
        for ev in events:
            self.db.add_event(ev)

        # 价格快照：无记录时补基线，此后仅在价格变化时记录
        new_price = state["cur_price"]
        if new_price is not None:
            if not self.db.price_series(code, 1) or p.get("cur_price") is None \
                    or new_price != p.get("cur_price"):
                self.db.add_price_snapshot(code, ts, new_price)

        # 老商品补主图/色图（仅一次）：图片字段只在搜索接口返回
        main_pic = p.get("main_pic")
        color_pics = p.get("color_pics")
        if not main_pic or not color_pics:
            try:
                it = self.client.resolve(code)
                if not main_pic:
                    main_pic = it.get("mainPic") or None
                if not color_pics:
                    cp = it.get("colorPic") or []
                    color_pics = json.dumps(cp, ensure_ascii=False) if cp else None
            except UQError:
                pass

        self.db.upsert_product({
            "code": code, "product_code": p["product_code"],
            "name": snap.get("name") or p.get("name"),
            "origin_price": snap.get("origin_price")
                if snap.get("origin_price") is not None else p.get("origin_price"),
            "cur_price": state["cur_price"]
                if state["cur_price"] is not None else p.get("cur_price"),
            "store_count": p.get("store_count"),
            "limited_begin": p.get("limited_begin"), "limited_end": p.get("limited_end"),
            "target_price": p.get("target_price"),
            "hist_low_price": state["hist_low_price"],
            "target_hit": state["target_hit"],
            "main_pic": main_pic,
            "color_pics": color_pics,
        })

        # 通知
        watches = self.db.list_watches()
        pending = [e for e in events if self._should_notify(e, watches)]
        notified_msg = ""
        if pending:
            try:
                ch = cfg.get("notify_channel", "ntfy")
                title = "优衣库监控 · %d 条更新" % len(pending)
                if ch == "pushplus":
                    body = self._rich_html(p, pending, snap)
                else:
                    from notifier import batch_summary
                    body = batch_summary(pending)
                r = send(cfg, title, body)
                notified_msg = " | 通知: %s" % r
            except Exception as e:                      # noqa: BLE001
                notified_msg = " | 通知失败: %s" % e

        msg = "%s 完成：%d SKU，%d 条事件%s" % (code, len(snap["skus"]), len(events), notified_msg)
        self.db.add_log(1, msg)
        return {"ok": True, "message": msg, "events": len(events), "skus": len(snap["skus"])}

    # ---------- 全部 ----------
    def collect_all(self) -> dict:
        """全量监控商品（订阅/手动添加，track_level='full'）的库存+价格采集。

        已判定下架（gone_ts）的商品跳过：详情接口对不存在/下架商品会返回
        PD_PRODUCT_03，持续请求只会刷失败日志（实测确认）。
        """
        products = [p for p in self.db.list_products(track_level="full")
                    if p.get("enabled") and not p.get("gone_ts")]
        ok = fail = 0
        total_events = 0
        msgs = []
        for p in products:
            r = self.collect_product(p["code"])
            if r.get("ok"):
                ok += 1
                total_events += r.get("events", 0)
            else:
                fail += 1
            msgs.append(r.get("message", ""))
        summary = "采集完成：成功 %d / 失败 %d / 事件 %d" % (ok, fail, total_events)
        self.db.add_log(1 if fail == 0 else 0, summary)
        return {"ok": fail == 0, "summary": summary, "ok_count": ok,
                "fail_count": fail, "events": total_events, "details": msgs}

    # ================= 商品库扫描（商城）=================

    def sweep_catalog(self, scope: str = "ALL", max_pages: int = 200) -> dict:
        """按官方分类翻页扫描商品库（默认 ALL）：入库价格/图片/分类 + 变价检测。

        价格事件只对 track_level='full'（订阅中）商品产出，通知在扫描结束后合并发送一次；
        其余商品的变价只落 price_snapshots，由「变价信息」页派生展示。
        """
        cfg = self.db.all_settings()
        ts = int(time.time())
        rank = 0
        page = 1
        pages = 0
        total = 0
        new_items = 0
        price_changes = 0
        seen: List[str] = []
        seen_set = set()
        dups = 0
        first_sum = 0
        pending: List[dict] = []
        watches = self.db.list_watches()
        err = ""
        try:
            while page <= max_pages:
                r = self.client.browse(category_code=scope, page=page,
                                       page_size=PAGE_SIZE_MAX, rank="overall")
                items = r.get("items") or []
                if not items:
                    break
                if page == 1:
                    first_sum = r.get("product_sum") or 0
                for it in items:
                    norm = normalize_item(it)
                    code = norm.get("code")
                    if not code:
                        continue
                    if code in seen_set:
                        # 实测：官方 ALL 列表里同一商品码会重复出现（不同聚合/价格），
                        # 以首次出现（官方排序靠前）为准，避免同轮写入互相覆盖。
                        dups += 1
                        continue
                    seen_set.add(code)
                    prev = self.db.get_product(code)          # 更新前，供价格事件比对
                    res = self.db.upsert_catalog_item(norm, rank_overall=rank, ts=ts)
                    rank += 1
                    total += 1
                    seen.append(code)
                    cate_codes = [c.get("cateCode") for c in (norm.get("cate_sequence") or [])]
                    if cate_codes:
                        self.db.set_item_categories(code, cate_codes)
                    if res.get("inserted"):
                        new_items += 1
                        if norm.get("min_price") is not None:
                            self.db.add_price_snapshot(code, ts, norm["min_price"])
                    elif res.get("price_changed"):
                        price_changes += 1
                        self.db.add_price_snapshot(code, ts, res["new_price"])
                        if prev and prev.get("track_level") == "full":
                            events, state = self._price_events(prev, res["new_price"], ts)
                            for ev in events:
                                self.db.add_event(ev)
                            if state.get("target_hit") != prev.get("target_hit"):
                                self.db.set_target_hit(code, state["target_hit"])
                            pending += [e for e in events if self._should_notify(e, watches)]
                pages += 1
                if pages * PAGE_SIZE_MAX >= (r.get("product_sum") or 0):
                    break
                page += 1
        except UQError as e:
            err = str(e)

        # ---- 下架判定：只在「完整扫完一轮」时做（半途中断/少页会误杀）----
        gone_cnt = 0
        complete = pages > 0 and (total + dups) >= first_sum and not err
        if complete:
            gone_cnt, gone_pending = self.handle_gone(int(cfg.get("catalog_hide_days") or 3), ts)
            pending += gone_pending

        notified = ""
        if pending:
            try:
                ch = cfg.get("notify_channel", "ntfy")
                title = "优衣库商品库 · %d 条更新" % len(pending)
                if ch == "pushplus":
                    body = self._rich_html(pending[0], pending, None)
                else:
                    from notifier import batch_summary
                    body = batch_summary(pending)
                notified = " | 通知: %s" % send(cfg, title, body)
            except Exception as e:                          # noqa: BLE001
                notified = " | 通知失败: %s" % e

        # ---- 史低自愈：史低必须等于价格历史的最低点 ----
        fixed_low = self.db.align_hist_low() if complete else 0

        msg = "商品库扫描(%s)：%d 页 / %d 件 / 新增 %d / 变价 %d / 下架 %d%s%s%s%s" % (
            scope, pages, total, new_items, price_changes, gone_cnt,
            (" / 史低修正 %d" % fixed_low) if fixed_low else "",
            (" / 重复条目 %d 已跳过" % dups) if dups else "", notified,
            (" | 中断: " + err) if err else "")
        self.db.log_sweep(scope, pages, total, new_items, price_changes, 0 if err else 1, msg)
        self.db.add_log(0 if err else 1, msg)
        return {"ok": not err, "scope": scope, "pages": pages, "items": total,
                "new_items": new_items, "price_changes": price_changes,
                "gone": gone_cnt, "message": msg, "error": err}

    def handle_gone(self, hide_days: int, ts: int):
        """判定下架并为「关注中」的下架商品生成 GONE 事件。

        返回 (下架数量, 待通知条目)；重新上架由扫描时的 upsert 自动清空 gone_ts。
        """
        gone_cnt = 0
        pending: List[dict] = []
        for g in self.db.mark_gone(hide_days):
            gone_cnt += 1
            if g.get("track_level") != "full":
                continue
            last = time.strftime("%Y-%m-%d", time.localtime(g.get("last_seen_ts") or ts))
            self.db.add_event({
                "product_code": g.get("product_code"), "sku_id": None,
                "kind": "GONE", "notify": True, "ts": ts,
                "title": "已下架：%s" % (g.get("name") or g.get("code")),
                "detail": "连续 %d 天未在官网分类列表中出现（最后在售 %s，现价 ¥%s），"
                          "已暂停自动采集" % (hide_days, last, g.get("cur_price")),
                "old_val": None, "new_val": None})
            pending.append({
                "product_code": g.get("product_code"), "kind": "GONE",
                "title": "已下架：%s（%s）" % (g.get("name") or "", g.get("code")),
                "detail": "连续 %d 天未在官网出现（最后在售 %s），已暂停采集；重新上架自动恢复"
                          % (hide_days, last), "notify": True})
        return gone_cnt, pending

    def sweep_new_arrivals(self, max_pages: int = 12) -> dict:
        """官方「新作商品」榜单（identity=new_product + rank=newest）-> rank_new 排序位次。"""
        rank = 0
        page = 1
        total = 0
        try:
            while page <= max_pages:
                r = self.client.browse(rank="newest", identity=["new_product"],
                                       page=page, page_size=PAGE_SIZE_MAX)
                items = r.get("items") or []
                if not items:
                    break
                for it in items:
                    norm = normalize_item(it)
                    code = norm.get("code")
                    if not code:
                        continue
                    # 旁路榜单：已存在的商品只补排名、不改价格，避免交叉榜单把史低带偏
                    res = self.db.upsert_catalog_item(norm, ts=int(time.time()),
                                                      update_price=False)
                    if res.get("inserted") and norm.get("min_price") is not None:
                        self.db.add_price_snapshot(code, int(time.time()), norm["min_price"])
                    self.db.set_rank_new(code, rank)
                    rank += 1
                    total += 1
                if total >= (r.get("product_sum") or 0):
                    break
                page += 1
        except UQError as e:
            self.db.add_log(0, "新品榜扫描失败: %s" % e)
            return {"ok": False, "items": total, "message": str(e)}
        msg = "新品榜扫描：%d 件" % total
        self.db.add_log(1, msg)
        return {"ok": True, "items": total, "message": msg}

    def sync_categories(self, budget: int = 80) -> dict:
        """抓官方分类树（withSideBar 的「品类」facet）-> categories 表。

        一次请求某顶层分类即返回其整棵子树（含中文名/层级），故请求量≈顶层分类数。
        """
        seeds = list(dict.fromkeys(CATEGORY_SEEDS + self.db.top_level_codes()))
        nodes: Dict[str, dict] = {}
        used = 0
        errors = 0
        for code in seeds:
            if used >= budget:
                break
            try:
                tree = self.client.category_tree(code)
                used += 1
            except UQError:
                errors += 1
                continue
            for n in tree:
                cur = nodes.get(n["code"])
                if cur is None or n["level"] < cur["level"]:
                    nodes[n["code"]] = n
        if nodes:
            self.db.upsert_categories(list(nodes.values()))
        # 二次展开：level<=2 且没有子节点的（可能是没被父树覆盖的顶层）
        have_children = {n["parent"] for n in nodes.values() if n.get("parent")}
        todo = [c for c, n in nodes.items()
                if n["level"] <= 2 and c not in have_children and c not in seeds]
        for code in todo:
            if used >= budget:
                break
            try:
                tree = self.client.category_tree(code)
                used += 1
            except UQError:
                errors += 1
                continue
            for n in tree:
                cur = nodes.get(n["code"])
                if cur is None or n["level"] < cur["level"]:
                    nodes[n["code"]] = n
            self.db.upsert_categories(list(nodes.values()))
        msg = "分类同步：%d 个节点（%d 次请求，%d 次失败）" % (len(nodes), used, errors)
        self.db.add_log(1 if nodes else 0, msg)
        return {"ok": bool(nodes), "nodes": len(nodes), "requests": used, "message": msg}

