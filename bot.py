import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "deal-21")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")


def fetch_amazon_page(asin):
    if not SCRAPER_API_KEY:
        print("[ERROR] SCRAPER_API_KEY is missing!")
        return None

    target_url = f"https://www.amazon.in/dp/{asin}"
    api_url = (
        f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}"
        f"&url={target_url}&country_code=in"
    )
    print(f"[INFO] Fetching {asin} through ScraperAPI proxy...")
    try:
        response = requests.get(api_url, timeout=60)
        if response.status_code == 200:
            return response.text
        print(f"[ERROR] Proxy returned status {response.status_code}")
    except Exception as err:
        print(f"[ERROR] Request error: {err}")
    return None


def clean_price(text):
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned.split(".")[0])
    except ValueError:
        return None


def parse_product(html, asin):
    soup = BeautifulSoup(html, "html.parser")

    # 1. Product Title
    title_elem = soup.select_one("#productTitle")
    title = title_elem.get_text(strip=True) if title_elem else f"Product ({asin})"
    if len(title) > 85:
        title = title[:82] + "..."

    # 2. Deal Price
    deal_price = None
    price_selectors = [
        "span.priceToPay span.a-price-whole",
        "#corePriceDisplay_desktop_feature_div span.a-price-whole",
        "#apex_desktop span.a-price-whole",
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        "span.a-price span.a-offscreen",
    ]
    for sel in price_selectors:
        elem = soup.select_one(sel)
        if elem:
            val = clean_price(elem.get_text(strip=True))
            if val:
                deal_price = val
                break

    # 3. Regular / MRP Price
    regular_price = None
    mrp_selectors = [
        "span.a-price.a-text-price[data-a-strike='true'] span.a-offscreen",
        "span.basisPrice span.a-price-whole",
        "#corePrice_desktop span.a-text-strike",
        "span.priceBlockStrikePriceString",
    ]
    for sel in mrp_selectors:
        elem = soup.select_one(sel)
        if elem:
            val = clean_price(elem.get_text(strip=True))
            if val and (deal_price is None or val > deal_price):
                regular_price = val
                break

    # 4. Product Image
    image_url = None
    img_elem = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
    if img_elem:
        image_url = img_elem.get("data-old-hires") or img_elem.get("src")

    return title, deal_price, regular_price, image_url


def send_telegram(title, deal_price, regular_price, asin, image_url):
    deal_url = f"https://www.amazon.in/dp/{asin}?th=1&tag={AFFILIATE_TAG}"

    caption = f"<b>{title} @ ₹{int(deal_price):,}</b>\n\n"
    caption += f"🔗 {deal_url}\n\n"
    if regular_price:
        caption += f"❌ Regular price @ ₹{int(regular_price):,}"

    if image_url:
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": CHAT_ID, "photo": image_url, "caption": caption, "parse_mode": "HTML"},
                timeout=25,
            )
            if res.status_code == 200:
                print(f"[SUCCESS] Telegram photo alert sent for {asin}!")
                return
        except Exception as e:
            print(f"[WARNING] Image upload failed: {e}")

    # Fallback to text message
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=25,
    )
    print(f"[SUCCESS] Telegram text alert sent for {asin}!")


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

        html = fetch_amazon_page(asin)
        if not html:
            continue

        title, deal_price, regular_price, image_url = parse_product(html, asin)

        if not deal_price:
            print(f"[WARNING] Could not parse price for {asin}")
            continue

        print(f"[INFO] {asin} | Current: ₹{deal_price} | MRP: ₹{regular_price} | Threshold: ₹{threshold}")

        if deal_price <= threshold and (last_price is None or deal_price < last_price):
            print(f"[ALERT] Target reached for {asin}! Posting to Telegram...")
            send_telegram(title, deal_price, regular_price, asin, image_url)
            item["last_price"] = deal_price
            updated = True
        elif last_price is None:
            item["last_price"] = deal_price
            updated = True

        time.sleep(2)

    if updated:
        with open("products.json", "w") as f:
            json.dump(products, f, indent=2)
        print("[INFO] Saved updated baseline to products.json.")


if __name__ == "__main__":
    main()
