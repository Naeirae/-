#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PROORGANIC public research collector.

Collects only publicly accessible information:
- PROORGANIC website pages, product pages, links/CTA and UTM labels
- public Telegram channel posts and links
- observed purchase destinations and launch routes
- public technical signals such as Yandex Metrika presence
- product/task vocabulary from public product names/text

Outputs:
CSV + XLSX + SQLite + JSON + GraphML + standalone HTML report.

Important:
This reconstructs OBSERVED PUBLIC ROUTES. It does not claim actual clicks,
conversions, sales, attribution, cohorts or repeat purchases without internal data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_lib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
import traceback
import urllib.parse
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import requests
from bs4 import BeautifulSoup

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except Exception:
    Workbook = None


VERSION = "2.0.0"
DEFAULT_CONFIG: dict[str, Any] = {
    "site": {
        "base_url": "https://proorganic.ru/",
        "start_urls": [
            "https://proorganic.ru/",
            "https://proorganic.ru/catalog"
        ],
        "max_pages": 300,
        "max_depth": 5,
        "delay_seconds": 0.30,
        "timeout_seconds": 25,
        "include_queryless_only": True
    },
    "telegram": {
        "channel": "proorganic_vitamin",
        "max_posts": 800,
        "delay_seconds": 0.35,
        "timeout_seconds": 25
    },
    "external_links": {
        "resolve_redirects": True,
        "max_to_resolve": 250,
        "timeout_seconds": 15,
        "domains": [
            "taplink.cc", "taplink.ws", "taplink.ru",
            "ozon.ru", "www.ozon.ru",
            "wildberries.ru", "www.wildberries.ru",
            "wb.ru", "www.wb.ru"
        ]
    },
    "output": {
        "root": "output",
        "make_xlsx": True,
        "make_sqlite": True,
        "make_graphml": True,
        "make_html_report": True
    },
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36 PROORGANICResearchCollector/2.0"
}


TASK_RULES: list[tuple[str, list[str]]] = [
    ("Дети и родители", [
        "дет", "ребен", "ребён", "для детей", "baby", "kids", "3 лет", "2-6", "6-12", "0-2"
    ]),
    ("Иммунитет", [
        "иммун", "витамин d", "витамин d3", "витамин c", "цинк", "омега-3", "омега 3"
    ]),
    ("Сон, стресс и нервная система", [
        "сон", "стресс", "антистресс", "нерв", "магний", "5-htp", "теанин", "глицин"
    ]),
    ("Уход за полостью рта", [
        "зуб", "паста", "эмаль", "дес", "кариес", "полости рта", "чувствительн"
    ]),
    ("ЖКТ, печень и микробиота", [
        "жкт", "кишеч", "микроб", "метабиот", "печен", "печён", "артишок", "силимар", "пищевар"
    ]),
    ("Сердце и сосуды", [
        "серд", "сосуд", "омега-3", "омега 3", "коэнзим", "q10"
    ]),
    ("Кожа, волосы и ногти", [
        "кож", "волос", "ногт", "коллаген", "биотин"
    ]),
    ("Женское здоровье", [
        "женск", "пмс", "фолат", "беремен", "железо"
    ])
]


CTA_PATTERNS: list[tuple[str, list[str]]] = [
    ("purchase", ["купить", "заказать", "в корзину", "оформить", "получить со скидкой"]),
    ("learn", ["подробнее", "читать", "узнать", "смотреть", "разобраться"]),
    ("subscribe", ["подпис", "телеграм", "telegram", "в канал"]),
    ("contact", ["написать", "задать вопрос", "чат", "служба заботы", "связаться"]),
    ("profile", ["войти", "профиль", "личный кабинет"]),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("\x00", "").strip()


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", safe_text(text)).strip()


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except Exception:
        return ""


def clean_url(url: str, base: str = "") -> str:
    url = safe_text(url)
    if not url:
        return ""
    if base:
        url = urllib.parse.urljoin(base, url)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return ""
    # Strip fragments. Keep query because it may contain UTM labels.
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def canonical_crawl_url(url: str, queryless: bool = True) -> str:
    parts = urllib.parse.urlsplit(url)
    query = "" if queryless else parts.query
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def parse_utm(url: str) -> dict[str, str]:
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    except Exception:
        return {}
    keys = [
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "yclid", "gclid", "fbclid", "erid"
    ]
    return {k: safe_text(q.get(k, [""])[0]) for k in keys if q.get(k)}


def destination_type(url: str, own_host: str = "proorganic.ru") -> str:
    h = host(url)
    p = urllib.parse.urlsplit(url).path.lower() if url else ""
    if not h:
        return "unknown"
    if h == own_host or h.endswith("." + own_host):
        if "/products/" in p:
            return "own_product"
        if "/catalog" in p:
            return "own_catalog"
        if "cart" in p or "basket" in p or "korzin" in p:
            return "own_cart"
        if "profile" in p or "account" in p or "login" in p:
            return "own_profile"
        return "own_site"
    if h.endswith("t.me") or h.endswith("telegram.me"):
        return "telegram"
    if "ozon." in h:
        return "ozon"
    if "wildberries." in h or h.endswith("wb.ru"):
        return "wildberries"
    if "taplink." in h:
        return "taplink"
    if h.endswith("vk.com") or h.endswith("vk.ru"):
        return "vk"
    if "youtube." in h or h == "youtu.be":
        return "youtube"
    if "rutube." in h:
        return "rutube"
    return "external"


def classify_cta(text: str) -> str:
    t = collapse_ws(text).lower()
    for label, words in CTA_PATTERNS:
        if any(w in t for w in words):
            return label
    return "other"


def detect_tasks(text: str) -> list[str]:
    t = collapse_ws(text).lower()
    out = []
    for label, keys in TASK_RULES:
        if any(k in t for k in keys):
            out.append(label)
    return out


def detect_page_type(url: str, soup: BeautifulSoup) -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    if "/products/" in path:
        return "product"
    if "/catalog" in path:
        return "catalog"
    if "cart" in path or "basket" in path or "korzin" in path:
        return "cart"
    if "profile" in path or "account" in path:
        return "profile"
    if path in ("", "/"):
        return "home"
    if soup.select_one('[itemtype*="Product"], [type="application/ld+json"]'):
        # JSON-LD may be present on non-product pages; URL rule stays primary.
        pass
    return "page"


def parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for node in soup.select('script[type="application/ld+json"]'):
        raw = node.string or node.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            if isinstance(obj.get("@graph"), list):
                for x in obj["@graph"]:
                    if isinstance(x, dict):
                        items.append(x)
            items.append(obj)
        elif isinstance(obj, list):
            items.extend(x for x in obj if isinstance(x, dict))
    return items


def product_from_page(url: str, soup: BeautifulSoup, page_text: str) -> Optional[dict[str, Any]]:
    path = urllib.parse.urlsplit(url).path.lower()
    if "/products/" not in path:
        return None

    title = ""
    price = ""
    currency = ""
    sku = ""
    availability = ""
    image = ""
    brand = ""

    for obj in parse_json_ld(soup):
        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if "Product" not in types:
            continue
        title = safe_text(obj.get("name")) or title
        sku = safe_text(obj.get("sku")) or sku
        brand_obj = obj.get("brand")
        if isinstance(brand_obj, dict):
            brand = safe_text(brand_obj.get("name"))
        elif isinstance(brand_obj, str):
            brand = brand_obj
        img = obj.get("image")
        if isinstance(img, list) and img:
            image = safe_text(img[0])
        elif isinstance(img, str):
            image = img
        offers = obj.get("offers")
        if isinstance(offers, dict):
            price = safe_text(offers.get("price"))
            currency = safe_text(offers.get("priceCurrency"))
            availability = safe_text(offers.get("availability"))
        elif isinstance(offers, list) and offers:
            o = offers[0] if isinstance(offers[0], dict) else {}
            price = safe_text(o.get("price"))
            currency = safe_text(o.get("priceCurrency"))
            availability = safe_text(o.get("availability"))
        break

    if not title:
        h1 = soup.find("h1")
        title = collapse_ws(h1.get_text(" ", strip=True)) if h1 else ""
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        title = safe_text(og.get("content")) if og else ""

    tasks = detect_tasks(title + " " + page_text[:8000])
    return {
        "product_url": url,
        "title": title,
        "sku": sku,
        "brand": brand,
        "price": price,
        "currency": currency,
        "availability": availability,
        "image": image,
        "tasks": " | ".join(tasks),
        "task_count": len(tasks),
        "text_chars": len(page_text),
        "collected_at": now_iso(),
    }


@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    elapsed_ms: int
    error: str = ""


class Fetcher:
    def __init__(self, user_agent: str, timeout: int = 25):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.5",
        })
        self.timeout = timeout

    def get(self, url: str, timeout: Optional[int] = None) -> FetchResult:
        start = time.perf_counter()
        try:
            r = self.s.get(url, timeout=timeout or self.timeout, allow_redirects=True)
            elapsed = int((time.perf_counter() - start) * 1000)
            ct = safe_text(r.headers.get("content-type"))
            enc = r.encoding or r.apparent_encoding or "utf-8"
            try:
                r.encoding = enc
            except Exception:
                pass
            text = r.text if ("text" in ct or "html" in ct or "json" in ct or not ct) else ""
            return FetchResult(
                ok=r.ok,
                url=url,
                final_url=safe_text(r.url),
                status=int(r.status_code),
                content_type=ct,
                text=text,
                elapsed_ms=elapsed,
                error="" if r.ok else f"HTTP {r.status_code}",
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return FetchResult(False, url, "", 0, "", "", elapsed, f"{type(e).__name__}: {e}")


def collect_site(fetcher: Fetcher, cfg: dict[str, Any], errors: list[dict[str, Any]]):
    base_url = cfg["base_url"]
    own_host = host(base_url)
    max_pages = int(cfg.get("max_pages", 300))
    max_depth = int(cfg.get("max_depth", 5))
    delay = float(cfg.get("delay_seconds", 0.3))
    queryless = bool(cfg.get("include_queryless_only", True))

    starts = cfg.get("start_urls") or [base_url]
    q = deque((canonical_crawl_url(clean_url(u, base_url), queryless=True), 0, "") for u in starts)
    seen: set[str] = set()

    pages: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    while q and len(seen) < max_pages:
        url, depth, discovered_from = q.popleft()
        if not url or url in seen or depth > max_depth:
            continue
        if host(url) != own_host:
            continue
        seen.add(url)
        logging.info("SITE %d/%d depth=%d %s", len(seen), max_pages, depth, url)

        fr = fetcher.get(url, timeout=int(cfg.get("timeout_seconds", 25)))
        if not fr.ok or not fr.text:
            errors.append({
                "scope": "site", "url": url, "status": fr.status,
                "error": fr.error or "No HTML", "collected_at": now_iso()
            })
            continue

        soup = BeautifulSoup(fr.text, "html.parser")
        title = collapse_ws(soup.title.get_text(" ", strip=True)) if soup.title else ""
        h1 = soup.find("h1")
        h1_text = collapse_ws(h1.get_text(" ", strip=True)) if h1 else ""
        desc = soup.find("meta", attrs={"name": "description"})
        description = safe_text(desc.get("content")) if desc else ""
        canonical = soup.find("link", rel=lambda x: x and "canonical" in x)
        canonical_url = clean_url(canonical.get("href"), fr.final_url) if canonical else ""
        page_text = collapse_ws(soup.get_text(" ", strip=True))
        page_type = detect_page_type(fr.final_url or url, soup)

        metrika = ("mc.yandex.ru" in fr.text or "yandex_metrika" in fr.text.lower() or re.search(r"\bym\s*\(", fr.text) is not None)
        ecommerce_words = any(x in page_text.lower() for x in ["корзин", "оформ", "достав", "оплат", "заказ", "личный кабинет", "профиль"])
        subscription_words = any(x in page_text.lower() for x in ["подпис", "e-mail", "email", "скидк"])
        pages.append({
            "url": url,
            "final_url": fr.final_url,
            "depth": depth,
            "discovered_from": discovered_from,
            "status": fr.status,
            "elapsed_ms": fr.elapsed_ms,
            "content_type": fr.content_type,
            "page_type": page_type,
            "title": title,
            "h1": h1_text,
            "meta_description": description,
            "canonical": canonical_url,
            "text_chars": len(page_text),
            "has_yandex_metrika": int(bool(metrika)),
            "has_ecommerce_language": int(bool(ecommerce_words)),
            "has_subscription_language": int(bool(subscription_words)),
            "collected_at": now_iso(),
        })

        for label, matched in [
            ("yandex_metrika", metrika),
            ("ecommerce_language", ecommerce_words),
            ("subscription_language", subscription_words),
        ]:
            if matched:
                signals.append({
                    "page_url": url,
                    "page_type": page_type,
                    "signal": label,
                    "evidence": "public HTML/text signal",
                    "collected_at": now_iso(),
                })

        p = product_from_page(fr.final_url or url, soup, page_text)
        if p:
            products.append(p)

        for a in soup.find_all("a", href=True):
            raw = safe_text(a.get("href"))
            dst = clean_url(raw, fr.final_url or url)
            if not dst:
                continue
            anchor = collapse_ws(a.get_text(" ", strip=True))
            utm = parse_utm(dst)
            dtype = destination_type(dst, own_host)
            links.append({
                "source_url": fr.final_url or url,
                "source_page_type": page_type,
                "anchor_text": anchor,
                "cta_type": classify_cta(anchor),
                "destination_url": dst,
                "destination_type": dtype,
                "destination_host": host(dst),
                "utm_source": utm.get("utm_source", ""),
                "utm_medium": utm.get("utm_medium", ""),
                "utm_campaign": utm.get("utm_campaign", ""),
                "utm_content": utm.get("utm_content", ""),
                "utm_term": utm.get("utm_term", ""),
                "yclid": utm.get("yclid", ""),
                "gclid": utm.get("gclid", ""),
                "erid": utm.get("erid", ""),
                "collected_at": now_iso(),
            })

            if host(dst) == own_host and depth < max_depth:
                crawl = canonical_crawl_url(dst, queryless=queryless)
                # Avoid obvious non-content endpoints/assets.
                path = urllib.parse.urlsplit(crawl).path.lower()
                bad_ext = (".jpg", ".jpeg", ".png", ".webp", ".svg", ".pdf", ".zip", ".xml", ".json", ".css", ".js", ".ico")
                if not path.endswith(bad_ext) and crawl not in seen:
                    q.append((crawl, depth + 1, fr.final_url or url))

        time.sleep(delay)

    # Deduplicate products by URL.
    uniq = {}
    for p in products:
        uniq[p["product_url"]] = p
    return pages, links, list(uniq.values()), signals


def parse_counter_value(text: str) -> int:
    t = collapse_ws(text).replace(" ", "").replace("\xa0", "").lower()
    if not t:
        return 0
    mult = 1
    if t.endswith("k") or t.endswith("к"):
        mult = 1000
        t = t[:-1].replace(",", ".")
    elif t.endswith("m") or t.endswith("м"):
        mult = 1000000
        t = t[:-1].replace(",", ".")
    try:
        return int(float(t) * mult)
    except Exception:
        m = re.search(r"\d+", t)
        return int(m.group(0)) if m else 0


def collect_telegram(fetcher: Fetcher, cfg: dict[str, Any], errors: list[dict[str, Any]]):
    channel = safe_text(cfg.get("channel", "proorganic_vitamin")).lstrip("@")
    max_posts = int(cfg.get("max_posts", 800))
    delay = float(cfg.get("delay_seconds", 0.35))
    timeout = int(cfg.get("timeout_seconds", 25))
    base = f"https://t.me/s/{channel}"

    posts_by_id: dict[int, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    before: Optional[int] = None
    pages_guard = 0

    while len(posts_by_id) < max_posts and pages_guard < 100:
        pages_guard += 1
        url = base if before is None else f"{base}?before={before}"
        logging.info("TELEGRAM page=%d posts=%d %s", pages_guard, len(posts_by_id), url)
        fr = fetcher.get(url, timeout=timeout)
        if not fr.ok or not fr.text:
            errors.append({
                "scope": "telegram", "url": url, "status": fr.status,
                "error": fr.error or "No HTML", "collected_at": now_iso()
            })
            break

        soup = BeautifulSoup(fr.text, "html.parser")
        message_nodes = soup.select(".tgme_widget_message")
        found_ids: list[int] = []

        for node in message_nodes:
            data_post = safe_text(node.get("data-post"))
            if "/" not in data_post:
                continue
            try:
                post_id = int(data_post.rsplit("/", 1)[-1])
            except Exception:
                continue
            found_ids.append(post_id)
            if post_id in posts_by_id:
                continue

            text_node = node.select_one(".tgme_widget_message_text")
            text = collapse_ws(text_node.get_text(" ", strip=True)) if text_node else ""
            time_node = node.select_one("time")
            dt = safe_text(time_node.get("datetime")) if time_node else ""
            views_node = node.select_one(".tgme_widget_message_views")
            views = parse_counter_value(views_node.get_text(" ", strip=True)) if views_node else 0
            forwards_node = node.select_one(".tgme_widget_message_forwards")
            forwards = parse_counter_value(forwards_node.get_text(" ", strip=True)) if forwards_node else 0

            reactions = []
            for reaction in node.select(".tgme_widget_message_reaction"):
                reactions.append(collapse_ws(reaction.get_text(" ", strip=True)))
            reaction_text = " | ".join(x for x in reactions if x)

            is_poll = bool(node.select_one(".tgme_widget_message_poll"))
            is_video = bool(node.select_one(".tgme_widget_message_video_player, .tgme_widget_message_video"))
            is_photo = bool(node.select_one(".tgme_widget_message_photo_wrap"))
            is_voice = bool(node.select_one(".tgme_widget_message_voice_player"))
            media_type = "video" if is_video else "photo" if is_photo else "voice" if is_voice else "text"

            hashtags = sorted(set(re.findall(r"(?<!\w)#[\w_А-Яа-яЁё]+", text)))
            tariff = int("тариф доверия" in text.lower() or any("тариф" in h.lower() and "довер" in h.lower() for h in hashtags))
            tasks = detect_tasks(text)

            post_url = f"https://t.me/{channel}/{post_id}"
            row = {
                "post_id": post_id,
                "post_url": post_url,
                "datetime": dt,
                "text": text,
                "text_chars": len(text),
                "views": views,
                "forwards": forwards,
                "reactions": reaction_text,
                "is_poll": int(is_poll),
                "media_type": media_type,
                "hashtags": " | ".join(hashtags),
                "is_tariff_of_trust": tariff,
                "tasks": " | ".join(tasks),
                "collected_at": now_iso(),
            }
            posts_by_id[post_id] = row

            for a in node.find_all("a", href=True):
                dst = clean_url(a.get("href"), base)
                if not dst:
                    continue
                # Ignore links that are only the post permalink itself where possible.
                anchor = collapse_ws(a.get_text(" ", strip=True))
                utm = parse_utm(dst)
                links.append({
                    "post_id": post_id,
                    "post_url": post_url,
                    "post_datetime": dt,
                    "is_tariff_of_trust": tariff,
                    "anchor_text": anchor,
                    "cta_type": classify_cta(anchor),
                    "destination_url": dst,
                    "destination_type": destination_type(dst),
                    "destination_host": host(dst),
                    "utm_source": utm.get("utm_source", ""),
                    "utm_medium": utm.get("utm_medium", ""),
                    "utm_campaign": utm.get("utm_campaign", ""),
                    "utm_content": utm.get("utm_content", ""),
                    "utm_term": utm.get("utm_term", ""),
                    "yclid": utm.get("yclid", ""),
                    "gclid": utm.get("gclid", ""),
                    "erid": utm.get("erid", ""),
                    "collected_at": now_iso(),
                })

        if not found_ids:
            break
        new_before = min(found_ids)
        if before is not None and new_before >= before:
            break
        before = new_before
        time.sleep(delay)

    posts = sorted(posts_by_id.values(), key=lambda x: x["post_id"], reverse=True)[:max_posts]
    post_ids = {p["post_id"] for p in posts}
    links = [x for x in links if x["post_id"] in post_ids]
    # Deduplicate repeated hrefs inside one message.
    uniq = {}
    for x in links:
        key = (x["post_id"], x["destination_url"], x["anchor_text"])
        uniq[key] = x
    return posts, list(uniq.values())


def resolve_external_links(fetcher: Fetcher, links: list[dict[str, Any]], cfg: dict[str, Any], errors: list[dict[str, Any]]):
    if not cfg.get("resolve_redirects", True):
        return []
    max_n = int(cfg.get("max_to_resolve", 250))
    timeout = int(cfg.get("timeout_seconds", 15))
    domains = set(d.lower() for d in cfg.get("domains", []))

    candidates: list[str] = []
    for x in links:
        u = x.get("destination_url", "")
        h = host(u)
        if h in domains or any(h.endswith("." + d) for d in domains):
            if u not in candidates:
                candidates.append(u)
        if len(candidates) >= max_n:
            break

    out = []
    for i, u in enumerate(candidates, 1):
        logging.info("RESOLVE %d/%d %s", i, len(candidates), u)
        fr = fetcher.get(u, timeout=timeout)
        out.append({
            "input_url": u,
            "input_type": destination_type(u),
            "status": fr.status,
            "final_url": fr.final_url,
            "final_type": destination_type(fr.final_url) if fr.final_url else "",
            "redirected": int(bool(fr.final_url and fr.final_url != u)),
            "elapsed_ms": fr.elapsed_ms,
            "error": fr.error,
            "collected_at": now_iso(),
        })
        if not fr.ok and fr.error:
            errors.append({
                "scope": "external_resolve", "url": u, "status": fr.status,
                "error": fr.error, "collected_at": now_iso()
            })
    return out


def build_launches(posts: list[dict[str, Any]], tg_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_post: dict[int, list[dict[str, Any]]] = {}
    for x in tg_links:
        by_post.setdefault(int(x["post_id"]), []).append(x)

    rows = []
    for p in posts:
        if not int(p.get("is_tariff_of_trust", 0)):
            continue
        ls = by_post.get(int(p["post_id"]), [])
        types = sorted(set(x["destination_type"] for x in ls))
        text = p.get("text", "")
        rows.append({
            "post_id": p["post_id"],
            "datetime": p.get("datetime", ""),
            "post_url": p.get("post_url", ""),
            "views": p.get("views", 0),
            "text_excerpt": text[:400],
            "has_own_site": int(any(t.startswith("own_") for t in types)),
            "has_ozon": int("ozon" in types),
            "has_wildberries": int("wildberries" in types),
            "has_taplink": int("taplink" in types),
            "has_telegram": int("telegram" in types),
            "destination_types": " | ".join(types),
            "destination_count": len(set(x["destination_url"] for x in ls)),
            "utm_link_count": sum(1 for x in ls if x.get("utm_source") or x.get("utm_campaign")),
            "collected_at": now_iso(),
        })
    return sorted(rows, key=lambda x: (x.get("datetime", ""), x["post_id"]))


def build_route_edges(site_links: list[dict[str, Any]], tg_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str, str]] = Counter()
    examples: dict[tuple[str, str, str, str], str] = {}

    for x in site_links:
        src = x.get("source_page_type") or "site_page"
        dst = x.get("destination_type") or "unknown"
        key = ("site", src, "link", dst)
        counter[key] += 1
        examples.setdefault(key, x.get("destination_url", ""))

    for x in tg_links:
        src = "tariff_post" if int(x.get("is_tariff_of_trust", 0)) else "telegram_post"
        dst = x.get("destination_type") or "unknown"
        key = ("telegram", src, "link", dst)
        counter[key] += 1
        examples.setdefault(key, x.get("destination_url", ""))

    rows = []
    for (channel, src, relation, dst), count in sorted(counter.items()):
        rows.append({
            "channel": channel,
            "source_node": src,
            "relation": relation,
            "target_node": dst,
            "observed_link_count": count,
            "example_url": examples[(channel, src, relation, dst)],
            "claim_scope": "Observed public links; not click/conversion data",
        })
    return rows


def build_utm_audit(site_links: list[dict[str, Any]], tg_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for origin, data in [("site", site_links), ("telegram", tg_links)]:
        relevant = [x for x in data if x.get("destination_type") not in ("unknown",)]
        total = len(relevant)
        with_utm = [x for x in relevant if x.get("utm_source") or x.get("utm_medium") or x.get("utm_campaign")]
        srcs = Counter(x.get("utm_source", "") for x in with_utm if x.get("utm_source"))
        meds = Counter(x.get("utm_medium", "") for x in with_utm if x.get("utm_medium"))
        camps = Counter(x.get("utm_campaign", "") for x in with_utm if x.get("utm_campaign"))
        rows.append({
            "origin": origin,
            "links_total": total,
            "links_with_utm": len(with_utm),
            "utm_share_pct": round(100 * len(with_utm) / total, 1) if total else 0,
            "utm_sources": " | ".join(f"{k}:{v}" for k, v in srcs.most_common(20)),
            "utm_mediums": " | ".join(f"{k}:{v}" for k, v in meds.most_common(20)),
            "utm_campaigns": " | ".join(f"{k}:{v}" for k, v in camps.most_common(20)),
            "interpretation_limit": "Public URL markup only; internal analytics may contain additional attribution.",
        })
    return rows


def task_summary(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for p in products:
        tasks = [x.strip() for x in p.get("tasks", "").split("|") if x.strip()]
        for t in tasks:
            counts[t] += 1
            examples.setdefault(t, [])
            if p.get("title") and len(examples[t]) < 8:
                examples[t].append(p["title"])
    return [
        {
            "task": task,
            "product_count": count,
            "examples": " | ".join(examples.get(task, [])),
            "scope": "Keyword-based public assortment map; groups may overlap.",
        }
        for task, count in counts.most_common()
    ]


def dedupe_rows(rows: list[dict[str, Any]], keys: Iterable[str]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        key = tuple(safe_text(r.get(k)) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: safe_text(r.get(k)) for k in fields})


def write_xlsx(path: Path, tables: dict[str, list[dict[str, Any]]]) -> bool:
    if Workbook is None:
        return False
    wb = Workbook()
    wb.remove(wb.active)
    used_names = set()

    def safe_sheet_name(name: str) -> str:
        base = re.sub(r"[\[\]\*\?/\\:]", "_", name)[:31] or "Sheet"
        candidate = base
        i = 2
        while candidate in used_names:
            suffix = f"_{i}"
            candidate = base[:31-len(suffix)] + suffix
            i += 1
        used_names.add(candidate)
        return candidate

    for name, rows in tables.items():
        ws = wb.create_sheet(safe_sheet_name(name))
        if not rows:
            ws["A1"] = "No rows collected"
            continue
        fields: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in fields:
                    fields.append(k)
        for c, field in enumerate(fields, 1):
            cell = ws.cell(1, c, field)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E8EFE5")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for rr, row in enumerate(rows, 2):
            for c, field in enumerate(fields, 1):
                value = row.get(field, "")
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                ws.cell(rr, c, value)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for c, field in enumerate(fields, 1):
            max_len = len(field)
            for rr in range(2, min(ws.max_row, 80) + 1):
                max_len = max(max_len, min(80, len(safe_text(ws.cell(rr, c).value))))
            ws.column_dimensions[get_column_letter(c)].width = min(max(max_len + 2, 10), 55)
    wb.save(path)
    return True


def write_sqlite(path: Path, tables: dict[str, list[dict[str, Any]]]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        for name, rows in tables.items():
            table = re.sub(r"\W+", "_", name).strip("_") or "data"
            if not rows:
                con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (placeholder TEXT)')
                continue
            fields: list[str] = []
            for r in rows:
                for k in r.keys():
                    if k not in fields:
                        fields.append(k)
            cols = ", ".join(f'"{re.sub(r"[^0-9A-Za-z_]+", "_", k)}" TEXT' for k in fields)
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            con.execute(f'CREATE TABLE "{table}" ({cols})')
            qmarks = ",".join("?" for _ in fields)
            colnames = ",".join(f'"{re.sub(r"[^0-9A-Za-z_]+", "_", k)}"' for k in fields)
            sql = f'INSERT INTO "{table}" ({colnames}) VALUES ({qmarks})'
            con.executemany(sql, [[safe_text(r.get(k)) for k in fields] for r in rows])
        con.commit()
    finally:
        con.close()


def write_graphml(path: Path, route_edges: list[dict[str, Any]]) -> None:
    nodes = sorted(set([r["source_node"] for r in route_edges] + [r["target_node"] for r in route_edges]))
    node_ids = {n: f"n{i}" for i, n in enumerate(nodes)}
    esc = html_lib.escape
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '<key id="label" for="node" attr.name="label" attr.type="string"/>',
        '<key id="count" for="edge" attr.name="observed_link_count" attr.type="int"/>',
        '<key id="scope" for="edge" attr.name="scope" attr.type="string"/>',
        '<graph id="G" edgedefault="directed">'
    ]
    for n in nodes:
        parts.append(f'<node id="{node_ids[n]}"><data key="label">{esc(n)}</data></node>')
    for i, r in enumerate(route_edges):
        parts.append(
            f'<edge id="e{i}" source="{node_ids[r["source_node"]]}" target="{node_ids[r["target_node"]]}">'
            f'<data key="count">{int(r["observed_link_count"])}</data>'
            f'<data key="scope">{esc(r["claim_scope"])}</data></edge>'
        )
    parts.extend(["</graph>", "</graphml>"])
    path.write_text("\n".join(parts), encoding="utf-8")


def build_summary(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    site_links = tables.get("site_links", [])
    tg_links = tables.get("telegram_links", [])
    products = tables.get("products", [])
    posts = tables.get("telegram_posts", [])
    launches = tables.get("tariff_launches", [])
    errors = tables.get("errors", [])

    def dest_counts(rows):
        return dict(Counter(x.get("destination_type", "") for x in rows if x.get("destination_type")))

    return {
        "collector_version": VERSION,
        "generated_at": now_iso(),
        "counts": {
            "site_pages": len(tables.get("site_pages", [])),
            "site_links": len(site_links),
            "products": len(products),
            "telegram_posts": len(posts),
            "telegram_links": len(tg_links),
            "tariff_launches": len(launches),
            "errors": len(errors),
        },
        "destinations": {
            "site": dest_counts(site_links),
            "telegram": dest_counts(tg_links),
        },
        "methodology": {
            "what_is_observed": "Public pages, public posts, visible links/CTA, visible URL labels and public technical signals.",
            "what_is_not_observed": "Actual user clicks, conversion, revenue attribution, CRM cohorts, LTV and repeat purchases.",
        },
    }


def html_table(rows: list[dict[str, Any]], cols: list[str], limit: int = 40) -> str:
    if not rows:
        return "<p class='muted'>Нет данных.</p>"
    out = ["<div class='table-wrap'><table><thead><tr>"]
    for c in cols:
        out.append(f"<th>{html_lib.escape(c)}</th>")
    out.append("</tr></thead><tbody>")
    for r in rows[:limit]:
        out.append("<tr>")
        for c in cols:
            v = safe_text(r.get(c))
            if v.startswith("http://") or v.startswith("https://"):
                e = html_lib.escape(v)
                out.append(f"<td><a href='{e}' target='_blank' rel='noopener'>{e[:90]}</a></td>")
            else:
                out.append(f"<td>{html_lib.escape(v)[:900]}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def write_report(path: Path, summary: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> None:
    c = summary["counts"]
    launches = tables.get("tariff_launches", [])
    utm = tables.get("utm_audit", [])
    tasks = tables.get("task_summary", [])
    routes = tables.get("route_edges", [])
    errors = tables.get("errors", [])

    cards = "".join(
        f"<div class='card'><strong>{html_lib.escape(str(v))}</strong><span>{html_lib.escape(k)}</span></div>"
        for k, v in [
            ("Страниц сайта", c["site_pages"]),
            ("Товаров", c["products"]),
            ("Постов Telegram", c["telegram_posts"]),
            ("Публичных ссылок", c["site_links"] + c["telegram_links"]),
            ("«Тариф доверия»", c["tariff_launches"]),
            ("Ошибок сбора", c["errors"]),
        ]
    )

    doc = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PROORGANIC — публичный исследовательский сбор</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;margin:0;background:#f5f2e9;color:#12251d}}
main{{max-width:1180px;margin:auto;padding:36px 24px 80px}}
h1{{font-size:clamp(30px,5vw,58px);line-height:1.02;margin:0 0 14px}}
h2{{margin-top:52px;font-size:28px}} p{{line-height:1.55}}
.notice{{background:#0b2c22;color:#f5f2e9;padding:20px 22px;border-radius:16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}
.card{{background:#fff;border:1px solid #d8d3c7;border-radius:14px;padding:18px}}
.card strong{{display:block;font-size:30px}} .card span{{color:#52635c}}
.table-wrap{{overflow:auto;background:#fff;border:1px solid #d8d3c7;border-radius:14px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:10px 12px;border-bottom:1px solid #eee;vertical-align:top;text-align:left}}
th{{position:sticky;top:0;background:#e8efe5}} a{{color:#235f45}} .muted{{color:#6a746f}}
code{{background:#e8efe5;padding:2px 5px;border-radius:5px}}
</style>
</head>
<body><main>
<h1>PROORGANIC: что видно по открытым данным</h1>
<p>Автоматический технический сбор. Версия коллектора {VERSION}. Сформировано {html_lib.escape(summary["generated_at"])}.</p>
<div class="notice"><b>Граница вывода:</b> этот отчёт показывает наблюдаемые публичные страницы, посты, CTA, ссылки, URL-разметку и технические сигналы. Он <b>не</b> показывает фактические клики, конверсию, выручку, LTV, CRM-когорты или повторные покупки без внутренних данных.</div>
<div class="cards">{cards}</div>

<h2>Маршруты, которые бренд публично предлагает</h2>
{html_table(routes, ["channel","source_node","target_node","observed_link_count","example_url","claim_scope"], 80)}

<h2>Аудит UTM-разметки</h2>
{html_table(utm, ["origin","links_total","links_with_utm","utm_share_pct","utm_sources","utm_mediums","utm_campaigns","interpretation_limit"], 20)}

<h2>«Тариф доверия»: публичные направления покупки</h2>
{html_table(launches, ["datetime","post_id","has_own_site","has_ozon","has_wildberries","has_taplink","destination_types","destination_count","utm_link_count","post_url"], 100)}

<h2>Карта задач ассортимента</h2>
<p class="muted">Автоматическая лексическая разметка. Группы могут пересекаться; это не рейтинг спроса.</p>
{html_table(tasks, ["task","product_count","examples","scope"], 40)}

<h2>Товары</h2>
{html_table(tables.get("products", []), ["title","price","currency","tasks","product_url"], 100)}

<h2>Последние собранные посты Telegram</h2>
{html_table(tables.get("telegram_posts", []), ["datetime","post_id","views","media_type","is_tariff_of_trust","tasks","text","post_url"], 60)}

<h2>Ошибки и ограничения сбора</h2>
{html_table(errors, ["scope","url","status","error","collected_at"], 100)}
</main></body></html>"""
    path.write_text(doc, encoding="utf-8")


def make_latest(run_dir: Path, output_root: Path) -> Path:
    latest = output_root / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(run_dir, latest)
    return latest


def configure_logging(run_dir: Path, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def load_config(path: Optional[str]) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path:
        return cfg
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    user = json.loads(p.read_text(encoding="utf-8-sig"))

    def merge(a: dict[str, Any], b: dict[str, Any]):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    merge(cfg, user)
    return cfg


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="PROORGANIC public research collector")
    ap.add_argument("--config", default="config.json", help="Path to config.json")
    ap.add_argument("--output", default=None, help="Override output root")
    ap.add_argument("--max-site-pages", type=int, default=None)
    ap.add_argument("--max-site-depth", type=int, default=None)
    ap.add_argument("--max-telegram-posts", type=int, default=None)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--no-site", action="store_true")
    ap.add_argument("--no-resolve", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cfg = load_config(args.config if args.config and Path(args.config).exists() else None)
    except Exception as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    if args.output:
        cfg["output"]["root"] = args.output
    if args.max_site_pages is not None:
        cfg["site"]["max_pages"] = args.max_site_pages
    if args.max_site_depth is not None:
        cfg["site"]["max_depth"] = args.max_site_depth
    if args.max_telegram_posts is not None:
        cfg["telegram"]["max_posts"] = args.max_telegram_posts
    if args.no_resolve:
        cfg["external_links"]["resolve_redirects"] = False

    output_root = Path(cfg["output"]["root"]).resolve()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = output_root / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(run_dir, args.verbose)

    logging.info("PROORGANIC collector v%s", VERSION)
    logging.info("Run directory: %s", run_dir)
    (run_dir / "config_used.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    errors: list[dict[str, Any]] = []
    fetcher = Fetcher(cfg.get("user_agent", DEFAULT_CONFIG["user_agent"]))

    site_pages: list[dict[str, Any]] = []
    site_links: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    site_signals: list[dict[str, Any]] = []
    telegram_posts: list[dict[str, Any]] = []
    telegram_links: list[dict[str, Any]] = []

    try:
        if not args.no_site:
            logging.info("Collecting site...")
            site_pages, site_links, products, site_signals = collect_site(fetcher, cfg["site"], errors)

        if not args.no_telegram:
            logging.info("Collecting public Telegram...")
            telegram_posts, telegram_links = collect_telegram(fetcher, cfg["telegram"], errors)

        all_public_links = site_links + telegram_links
        redirects = resolve_external_links(fetcher, all_public_links, cfg["external_links"], errors)

        launches = build_launches(telegram_posts, telegram_links)
        routes = build_route_edges(site_links, telegram_links)
        utm = build_utm_audit(site_links, telegram_links)
        tasks = task_summary(products)

        site_links = dedupe_rows(site_links, ["source_url","destination_url","anchor_text"])
        telegram_links = dedupe_rows(telegram_links, ["post_id","destination_url","anchor_text"])

        tables: dict[str, list[dict[str, Any]]] = {
            "site_pages": site_pages,
            "site_links": site_links,
            "products": products,
            "site_signals": site_signals,
            "telegram_posts": telegram_posts,
            "telegram_links": telegram_links,
            "tariff_launches": launches,
            "redirects": redirects,
            "route_edges": routes,
            "utm_audit": utm,
            "task_summary": tasks,
            "errors": errors,
        }

        csv_dir = run_dir / "csv"
        for name, rows in tables.items():
            write_csv(csv_dir / f"{name}.csv", rows)

        summary = build_summary(tables)
        (run_dir / "dashboard_data.json").write_text(
            json.dumps({"summary": summary, "tables": tables}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        if cfg["output"].get("make_xlsx", True):
            ok = write_xlsx(run_dir / "PROORGANIC_public_research.xlsx", tables)
            if not ok:
                logging.warning("openpyxl unavailable; XLSX skipped")

        if cfg["output"].get("make_sqlite", True):
            write_sqlite(run_dir / "PROORGANIC_public_research.sqlite", tables)

        if cfg["output"].get("make_graphml", True):
            write_graphml(run_dir / "routes.graphml", routes)

        if cfg["output"].get("make_html_report", True):
            write_report(run_dir / "report.html", summary, tables)

        manifest = {
            "collector_version": VERSION,
            "generated_at": now_iso(),
            "run_dir": str(run_dir),
            "files": sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file()),
        }
        (run_dir / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        latest = make_latest(run_dir, output_root)
        logging.info("DONE. Latest result: %s", latest)
        print()
        print("=" * 70)
        print("Готово.")
        print(f"HTML:  {latest / 'report.html'}")
        print(f"Excel: {latest / 'PROORGANIC_public_research.xlsx'}")
        print(f"JSON:  {latest / 'dashboard_data.json'}")
        print(f"CSV:   {latest / 'csv'}")
        print("=" * 70)
        return 0

    except KeyboardInterrupt:
        logging.warning("Interrupted by user.")
        return 130
    except Exception as e:
        logging.error("Fatal error: %s", e)
        logging.debug(traceback.format_exc())
        (run_dir / "FATAL_ERROR.txt").write_text(traceback.format_exc(), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
