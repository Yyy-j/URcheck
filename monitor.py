# -*- coding: utf-8 -*-
# 依赖安装：
#   pip install requests playwright beautifulsoup4
#   python -m playwright install chromium

import json
import logging
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

TARGETS = {
    "かわさきテクノピア堀川町ハイツ": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/132.html",
    "アーベインビオ川崎": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/132.html",
    "サンスクエア川崎": "https://www.ur-net.go.jp/chintai/sp/kanto/kanagawa/area/131.html"
}

LAST_STATE_FILE = "last_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


def load_last_state():
    if os.path.exists(LAST_STATE_FILE):
        with open(LAST_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {name: 0 for name in TARGETS.keys()}


def save_last_state(state):
    with open(LAST_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_page(url, retries=3):
    """用 Playwright 抓渲染后的 HTML。"""
    for attempt in range(1, retries + 1):
        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent=HEADERS["User-Agent"],
                    viewport={"width": 1440, "height": 1200},
                )

                # 不用 networkidle，避免 UR 页面慢脚本导致超时
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # 给页面一点时间，把动态数字填进 DOM
                page.wait_for_timeout(5000)

                html = page.content()
                browser.close()
                return html

        except Exception as e:
            logging.error(f"Attempt {attempt}/{retries} failed to fetch {url}: {e}")
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

            if attempt < retries:
                time.sleep(5)
            else:
                return None


def extract_vacancy_count(text):
    """弱兜底：只在 DOM 没抓到时才使用。"""
    if re.search(r"空室なし|満室|募集停止", text):
        return 0

    m = re.search(r"(\d+)\s*(室|件|戸)", text)
    if m:
        return int(m.group(1))

    return -1


def extract_page_total_vacancy(html):
    """
    提取页面总空室数。
    优先读取：
      <strong class="rep_hit-row-count-top">0/1/2...</strong>

    返回:
      >0: 页面有空室
       0: 页面明确无空室
      -1: 未识别到
    """
    soup = BeautifulSoup(html, "html.parser")

    # 优先抓动态数字节点
    tag = soup.select_one("strong.rep_hit-row-count-top")
    if tag:
        text = tag.get_text(strip=True)
        logging.info(f"[TOTAL-CLASS] rep_hit-row-count-top = '{text}'")
        if text.isdigit():
            return int(text)

    # 文字兜底
    page_text = soup.get_text(" ", strip=True)
    page_text = re.sub(r"\s+", " ", page_text)
    m = re.search(r"該当空室数\s*(\d+)\s*部屋", page_text)
    if m:
        total = int(m.group(1))
        logging.info(f"[TOTAL-TEXT] 該当空室数 = {total} 部屋")
        return total

    logging.warning("[TOTAL] Could not determine page total vacancy")
    return -1


def find_vacancy_count(html, name):
    """
    当页面总空室数 > 0 时，进一步识别具体某个房子的空室数。
    优先读取：
      <strong class="rep_bukken-count-room">1</strong>
    """
    soup = BeautifulSoup(html, "html.parser")

    # 第一步：找到包含物件名的文字节点
    nodes = soup.find_all(string=re.compile(re.escape(name)))
    if not nodes:
        logging.warning(f"'{name}' not found on page")
        return -1

    for node in nodes:
        parent = node.parent
        ancestors = [parent] + list(parent.parents)[:20]

        # Pass 1：在祖先容器内直接找
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

        # Pass 2：在更大的 card/list 容器里找
        card_selectors = [
            "li",
            "article",
            "section",
            "[class*='item']",
            "[class*='card']",
            "[class*='bukken']",
            "[class*='result']",
            "[class*='list']",
            "[class*='property']",
        ]

        checked_ids = set()
        for tag in ancestors:
            for selector in card_selectors:
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
                        logging.info(
                            f"[CARD] '{name}' -> rep_bukken-count-room = '{text}' (selector={selector})"
                        )
                        if text.isdigit():
                            return int(text)

    # 最后弱兜底：找物件名附近的文本
    page_text = soup.get_text(" ", strip=True)
    idx = page_text.find(name)
    if idx != -1:
        snippet = page_text[max(0, idx - 50): idx + 200]
        logging.debug(f"Fallback snippet for '{name}': {snippet}")
        return extract_vacancy_count(snippet)

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
                logging.warning(f"  Failed to fetch page, keeping last state for '{name}'")
                current_state[name] = last_state.get(name, 0)
                continue

            page_total = extract_page_total_vacancy(html)

            if page_total == 0:
                # 页面明确显示 0 部屋，则不用继续识别具体房屋
                logging.info(f"  → {name}: 0 件空室（page total = 0，skip detail lookup）")
                count = 0

            elif page_total > 0:
                # 页面总空室数 > 0，才继续查具体哪个房子空了
                logging.info(f"  页面总空室数 = {page_total}，开始定位具体物件: {name}")
                count = find_vacancy_count(html, name)
                logging.info(f"  → {name}: {count} 件空室")

            else:
                # 页面总数没识别到，走 fallback
                logging.warning(f"  页面总空室数未识别，fallback 到具体物件识别: {name}")
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
        if not chat_id:
            logging.error("No TELEGRAM_CHAT_ID provided.")
            return

        chat_ids = [cid.strip() for cid in chat_id.splitlines() if cid.strip()]
        for msg in notifications:
            for cid in chat_ids:
                send_telegram(bot_token, cid, msg)
    else:
        logging.info("No new vacancies detected")


if __name__ == "__main__":
    main()