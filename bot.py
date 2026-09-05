"""
Amazon India Deal Alert Bot
----------------------------
Scrapes tracked Amazon.in ASINs, compares current price against a threshold
and/or the last recorded price, and sends a Telegram photo alert when a
genuine price drop is detected. State (last known price) is persisted in
products.json, which this script rewrites and which your CI workflow
commits back to the repo.

Env vars required (set as GitHub Actions secrets):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    AMAZON_AFFILIATE_TAG
"""

import json
import os
import re
import time
import random
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------- CONFIG ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PLACEHOLDER_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PLACEHOLDER_CHAT_ID")
AMAZON_AFFILIATE_TAG = os.environ.get("AMAZON_AFFILIATE_TAG", "yourtag-21")
CHANNEL_TITLE = "🔥 DAILY DEAL ALERTS 🔥"

PRODUCTS_FILE = Path("products.json")
REQUEST_DELAY_RANGE = (3, 7)  # polite delay between product checks, in seconds

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def get_headers():
    """Rotate a realistic browser-like header set for each request."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
        "DNT": "1",
    }


def load_products():
    if not PRODUCTS_FILE.exists():
        raise FileNotFoundError("products.json not found — create it with your tracked ASINs first.")
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_products(products):
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)


def clean_price(text):
    """Extract a float from strings like '₹1,499.00' or 'M.R.P.: ₹2,999'."""
    if not text:
        return None
    match = re.search(r"[\d,]+(?:\.\d+)?", text.replace("\xa0", ""))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def build_affiliate_link(asin):
    return f"https://www.amazon.in/dp/{asin}?tag={AMAZON_AFFILIATE_TAG}"


def fetch_product_data(asin, session, max_retries=3):
    """
    Fetch and parse a single Amazon.in product page.
    Returns dict with title, deal_price, mrp, image_url — or None on failure.

    NOTE: Amazon frequently changes its markup/selectors. The multiple
    fallback selectors below are an attempt at resilience, but you should
    expect to update these periodically.
    """
    url = f"https://www.amazon.in/dp/{asin}"

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, headers=get_headers(), timeout=15)

            if resp.status_code == 503 or "captcha" in resp.text.lower()[:3000]:
                logger.warning(f"[{asin}] Blocked / CAPTCHA challenge on attempt {attempt}, backing off")
                time.sleep(random.uniform(5, 12) * attempt)
                continue

            if resp.status_code != 200:
                logger.warning(f"[{asin}] HTTP {resp.status_code} on attempt {attempt}")
                time.sleep(random.uniform(3, 6))
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            title_el = soup.select_one("#productTitle")
            title = title_el.get_text(strip=True) if title_el else None

            deal_price = None
            for sel in [
                "span.priceToPay span.a-offscreen",
                "#corePrice_feature_div span.a-offscreen",
                "#priceblock_dealprice",
                "#priceblock_ourprice",
                "span.a-price span.a-offscreen",
            ]:
                el = soup.select_one(sel)
                if el:
                    price = clean_price(el.get_text())
                    if price:
                        deal_price = price
                        break

            mrp = None
            for sel in [
                "span.a-price.a-text-price span.a-offscreen",
                ".basisPrice span.a-offscreen",
                ".a-text-strike",
            ]:
                el = soup.select_one(sel)
                if el:
                    price = clean_price(el.get_text())
                    if price and price != deal_price:
                        mrp = price
                        break
            if mrp is None:
                mrp = deal_price

            img_el = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
            image_url = None
            if img_el:
                image_url = img_el.get("data-old-hires") or img_el.get("src")

            if not title or not deal_price:
                logger.warning(f"[{asin}] Could not parse title/price — page layout may have changed")
                return None

            return {
                "title": title,
                "deal_price": deal_price,
                "mrp": mrp,
                "image_url": image_url,
            }

        except requests.RequestException as e:
            logger.warning(f"[{asin}] Request error on attempt {attempt}: {e}")
            time.sleep(random.uniform(3, 6))

    logger.error(f"[{asin}] Failed after {max_retries} attempts")
    return None


def send_telegram_alert(product_name, deal_price, mrp, affiliate_link, image_url):
    discount_pct = None
    if mrp and mrp > deal_price:
        discount_pct = round((1 - deal_price / mrp) * 100)

    caption_lines = [
        f"*{CHANNEL_TITLE}*",
        "",
        f"🛍️ *{product_name}*",
        f"✅ Deal Price: ₹{deal_price:,.0f}" + (f"  ({discount_pct}% OFF)" if discount_pct else ""),
    ]
    if mrp and mrp > deal_price:
        caption_lines.append(f"❌ ~Regular Price: ₹{mrp:,.0f}~")
    caption_lines.append("")
    caption_lines.append(f"🔗 [Buy Now]({affiliate_link})")

    caption = "\n".join(caption_lines)

    try:
        if image_url:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "Markdown",
            }
        else:
            api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": caption,
                "parse_mode": "Markdown",
            }

        resp = requests.post(api_url, data=payload, timeout=20)
        if resp.status_code != 200:
            logger.error(f"Telegram API error: {resp.status_code} {resp.text}")
        else:
            logger.info(f"Alert sent: {product_name}")
    except requests.RequestException as e:
        logger.error(f"Telegram send failed: {e}")


def main():
    if TELEGRAM_BOT_TOKEN.startswith("PLACEHOLDER") or TELEGRAM_CHAT_ID.startswith("PLACEHOLDER"):
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set. Configure them as GitHub Actions secrets.")
        return

    products = load_products()
    session = requests.Session()
    changed = False

    for product in products:
        asin = product["asin"]
        threshold = product.get("threshold_price")
        last_price = product.get("last_price")

        logger.info(f"Checking {asin} ...")
        data = fetch_product_data(asin, session)
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

        if not data:
            continue

        deal_price = data["deal_price"]
        should_alert = False

        if threshold and deal_price <= threshold:
            should_alert = True
        elif last_price and deal_price < last_price:
            should_alert = True
        # First time seeing this ASIN: just record baseline, don't spam an alert.

        if should_alert:
            affiliate_link = build_affiliate_link(asin)
            send_telegram_alert(data["title"], deal_price, data["mrp"], affiliate_link, data["image_url"])

        if last_price != deal_price:
            product["last_price"] = deal_price
            product["last_title"] = data["title"]
            changed = True

    if changed:
        save_products(products)
        logger.info("products.json updated with new prices.")
    else:
        logger.info("No price changes detected this run.")


if __name__ == "__main__":
    main()
