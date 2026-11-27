"""Watches UN press releases for 1718 Committee vessel announcements.

The 1718 Designated Vessels List PDF sits behind a bot challenge that blocks
every automated download (GitHub runners included), but press.un.org is open.
The 1718 Committee announces every vessel designation, amendment and removal
there, so a new vessel-related 1718 press release means the PDF needs a
manual refresh: the build marks the feed "pending" until it is uploaded.
"""
from __future__ import annotations

import html
import re
import urllib.request

LIST_URL = "https://press.un.org/en/content/security-council/press-release"
BASE = "https://press.un.org"
UA = "un-sanctioned-vessels feed (+https://github.com/nelson920515/un-sanctioned-vessels)"

LINK_RE = re.compile(r'href="(/en/(\d{4})/sc(\d+)\.doc\.htm)"[^>]*>(.*?)</a>', re.S)


def _get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def list_releases(pages: int = 3, fetch=_get) -> list[dict]:
    """Newest first: [{'number': 16519, 'url': ..., 'title': ...}]"""
    seen: dict[int, dict] = {}
    for page in range(pages):
        doc = fetch(LIST_URL + (f"?page={page}" if page else ""))
        for path, _year, num, title in LINK_RE.findall(doc):
            n = int(num)
            if n not in seen:
                seen[n] = {"number": n, "url": BASE + path, "title": _text(title)}
    if not seen:
        raise RuntimeError("no Security Council press releases found on press.un.org (page layout changed?)")
    return sorted(seen.values(), key=lambda r: r["number"], reverse=True)


def is_1718(title: str) -> bool:
    t = title.lower()
    return "1718" in t or "democratic people" in t or "dprk" in t


def vessel_related(body: str) -> bool:
    b = body.lower()
    return "vessel" in b and ("imo" in b or "designated vessels list" in b or "port entry" in b or "deflag" in b or "de-flag" in b)


def check(last_seen: int, fetch=_get) -> tuple[int, list[dict]]:
    """Returns (newest number seen, vessel-related 1718 releases newer than
    last_seen). With last_seen 0 (first run) nothing is flagged: the current
    PDF is taken as up to date."""
    releases = list_releases(fetch=fetch)
    newest = max(r["number"] for r in releases)
    if last_seen <= 0:
        return newest, []
    flagged = []
    for r in releases:
        if r["number"] <= last_seen or not is_1718(r["title"]):
            continue
        body = _text(fetch(r["url"]))
        if vessel_related(body):
            r = dict(r)
            r["imos"] = sorted(set(re.findall(r"IMO[^0-9]{0,20}(\d{7})", body)))
            flagged.append(r)
    return newest, flagged
