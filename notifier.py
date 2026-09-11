# -*- coding: utf-8 -*-
"""变更通知：ntfy / 企业微信机器人 / PushPlus。含免打扰时段判断。"""
import time
from typing import List

import httpx

TIMEOUT = 15.0


def in_quiet_hours(quiet: str) -> bool:
    """quiet 形如 '23:00-08:00'，跨零点也支持。格式异常则视为不静默。"""
    if not quiet or "-" not in quiet:
        return False
    try:
        start, end = [x.strip() for x in quiet.split("-", 1)]
        sh, sm = [int(x) for x in start.split(":")]
        eh, em = [int(x) for x in end.split(":")]
    except Exception:
        return False
    now = time.localtime()
    cur = now.tm_hour * 60 + now.tm_min
    s, e = sh * 60 + sm, eh * 60 + em
    return cur >= s or cur < e if s > e else s <= cur < e


def _post(url: str, **kw) -> str:
    r = httpx.post(url, timeout=TIMEOUT, **kw)
    if r.status_code >= 400:
        raise RuntimeError("HTTP %s: %s" % (r.status_code, r.text[:200]))
    return r.text[:200]


def send(cfg: dict, title: str, body: str) -> str:
    """cfg 来自 settings。未启用通知时直接返回。"""
    if not cfg.get("notify_enabled"):
        return "通知未启用"
    if in_quiet_hours(cfg.get("quiet_hours", "")):
        return "免打扰时段，已跳过"

    ch = cfg.get("notify_channel", "ntfy")
    if ch == "ntfy":
        topic = (cfg.get("ntfy_topic") or "").strip()
        if not topic:
            return "ntfy topic 未配置"
        server = (cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
        return _post("%s/%s" % (server, topic), content=body.encode("utf-8"),
                     headers={"Title": title, "Tags": "tshirt",
                              "Content-Type": "text/plain; charset=utf-8"})
    if ch == "wecom":
        hook = (cfg.get("wecom_webhook") or "").strip()
        if not hook:
            return "企业微信 webhook 未配置"
        return _post(hook, json={"msgtype": "text",
                                 "text": {"content": "%s\n%s" % (title, body)}})
    if ch == "pushplus":
        token = (cfg.get("pp_token") or "").strip()
        if not token:
            return "PushPlus token 未配置"
        return _post("http://www.pushplus.plus/send", json={
            "token": token, "title": title, "content": body, "template": "html"})
    return "未知渠道 %s" % ch


def batch_summary(events: List[dict]) -> str:
    """把多条事件压成一条消息（同 SKU 多次抖动时避免刷屏）。"""
    lines = []
    for e in events[:20]:
        lines.append("· %s" % (e.get("title") or ""))
        if e.get("detail"):
            lines.append("  %s" % e["detail"])
    if len(events) > 20:
        lines.append("… 另有 %d 条" % (len(events) - 20))
    return "\n".join(lines)
