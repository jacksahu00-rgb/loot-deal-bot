import os
import re
import json
import time
import requests
from urllib.parse import quote
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AMAZON_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "lootdeals-21")
FLIPKART_AFFID = os.getenv("FLIPKART_AFFILIATE_ID", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")

ALLOWED_BRANDS = ["jbl", "oneplus", "bose", "samsung", "oppo", "realme"]

BLOCKED_BRANDS = [
    "noise", "boat", "boult", "ptron", "mivi", "fire-boltt",
    "zebronics", "hammer", "portronics", "ambrane", "truke", "wings", "qqlike"
]

# Clone sellers exploit these keywords to mimic genuine brands
CLONE_KEYWORDS = [
    "compatible with", "compatible for", "suitable for", "replacement for",
    "designed for", "supports", "for oneplus", "for samsung", "for jbl", "for bose"
]

JUNK_KEYWORDS = [
    "back cover", "case", "strap", "tempered glass", "screen protector",
    "pouch", "skin", "cleaning kit", "cable protector", "stand holder", "silicone"
]

BRAND_MIN_DISCOUNT = 15
MAX_DEALS_PER_RUN = 3

# 3 balanced feeds (Page 1 + Page 2) ensures fresh deals without quota burn
FEEDS = [
    {
        "store": "Amazon",
        "url": "https://www.amazon.in/s?i=electronics&rh=p_89%3ABose%7Cp_89%3AJBL%7Cp_89%3AOnePlus%7Cp_89%3AOppo%7Cp_89%3ARealme%7Cp_89%3ASamsung&s=popularity-rank"
    },
    {
        "store": "Amazon",
        # Page 2 keeps pipeline full when page 1 items are already posted
        "url": "https://www.amazon.in/s?i=electronics&rh=p_89%3ABose%7Cp_89%3AJBL%7Cp_89%3AOnePlus%7Cp_89%3AOppo%7Cp_89%3ARealme%7Cp_89%3ASamsung&page=2&s=popularity-rank"
    },
    {
        "store": "Flipkart",
        "url": "https://www.flipkart.com/search?q=oneplus+realme+jbl+buds&sort=popularity"
    }
]


def fetch_page(url, retries=2):
    encoded_target = quote(url, safe="")
    api_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={encoded_target}&country_code=in&keep_headers=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
    }

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(api_url, headers=headers, timeout=60)
            if response.status_code == 200:
                return response.text
            print(f"[WARNING] Proxy HTTP {response.status_code} (attempt {attempt}/{retries})")
        except Exception as err:
            print(f"[WARNING] Fetch error on attempt {attempt}/{retries}: {err}")
        time.sleep(3)

    print(f"[ERROR] Failed to fetch feed after {retries} attempts.")
    return None


def clean_price(text):
    if not text:
        return None
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits.split(".")[0])
    except ValueError:
        return None


def is_genuine_brand_product(title):
    title_lower = title.lower()

    # 1. Reject compatibility/clone phrasing
    if any(clone_phrase in title_lower for clone_phrase in CLONE_KEYWORDS):
        return False

    # 2. Reject budget/competitor brands
    for blocked in BLOCKED_BRANDS:
        if re.search(r"\b" + re.escape(blocked) + r"\b", title_lower):
            return False

    # 3. Genuine products: The brand name MUST appear in the first 4 words
    tokens = re.findall(r"\b[a-z0-9]+\b", title_lower)
    first_four = tokens[:4] if len(tokens) >= 4 else tokens

    return any(brand in first_four for brand in ALLOWED_BRANDS)


def is_junk_item(title):
    title_lower = title.lower()
    return any(kw in title_lower for kw in JUNK_KEYWORDS)


def get_title_signature(title):
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    words = [w for w in cleaned.split() if w not in ["with", "for", "in", "and", "the", "true", "wireless", "earbuds", "headphones"]][:4]
    return " ".join(words)


def evaluate_deal(title, deal_price, mrp_price, image_url, deal_url, unique_id, store):
    if not deal_price or deal_price < 299:
        return None

    if is_junk_item(title):
        return None

    # Strict authenticity & brand verification
    if not is_genuine_brand_product(title):
        return None

    if mrp_price and mrp_price > deal_price:
        discount = int(((mrp_price - deal_price) / mrp_price) * 100)
    else:
        discount = 0

    short_title = title[:82] + "..." if len(title) > 85 else title
    is_glitch = discount >= 55 or (mrp_price and mrp_price >= 3000 and deal_price <= 799)
    is_brand_deal = discount >= BRAND_MIN_DISCOUNT

    if is_glitch or is_brand_deal:
        return {
            "id": unique_id,
            "signature": get_title_signature(title),
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

    cards = soup.select("div[data-id], div._1sdMkc, div.slAVV4, div._1AtVbE")

    for card in cards:
        link_el = card.select_one("a[href*='/p/']")
        if not link_el:
            continue

        raw_href = link_el.get("href", "")
        pid_match = re.search(r"pid=([A-Z0-9]+)", raw_href)
        pid = card.get("data-id") or (pid_match.group(1) if pid_match else None)
        if not pid:
            continue

        clean_path = raw_href.split("?")[0] if "?" in raw_href else raw_href
        base_url = f"https://www.flipkart.com{clean_path}"
        deal_url = f"{base_url}?affid={FLIPKART_AFFID}" if FLIPKART_AFFID else base_url

        img_el = card.select_one("img[src*='rukminim']") or card.select_one("img")
        image_url = img_el.get("src") if img_el else None

        title = None
        for selector in ["a.wjcEIp", "div.KzDlHZ", "a.s1Q9rs", "div._4rR01T", "a[title]"]:
            el = card.select_one(selector)
            if el:
                title = el.get("title") or el.get_text(strip=True)
                if title:
                    break

        if not title and img_el:
            title = img_el.get("alt", "").strip()

        if not title:
            continue

        card_text = card.get_text(separator=" ")
        extracted = [clean_price(p) for p in re.findall(r"₹\s*([0-9,]+)", card_text)]
        extracted = [p for p in extracted if p and p > 100]

        deal_price = None
        mrp_price = None

        if len(extracted) >= 2:
            deal_price = min(extracted[0], extracted[1])
            mrp_price = max(extracted[0], extracted[1])
        elif len(extracted) == 1:
            deal_price = extracted[0]

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
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n"
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
    current_run_signatures = set()

    for feed in FEEDS:
        print(f"[INFO] Scanning {feed['store']} feed...")
        html = fetch_page(feed["url"])
        if not html:
            continue

        deals = parse_amazon(html) if feed["store"] == "Amazon" else parse_flipkart(html)
        print(f"[INFO] Found {len(deals)} genuine brand items from {feed['store']}.")

        for d in deals:
            if d["id"] not in posted_ids and d["signature"] not in current_run_signatures:
                deals_to_post.append(d)
                current_run_signatures.add(d["signature"])

        time.sleep(2)

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
        print(f"[INFO] Posted {posted_count} verified deals. History updated.")
    else:
        print("[INFO] No new verified brand deals met criteria this run.")


if __name__ == "__main__":
    main()
