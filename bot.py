import os
import re
import json
import time
import random
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "deal-21")
JINA_API_KEY = os.getenv("JINA_API_KEY")  # optional but recommended — free at jina.ai, raises rate limit


def get_clean_price(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_via_reader(asin, max_retries=3):
    """
    Fetches the Amazon product page via Jina Reader, which runs the
    browser rendering server-side and strips Amazon WAF/CAPTCHA blocks.
    Now with visible diagnostics instead of a silent None on failure.
    """
    url = f"https://r.jina.ai/https://www.amazon.in/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-No-Cache": "true",
    }
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"

    for attempt in range(1, max_retries + 1):
        print(f"[INFO] Fetching {asin} through cloud reader (attempt {attempt}/{max_retries})...")
        try:
            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 200 and len(response.text) > 500:
                return response.text

            # This is the part that was previously silent — now we can see why it failed
            print(
                f"[DEBUG] {asin}: reader returned status={response.status_code}, "
                f"len={len(response.text)}, preview={response.text[:200]!r}"
            )

            if response.status_code == 429:
                # Rate limited — back off harder
                wait = 15 * attempt
                print(f"[WARNING] Rate limited by reader, waiting {wait}s")
                time.sleep(wait)
                continue

            if response.status_code in (403, 451, 999):
                # Amazon blocked the reader itself for this page
                print(f"[WARNING] Reader was blocked fetching {asin} (status {response.status_code})")
                time.sleep(random.uniform(5, 10))
                continue

        except Exception as e:
            print(f"[WARNING] Reader fetch error for {asin}: {e}")
            time.sleep(random.uniform(3, 6))

    return None
