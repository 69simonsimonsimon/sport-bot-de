"""
Sport News Scraper — DE
========================
Holt aktuelle Sport-News via RSS-Feeds (Fußball, NBA, NFL).
Filtert nach Relevanz und vermeidet Duplikate.
"""

import json
import logging
import random
import time
import urllib.request
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

# Sport weights for random selection
SPORT_WEIGHTS = {"fussball": 50, "nba": 30, "nfl": 20}

# Trend cache: refreshed every 60 minutes
_trend_cache = {"weights": None, "ts": 0}


def _get_trending_weights() -> dict:
    """
    Returns sport weights boosted by Google Trends and recent article counts.
    Falls back to SPORT_WEIGHTS on any error. Caches result for 60 minutes.
    """
    global _trend_cache
    now = time.time()
    if _trend_cache["weights"] is not None and now - _trend_cache["ts"] < 3600:
        return _trend_cache["weights"]

    weights = dict(SPORT_WEIGHTS)

    try:
        # ── Step 1: Google Trends daily RSS boost ──────────────────────────
        trends_url = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=DE"
        req = urllib.request.Request(trends_url, headers={"User-Agent": "Mozilla/5.0 (compatible; SportBot/1.0)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            trends_xml = resp.read().decode("utf-8", errors="replace").lower()

        fussball_keywords = ["fußball", "bundesliga", "fussball", "football", "champions league"]
        nba_keywords = ["nba", "basketball"]
        nfl_keywords = ["nfl"]

        for kw in fussball_keywords:
            if kw in trends_xml:
                weights["fussball"] = weights.get("fussball", 0) + 15
                logger.info(f"[news] Trends boost: fussball +15 ({kw})")
                break
        for kw in nba_keywords:
            if kw in trends_xml:
                weights["nba"] = weights.get("nba", 0) + 15
                logger.info(f"[news] Trends boost: nba +15 ({kw})")
                break
        for kw in nfl_keywords:
            if kw in trends_xml:
                weights["nfl"] = weights.get("nfl", 0) + 15
                logger.info(f"[news] Trends boost: nfl +15 ({kw})")
                break

    except Exception as e:
        logger.warning(f"[news] Trends fetch failed: {e}")

    try:
        # ── Step 2: Recent article count bonus (up to +10 per sport) ───────
        cutoff = datetime.utcnow() - timedelta(hours=48)
        for sport, feed_list in FEEDS.items():
            if not feed_list:
                continue
            first_url = feed_list[0]
            try:
                headers = {"User-Agent": "Mozilla/5.0 (compatible; SportBot/1.0)"}
                resp = requests.get(first_url, headers=headers, timeout=8)
                feed = feedparser.parse(resp.content)
                recent_count = 0
                for entry in feed.entries:
                    published = entry.get("published_parsed") or entry.get("updated_parsed")
                    if published:
                        pub_dt = datetime(*published[:6])
                        if pub_dt >= cutoff:
                            recent_count += 1
                bonus = min(recent_count, 10)
                if bonus > 0:
                    weights[sport] = weights.get(sport, 0) + bonus
                    logger.info(f"[news] Recent-articles bonus: {sport} +{bonus} ({recent_count} Artikel < 48h)")
            except Exception as e:
                logger.debug(f"[news] Recent-articles check failed for {sport}: {e}")

    except Exception as e:
        logger.warning(f"[news] Recent-articles bonus failed: {e}")

    _trend_cache["weights"] = weights
    _trend_cache["ts"] = now
    return weights


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


def _fetch_article_text(url: str, max_chars: int = 2000) -> str:
    """Versucht den Volltext eines Artikels zu scrapen."""
    import re
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "de-DE,de;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        # Paragraphen extrahieren
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL | re.IGNORECASE)
        text = " ".join(re.sub(r"<[^>]+>", "", p).strip() for p in paragraphs)
        text = re.sub(r"\s+", " ", text).strip()

        # Mindestlänge prüfen — wenn zu kurz, ist es wahrscheinlich nur Navigation
        if len(text) < 200:
            return ""
        return text[:max_chars]
    except Exception as e:
        logger.debug(f"Volltext-Fetch fehlgeschlagen für {url}: {e}")
        return ""


def _parse_feed(url: str) -> list[dict]:
    """Parst einen RSS-Feed und gibt Artikel mit Volltext zurück."""
    import re
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
            summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]

            if not title or not link:
                continue

            # Volltext holen für bessere Fakten-Basis
            fulltext = _fetch_article_text(link)

            articles.append({
                "title":    title,
                "summary":  summary,
                "fulltext": fulltext,   # neu: Volltext für Claude
                "link":     link,
                "id":       link,
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

    # Sport wählen (dynamisch gewichtet via Trends)
    if sport is None:
        w = _get_trending_weights()
        sports = list(w.keys())
        weights = list(w.values())
        sport = random.choices(sports, weights=weights, k=1)[0]
        logger.info(f"[news] Dynamic weights: {w} → picked {sport}")

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
