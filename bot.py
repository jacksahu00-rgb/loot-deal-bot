import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "lootdeals-21")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")

# Amazon India search feeds filtered for 50%+ discounts
DEAL_FEEDS = [
    # Electronics 50%+ off
    "https://www.amazon.in/s?i=electronics&rh=p_8%3A50-&s=popularity-rank",
    # Computers & Accessories 50%+ off
    "https://www.amazon.in/s?i=computers&rh=p_8%3A50-&s=popularity-rank",
]

# Minimum discount percentage to qualify as a "Loot" deal
MIN_DISCOUNT_PERCENT = 40
# Maximum deals to broadcast per run to avoid spamming the channel
MAX_DEALS_PER_RUN = 3


def fetch_page(url):
    api_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={url}&country_code=in"
    print(f"[INFO] Fetching deal category feed...")
    try:
        response = requests.get(api_url, timeout=45)
        if response.status_code == 200:
            return response.text
        print(f"[ERROR] Proxy error: HTTP {response.status_code}")
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


def parse_deals_from_feed(html):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"]')
    discovered = []

    for item in items:
        asin = item.get("data-asin")
        if not asin:
            continue

        # Title
        title_el = item.select_one("h2 span") or item.select_one("h2 a span")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) > 85:
            title = title[:82] + "..."

        # Deal Price
        price_el = item.select_one("span.a-price-whole")
        if not price_el:
            continue
        deal_price = clean_price(price_el.get_text(strip=True))
        if not deal_price or deal_price < 99:
            continue

        # Regular / MRP Price
        mrp_price = None
        mrp_el = item.select_one("span.a-price.a-text-price span.a-offscreen") or item.select_one("span.a-text-strike")
        if mrp_el:
            mrp_price = clean_price(mrp_el.get_text(strip=True))

        # Check discount %
        if mrp_price and mrp_price > deal_price:
            discount = int(((mrp_price - deal_price) / mrp_price) * 100)
        else:
            discount = 0

        # High-res Image
        img_el = item.select_one("img.s-image")
        image_url = img_el.get("src") if img_el else None

        if discount >= MIN_DISCOUNT_PERCENT:
            discovered.append({
                "asin": asin,
                "title": title,
                "deal_price": deal_price,
                "mrp_price": mrp_price,
                "discount": discount,
                "image_url": image_url,
            })

    return discovered


def send_telegram(deal):
    asin = deal["asin"]
    deal_url = f"https://www.amazon.in/dp/{asin}?th=1&tag={AFFILIATE_TAG}"

    caption = f"🔥 <b>{deal['discount']}% OFF | {deal['title']}</b>\n\n"
    caption += f"💰 <b>Deal Price:</b> ₹{int(deal['deal_price']):,}\n"
    if deal["mrp_price"]:
        caption += f"❌ <b>MRP:</b> ₹{int(deal['mrp_price']):,}\n\n"
    else:
        caption += "\n"
    caption += f"🛒 <b>Buy Now:</b>\n{deal_url}"

    # Try sending with image
    if deal["image_url"]:
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": CHAT_ID, "photo": deal["image_url"], "caption": caption, "parse_mode": "HTML"},
                timeout=25,
            )
            if res.json().get("ok"):
                print(f"[SUCCESS] Posted loot deal: {asin} ({deal['discount']}% off)")
                return True
        except Exception as e:
            print(f"[WARNING] Image post failed: {e}")

    # Fallback to text message
    res = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=25,
    )
    if res.json().get("ok"):
        print(f"[SUCCESS] Posted text deal: {asin}")
        return True

    return False


def main():
    history_file = "posted_deals.json"
    posted_asins = set()

    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                history_data = json.load(f)
                posted_asins = set(history_data)
        except Exception:
            posted_asins = set()

    deals_to_post = []

    for feed_url in DEAL_FEEDS:
        html = fetch_page(feed_url)
        if not html:
            continue

        deals = parse_deals_from_feed(html)
        print(f"[INFO] Found {len(deals)} items with >= {MIN_DISCOUNT_PERCENT}% discount.")

        for d in deals:
            if d["asin"] not in posted_asins:
                deals_to_post.append(d)

        time.sleep(2)

    # Sort discovered deals by highest discount first
    deals_to_post.sort(key=lambda x: x["discount"], reverse=True)

    posted_count = 0
    newly_posted_asins = []

    for deal in deals_to_post:
        if posted_count >= MAX_DEALS_PER_RUN:
            break

        sent = send_telegram(deal)
        if sent:
            posted_asins.add(deal["asin"])
            newly_posted_asins.append(deal["asin"])
            posted_count += 1
            time.sleep(3)

    if newly_posted_asins:
        # Keep only the last 500 ASINs to prevent the history file from bloating
        trimmed_history = list(posted_asins)[-500:]
        with open(history_file, "w") as f:
            json.dump(trimmed_history, f, indent=2)
        print(f"[INFO] Broadcasted {posted_count} loot deals. Updated {history_file}.")
    else:
        print("[INFO] No new deals met the criteria this run.")


if __name__ == "__main__":
    main()
