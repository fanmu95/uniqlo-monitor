# -*- coding: utf-8 -*-
"""
优衣库（中国区）接口客户端

三跳取数：
  1) 搜索        POST /p/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN
  2) H5 详情     GET  /h/product/i/product/spu/h5/query/{productCode}/zh_CN
  3) 尺码库存    POST /h/stock/stock/query/zh_CN

约束：只走真实接口。任何一步失败即抛 UQError，绝不返回构造/兜底数据。
"""
import re
import time
import threading

import httpx

# styleText 形如 "486980/32深米色"：商品页会把同款的不同商品码聚合展示，
# 此时 style 字段会退化成 "任意色"，只有 styleText 保留了真实颜色。
STYLE_RE = re.compile(r"^(\d{6})/(\d{2,3})(.*)$")

GW_PC = "https://d.uniqlo.cn/p"
GW_H5 = "https://d.uniqlo.cn/h"

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
UA_H5 = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

HDR_PC = {"User-Agent": UA_PC, "Content-Type": "application/json",
          "Referer": "https://www.uniqlo.cn/", "authorization": ""}
HDR_H5 = {"User-Agent": UA_H5, "Content-Type": "application/json",
          "Referer": "https://h.uniqlo.cn/", "authorization": ""}


class UQError(Exception):
    """接口调用失败或返回结构异常。"""


def _search_body(keyword: str, page: int, page_size: int) -> dict:
    return {
        "url": "/search",
        "pageInfo": {"page": page, "pageSize": page_size, "withSideBar": "N"},
        "belongTo": "pc", "rank": "overall",
        "priceRange": {"low": 0, "high": 0},
        "color": [], "size": [], "season": [], "material": [], "sex": [],
        "categoryFilter": {}, "identity": [], "insiteDescription": str(keyword),
        "exist": [], "categoryCode": "ALL", "searchFlag": True,
    }


class UQClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 20.0, retries: int = 2):
        self.min_interval = min_interval
        self.retries = retries
        self._lock = threading.Lock()
        self._last = 0.0
        self._cli = httpx.Client(timeout=timeout, follow_redirects=True)

    # ---------- 基础 ----------
    def _throttle(self):
        with self._lock:
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def _request(self, method: str, url: str, headers: dict, body: dict = None) -> dict:
        last_err = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                r = self._cli.request(method, url, headers=headers,
                                      json=body if body is not None else None)
                if r.status_code != 200:
                    last_err = "HTTP %s" % r.status_code
                    time.sleep(0.8)
                    continue
                data = r.json()
                if data.get("success") is False:
                    raise UQError("接口返回失败: %s" % (data.get("msgCode") or data.get("msg") or ""))
                return data
            except UQError:
                raise
            except Exception as e:          # noqa: BLE001
                last_err = str(e)
                time.sleep(0.8)
        raise UQError("请求失败 %s (%s)" % (url, last_err))

    # ---------- 1. 搜索 ----------
    def search(self, keyword: str, page: int = 1, page_size: int = 20) -> list:
        """关键词/商品编号搜索，返回商品条目列表。"""
        d = self._request("POST",
                          GW_PC + "/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN",
                          HDR_PC, _search_body(keyword, page, page_size))
        resp = d.get("resp") or []
        if len(resp) < 3:
            return []
        return resp[1] or []

    def resolve(self, code: str) -> dict:
        """6 位商品编号 -> 商品条目（含内部 productCode）。精确匹配，不做模糊回退。"""
        code = str(code).strip()
        for it in self.search(code, page_size=20):
            if str(it.get("code")) == code:
                return it
        raise UQError("未找到商品编号 %s（可能已下架）" % code)

    # ---------- 2. H5 详情 ----------
    def detail(self, product_code: str) -> dict:
        """返回 resp[0]，含 summary / rows(SKU 字典) / sizeList。"""
        d = self._request("GET",
                          "%s/product/i/product/spu/h5/query/%s/zh_CN" % (GW_H5, product_code),
                          HDR_H5)
        resp = d.get("resp") or []
        if not resp:
            raise UQError("详情接口无数据: %s" % product_code)
        top = resp[0]
        if not (top.get("rows") or []):
            raise UQError("详情接口无 SKU: %s" % product_code)
        return top

    # ---------- 3. 尺码库存 ----------
    def stock(self, product_code: str) -> dict:
        """返回 resp[0]，skuStocks 等以 SKU productId 为 key。"""
        d = self._request("POST", GW_H5 + "/stock/stock/query/zh_CN", HDR_H5,
                          {"type": "DETAIL", "distribution": "EXPRESS",
                           "productCode": product_code})
        resp = d.get("resp") or []
        if not resp:
            raise UQError("库存接口无数据: %s" % product_code)
        return resp[0]

    # ---------- 组合：一次拿全 ----------
    def snapshot(self, product_code: str) -> dict:
        """详情 + 库存，拼成结构化快照。"""
        top = self.detail(product_code)
        st = self.stock(product_code)
        summary = top.get("summary") or {}
        rows = top.get("rows") or []

        express = st.get("skuStocks") or {}
        bpl = st.get("bplStocks") or {}
        dc = st.get("dcSkuStocks") or {}
        transit = st.get("transitSkuStocks") or {}

        skus = []
        for r in rows:
            sku_id = r.get("productId")
            if not sku_id:
                continue
            # 优先从 styleText 解析真实颜色（style 字段在同款聚合时会变成 "任意色"）
            sub_code, color = "", (r.get("style") or "").strip()
            m = STYLE_RE.match((r.get("styleText") or "").strip())
            if m:
                sub_code, _cno, cname = m.group(1), m.group(2), m.group(3).strip()
                if cname:
                    color = cname
            skus.append({
                "sku_id": sku_id,
                "size": r.get("size") or r.get("sizeText") or "",
                "size_no": r.get("sizeNo") or "",
                "color": color,
                "color_no": r.get("colorNo") or "",
                "sub_code": sub_code,
                "price": r.get("price"),
                "express": express.get(sku_id),
                "bpl": bpl.get(sku_id),
                "dc": dc.get(sku_id),
                "transit": transit.get(sku_id),
            })

        return {
            "product_code": product_code,
            "name": summary.get("name") or "",
            "origin_price": summary.get("originPrice"),
            "min_price": summary.get("minPrice"),
            "max_price": summary.get("maxPrice"),
            "has_stock": st.get("hasStock"),
            "total_stock": st.get("totalStock"),
            "skus": skus,
        }

    def close(self):
        try:
            self._cli.close()
        except Exception:
            pass
