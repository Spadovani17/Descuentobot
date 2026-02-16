#!/usr/bin/env python3
"""Monitor Peruvian e-commerce pages and notify steep price drops on Telegram."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

STATE_PATH = Path(os.getenv("STATE_PATH", "projects/telegram-discount-bot/state/prices.json"))
DROP_THRESHOLD = float(os.getenv("DROP_THRESHOLD", "50"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_ITEMS_PER_SITE = int(os.getenv("MAX_ITEMS_PER_SITE", "60"))
MAX_ALERTS_PER_RUN = int(os.getenv("MAX_ALERTS_PER_RUN", "20"))

SITES = {
    "mercadolibre": "https://www.mercadolibre.com.pe/ofertas",
    "falabella": "https://www.falabella.com.pe/falabella-pe/category/cat40712/Ofertas",
    "hm": "https://pe.hm.com/sale/view-all.html",
    "shopstar": "https://www.shopstar.pe/collections/ofertas",
}

SITE_PRODUCT_SELECTORS: dict[str, list[str]] = {
    "mercadolibre": ["li.ui-search-layout__item", "div.poly-card", "article"],
    "falabella": ["div.pod", "article", "li"],
    "hm": ["article.product-item", "li.product-item", "article", "li"],
    "shopstar": ["div.grid-product", "li.grid__item", "article", "li"],
}

TITLE_SELECTORS = [
    '[itemprop="name"]',
    "h1",
    "h2",
    "h3",
    ".title",
    ".product-name",
    ".poly-component__title",
    ".pod-subTitle",
    ".product-item__title",
]

PRICE_SELECTORS = [
    '[itemprop="price"]',
    '[data-testid="price-part"]',
    ".andes-money-amount__fraction",
    ".price",
    ".product-price",
    ".sales",
    ".money",
    ".price__current",
    ".pod-prices",
]

LINK_SELECTORS = [
    'a[href][title]',
    'a[href][aria-label]',
    'a[href*="/p/"]',
    'a[href*="/producto"]',
    "a[href]",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
}


@dataclass
class Product:
    site: str
    product_id: str
    title: str
    price_pen: float
    url: str


def parse_price(value: str) -> float | None:
    cleaned = re.sub(r"[^\d,\.]", "", value)
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def stable_id(site: str, title: str, url: str) -> str:
    canonical = f"{site}|{title.strip().lower()}|{url.split('?')[0]}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _pick_first_text(block: Any, selectors: list[str]) -> str:
    for selector in selectors:
        node = block.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _pick_first_href(block: Any, selectors: list[str]) -> str:
    for selector in selectors:
        node = block.select_one(selector)
        if node and node.get("href"):
            return str(node.get("href")).strip()
    return ""


def extract_products(site: str, html: str, base_url: str) -> list[Product]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[Product] = []

    selectors = SITE_PRODUCT_SELECTORS.get(site, ["article", "li", "div"])
    seen_blocks: set[int] = set()

    for selector in selectors:
        for block in soup.select(selector):
            block_id = id(block)
            if block_id in seen_blocks:
                continue
            seen_blocks.add(block_id)

            title = _pick_first_text(block, TITLE_SELECTORS)
            raw_price = _pick_first_text(block, PRICE_SELECTORS)
            href = _pick_first_href(block, LINK_SELECTORS)
            if not title or not raw_price or not href:
                continue

            price = parse_price(raw_price)
            if price is None or price <= 0:
                continue

            url = urljoin(base_url, href)
            products.append(
                Product(
                    site=site,
                    product_id=stable_id(site, title, url),
                    title=title,
                    price_pen=price,
                    url=url,
                )
            )

            if len(products) >= MAX_ITEMS_PER_SITE:
                break
        if len(products) >= MAX_ITEMS_PER_SITE:
            break

    deduped: dict[str, Product] = {}
    for product in products:
        deduped.setdefault(product.product_id, product)

    return list(deduped.values())


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"products": {}, "alerts_sent": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def monitor() -> None:
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not telegram_token or not telegram_chat_id:
        raise RuntimeError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en secrets de GitHub.")

    state = load_state()
    known_products: dict[str, dict[str, Any]] = state.get("products", {})
    alerts_sent: dict[str, int] = state.get("alerts_sent", {})
    alerts: list[str] = []

    for site, url in SITES.items():
        logging.info("Analizando %s", url)
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            logging.warning("No se pudo consultar %s: %s", site, exc)
            continue

        products = extract_products(site, response.text, url)
        logging.info("%s: %s productos detectados", site, len(products))

        now = int(time.time())
        for product in products:
            prev = known_products.get(product.product_id)
            known_products[product.product_id] = {
                "title": product.title,
                "site": site,
                "url": product.url,
                "last_price": product.price_pen,
                "updated_at": now,
            }

            if not prev:
                continue

            old_price = float(prev.get("last_price", 0))
            if old_price <= 0 or product.price_pen >= old_price:
                continue

            drop_pct = ((old_price - product.price_pen) / old_price) * 100
            if drop_pct < DROP_THRESHOLD:
                continue

            alert_key = f"{product.product_id}:{old_price:.2f}->{product.price_pen:.2f}"
            if alert_key in alerts_sent:
                continue

            alert = (
                f"🔥 DESCUENTO FUERTE ({drop_pct:.1f}%)\n"
                f"Tienda: {site}\n"
                f"Producto: {product.title}\n"
                f"Antes: S/ {old_price:.2f}\n"
                f"Ahora: S/ {product.price_pen:.2f}\n"
                f"Link: {product.url}"
            )
            alerts.append(alert)
            alerts_sent[alert_key] = now

    for alert in alerts[:MAX_ALERTS_PER_RUN]:
        send_telegram_message(telegram_token, telegram_chat_id, alert)
        logging.info("Alerta enviada")

    state["products"] = known_products
    state["alerts_sent"] = alerts_sent
    state["last_run"] = int(time.time())
    save_state(state)
    logging.info(
        "Ejecución finalizada. Alertas detectadas: %s. Alertas enviadas: %s",
        len(alerts),
        min(len(alerts), MAX_ALERTS_PER_RUN),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    monitor()
