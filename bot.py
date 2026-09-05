import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AMAZON_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "lootdeals-21")
FLIPKART_AFFID = os.getenv("FLIPKART_AFFILIATE_ID", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")

# Target brands (case-insensitive)
ALLOWED_BRANDS = ["jbl", "oneplus", "bose", "samsung", "oppo", "realme"]

# Blacklist to ignore fake 80-90% off junk accessories
JUNK_KEYWORDS = [
    "back cover", "case", "strap", "tempered glass", "screen protector",
    "pouch", "skin", "cleaning kit", "cable protector", "stand holder"
]

BRAND_MIN_DISCOUNT = 15
MAX_DEALS_PER_RUN = 3

FEEDS = [
    {
        "store": "Amazon",
        "url": "https://www.amazon.in/s?i=electronics&rh=p_89%3ABose%7Cp_89%3AJBL%7Cp_89%3AOnePlus%7Cp_89%3AOppo%7Cp_89%3ARealme%7Cp_89%3ASamsung&s=popularity-rank"
    },
    {
        "store": "Amazon",
        "url": "https://www.amazon.in/s?i=electronics&rh=n%3A1388921031%2Cp_89%3ABose%7Cp_89%3AJBL%7Cp_89%3AOnePlus%7Cp_89%3AOppo%7Cp_89%3ARealme%7Cp_89%3ASamsung&s=popularity-rank"
    },
    {
        "store": "Flipkart",
        "url": "https://www.flipkart.com/search?q=oneplus+jbl+bose+samsung+oppo+realme+headphones&sort=popularity"
    }
]


def fetch_page(url):
    api_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={url}&country_code=in&keep_headers=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
    }
    try:
        response = requests.get(api_url, headers=headers, timeout=45)
        if response.status_code == 200:
            return response.text
        print(f"[ERROR] Proxy HTTP {response.status_code} for {url[:45]}...")
    except Exception as err:
        print(f"[ERROR] Request failed: {err}")
    return None


def clean_price(text):
    if not text:
        return None
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits.split(".")[0])
    except ValueError:
        return None


def is_allowed_brand(title):
    title_lower = title.lower()
    return any(re.search(r"\b" + re.escape(brand) + r"\b", title_lower) for brand in ALLOWED_BRANDS)


def is_junk_item(title):
    title_lower = title.lower()
    return any(kw in title_lower for kw in JUNK_KEYWORDS)


def evaluate_deal(title, deal_price, mrp_price, image_url, deal_url, unique_id, store):
    # Minimum price check (filters out ₹49/₹99 fake items)
    if not deal_price or deal_price < 299:
        return None

    # Filter out cases, straps, screen protectors
    if is_junk_item(title):
        return None

    has_brand = is_allowed_brand(title)
    if not has_brand:
        return None  # Strictly enforce your selected brands

    if mrp_price and mrp_price > deal_price:
        discount = int(((mrp_price - deal_price) / mrp_price) * 100)
    else:
        discount = 0

    short_title = title[:82] + "..." if len(title) > 85 else title

    # Glitch Criteria for your preferred brands: 55%+ discount or huge price collapse
    is_glitch = discount >= 55 or (mrp_price and mrp_price >= 3000 and deal_price <= 799)
    is_brand_deal = discount >= BRAND_MIN_DISCOUNT

    if is_glitch or is_brand_deal:
        return {
            "id": unique_id,
            "store": store,
            "title": short_title,
            "deal_price": deal_price,
            "mrp_price": mrp_price,
            "discount": discount,
            "image_url": image_url,
            "deal_url": deal_url,
            "is_glitch": is_glitch
        }
    return None


# --- AMAZON PARSER ---
def parse_amazon(html):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"]')
    results = []

    for item in items:
        asin = item.get("data-asin")
        if not asin:
            continue

        title_el = item.select_one("h2 span") or item.select_one("h2 a span")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)

        price_el = item.select_one("span.a-price-whole")
        deal_price = clean_price(price_el.get_text(strip=True)) if price_el else None

        mrp_el = item.select_one("span.a-price.a-text-price span.a-offscreen") or item.select_one("span.a-text-strike")
        mrp_price = clean_price(mrp_el.get_text(strip=True)) if mrp_el else None

        img_el = item.select_one("img.s-image")
        image_url = img_el.get("src") if img_el else None

        deal_url = f"https://www.amazon.in/dp/{asin}?th=1&tag={AMAZON_TAG}"
        unique_id = f"amz_{asin}"

        deal = evaluate_deal(title, deal_price, mrp_price, image_url, deal_url, unique_id, "Amazon")
        if deal:
            results.append(deal)

    return results


# --- FLIPKART PARSER ---
def parse_flipkart(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Try both grid and row product wrappers used by Flipkart
    cards = soup.select("div[data-id]")
    if not cards:
        cards = soup.select("div._1AtVbE")

    for card in cards:
        pid = card.get("data-id")

        link_el = card.select_one("a[href*='/p/']")
        if not link_el:
            continue

        if not pid:
            match = re.search(r"pid=([A-Z0-9]+)", link_el.get("href", ""))
            pid = match.group(1) if match else None

        if not pid:
            continue

        raw_href = link_el.get("href", "")
        clean_path = raw_href.split("?")[0] if "?" in raw_href else raw_href
        base_url = f"https://www.flipkart.com{clean_path}"
        deal_url = f"{base_url}?affid={FLIPKART_AFFID}" if FLIPKART_AFFID else base_url

        title_el = (
            card.select_one("div.wjcEIp")
            or card.select_one("div.KzDlHZ")
            or card.select_one("a.s1Q9rs")
            or card.select_one("div._4rR01T")
            or card.select_one("a[title]")
            or link_el
        )
        title = title_el.get("title") or title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        price_el = card.select_one("div.Nx9bqj") or card.select_one("div._30jeq3")
        deal_price = clean_price(price_el.get_text(strip=True)) if price_el else None

        mrp_el = card.select_one("div.yRaY8j") or card.select_one("div._3I9_wc")
        mrp_price = clean_price(mrp_el.get_text(strip=True)) if mrp_el else None

        img_el = card.select_one("img[src*='rukminim']") or card.select_one("img")
        image_url = img_el.get("src") if img_el else None

        unique_id = f"fk_{pid}"

        deal = evaluate_deal(title, deal_price, mrp_price, image_url, deal_url, unique_id, "Flipkart")
        if deal:
            results.append(deal)

    return results


def send_telegram(deal):
    store_badge = "🛒 <b>Flipkart</b>" if deal["store"] == "Flipkart" else "📦 <b>Amazon</b>"

    if deal["is_glitch"]:
        caption = f"🚨 <b>[PRICE GLITCH / LOOT DROP]</b> 🚨\n"
        caption += f"{store_badge} | <b>{deal['discount']}% OFF</b>\n\n"
        caption += f"⚡ <b>{deal['title']}</b>\n\n"
        caption += f"💰 <b>Glitch Price:</b> ₹{int(deal['deal_price']):,}\n"
        if deal["mrp_price"]:
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n"
        caption += "\n⚠️ <i>Price can expire any minute! Order fast!</i>\n\n"
        caption += f"🔗 <b>Buy Deal:</b>\n{deal['deal_url']}"
    else:
        caption = f"{store_badge} | 🔥 <b>{deal['discount']}% OFF</b>\n\n"
        caption += f"<b>{deal['title']}</b>\n\n"
        caption += f"💰 <b>Deal Price:</b> ₹{int(deal['deal_price']):,}\n"
        if deal["mrp_price"]:
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n\n"
        else:
            caption += "\n"
        caption += f"🛒 <b>Buy Now:</b>\n{deal['deal_url']}"

    if deal["image_url"]:
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": CHAT_ID, "photo": deal["image_url"], "caption": caption, "parse_mode": "HTML"},
                timeout=25,
            )
            if res.json().get("ok"):
                print(f"[SUCCESS] Posted {deal['store']} deal: {deal['title']}")
                return True
        except Exception as e:
            print(f"[WARNING] Image upload failed: {e}")

    # Fallback to Text
    res = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=25,
    )
    if res.json().get("ok"):
        print(f"[SUCCESS] Posted {deal['store']} text: {deal['title']}")
        return True

    return False


def main():
    history_file = "posted_deals.json"
    posted_ids = set()

    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                posted_ids = set(json.load(f))
        except Exception:
            posted_ids = set()

    deals_to_post = []

    for feed in FEEDS:
        print(f"[INFO] Scanning {feed['store']} feed...")
        html = fetch_page(feed["url"])
        if not html:
            continue

        deals = parse_amazon(html) if feed["store"] == "Amazon" else parse_flipkart(html)
        print(f"[INFO] Found {len(deals)} valid brand items from {feed['store']}.")

        for d in deals:
            if d["id"] not in posted_ids:
                deals_to_post.append(d)

        time.sleep(2)

    # Sort glitches first, then highest discount percentage
    deals_to_post.sort(key=lambda x: (x["is_glitch"], x["discount"]), reverse=True)

    posted_count = 0
    newly_posted = []

    for deal in deals_to_post:
        if posted_count >= MAX_DEALS_PER_RUN:
            break

        sent = send_telegram(deal)
        if sent:
            posted_ids.add(deal["id"])
            newly_posted.append(deal["id"])
            posted_count += 1
            time.sleep(3)

    if newly_posted:
        trimmed = list(posted_ids)[-500:]
        with open(history_file, "w") as f:
            json.dump(trimmed, f, indent=2)
        print(f"[INFO] Posted {posted_count} brand deals to Telegram. History updated.")
    else:
        print("[INFO] No new brand deals met criteria this run.")


if __name__ == "__main__":
    main()
