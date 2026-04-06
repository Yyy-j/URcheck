# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Targets: mapping from complex name to the page URL where it appears
TARGETS = {
    "かわさきテクノピア堀川町ハイツ": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/132.html",
    "アーベインビオ川崎": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/132.html",
    "サンスクエア川崎": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/131.html",
}

LAST_STATE_FILE = "last_state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; URcheck/1.0; +https://github.com/Yyy-j/URcheck)"}

def load_last_state():
    if os.path.exists(LAST_STATE_FILE):
        with open(LAST_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # Initialize with zeros if missing
    return {name: 0 for name in TARGETS.keys()}

def save_last_state(state):
    with open(LAST_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        r.encoding = r.apparent_encoding
        return r.text
    except Exception as e:
        logging.error(f"Failed to fetch {url}: {e}")
        return None

def extract_count_from_text(text):
    # Try to find explicit numbers like "1室", "2件" or standalone digits
    m = re.search(r"(\d+)\s*(室|件|戸)?", text)
    if m:
        return int(m.group(1))
    # If contains phrases like 空室あり, 募集中, 空き, treat as 1
    if re.search(r"空室.*(あり|有|募集|募集中)|募集中|空き", text):
        return 1
    return 0

def find_vacancy_count(html, name):
    soup = BeautifulSoup(html, "html.parser")
    # Search for text node containing the name
    nodes = soup.find_all(string=re.compile(re.escape(name)))
    if not nodes:
        # not found on this page
        return 0
    # For each occurrence, try to inspect nearby text
    for node in nodes:
        parent = node.parent
        # Check parent and a few ancestor levels
        for tag in [parent] + parent.find_parents()[:4]:
            text = tag.get_text(" ", strip=True)
            count = extract_count_from_text(text)
            if count > 0:
                return count
        # Also check next siblings and previous siblings text
        for sibling in list(parent.next_siblings)[:6] + list(parent.previous_siblings)[:6]:
            if hasattr(sibling, 'get_text'):
                stext = sibling.get_text(" ", strip=True)
            else:
                stext = str(sibling).strip()
            count = extract_count_from_text(stext)
            if count > 0:
                return count
    # As a wider fallback, search the whole page near name position
    page_text = soup.get_text(" ", strip=True)
    # try to find name and take substring around it
    idx = page_text.find(name)
    if idx != -1:
        snippet = page_text[max(0, idx - 100): idx + 200]
        count = extract_count_from_text(snippet)
        if count > 0:
            return count
    return 0

def send_telegram(bot_token, chat_id, message):
    if not bot_token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        logging.info("Telegram notification sent")
        return True
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

def main():
    last_state = load_last_state()
    current_state = {}

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    notifications = []

    for name, url in TARGETS.items():
        logging.info(f"Checking {name} at {url}")
        html = fetch_page(url)
        if html is None:
            # leave previous state if fetch failed
            current_state[name] = last_state.get(name, 0)
            continue
        count = find_vacancy_count(html, name)
        logging.info(f"Detected {count} vacancies for {name}")
        current_state[name] = count

        last = int(last_state.get(name, 0))
        now = int(count)
        # Notify only on transition from 0 -> >0
        if last == 0 and now > 0:
            msg = f"[UR通知] {name} 有空房: {now} 件\n{url}"
            notifications.append(msg)

    # Send notifications (one message per complex)
    if notifications:
        for msg in notifications:
            send_telegram(bot_token, chat_id, msg)
    else:
        logging.info("No new vacancies detected")

    # Save current state (will be committed by GitHub Actions if changed)
    save_last_state(current_state)

if __name__ == '__main__':
    main()