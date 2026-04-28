"""
Sport News Scraper — DE
========================
Holt aktuelle Sport-News via RSS-Feeds (Fußball, NBA, NFL).
Filtert nach Relevanz und vermeidet Duplikate.
"""

import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import requests

logger = logging.getLogger("syncin")

USED_ARTICLES_FILE = Path(__file__).parent.parent / "output" / "used_articles.json"

# ── RSS Feeds ──────────────────────────────────────────────────────────────────
FEEDS = {
    "fussball": [
        "https://news.google.com/rss/search?q=fussball+bundesliga&hl=de&gl=DE&ceid=DE:de",
        "https://news.google.com/rss/search?q=fussball+champions+league&hl=de&gl=DE&ceid=DE:de",
        "https://news.google.com/rss/search?q=fussball+transfer&hl=de&gl=DE&ceid=DE:de",
    ],
    "nba": [
        "https://news.google.com/rss/search?q=NBA+basketball&hl=de&gl=DE&ceid=DE:de",
        "https://news.google.com/rss/search?q=NBA+highlights&hl=de&gl=DE&ceid=DE:de",
    ],
    "nfl": [
        "https://news.google.com/rss/search?q=NFL+american+football&hl=de&gl=DE&ceid=DE:de",
        "https://news.google.com/rss/search?q=NFL+touchdown&hl=de&gl=DE&ceid=DE:de",
    ],
}

# Schlüsselwörter für Rage-Bait Potenzial
SPICY_KEYWORDS = [
    "entlassen", "kritik", "skandal", "streit", "forderung", "versagt",
    "blamage", "niederlage", "fehler", "kontroverse", "wechsel", "raus",
    "fired", "benched", "trade", "worst", "fail", "drama", "feud",
    "überbewertet", "enttäuschung", "flop", "verletzt", "gesperrt",
]


def _load_used() -> set:
    try:
        return set(json.loads(USED_ARTICLES_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_used(used: set):
    USED_ARTICLES_FILE.parent.mkdir(exist_ok=True)
    # Nur die letzten 500 behalten
    entries = list(used)[-500:]
    USED_ARTICLES_FILE.write_text(json.dumps(entries), encoding="utf-8")


def _parse_feed(url: str) -> list[dict]:
    """Parst einen RSS-Feed und gibt Artikel zurück."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SportBot/1.0)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        articles = []
        for entry in feed.entries[:20]:
            title   = entry.get("title", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            link    = entry.get("link", "")
            # HTML-Tags entfernen
            import re
            summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]
            if title and link:
                articles.append({
                    "title":   title,
                    "summary": summary,
                    "link":    link,
                    "id":      link,
                })
        return articles
    except Exception as e:
        logger.warning(f"Feed-Fehler {url}: {e}")
        return []


def _spicy_score(article: dict) -> int:
    """Bewertet Rage-Bait-Potenzial (höher = spannender)."""
    text = (article["title"] + " " + article["summary"]).lower()
    score = 0
    for kw in SPICY_KEYWORDS:
        if kw in text:
            score += 2
    # Fragezeichen oder Ausrufezeichen = klickbarer Titel
    if "?" in article["title"]:
        score += 3
    if "!" in article["title"]:
        score += 1
    return score


def fetch_news(sport: str = None) -> dict:
    """
    Holt einen frischen, noch nicht verwendeten Artikel.
    sport: 'fussball', 'nba', 'nfl' oder None (zufällig)
    Gibt zurück: {title, summary, link, sport}
    """
    used = _load_used()

    # Sport wählen (gewichtet: Fußball häufiger)
    if sport is None:
        sport = random.choices(
            ["fussball", "nba", "nfl"],
            weights=[50, 30, 20],
            k=1
        )[0]

    feeds = FEEDS.get(sport, FEEDS["fussball"])
    random.shuffle(feeds)

    candidates = []
    for feed_url in feeds:
        articles = _parse_feed(feed_url)
        for a in articles:
            if a["id"] not in used:
                a["sport"] = sport
                a["spicy_score"] = _spicy_score(a)
                candidates.append(a)

    if not candidates:
        logger.warning(f"Keine neuen Artikel für {sport} gefunden")
        # Alle als "frisch" behandeln
        used.clear()
        for feed_url in feeds:
            articles = _parse_feed(feed_url)
            for a in articles:
                a["sport"] = sport
                a["spicy_score"] = _spicy_score(a)
                candidates.append(a)

    if not candidates:
        raise RuntimeError(f"Keine Artikel für {sport} gefunden — Feeds prüfen")

    # Nach Spicy-Score sortieren, top 5 zufällig auswählen
    candidates.sort(key=lambda x: x["spicy_score"], reverse=True)
    top = candidates[:min(5, len(candidates))]
    chosen = random.choice(top)

    used.add(chosen["id"])
    _save_used(used)

    logger.info(f"[news] Artikel gewählt ({sport}): {chosen['title'][:70]}")
    return chosen
