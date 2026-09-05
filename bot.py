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

ALLOWED_PREFIXES = ["all-new", "new", "newly", "the", "latest", "truly", "wireless", "original", "genuine"]

# Top primary-source channels to monitor via open web bridge
UPSTREAM_TELEGRAM_CHANNELS = [
    "https://t.me/s/Desidime",
    "https://t.me/s/Dealsmagnet"
]

# Rotating store search feeds
ALL_FEEDS = [
    {"store": "Amazon", "url": "https://www.amazon.in/s?k=oneplus+buds&s=popularity-rank"},
    {"store": "Amazon", "url": "https://www.amazon.in/s?k=jbl+earbuds&s=popularity-rank"},
    {"store": "Amazon", "url": "https://www.amazon.in/s?k=samsung+galaxy+buds&s=popularity-rank"},
    {"store": "Flipkart", "url": "https://www.flipkart.com/search?q=oneplus+buds&sort=popularity"},
    {"store": "Flipkart", "url": "https://www.flipkart.com/search?q=realme+buds&sort=popularity"},
    {"store": "Flipkart", "url": "https://www.flipkart.com/search?q=oppo+bose+buds&sort=popularity"},
]

MAX_DEALS_PER_RUN = 3


def get_active_feeds():
    run_slot = int(time.time() // 1800)
    total = len(ALL_FEEDS)
    idx1 = run_slot % total
    idx2 = (run_slot + 1) % total
    idx3 = (run_slot + 2) % total
    return [ALL_FEEDS[idx1], ALL_FEEDS[idx2], ALL_FEEDS[idx3]]


def fetch_page(url, retries=2):
    params = {"api_key": SCRAPER_API_KEY, "url": url, "country_code": "in"}
    for attempt in range(1, retries + 1):
        try:
            response = requests.get("http://api.scraperapi.com", params=params, timeout=60)
            if response.status_code == 200 and len(response.text) > 2000:
                if "api-services-support@amazon.com" in response.text or "Robot Check" in response.text:
                    time.sleep(4)
                    continue
                return response.text
        except Exception:
            pass
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


# --- UPSTREAM TELEGRAM CHANNEL ENGINE ---

def unwrap_deal_url(url):
    """Resolves shorteners (amzn.to, fkrt.co, bit.ly) and injects your affiliate tag."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.head(url, allow_redirects=True, timeout=6, headers=headers)
        final_url = resp.url
    except Exception:
        final_url = url

    # Amazon Link
    if "amazon.in" in final_url:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", final_url)
        if asin_match:
            asin = asin_match.group(1)
            return f"https://www.amazon.in/dp/{asin}?tag={AMAZON_TAG}", "Amazon", f"amz_{asin}"

    # Flipkart Link
    elif "flipkart.com" in final_url:
        pid_match = re.search(r"pid=([A-Z0-9]+)", final_url)
        clean_path = final_url.split("?")[0]
        if pid_match:
            pid = pid_match.group(1)
            aff_url = f"{clean_path}?affid={FLIPKART_AFFID}" if FLIPKART_AFFID else clean_path
            return aff_url, "Flipkart", f"fk_{pid}"

    return None, None, None


def parse_telegram_upstream():
    """Scrapes DesiDime & DealsMagnet public streams for real-time loots."""
    discovered = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for channel_url in UPSTREAM_TELEGRAM_CHANNELS:
        try:
            res = requests.get(channel_url, headers=headers, timeout=15)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            messages = soup.select("div.tgme_widget_message_wrap")

            # Check last 10 messages from each channel
            for msg in messages[-10:]:
                text_el = msg.select_one("div.tgme_widget_message_text")
                if not text_el:
                    continue
                raw_text = text_el.get_text(separator=" ")
                text_lower = raw_text.lower()

                # 1. Filter out junk and competitor brands
                if any(junk in text_lower for junk in JUNK_KEYWORDS):
                    continue
                if any(re.search(r"\b" + re.escape(b) + r"\b", text_lower) for b in BLOCKED_BRANDS):
                    continue

                # 2. Check if it's one of your target brands OR a price glitch
                has_brand = any(re.search(r"\b" + re.escape(brand) + r"\b", text_lower) for brand in ALLOWED_BRANDS)
                is_glitch = bool(re.search(r"\b(glitch|price error|price bug|loot drop|flat ₹1|loot at ₹|90% off|85% off)\b", text_lower))

                if not (has_brand or is_glitch):
                    continue

                # 3. Extract deal link
                raw_links = [a["href"] for a in text_el.find_all("a", href=True)]
                if not raw_links:
                    raw_links = re.findall(r"(https?://[^\s]+)", raw_text)

                clean_url, store, unique_id = None, None, None
                for link in raw_links:
                    clean_url, store, unique_id = unwrap_deal_url(link)
                    if clean_url:
                        break

                if not clean_url:
                    continue

                # 4. Extract photo if present
                photo_el = msg.select_one("a.tgme_widget_message_photo_wrap")
                image_url = None
                if photo_el and photo_el.get("style"):
                    img_match = re.search(r"background-image:url\(['\"]?(.*?)['\"]?\)", photo_el["style"])
                    if img_match:
                        image_url = img_match.group(1)

                # Clean caption
                headline = raw_text.split("\n")[0][:90]
                headline = re.sub(r"@[A-Za-z0-9_]+", "", headline).strip()

                discovered.append({
                    "id": unique_id,
                    "signature": get_title_signature(headline),
                    "store": store,
                    "title": headline,
                    "deal_price": None,
                    "mrp_price": None,
                    "discount": None,
                    "image_url": image_url,
                    "deal_url": clean_url,
                    "is_glitch": is_glitch,
                    "source": "upstream"
                })
        except Exception as e:
            print(f"[WARNING] Upstream scan error: {e}")

    return discovered


# --- DIRECT SEARCH ENGINE ---

def extract_deal_info(title, deal_price, mrp_price, image_url, deal_url, unique_id, store):
    if not title or len(title) < 15 or not deal_price or deal_price < 299:
        return None
    if is_junk_item(title) or not is_genuine_brand_product(title):
        return None

    discount = int(((mrp_price - deal_price) / mrp_price) * 100) if mrp_price and mrp_price > deal_price else 0
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
        "deal_url": deal_url,
        "source": "store_search"
    }


def parse_amazon(html):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"], div[data-asin]:not([data-asin=""])')
    results = []

    for item in items:
        asin = item.get("data-asin", "").strip()
        if not asin or len(asin) != 10 or not asin.isalnum():
            continue

        title_el = item.select_one("h2 span") or item.select_one("h2 a span") or item.select_one("h2")
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
        deal = extract_deal_info(title, deal_price, mrp_price, image_url, deal_url, f"amz_{asin}", "Amazon")
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

        deal_price = min(extracted[0], extracted[1]) if len(extracted) >= 2 else (extracted[0] if extracted else None)
        mrp_price = max(extracted[0], extracted[1]) if len(extracted) >= 2 else None

        deal = extract_deal_info(title, deal_price, mrp_price, image_url, deal_url, f"fk_{pid}", "Flipkart")
        if deal:
            results.append(deal)

    return results


def send_telegram(deal):
    store_badge = "🛒 <b>Flipkart</b>" if deal["store"] == "Flipkart" else "📦 <b>Amazon</b>"

    if deal.get("is_glitch"):
        caption = f"🚨 <b>[PRICE GLITCH / LOOT DROP]</b> 🚨\n"
        caption += f"{store_badge} | ⚡ <b>Fast Loot Alert!</b>\n\n"
        caption += f"<b>{deal['title']}</b>\n\n"
        if deal.get("deal_price"):
            caption += f"💰 <b>Glitch Price:</b> ₹{int(deal['deal_price']):,}\n"
        if deal.get("mrp_price"):
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n"
        caption += "\n⚠️ <i>Price can expire any minute! Order fast!</i>\n\n"
        caption += f"🔗 <b>Buy Deal:</b>\n{deal['deal_url']}"
    elif deal.get("is_price_drop"):
        caption = f"📉 <b>[PRICE DROP ALERT!]</b> 📉\n"
        caption += f"{store_badge} | <b>Dropped ₹{int(deal['drop_amount']):,}!</b>\n\n"
        caption += f"⚡ <b>{deal['title']}</b>\n\n"
        caption += f"💰 <b>Dropped Price:</b> ₹{int(deal['deal_price']):,}\n"
        caption += f"🏷️ <b>Previous Deal Price:</b> <s>₹{int(deal['previous_price']):,}</s>\n"
        if deal["mrp_price"]:
            caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n"
        caption += "\n🔥 <i>Price just fell below normal! Grab fast!</i>\n\n"
        caption += f"🛒 <b>Order Now:</b>\n{deal['deal_url']}"
    else:
        caption = f"{store_badge} | 🔥 <b>Brand Deal Alert</b>\n\n"
        caption += f"<b>{deal['title']}</b>\n\n"
        if deal.get("deal_price"):
            caption += f"💰 <b>Deal Price:</b> ₹{int(deal['deal_price']):,}\n"
        caption += f"🛒 <b>Buy Now:</b>\n{deal['deal_url']}"

    caption += f"\n\n📢 <b>Join:</b> @jacklootdeals"

    if deal.get("image_url"):
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": CHAT_ID, "photo": deal["image_url"], "caption": caption, "parse_mode": "HTML"},
                timeout=25,
            )
            if res.json().get("ok"):
                print(f"[SUCCESS] Dispatched to channel: {deal['title']}")
                return True
        except Exception as e:
            print(f"[WARNING] Photo send failed: {e}")

    res = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=25,
    )
    if res.json().get("ok"):
        print(f"[SUCCESS] Text dispatched: {deal['title']}")
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
                    history = {item_id: {"base_price": None} for item_id in raw_data}
        except Exception:
            history = {}

    alerts_to_send = []
    current_run_signatures = set()

    # --- ENGINE 1: UPSTREAM PRIMARY SOURCES (DesiDime & DealsMagnet) ---
    print("[INFO] Checking DesiDime & DealsMagnet live feeds...")
    upstream_deals = parse_telegram_upstream()
    print(f"[INFO] Discovered {len(upstream_deals)} qualifying upstream items.")

    for deal in upstream_deals:
        uid = deal["id"]
        sig = deal["signature"]
        if uid not in history and sig not in current_run_signatures:
            alerts_to_send.append(deal)
            current_run_signatures.add(sig)
            history[uid] = {"base_price": None}

    # --- ENGINE 2: DIRECT SEARCH FOR PRICE DROPS ---
    active_feeds = get_active_feeds()
    for feed in active_feeds:
        print(f"[INFO] Scanning {feed['store']} feed: {feed['url'][:45]}...")
        html = fetch_page(feed["url"])
        if not html:
            continue

        store_items = parse_amazon(html) if feed["store"] == "Amazon" else parse_flipkart(html)
        print(f"[INFO] Found {len(store_items)} genuine brand items.")

        for item in store_items:
            uid = item["id"]
            current_price = item["deal_price"]
            mrp = item["mrp_price"]
            discount = item["discount"]
            sig = item["signature"]

            if sig in current_run_signatures:
                continue

            # Severe Price Bug
            is_extreme_glitch = discount >= 80 or (mrp and mrp >= 3000 and current_price <= 499)
            if is_extreme_glitch:
                item["is_glitch"] = True
                alerts_to_send.append(item)
                current_run_signatures.add(sig)
                history[uid] = {"base_price": current_price}
                continue

            # Price Drop Check
            if uid in history and history[uid].get("base_price"):
                base_price = history[uid]["base_price"]
                price_drop = base_price - current_price
                if current_price < base_price and (price_drop >= 150 or (price_drop / base_price) >= 0.05):
                    item["is_price_drop"] = True
                    item["previous_price"] = base_price
                    item["drop_amount"] = price_drop
                    alerts_to_send.append(item)
                    current_run_signatures.add(sig)
                    history[uid]["base_price"] = current_price
                    continue
                elif current_price < base_price:
                    history[uid]["base_price"] = current_price

            # Record baseline
            if uid not in history:
                history[uid] = {"base_price": current_price}
                print(f"[BASELINE] Saved {item['title'][:35]} at ₹{int(current_price)}")

        time.sleep(2)

    # Dispatch alerts
    posted_count = 0
    for deal in alerts_to_send:
        if posted_count >= MAX_DEALS_PER_RUN:
            break
        if send_telegram(deal):
            posted_count += 1
            time.sleep(3)

    # Trim tracking history to last 500 items
    if len(history) > 500:
        keys_to_keep = list(history.keys())[-500:]
        history = {k: history[k] for k in keys_to_keep}

    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)

    print(f"[INFO] Run complete. {posted_count} alerts broadcasted.")


if __name__ == "__main__":
    main()
