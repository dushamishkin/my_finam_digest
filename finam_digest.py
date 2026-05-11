#!/usr/bin/env python3
"""
Finam.ru Daily & Weekly Digest → Telegram
Разделы: Новости компаний, Аналитика, Прогнозы
"""

import os
import sys
import json
import hashlib
import feedparser
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ─── Настройки ────────────────────────────────────────────────────────────────

FEEDS = {
    "Новости компаний и экономики": "https://www.finam.ru/rsspoint/rss/?cat=companies",
    "Аналитика и комментарии":     "https://www.finam.ru/rsspoint/rss/?cat=market",
    "Прогнозы и сценарии":         "https://www.finam.ru/rsspoint/rss/?cat=forecasts",
}

MOSCOW_TZ = timezone(timedelta(hours=3))
MAX_ITEMS_PER_FEED = 15       # статей с каждой ленты
SEEN_FILE = "seen_ids.json"   # кэш уже отправленных статей

# ─── Вспомогательные функции ─────────────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def article_id(entry) -> str:
    return hashlib.md5((entry.get("id") or entry.get("link", "")).encode()).hexdigest()

def fetch_articles(seen: set, weekly: bool = False) -> list[dict]:
    """Собирает статьи из RSS. При weekly=True берёт за 7 дней, иначе за 1 день."""
    articles = []
    cutoff_hours = 168 if weekly else 26   # 7 дней или ~1 день
    now = datetime.now(MOSCOW_TZ)

    for section, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[WARN] Не удалось получить ленту {section}: {e}")
            continue

        for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
            aid = article_id(entry)

            # Пропускаем уже виденные (только для ежедневного)
            if not weekly and aid in seen:
                continue

            # Фильтр по времени
            pub = entry.get("published_parsed")
            if pub:
                pub_dt = datetime(*pub[:6], tzinfo=timezone.utc).astimezone(MOSCOW_TZ)
                if (now - pub_dt).total_seconds() > cutoff_hours * 3600:
                    continue

            articles.append({
                "id":      aid,
                "section": section,
                "title":   entry.get("title", "").strip(),
                "summary": entry.get("summary", "")[:600].strip(),
                "link":    entry.get("link", ""),
                "pub":     pub_dt.strftime("%d.%m %H:%M") if pub else "",
            })

    return articles

def build_prompt(articles: list[dict], weekly: bool) -> str:
    period = "неделю" if weekly else "день"
    lines = []
    for a in articles:
        lines.append(
            f"[{a['section']}] {a['pub']} | {a['title']}\n{a['summary']}\n{a['link']}\n"
        )
    content = "\n".join(lines)

    return f"""Ты финансовый аналитик. На основе материалов с Finam.ru составь структурированный дайджест за {period}.

ПРАВИЛА:
- Пиши по-русски, чётко и по делу
- Не копируй текст дословно — излагай суть своими словами
- Для каждого раздела выдели 2-4 самые важные идеи
- В конце — 3-5 ключевых вывода
- Формат: Telegram Markdown (жирный **текст**, курсив _текст_, ссылки [текст](url))
- Объём: {"1500-2500" if weekly else "800-1500"} символов

МАТЕРИАЛЫ:
{content}

СТРУКТУРА ДАЙДЖЕСТА:
{"📅 *ЕЖЕНЕДЕЛЬНЫЙ ДАЙДЖЕСТ FINAM*" if weekly else "🌅 *ЕЖЕДНЕВНЫЙ ДАЙДЖЕСТ FINAM*"} — {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y')}

📰 *Новости компаний и экономики*
...

📊 *Аналитика и комментарии*
...

🔮 *Прогнозы и сценарии*
...

💡 *Ключевые выводы*
...
"""

def generate_digest(articles: list[dict], weekly: bool) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": build_prompt(articles, weekly)}]}],
        "generationConfig": {"maxOutputTokens": 2000, "temperature": 0.4},
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result["candidates"][0]["content"]["parts"][0]["text"]

def send_telegram(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    # Telegram ограничивает сообщение 4096 символами — режем при необходимости
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                raise RuntimeError(f"Telegram error: {result}")

# ─── Точка входа ─────────────────────────────────────────────────────────────

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    weekly = mode == "weekly"

    print(f"[INFO] Режим: {'еженедельный' if weekly else 'ежедневный'}")

    seen = load_seen()
    articles = fetch_articles(seen, weekly=weekly)

    if not articles:
        print("[INFO] Нет новых статей — дайджест не отправляется.")
        return

    print(f"[INFO] Найдено статей: {len(articles)}")
    digest = generate_digest(articles, weekly=weekly)
    send_telegram(digest)
    print("[INFO] Дайджест отправлен в Telegram ✓")

    # Обновляем кэш виденных (только для ежедневного)
    if not weekly:
        for a in articles:
            seen.add(a["id"])
        save_seen(seen)

if __name__ == "__main__":
    main()
