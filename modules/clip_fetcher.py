"""
Clip Fetcher — Sport Bot DE
============================
3-stufige Suche:
  Phase 1 — YouTube, Spieler-spezifisch (yt-dlp ytsearch5)
  Phase 2 — YouTube generisch + Dailymotion API (kein Key nötig)
  Phase 3 — Pexels Stock-Video API (PEXELS_API_KEY in Railway)
Kein Gradient-Fallback — lieber Job abbrechen als Müll posten.
"""

import json
import logging
import os
import random
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("syncin")

# ── YouTube Cookie Support ────────────────────────────────────────────────────
_cookie_file: str | None = None

def _get_cookie_file() -> str | None:
    """Schreibt YOUTUBE_COOKIES env var einmalig in eine Temp-Datei, gibt Pfad zurück."""
    global _cookie_file
    if _cookie_file and Path(_cookie_file).exists():
        return _cookie_file
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not cookies:
        return None
    try:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="yt_cookies_", delete=False
        )
        f.write(cookies)
        f.close()
        _cookie_file = f.name
        logger.info(f"[clip] Cookie-Datei geschrieben: {_cookie_file}")
    except Exception as e:
        logger.warning(f"[clip] Cookie-Datei Fehler: {e}")
        return None
    return _cookie_file

# ── Query-Templates ───────────────────────────────────────────────────────────

# Phase 1 — Spieler-spezifisch, geringes Copyright-Risiko
# Pressekonferenzen, Training, Interviews = Club-eigener Content, selten geclaimt
PLAYER_QUERIES = {
    "fussball": [
        "{player} Pressekonferenz",
        "{player} Training",
        "{player} Interview 2025",
        "{player} press conference",
        "{player} Trainingseinheit",
        "{player} Spieler Interview",
        "{player} pre-match press conference",
        "{player} nach dem Spiel Interview",
    ],
    "nba": [
        "{player} press conference",
        "{player} postgame interview",
        "{player} pregame interview",
        "{player} media day",
        "{player} practice footage",
        "{player} interview 2025",
        "{player} NBA media session",
    ],
    "nfl": [
        "{player} press conference",
        "{player} postgame press conference",
        "{player} interview 2025",
        "{player} practice footage",
        "{player} media day",
        "{player} training camp",
    ],
}

# Phase 2 — Vereins-/Teamebene, ebenfalls geringes Copyright-Risiko
GENERIC_YT_QUERIES = {
    "fussball": [
        "{player} Verein Training",
        "{player} Vereins Pressekonferenz",
        "Fussball Spieler Interview 2025",
        "Bundesliga Pressekonferenz 2025",
        "Fussball Training Einheit",
        "Fussball Spieler Pressekonferenz",
        "Trainer Pressekonferenz Bundesliga",
        "Fussball Media Day 2025",
    ],
    "nba": [
        "{player} Team Practice",
        "NBA Spieler Pressekonferenz 2025",
        "Basketball Spieler Interview 2025",
        "NBA Media Day 2025",
        "Basketball Training Footage",
        "NBA Postgame Interview",
    ],
    "nfl": [
        "{player} Team Pressekonferenz",
        "NFL Spieler Interview 2025",
        "Football Pressekonferenz",
        "NFL Training Camp 2025",
        "NFL Media Day 2025",
    ],
}

# Dailymotion — risikoarmer Content
DAILYMOTION_QUERIES = {
    "fussball": [
        "football press conference",
        "soccer player interview",
        "football training session",
        "soccer media day",
        "fussball pressekonferenz",
    ],
    "nba": [
        "nba press conference",
        "basketball player interview",
        "nba media day",
    ],
    "nfl": [
        "nfl press conference",
        "football player interview",
        "nfl training camp",
    ],
}

PEXELS_QUERIES = {
    "fussball": ["soccer player", "football game", "soccer match", "football stadium"],
    "nba":      ["basketball player", "basketball game", "basketball court", "basketball dunk"],
    "nfl":      ["american football", "football game", "football player", "football stadium"],
}

# ── Highlight-Queries — TikTok + Instagram (hohes Engagement, YouTube Content ID Risiko)
PLAYER_QUERIES_HIGHLIGHTS = {
    "fussball": [
        "{player} Highlights 2025",
        "{player} beste Tore 2025",
        "{player} Skills 2025",
        "{player} Tore Zusammenfassung",
        "{player} beste Momente 2025",
        "{player} unglaubliche Tore",
        "{player} Skills Zusammenfassung",
        "{player} Top Plays 2025",
    ],
    "nba": [
        "{player} highlights 2025",
        "{player} best plays 2025",
        "{player} dunks 2025",
        "{player} skills 2025",
        "{player} top moments 2025",
        "{player} clutch plays",
        "{player} scoring highlights",
        "{player} NBA highlights",
    ],
    "nfl": [
        "{player} highlights 2025",
        "{player} best plays 2025",
        "{player} touchdowns 2025",
        "{player} skills 2025",
        "{player} top moments 2025",
        "{player} NFL highlights",
        "{player} best catches 2025",
        "{player} big plays 2025",
    ],
}

GENERIC_YT_QUERIES_HIGHLIGHTS = {
    "fussball": [
        "Fussball Highlights 2025",
        "Bundesliga beste Tore 2025",
        "Fussball beste Momente 2025",
        "Fussball Skills Zusammenfassung 2025",
        "Fussball unglaubliche Tore",
        "Champions League Highlights 2025",
        "Premier League Highlights 2025",
        "Fussball Highlights Zusammenfassung",
    ],
    "nba": [
        "NBA Highlights 2025",
        "Basketball beste Plays 2025",
        "NBA Dunks Zusammenfassung 2025",
        "Basketball Highlights Zusammenfassung",
        "NBA Top Plays 2025",
        "Basketball unglaubliche Plays",
    ],
    "nfl": [
        "NFL Highlights 2025",
        "Football beste Plays 2025",
        "NFL Touchdowns 2025",
        "Football unglaubliche Plays",
        "NFL Top Momente 2025",
    ],
}

DAILYMOTION_QUERIES_HIGHLIGHTS = {
    "fussball": [
        "football highlights",
        "soccer goals compilation",
        "football best goals",
        "fussball tore zusammenfassung",
    ],
    "nba": [
        "nba highlights",
        "basketball dunks",
        "nba best plays",
    ],
    "nfl": [
        "nfl highlights",
        "football touchdowns",
        "nfl best plays",
    ],
}

# ── Reddit Config ─────────────────────────────────────────────────────────────

REDDIT_SUBREDDITS = {
    "fussball": ["soccer", "bundesliga", "footballhighlights", "championsleague"],
    "nba":      ["nba", "nbahighlights"],
    "nfl":      ["nfl", "nflstreams"],
}

_REDDIT_VIDEO_DOMAINS = {
    "v.redd.it", "streamable.com", "youtu.be", "youtube.com",
    "clips.twitch.tv", "medal.tv", "streamff.com", "dubz.co",
    "clippituser.tv", "mixtape.moe", "gfycat.com",
}


# ── Download-Helfer ───────────────────────────────────────────────────────────

def _ytdlp(query_or_url: str, output_dir: Path, before: set,
           is_search: bool = True, timeout: int = 90) -> Path | None:
    """yt-dlp Suche oder direkter URL-Download."""
    inp = f"ytsearch5:{query_or_url}" if is_search else query_or_url
    logger.info(f"[clip] yt-dlp: {inp[:80]}")
    cmd = [
        "yt-dlp", inp,
        "--match-filter", "duration < 360",
        "--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]",
        "--merge-output-format", "mp4",
        "-o", str(output_dir / "clip_%(id)s.%(ext)s"),
        "--no-playlist", "--quiet", "--no-warnings",
        "--max-downloads", "1",
        "--socket-timeout", "20",
        "--retries", "3",
        "--no-check-certificate",
    ]
    cookie_file = _get_cookie_file()
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        after = set(output_dir.glob("clip_*.mp4"))
        new = after - before
        if new:
            clip = sorted(new, key=lambda f: f.stat().st_mtime)[-1]
            logger.info(f"[clip] Geladen: {clip.name} ({clip.stat().st_size/1024/1024:.1f} MB)")
            return clip
    except Exception as e:
        logger.warning(f"[clip] yt-dlp Fehler: {e}")
    return None


def _dailymotion(query: str, output_dir: Path, before: set) -> Path | None:
    """Dailymotion Public API — kein Key benötigt."""
    try:
        url = (
            "https://api.dailymotion.com/videos"
            f"?search={urllib.parse.quote(query)}"
            "&fields=id,url,duration"
            "&longer_than=20&shorter_than=300"
            "&limit=5&sort=relevance"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            videos = json.loads(resp.read()).get("list", [])
        for v in videos:
            video_url = v.get("url", "")
            if not video_url:
                continue
            logger.info(f"[clip] Dailymotion: {video_url}")
            clip = _ytdlp(video_url, output_dir, before, is_search=False, timeout=60)
            if clip:
                return clip
    except Exception as e:
        logger.warning(f"[clip] Dailymotion Fehler: {e}")
    return None


def _pexels(query: str, output_dir: Path, before: set) -> Path | None:
    """Pexels Video API — braucht PEXELS_API_KEY in Railway Variables."""
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import requests, certifi
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 10, "size": "medium"},
            timeout=15,
            verify=certifi.where(),
        )
        if not r.ok:
            logger.warning(f"[clip] Pexels HTTP {r.status_code}: {r.text[:120]}")
            return None
        videos = r.json().get("videos", [])

        random.shuffle(videos)
        for video in videos:
            files = sorted(
                [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"],
                key=lambda f: abs(f.get("height", 0) - 720)
            )
            for f in files:
                if 360 <= f.get("height", 0) <= 1080:
                    dl_url = f["link"]
                    out = output_dir / f"clip_pexels_{video['id']}.mp4"
                    logger.info(f"[clip] Pexels Download: {video['id']} ({f.get('height')}p)")
                    dl = requests.get(dl_url, timeout=90, stream=True, verify=certifi.where())
                    if not dl.ok:
                        continue
                    with open(out, "wb") as fh:
                        for chunk in dl.iter_content(chunk_size=1024 * 1024):
                            fh.write(chunk)
                    if out.stat().st_size > 500_000:
                        logger.info(f"[clip] Pexels OK: {out.name} ({out.stat().st_size/1024/1024:.1f} MB)")
                        return out
                    out.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"[clip] Pexels Fehler: {e}")
    return None


# ── Reddit Highlights ─────────────────────────────────────────────────────────

def _reddit_token() -> str | None:
    """Reddit OAuth2 Token via Client Credentials (kein User-Login nötig)."""
    import base64
    import requests as _rq
    client_id     = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        r = _rq.post(
            "https://www.reddit.com/api/v1/access_token",
            headers={"Authorization": f"Basic {auth}",
                     "User-Agent": "SynCinSportBot/1.0"},
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
        token = r.json().get("access_token")
        if token:
            logger.info("[clip] Reddit OAuth2 Token OK")
        return token
    except Exception as e:
        logger.warning(f"[clip] Reddit OAuth2 Fehler: {e}")
        return None


def _reddit(player: str, sport: str, output_dir: Path, before: set) -> Path | None:
    """
    Browsed Sport-Subreddits nach Spieler-Highlight-Clips.
    Nutzt OAuth2 (oauth.reddit.com) wenn REDDIT_CLIENT_ID/SECRET gesetzt —
    umgeht den 403 den Railway-IPs auf dem anonymen Endpoint bekommen.
    """
    import requests as _rq

    subs = REDDIT_SUBREDDITS.get(sport, ["sports"])

    # Nachname matchen — z.B. "Victor Wembanyama" → "wembanyama" suchen
    player_words = [w.lower() for w in player.split() if len(w) > 3]
    match_words  = player_words[-1:] if player_words else player_words

    token    = _reddit_token()
    base_url = "https://oauth.reddit.com" if token else "https://www.reddit.com"
    headers  = {"User-Agent": "SynCinSportBot/1.0 highlight-fetcher"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def _fetch(url: str) -> list:
        r = _rq.get(url, headers=headers, timeout=12)
        return r.json().get("data", {}).get("children", [])

    def _try_posts(posts: list) -> Path | None:
        for post in posts:
            d        = post.get("data", {})
            title    = d.get("title", "").lower()
            post_url = d.get("url", "")
            domain   = d.get("domain", "")
            if not all(w in title for w in match_words):
                continue
            is_video = (
                d.get("is_video", False)
                or domain in _REDDIT_VIDEO_DOMAINS
                or any(dom in post_url for dom in _REDDIT_VIDEO_DOMAINS)
            )
            if not is_video:
                continue
            logger.info(f"[clip] Reddit '{d.get('title','')[:60]}'")
            clip = _ytdlp(post_url, output_dir, before, is_search=False, timeout=60)
            if clip:
                return clip
        return None

    for sub in subs:
        for sort in ["new", "hot"]:
            try:
                posts = _fetch(f"{base_url}/r/{sub}/{sort}.json?limit=100")
                clip  = _try_posts(posts)
                if clip:
                    return clip
            except Exception as e:
                logger.warning(f"[clip] Reddit r/{sub}/{sort} Fehler: {e}")

    # Fallback: Reddit-Suche
    try:
        query = urllib.parse.quote(f"{player} highlights")
        posts = _fetch(
            f"{base_url}/r/{subs[0]}/search.json"
            f"?q={query}&sort=new&t=month&restrict_sr=1&limit=25"
        )
        clip = _try_posts(posts)
        if clip:
            return clip
    except Exception as e:
        logger.warning(f"[clip] Reddit-Suche Fallback Fehler: {e}")

    return None


# ── Haupt-Funktion ────────────────────────────────────────────────────────────

def fetch_clips(player: str, sport: str, output_dir: Path,
                duration_hint: float = 60.0, count: int = 3,
                mode: str = "youtube") -> list[Path]:
    """
    3-stufige Suche — gibt leere Liste zurück wenn alles fehlschlägt.
    mode="youtube"     → Pressekonferenz/Training-Clips (geringes Content-ID-Risiko)
    mode="highlights"  → Highlights/Tore/Skills-Clips (nur TikTok/IG)
    Kein Gradient-Fallback.
    """
    # Query-Dicts je nach Mode wählen
    if mode == "highlights":
        p_queries = PLAYER_QUERIES_HIGHLIGHTS
        g_queries = GENERIC_YT_QUERIES_HIGHLIGHTS
        d_queries = DAILYMOTION_QUERIES_HIGHLIGHTS
        fallback_sport = "fussball"
    else:
        p_queries = PLAYER_QUERIES
        g_queries = GENERIC_YT_QUERIES
        d_queries = DAILYMOTION_QUERIES
        fallback_sport = "fussball"

    output_dir.mkdir(exist_ok=True, parents=True)
    downloaded: list[Path] = []
    before = set(output_dir.glob("clip_*.mp4"))

    def _add(clip):
        if clip and clip not in downloaded:
            downloaded.append(clip)
            before.add(clip)

    # ── Phase 1: YouTube, Spieler-spezifisch ─────────────────────────────────
    if player and len(player.strip()) >= 3:
        templates = p_queries.get(sport, p_queries.get(fallback_sport, list(p_queries.values())[0]))
        queries = [t.format(player=player) for t in random.sample(templates, min(count + 1, len(templates)))]
        for q in queries:
            if len(downloaded) >= count:
                break
            _add(_ytdlp(q, output_dir, before))

    # ── Phase 1.5: Reddit Spieler-spezifische Highlights ─────────────────────
    if len(downloaded) < count and player and len(player.strip()) >= 3:
        logger.info(f"[clip] Phase 1.5: Reddit-Suche für '{player}'...")
        for _ in range(count - len(downloaded)):
            if len(downloaded) >= count:
                break
            _add(_reddit(player, sport, output_dir, before))

    # ── Phase 2: YouTube generisch + Dailymotion ─────────────────────────────
    if len(downloaded) < count:
        logger.info(f"[clip] Phase 1.5: {len(downloaded)}/{count} — starte Phase 2 (YouTube generisch + Dailymotion)...")

        raw_generic = g_queries.get(sport, g_queries.get(fallback_sport, list(g_queries.values())[0]))
        yt_generic = [t.format(player=player) if player else t
                      for t in random.sample(raw_generic, min(count - len(downloaded) + 1, len(raw_generic)))]
        for q in yt_generic:
            if len(downloaded) >= count:
                break
            _add(_ytdlp(q, output_dir, before))

        dm_pool = d_queries.get(sport, d_queries.get(fallback_sport, list(d_queries.values())[0]))
        dm_sample = random.sample(dm_pool, min(2, len(dm_pool)))
        for q in dm_sample:
            if len(downloaded) >= count:
                break
            _add(_dailymotion(q, output_dir, before))

    # ── Phase 3: Pexels Stock-Video ───────────────────────────────────────────
    if len(downloaded) < count:
        logger.info(f"[clip] Phase 2: {len(downloaded)}/{count} — starte Phase 3 (Pexels)...")
        px_queries = PEXELS_QUERIES.get(sport, ["sports"])
        for q in random.sample(px_queries, min(2, len(px_queries))):
            if len(downloaded) >= count:
                break
            _add(_pexels(q, output_dir, before))

    if not downloaded:
        logger.error(f"[clip] Alle 3 Phasen fehlgeschlagen für '{player}' ({sport}) mode={mode}")
    else:
        logger.info(f"[clip] {len(downloaded)} Clip(s) gefunden (mode={mode})")

    return downloaded[:count]


def fetch_clip(player: str, sport: str, output_dir: Path,
               duration_hint: float = 60.0, mode: str = "youtube") -> Path | None:
    clips = fetch_clips(player, sport, output_dir, duration_hint, count=1, mode=mode)
    return clips[0] if clips else None


def trim_clip(clip_path: Path, duration: float, output_path: Path) -> Path:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
            capture_output=True, text=True, timeout=15,
        )
        clip_dur = float(probe.stdout.strip())
    except Exception:
        clip_dur = duration + 10

    max_start = max(0, clip_dur - duration - 2)
    start = random.uniform(0, max_start) if max_start > 0 else 0

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start), "-i", str(clip_path),
        "-t", str(duration),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        logger.warning(f"[clip] Trim-Fehler: {r.stderr[:200]}")
        import shutil
        shutil.copy(str(clip_path), str(output_path))
    logger.info(f"[clip] Getrimmt: {output_path.name}")
    return output_path
