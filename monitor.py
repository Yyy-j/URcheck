# -*- coding: utf-8 -*-
# 依赖安装：
#   pip install requests playwright beautifulsoup4
#   playwright install chromium
import requests
from playwright.sync_api import sync_playwright
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
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2500)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logging.error(f"Failed to fetch {url}: {e}")
        return None



def extract_page_total_vacancy(html):
    """从页面全文提取「該当空室数 x 部屋」中的 x。返回 int>=0 或 -1（未找到）。"""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"該当空室数\s*(\d+)\s*部屋", page_text)
    if m:
        total = int(m.group(1))
        logging.info(f"[TOTAL] 該当空室数 = {total} 部屋")
        return total
    logging.debug("[TOTAL] 該当空室数 not found in page text")
    return -1

def find_vacancy_count(html, name):
    soup = BeautifulSoup(html, "html.parser")

    # 第一步：定位包含物件名的文字节点
    nodes = soup.find_all(string=re.compile(re.escape(name)))
    if not nodes:
        logging.warning(f"'{name}' not found on page")
        return -1

    for node in nodes:
        parent = node.parent
        ancestors = [parent] + list(parent.parents)[:20]

        # Pass 1：在直接祖先容器内找 strong.rep_bukken-count-room
        for tag in ancestors:
            tag_text = tag.get_text(" ", strip=True)
            if name not in tag_text:
                continue

            count_tag = tag.select_one("strong.rep_bukken-count-room")
            if count_tag:
                text = count_tag.get_text(strip=True)
                logging.info(f"[CLASS] '{name}' -> rep_bukken-count-room = '{text}'")
                if text.isdigit():
                    return int(text)

        # Pass 2：从每个祖先容器出发，在其所有子孙中找同时含物件名
        #         且含 strong.rep_bukken-count-room 的更大 card 容器
        CARD_SELECTORS = [
            "li", "article", "section",
            "[class*='item']", "[class*='card']",
            "[class*='bukken']", "[class*='result']",
            "[class*='list']", "[class*='property']",
        ]
        checked_ids = set()
        for tag in ancestors:
            for selector in CARD_SELECTORS:
                for card in tag.select(selector):
                    card_id = id(card)
                    if card_id in checked_ids:
                        continue
                    checked_ids.add(card_id)

                    card_text = card.get_text(" ", strip=True)
                    if name not in card_text:
                        continue

                    count_tag = card.select_one("strong.rep_bukken-count-room")
                    if count_tag:
                        text = count_tag.get_text(strip=True)
                        logging.info(f"[CARD] '{name}' -> rep_bukken-count-room = '{text}' (selector={selector})")
                        if text.isdigit():
                            return int(text)

    logging.warning(f"Vacancy count not found for '{name}'")
    return -1

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

            page_total = extract_page_total_vacancy(html)

            if page_total == 0:
                # 页面明确显示空室数为 0，无需深入查找
                logging.info(f"  → {name}: 0 件空室（page total = 0）")
                count = 0
            else:
                # page_total > 0 或 -1（未识别）：继续用 find_vacancy_count 精确定位
                count = find_vacancy_count(html, name)
                logging.info(f"  → {name}: {count} 件空室")

            if count < 0:
                logging.warning(f"  Could not determine vacancy for '{name}', keeping last state")
                current_state[name] = last_state.get(name, 0)
                continue

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
