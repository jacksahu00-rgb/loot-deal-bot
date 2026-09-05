import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from amazoncaptcha import AmazonCaptcha

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "deal-21")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}


def fetch_product_page(session, asin):
    url = f"https://www.amazon.in/dp/{asin}"
    for attempt in range(1, 4):
        print(f"[INFO] Fetching {asin} (Attempt {attempt})...")
        resp = session.get(url, headers=HEADERS, timeout=25)

        # Detect Amazon CAPTCHA
        if "validateCaptcha" in resp.text or "Robot Check" in resp.text:
            print("[WARNING] Amazon CAPTCHA detected. Attempting auto-solve...")
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form")
            if form:
                img = form.find("img")
                if img and img.get("src"):
                    captcha = AmazonCaptcha.fromlink(img["src"])
                    solution = captcha.solve()
                    print(f"[INFO] Solved CAPTCHA text: {solution}")

                    params = {
                        inp.get("name"): inp.get("value", "")
                        for inp in form.find_all("input")
                        if inp.get("name")
                    }
                    params["field-keywords"] = solution

                    action = form.get("action", "/errors/validateCaptcha")
                    if not action.startswith("http"):
                        action = f"https://www.amazon.in{action}"

                    resp = session.get(action, params=params, headers=HEADERS, timeout=25)
                    if "validateCaptcha" not in resp.text and "Robot Check" not in resp.text:
                        print("[SUCCESS] CAPTCHA bypassed successfully!")
                        return resp.text

            time.sleep(2)
            continue

        return resp.text

    return None


def parse_details(html):
    soup = BeautifulSoup(html, "html.parser")

    # 1. Product Title
    title_elem = soup.select_one("#productTitle")
    title = title_elem.get_text(strip=True) if title_elem else "Amazon Deal Product"

    # 2. Current Price
    deal_price = None
    price_selectors = [
        "span.priceToPay span.a-price-whole",
        "#apex_desktop span.a-price-whole",
        "#corePriceDisplay_desktop_feature_div span.a-price-whole",
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        "span.a-price span.a-offscreen",
    ]
    for sel in price_selectors:
        elem = soup.select_one(sel)
        if elem:
            cleaned = re.sub(r"[^\d.]", "", elem.get_text(strip=True))
            if cleaned:
                deal_price = float(cleaned.split(".")[0])
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
            cleaned = re.sub(r"[^\d.]", "", elem.get_text(strip=True))
            if cleaned:
                val = float(cleaned.split(".")[0])
                if deal_price and val > deal_price:
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
        endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        res = requests.post(
            endpoint,
            data={"chat_id": CHAT_ID, "photo": image_url, "caption": caption, "parse_mode": "HTML"},
            timeout=20,
        )
        if res.status_code == 200:
            print("[INFO] Telegram photo alert sent successfully!")
            return

    # Fallback to plain text if photo fails
    text_endpoint = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        text_endpoint,
        data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
        timeout=20,
    )
    print("[INFO] Telegram text alert sent successfully!")


def main():
    if not os.path.exists("products.json"):
        print("[ERROR] products.json not found!")
        return

    with open("products.json", "r") as f:
        products = json.load(f)

    session = requests.Session()
    updated = False

    for item in products:
        asin = item.get("asin")
        threshold = item.get("threshold_price", float("inf"))
        last_price = item.get("last_price")

        html = fetch_product_page(session, asin)
        if not html:
            print(f"[ERROR] Could not load product page for {asin}")
            continue

        title, current_price, regular_price, image_url = parse_details(html)
        if not current_price:
            print(f"[WARNING] Could not parse price for {asin}")
            continue

        print(f"[INFO] {asin} | Current: ₹{current_price} | Target: ₹{threshold} | Last: {last_price}")

        # Trigger if price is within budget and changed from last run
        if current_price <= threshold and (last_price is None or current_price < last_price):
            print(f"[ALERT] Deal found for {asin}! Sending Telegram post...")
            send_telegram(title, current_price, regular_price, asin, image_url)
            item["last_price"] = current_price
            updated = True
        elif last_price is None:
            item["last_price"] = current_price
            updated = True

        time.sleep(2)

    if updated:
        with open("products.json", "w") as f:
            json.dump(products, f, indent=2)
        print("[INFO] products.json updated.")


if __name__ == "__main__":
    main()
