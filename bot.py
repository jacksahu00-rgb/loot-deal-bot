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

ALLOWED_BRANDS = ["jbl", "oneplus", "bose", "samsung", "oppo", "realme"]

BLOCKED_BRANDS = [
    "noise", "boat", "boult", "ptron", "mivi", "fire-boltt",
    "zebronics", "hammer", "portronics", "ambrane", "truke", "wings",
    "qqlike", "qexle", "czartech", "techonto"
]

CLONE_KEYWORDS = [
    "compatible with", "compatible for", "suitable for", "replacement for",
    "designed for", "supports", "for oneplus", "for samsung", "for jbl", "for bose"
]

JUNK_KEYWORDS = [
    "back cover", "case", "strap", "tempered glass", "screen protector",
    "pouch", "skin", "cleaning kit", "cable protector", "stand holder", "silicone"
]

ALLOWED_PREFIXES = ["all-new", "new", "newly", "the", "latest"]

# Minimum percentage discount from MRP to qualify as an instant flash loot
INSTANT_LOOT_DISCOUNT = 50
MAX_DEALS_PER_RUN = 3

FEEDS = [
    {
        "store": "Amazon",
        "url": "https://www.amazon.in/s?k=oneplus+buds&s=popularity-rank"
    },
    {
        "store": "Amazon",
        "url": "https://www.amazon.in/s?k=jbl+earbuds&s=popularity-rank"
    },
    {
        "store": "Flipkart",
        "url": "https://www.flipkart.com/search?q=oneplus+buds&sort=popularity"
    },
    {
        "store": "Flipkart",
        "url": "https://www.flipkart.com/search?q=realme+buds&sort=popularity"
    }
]


def fetch_page(url, retries=2):
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "country_code": "in",
        "keep_headers": "true"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
    }

    for attempt in range(1, retries + 1):
        try:
            response = requests.get("http://api.scraperapi.com", params=params, headers=headers, timeout=60)
            if response.status_code == 200 and len(response.text) > 1500:
                return response.text
            print(f"[WARNING] Proxy status {response.status_code} (attempt {attempt}/{retries})")
        except Exception as err:
            print(f"[WARNING] Fetch error on attempt {attempt}/{retries}: {err}")
        time.sleep(3)

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

    if any(clone in title_lower for clone in CLONE_KEYWORDS):
        return False

    for blocked in BLOCKED_BRANDS:
        if re.search(r"\b" + re.escape(blocked) + r"\b", title_lower):
            return False

    tokens = re.findall(r"\b[a-z0-9-]+\b", title_lower)
    if not tokens:
        return False

    while tokens and tokens[0] in ALLOWED_PREFIXES:
        tokens.pop(0)

    if not tokens:
        return False

    return tokens[0] in ALLOWED_BRANDS


def is_junk_item(title):
    title_lower = title.lower()
    return any(kw in title_lower for kw in JUNK_KEYWORDS)


def get_title_signature(title):
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    words = [w for w in cleaned.split() if w not in ["with", "for", "in", "and", "the", "true", "wireless", "earbuds", "headphones"]][:4]
    return " ".join(words)


def extract_deal_info(title, deal_price, mrp_price, image_url, deal_url, unique_id, store):
    if not title or len(title) < 15:
        return None

    if not deal_price or deal_price < 299:
        return None

    if is_junk_item(title) or not is_genuine_brand_product(title):
        return None

    if mrp_price and mrp_price > deal_price:
        discount = int(((mrp_price - deal_price) / mrp_price) * 100)
    else:
        discount = 0

    short_title = title[:82] + "..." if len(title) > 85 else title

    return {
        "id": unique_id,
        "signature": get_title_signature(title),
        "store": store,
        "title": short_title,
        "deal_price": deal_price,
        "mrp_price": mrp_price,
        "discount": discount,
        "image_url": image_url,
        "deal_url": deal_url
    }


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

        deal = extract_deal_info(title, deal_price, mrp_price, image_url, deal_url, unique_id, "Amazon")
        if deal:
            results.append(deal)

    return results


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

        deal = extract_deal_info(title, deal_price, mrp_price, image_url, deal_url, unique_id, "Flipkart")
        if deal:
            results.append(deal)

    return results


def send_telegram(deal):
    store_badge = "🛒 <b>Flipkart</b>" if deal["store"] == "Flipkart" else "📦 <b>Amazon</b>"

    if deal.get("is_price_drop"):
        caption = f"📉 <b>[PRICE DROP ALERT!]</b> 📉\n"
        caption += f"{store_badge} | <b>Dropped ₹{int(deal['drop_amount']):,}!</b>\n\n"
        caption += f"⚡ <b>{deal['title']}</b>\n\n"
        caption += f"💰 <b>Dropped Price:</b> ₹{int(deal['deal_price']):,}\n"
        caption += f"🏷️ <b>Previous Deal Price:</b> <s>₹{int(deal['previous_price']):,}</s>\n"
        if deal["mrp_price"]:
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n"
        caption += "\n🔥 <i>Price just fell below normal! Grab fast!</i>\n\n"
        caption += f"🛒 <b>Order Now:</b>\n{deal['deal_url']}"
    elif deal.get("is_glitch"):
        caption = f"🚨 <b>[PRICE GLITCH / LOOT DROP]</b> 🚨\n"
        caption += f"{store_badge} | <b>{deal['discount']}% OFF</b>\n\n"
        caption += f"⚡ <b>{deal['title']}</b>\n\n"
        caption += f"💰 <b>Glitch Price:</b> ₹{int(deal['deal_price']):,}\n"
        if deal["mrp_price"]:
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n"
        caption += "\n⚠️ <i>Price can revert any minute! Grab fast!</i>\n\n"
        caption += f"🔗 <b>Buy Deal:</b>\n{deal['deal_url']}"
    else:
        caption = f"{store_badge} | 🔥 <b>{deal['discount']}% OFF LOOT</b>\n\n"
        caption += f"<b>{deal['title']}</b>\n\n"
        caption += f"💰 <b>Loot Price:</b> ₹{int(deal['deal_price']):,}\n"
        if deal["mrp_price"]:
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n\n"
        caption += f"🛒 <b>Buy Now:</b>\n{deal['deal_url']}"

    if deal["image_url"]:
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": CHAT_ID, "photo": deal["image_url"], "caption": caption, "parse_mode": "HTML"},
                timeout=25,
            )
            if res.json().get("ok"):
                print(f"[SUCCESS] Alert sent: {deal['title']}")
                return True
        except Exception as e:
            print(f"[WARNING] Image upload failed: {e}")

    res = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=25,
    )
    if res.json().get("ok"):
        print(f"[SUCCESS] Text alert sent: {deal['title']}")
        return True

    return False


def main():
    history_file = "posted_deals.json"
    history = {}

    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                raw_data = json.load(f)
                if isinstance(raw_data, dict):
                    history = raw_data
                elif isinstance(raw_data, list):
                    # Migrate old list format to price-history dictionary
                    history = {item_id: {"base_price": None} for item_id in raw_data}
        except Exception:
            history = {}

    discovered_items = []

    for feed in FEEDS:
        print(f"[INFO] Scanning {feed['store']} feed...")
        html = fetch_page(feed["url"])
        if not html:
            continue

        deals = parse_amazon(html) if feed["store"] == "Amazon" else parse_flipkart(html)
        print(f"[INFO] Discovered {len(deals)} brand items on {feed['store']}.")
        discovered_items.extend(deals)
        time.sleep(2)

    alerts_to_send = []
    current_run_signatures = set()

    for item in discovered_items:
        uid = item["id"]
        current_price = item["deal_price"]
        mrp = item["mrp_price"]
        discount = item["discount"]
        sig = item["signature"]

        if sig in current_run_signatures:
            continue

        # Case 1: Extreme Price Glitch (always trigger immediately)
        is_glitch = discount >= 65 or (mrp and mrp >= 3000 and current_price <= 799)
        if is_glitch:
            item["is_glitch"] = True
            alerts_to_send.append(item)
            current_run_signatures.add(sig)
            history[uid] = {"base_price": current_price}
            continue

        # Case 2: Product already known -> Check for a real price drop
        if uid in history and history[uid].get("base_price"):
            base_price = history[uid]["base_price"]
            # Trigger if price dropped by at least ₹200 or 5% below previous price
            if current_price < base_price and (base_price - current_price >= 200 or (base_price - current_price) / base_price >= 0.05):
                item["is_price_drop"] = True
                item["previous_price"] = base_price
                item["drop_amount"] = base_price - current_price
                alerts_to_send.append(item)
                current_run_signatures.add(sig)
                history[uid]["base_price"] = current_price
                continue
            elif current_price < base_price:
                # Minor reduction; update lowest without triggering alert
                history[uid]["base_price"] = current_price

        # Case 3: First time seeing this product
        if uid not in history:
            # If it's already an extreme deal (>= 50% off MRP), post as Loot
            if discount >= INSTANT_LOOT_DISCOUNT:
                item["is_price_drop"] = False
                item["is_glitch"] = False
                alerts_to_send.append(item)
                current_run_signatures.add(sig)
                history[uid] = {"base_price": current_price}
            else:
                # Normal everyday price (e.g. ₹8,499) -> Save baseline, do NOT post yet
                history[uid] = {"base_price": current_price}
                print(f"[BASELINE] Saved {item['title'][:40]} at ₹{int(current_price)} (waiting for drop)")

    # Sort alerts: glitches first, then highest price drops
    alerts_to_send.sort(key=lambda x: (x.get("is_glitch", False), x.get("drop_amount", 0)), reverse=True)

    posted_count = 0
    for deal in alerts_to_send:
        if posted_count >= MAX_DEALS_PER_RUN:
            break

        if send_telegram(deal):
            posted_count += 1
            time.sleep(3)

    # Save updated baselines
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)
    print(f"[INFO] Run complete. {posted_count} price drop/glitch alerts sent. Baselines updated.")


if __name__ == "__main__":
    main()
