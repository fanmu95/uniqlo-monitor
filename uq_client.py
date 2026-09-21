# -*- coding: utf-8 -*-
"""
优衣库（中国区）接口客户端

三跳取数：
  1) 搜索        POST /p/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN
  2) H5 详情     GET  /h/product/i/product/spu/h5/query/{productCode}/zh_CN
  3) 尺码库存    POST /h/stock/stock/query/zh_CN

商城浏览（4）：
  4) 分类列表    POST /p/hmall-sc-service/search/searchWithCategoryCodeAndConditions/zh_CN
     - categoryCode 与官网 /c/<slug>.html 的 slug 一致（2026-09-21 实测：3msweat/utyinhua_m/4310011315 均可）
     - pageSize 服务端硬顶 25 条/页；withSideBar="Y" 时 resp[0] 为筛选面（含「品类」树）
     - rank: overall / newest / new / priceAsc / priceDesc / sales
     - identity: new_product(新作商品) / time_doptimal(限时特优) / concessional_rate(超值精选)
                 / revision(修改裤长) / pickUp(支持门店自提)

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

LIST_PATH = "/hmall-sc-service/search/searchWithCategoryCodeAndConditions/zh_CN"
SEARCH_PATH = "/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN"

PAGE_SIZE_MAX = 25          # 服务端硬顶（实测请求 30/50/100 均只返回 25）

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


def _list_body(category_code: str = "ALL", page: int = 1, page_size: int = PAGE_SIZE_MAX,
               rank: str = "overall", with_side_bar: str = "N", sex=None, identity=None,
               price_low: float = 0, price_high: float = 0,
               keyword: str = "", search_flag: bool = False) -> dict:
    """分类列表 / 关键词搜索共用请求体。categoryCode 用官网 slug（如 3msweat）。"""
    return {
        "url": "/search" if search_flag else "/c/%s.html" % category_code,
        "pageInfo": {"page": page, "pageSize": page_size, "withSideBar": with_side_bar},
        "belongTo": "pc", "rank": rank,
        "priceRange": {"low": price_low, "high": price_high},
        "color": [], "size": [], "season": [], "material": [], "sex": sex or [],
        "categoryFilter": {}, "identity": identity or [],
        "insiteDescription": str(keyword),
        "exist": [], "categoryCode": category_code, "searchFlag": search_flag,
    }


def _search_body(keyword: str, page: int, page_size: int) -> dict:
    return _list_body(keyword=keyword, page=page, page_size=page_size, search_flag=True)


def _parse_list(d: dict) -> dict:
    """解析列表响应：resp[0]=facets, resp[1]=商品数组, resp[2]=分页元信息。"""
    resp = d.get("resp") or []
    items = resp[1] if len(resp) > 1 and isinstance(resp[1], list) else []
    meta = resp[2] if len(resp) > 2 and isinstance(resp[2], dict) else {}
    facets = resp[0] if resp and isinstance(resp[0], list) else []
    return {
        "items": items,
        "product_sum": meta.get("productSum") or 0,
        "page_size": meta.get("pageSize") or 0,
        "facets": facets,
    }


def normalize_item(it: dict) -> dict:
    """列表条目 -> 入库结构（字段名对齐 products 表）。只做字段搬运，不构造数据。"""
    return {
        "code": str(it.get("code") or "").strip(),
        "product_code": it.get("productCode") or "",
        "name": it.get("name4zhCN") or it.get("name") or "",
        "min_price": it.get("minPrice"),
        "origin_price": it.get("originPrice"),
        "store_count": len(it.get("stores") or []),
        "main_pic": it.get("mainPic") or "",
        "color_pics": it.get("colorPic") or [],
        "chip_pics": it.get("chipPic") or [],
        "color_nos": it.get("colorNums") or [],
        "gender": it.get("gender4zhCN") or "",
        "sizes": it.get("size") or [],
        "min_size": it.get("minSize4zhCN") or "",
        "max_size": it.get("maxSize4zhCN") or "",
        "stock_flag": it.get("stock") or "",
        "identity": it.get("identity") or [],
        "style_text": it.get("styleText") or [],
        "season": it.get("season4zhCN") or "",
        "material": it.get("material4zhCN") or "",
        "limited_begin": it.get("timeLimitedBegin"),
        "limited_end": it.get("timeLimitedEnd"),
        "upstream_new_ts": it.get("new"),
        "sales": it.get("sales"),
        "evaluation_count": it.get("evaluationCount"),
        "score": it.get("score"),
        "is_presale": it.get("isPresale"),
        "cate_sequence": it.get("cateSequence") or [],
    }


def parse_category_tree(facets: list) -> list:
    """从筛选面里提取「品类」树，返回扁平列表 [{code,name,parent,level,sort}]。

    实测：请求任一分类页 + withSideBar="Y"，返回的「品类」facet 含该分类的
    祖先链与整棵子树（categoryCode 与官网 slug 一致）。
    """
    out = []

    def walk(nodes, parent, level):
        for n in nodes or []:
            code = (n or {}).get("categoryCode")
            if not code:
                continue
            out.append({
                "code": code,
                "name": (n.get("categoryName") or "").strip(),
                "parent": parent,
                "level": level,
                "sort": n.get("categorySequence") or 0,
            })
            walk(n.get("childs"), code, level + 1)

    for f in facets or []:
        if (f or {}).get("name") == "品类":
            walk(f.get("item"), None, 1)
    return out



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

    # ---------- 1b. 分类列表 / 筛选面 ----------
    def browse(self, category_code: str = "ALL", page: int = 1,
               page_size: int = PAGE_SIZE_MAX, rank: str = "overall",
               with_side_bar: str = "N", sex=None, identity=None,
               price_low: float = 0, price_high: float = 0) -> dict:
        """分类浏览。返回 {items, product_sum, page_size, facets}。"""
        d = self._request("POST", GW_PC + LIST_PATH, HDR_PC,
                          _list_body(category_code=category_code, page=page,
                                     page_size=page_size, rank=rank,
                                     with_side_bar=with_side_bar, sex=sex,
                                     identity=identity, price_low=price_low,
                                     price_high=price_high))
        return _parse_list(d)

    def browse_keyword(self, keyword: str, page: int = 1,
                       page_size: int = PAGE_SIZE_MAX, rank: str = "overall") -> dict:
        """关键词搜索（分类接口版，支持 rank/分页）。"""
        d = self._request("POST", GW_PC + LIST_PATH, HDR_PC,
                          _list_body(keyword=keyword, page=page, page_size=page_size,
                                     rank=rank, search_flag=True))
        return _parse_list(d)

    def category_tree(self, seed_code: str = "ALL") -> list:
        """请求一个分类页的筛选面，返回其「品类」树的扁平节点列表。"""
        r = self.browse(category_code=seed_code, page=1, page_size=5, with_side_bar="Y")
        return parse_category_tree(r["facets"])

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
