import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "deal-21")


def get_clean_price(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_via_reader(asin):
    """
    Fetches the Amazon product page via Jina Reader, which runs the
    browser rendering server-side and strips Amazon WAF/CAPTCHA blocks.
    """
    url = f"https://r.jina.ai/https://www.amazon.in/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-No-Cache": "true",
    }
    
    print(f"[INFO] Fetching {asin} through cloud reader...")
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200 and len(response.text) > 500:
            return response.text
    except Exception as e:
        print(f"[WARNING] Reader fetch error: {e}")
    
    return None


def parse_product_details(text, asin):
    # 1. Product Title
    title = None
    title_match = re.search(r"Title:\s*(.+)", text)
    if title_match:
        title = title_match.group(1).strip()
    else:
        # Fallback to first line
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        title = lines[0] if lines else f"Amazon Product ({asin})"

    # Clean title to keep it readable
    title = re.sub(r"\s*:\s*Amazon\.in.*", "", title)

    # 2. Extract Prices
    # Find deal/current price
    deal_price = None
    mrp_price = None

    # Search for MRP / Regular price patterns
    mrp_match = re.search(r"(?:M\.?R\.?P\.?|MRP|Typical price|Regular price)[:\s]+(?:₹|Rs\.?)*\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    if mrp_match:
        mrp_price = get_clean_price(mrp_match.group(1))

    # Search for visible ₹ price values
    all_prices = re.findall(r"(?:₹|Rs\.?)\s*([\d,]+(?:\.\d{2})?)", text)
    valid_prices = []
    for p in all_prices:
        val = get_clean_price(p)
        if val and 50 < val < 1000000:
            valid_prices.append(val)

    if valid_prices:
        # Deal price is typically the prominent current price
        deal_price = valid_prices[0]
        # If MRP wasn't extracted by label, take the highest price found as MRP
        if not mrp_price and len(valid_prices) > 1 and max(valid_prices) > deal_price:
            mrp_price = max(valid_prices)

    # 3. Extract High-Res Product Image
    image_url = None
    img_match = re.search(r"https://m\.media-amazon\.com/images/I/[A-Za-z0-9%_\-\.]+\.(?:jpg|png|jpeg)", text)
    if img_match:
        image_url = img_match.group(0)

    return title, deal_price, mrp_price, image_url


def send_telegram(title, deal_price, mrp_price, asin, image_url):
    deal_url = f"https://www.amazon.in/dp/{asin}?th=1&tag={AFFILIATE_TAG}"

    caption = f"<b>{title} @ ₹{int(deal_price):,}</b>\n\n"
    caption += f"🔗 {deal_url}\n\n"
    if mrp_price and mrp_price > deal_price:
        caption += f"❌ Regular price @ ₹{int(mrp_price):,}"

    # Try sending as photo first
    if image_url:
        endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        res = requests.post(
            endpoint,
            data={
                "chat_id": CHAT_ID,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML",
            },
            timeout=25,
        )
        if res.status_code == 200:
            print(f"[SUCCESS] Telegram photo alert posted for {asin}!")
            return

    # Fallback to text message
    text_endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        text_endpoint,
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=25,
    )
    print(f"[SUCCESS] Telegram text alert posted for {asin}!")


def main():
    if not os.path.exists("products.json"):
        print("[ERROR] products.json not found!")
        return

    with open("products.json", "r") as f:
        products = json.load(f)

    updated = False

    for item in products:
        asin = item.get("asin")
        threshold = item.get("threshold_price", float("inf"))
        last_price = item.get("last_price")

        content = fetch_via_reader(asin)
        if not content:
            print(f"[ERROR] Could not fetch content for {asin}")
            continue

        title, deal_price, mrp_price, image_url = parse_product_details(content, asin)

        if not deal_price:
            print(f"[WARNING] Could not parse price for {asin}")
            continue

        print(f"[INFO] {asin}: Title='{title[:35]}...' | Price=₹{deal_price} | MRP=₹{mrp_price} | Threshold=₹{threshold}")

        # Trigger if price meets threshold condition
        if deal_price <= threshold and (last_price is None or deal_price < last_price):
            print(f"[ALERT] Deal detected for {asin}! Posting to Telegram...")
            send_telegram(title, deal_price, mrp_price, asin, image_url)
            item["last_price"] = deal_price
            updated = True
        elif last_price is None:
            item["last_price"] = deal_price
            updated = True

        time.sleep(3)

    if updated:
        with open("products.json", "w") as f:
            json.dump(products, f, indent=2)
        print("[INFO] products.json updated with latest price baselines.")


if __name__ == "__main__":
    main()
