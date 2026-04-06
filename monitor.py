# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

TARGETS = {
    "かわさきテクノピア堀川町ハイツ": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/132.html",
    "アーベインビオ川崎": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/132.html",
    "サンスクエア川崎": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/131.html",
    "西菅田": "https://www.ur-net.go.jp/chintai/kanto/kanagawa/area/102.html",
}

LAST_STATE_FILE = "last_state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; URcheck/1.0; +https://github.com/Yyy-j/URcheck)"}

def load_last_state():
    if os.path.exists(LAST_STATE_FILE):
        with open(LAST_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
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

def extract_vacancy_count(text):
    if re.search(r"空室なし|満室|募集停止", text):
        return 0
    m = re.search(r"(\d+)\s*(室|件|戸)", text)
    if m:
        return int(m.group(1))
    if re.search(r"空室あり|空室有|募集中", text):
        return 1
    return 0

def find_vacancy_count(html, name):
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.find_all(string=re.compile(re.escape(name)))
    if not nodes:
        logging.warning(f"'{name}' not found on page")
        return 0

    for node in nodes:
        parent = node.parent
        ancestors = [parent] + list(parent.parents)[:5]
        for tag in ancestors:
            tag_text = tag.get_text(" ", strip=True)
            if re.search(r"空室|募集|満室", tag_text):
                count = extract_vacancy_count(tag_text)
                if len(tag_text) < 500:
                    logging.debug(f"Matched in ancestor text (len={len(tag_text)}): {tag_text[:200]}")
                    return count

        for sibling in list(parent.next_siblings)[:8] + list(parent.previous_siblings)[:8]:
            stext = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            if re.search(r"空室|募集|満室", stext):
                count = extract_vacancy_count(stext)
                if count > 0:
                    return count

    page_text = soup.get_text(" ", strip=True)
    idx = page_text.find(name)
    if idx != -1:
        snippet = page_text[max(0, idx - 50): idx + 150]
        logging.debug(f"Fallback snippet for '{name}': {snippet}")
        return extract_vacancy_count(snippet)

    return 0

def send_telegram(bot_token, chat_id, message):
    if not bot_token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
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
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    test_mode = os.environ.get("TEST_MODE", "false").lower() == "true"

    notifications = []

    if test_mode:
        logging.info("=== TEST MODE: using last_state.json values directly ===")
        for name, url in TARGETS.items():
            now = int(last_state.get(name, 0))
            logging.info(f"  {name}: {now} 件（from last_state.json）")
            if now > 0:
                msg = f"🧪 <b>[テスト通知]</b>\n{name}\n空室数: <b>{now} 件</b>\n{url}"
                notifications.append(msg)
    else:
        current_state = {}
        for name, url in TARGETS.items():
            logging.info(f"Checking: {name}")
            html = fetch_page(url)
            if html is None:
                current_state[name] = last_state.get(name, 0)
                continue

            count = find_vacancy_count(html, name)
            logging.info(f"  → {name}: {count} 件空室")
            current_state[name] = count

            last = int(last_state.get(name, 0))
            now = int(count)

            if last == 0 and now > 0:
                msg = f"🏠 <b>[UR空室通知]</b>\n{name}\n空室数: <b>{now} 件</b>\n{url}"
                notifications.append(msg)
            elif last > 0 and now == 0:
                logging.info(f"  {name} 已満室（之前: {last} 件）")

        save_last_state(current_state)

    if notifications:
        chat_ids = [cid.strip() for cid in chat_id.splitlines() if cid.strip()]
        for msg in notifications:
            for cid in chat_ids:
                send_telegram(bot_token, cid, msg)
    else:
        logging.info("No new vacancies detected")

if __name__ == "__main__":
    main()
