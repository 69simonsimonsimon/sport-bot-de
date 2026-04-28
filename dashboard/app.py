"""
SynCinSportDE Dashboard — FastAPI Backend
Sport News Bot: Fußball, NBA, NFL
Postet 4x täglich aktuelle Sport-News mit Rage-Bait auf TikTok, YouTube, Instagram.
"""

import json
import logging
import os
import random
import sys
import threading
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "modules"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

IS_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))

# ── Logging ───────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "output")))
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
LOG_DIR = OUTPUT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

_handler = RotatingFileHandler(str(LOG_DIR / "bot.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
logger = logging.getLogger("syncin")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler())

# ── Telegram ──────────────────────────────────────────────────────────────────
def _tg_credentials():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def notify(title: str, message: str):
    try:
        import urllib.request as _ur, json as _j
        token, chat_id = _tg_credentials()
        if not token or not chat_id:
            return
        body = _j.dumps({"chat_id": chat_id, "text": f"<b>{title}</b>\n{message}", "parse_mode": "HTML"}).encode()
        req = _ur.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                          data=body, headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=10)
    except Exception:
        pass

# ── Zernio Upload ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(ROOT / "modules"))

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="SynCinSportDE")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

jobs: dict = {}
_schedule_lock = threading.Lock()
_scheduler_thread = None

# ── Zeitplan ──────────────────────────────────────────────────────────────────
DEFAULT_SCHEDULE = [
    {"time": "08:00", "sport": None},   # zufällig
    {"time": "15:00", "sport": None},
    {"time": "20:00", "sport": None},
    {"time": "01:00", "sport": None},
]

_schedule_file = OUTPUT_DIR / "schedule.json"

def _load_schedule() -> list:
    try:
        return json.loads(_schedule_file.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_SCHEDULE

def _save_schedule(slots: list):
    _schedule_file.write_text(json.dumps(slots, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Bunny Queue Upload ────────────────────────────────────────────────────────
def _bunny_upload(video_path, filename: str, meta: dict) -> bool:
    import certifi, requests as _rq
    password = os.environ.get("BUNNY_STORAGE_PASSWORD", "").strip()
    hostname = os.environ.get("BUNNY_STORAGE_HOSTNAME", "storage.bunnycdn.com")
    zone     = os.environ.get("BUNNY_STORAGE_NAME", "syncin")
    cdn_url  = os.environ.get("BUNNY_CDN_URL", "https://syncin.b-cdn.net")
    if not password:
        logger.error("[upload] BUNNY_STORAGE_PASSWORD nicht gesetzt")
        return False
    try:
        with open(str(video_path), "rb") as f:
            _rq.put(f"https://{hostname}/{zone}/queue/{filename}",
                    headers={"AccessKey": password, "Content-Type": "video/mp4"},
                    data=f, verify=certifi.where(), timeout=300).raise_for_status()
        meta["cdn_url"] = f"{cdn_url}/queue/{filename}"
        import json as _j
        _rq.put(f"https://{hostname}/{zone}/queue/{filename.replace('.mp4', '.json')}",
                headers={"AccessKey": password, "Content-Type": "application/json"},
                data=_j.dumps(meta, ensure_ascii=False).encode(),
                verify=certifi.where(), timeout=30).raise_for_status()
        logger.info(f"[upload] ✅ Bunny Queue: {filename}")
        return True
    except Exception as e:
        logger.error(f"[upload] Bunny Upload fehlgeschlagen: {e}")
        return False


def _fetch_trim_render(player, sport, stamp, mode, audio_path, audio_duration, tts_words, script):
    """Clips holen (mode='highlights'|'youtube'), trimmen, kombinieren, rendern. Gibt Video-Path oder None zurück."""
    from clip_fetcher  import fetch_clips, trim_clip
    from video_creator import create_video
    clip_dir = OUTPUT_DIR / "clips" / mode
    clip_dir.mkdir(parents=True, exist_ok=True)
    raw_clips = fetch_clips(player, sport, clip_dir, audio_duration, count=3, mode=mode)
    if not raw_clips:
        logger.warning(f"[gen] Keine {mode} Clips — überspringe")
        return None
    seg_dur = max(10.0, audio_duration / len(raw_clips))
    trimmed = []
    for i, rc in enumerate(raw_clips):
        t = clip_dir / f"trimmed_{stamp}_{i}.mp4"
        trim_clip(rc, seg_dur, t)
        trimmed.append(t)
        rc.unlink(missing_ok=True)
    if len(trimmed) == 1:
        combined = trimmed[0]
    else:
        combined = clip_dir / f"combined_{stamp}.mp4"
        lf = clip_dir / f"list_{stamp}.txt"
        lf.write_text("\n".join(f"file '{str(t.resolve())}'" for t in trimmed))
        import subprocess as _sp
        _sp.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(lf), "-c", "copy", str(combined)],
                capture_output=True, timeout=120)
        lf.unlink(missing_ok=True)
        for t in trimmed:
            t.unlink(missing_ok=True)
    video_path = OUTPUT_DIR / f"video_{mode}_{stamp}.mp4"
    create_video(combined, audio_path, script["title"], video_path, script["sport"], words=tts_words)
    combined.unlink(missing_ok=True)
    mb = video_path.stat().st_size / 1024 / 1024
    logger.info(f"[gen] {mode} Video: {video_path.name} ({mb:.1f} MB)")
    return video_path


# ── Video-Generierung ─────────────────────────────────────────────────────────
def _run_generation(job_id: str, sport: str = None):
    from news_scraper     import fetch_news
    from script_generator import generate_script
    from tts_generator    import generate_tts

    def upd(msg, pct=None):
        j = jobs.setdefault(job_id, {})
        j["message"] = msg
        if pct is not None:
            j["progress"] = pct
        logger.info(f"[job:{job_id}] {msg}")

    jobs[job_id] = {"status": "running", "progress": 0, "message": "Starte...", "video": None}

    try:
        # 1. News holen
        upd("Hole aktuelle Sport-News...", 10)
        article = fetch_news(sport)
        logger.info(f"[gen] Artikel: {article['title'][:70]}")

        # 2. Script generieren
        upd("Generiere Script...", 20)
        script = generate_script(article)
        logger.info(f"[gen] Script ({script['mode']}): {script['title'][:60]}")

        # 3. TTS
        upd("Erstelle Voiceover...", 35)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_path = OUTPUT_DIR / f"audio_{stamp}.mp3"
        generate_tts(script["tts_text"], audio_path, voice="onyx")

        # Audio-Dauer ermitteln
        import subprocess
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=15,
        )
        audio_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 60.0

        # 3b. Whisper Wort-Timestamps für Karaoke-Untertitel
        upd("Transkribiere Audio für Karaoke-Untertitel...", 40)
        tts_words = []
        try:
            import openai as _oai
            _wc = _oai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            with open(str(audio_path), "rb") as _af:
                _tr = _wc.audio.transcriptions.create(
                    model="whisper-1",
                    file=_af,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                )
            tts_words = [
                {"word": w.word, "start": w.start, "end": w.end}
                for w in (_tr.words or [])
            ]
            logger.info(f"[gen] Whisper: {len(tts_words)} Wörter erkannt")
        except Exception as _e:
            logger.warning(f"[gen] Whisper fehlgeschlagen (kein Karaoke): {_e}")

        meta_base = {
            "title":   script["title"],
            "caption": script["caption"],
            "sport":   script["sport"],
            "player":  script["player"],
        }

        queued = []

        # 4a. Highlights-Video → TikTok + Instagram (de_<stamp>)
        upd(f"Lade Highlight-Clips: {script['player']}...", 50)
        video_hl = _fetch_trim_render(script["player"], script["sport"], stamp,
                                      "highlights", audio_path, audio_duration, tts_words, script)
        if video_hl:
            upd("Lade Highlights in Bunny hoch...", 65)
            if _bunny_upload(video_hl, f"de_{stamp}.mp4", dict(meta_base)):
                queued.append(f"de_{stamp}.mp4")
            video_hl.unlink(missing_ok=True)

        # 4b. Pressekonferenz-Video → YouTube only (de_yt_<stamp>)
        upd(f"Lade PK-Clips: {script['player']}...", 72)
        video_yt = _fetch_trim_render(script["player"], script["sport"], f"{stamp}_yt",
                                      "youtube", audio_path, audio_duration, tts_words, script)
        if video_yt:
            upd("Lade YouTube-Video in Bunny hoch...", 87)
            if _bunny_upload(video_yt, f"de_yt_{stamp}.mp4", dict(meta_base)):
                queued.append(f"de_yt_{stamp}.mp4")
            video_yt.unlink(missing_ok=True)

        audio_path.unlink(missing_ok=True)

        if queued:
            notify("🏆 Sport Bot DE", f"✅ {script['title'][:55]}\n📦 Queue: {', '.join(queued)}")
        else:
            notify("Sport Bot DE", f"⚠️ Keine Clips gefunden — nichts in Queue: {script['title'][:50]}")

        jobs[job_id].update({"status": "done", "progress": 100,
                              "message": f"Fertig: {len(queued)}/2 in Queue — {script['title'][:40]}",
                              "video": queued[0] if queued else None})

    except Exception as e:
        logger.error(f"[job:{job_id}] Fehler: {e}", exc_info=True)
        jobs[job_id].update({"status": "error", "message": str(e)})
        notify("Sport Bot DE", f"❌ Fehler: {str(e)[:80]}")


# ── Scheduler ─────────────────────────────────────────────────────────────────
_paused = False

def _run_scheduler():
    logger.info("[scheduler] Gestartet")
    while True:
        if not _paused:
            now = datetime.utcnow()
            slots = _load_schedule()
            for slot in slots:
                t = slot.get("time", "")
                h, m = map(int, t.split(":")) if ":" in t else (0, 0)
                if now.hour == h and now.minute == m:
                    jitter = random.randint(0, 720)
                    if jitter:
                        time.sleep(jitter)
                    job_id = uuid.uuid4().hex[:8]
                    sport = slot.get("sport") or None
                    logger.info(f"[scheduler] Slot {t} — Sport: {sport or 'zufällig'}")
                    t_gen = threading.Thread(target=_run_generation, args=(job_id, sport), daemon=True)
                    t_gen.start()
                    time.sleep(61)
                    break
        time.sleep(30)


# ── API Endpoints ─────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    global _scheduler_thread
    _scheduler_thread = threading.Thread(target=_run_scheduler, daemon=True)
    _scheduler_thread.start()
    logger.info("[startup] SynCinSportDE Bot gestartet")

@app.get("/health")
def health():
    return {"status": "ok", "bot": "sport-de"}

@app.post("/api/generate")
def generate(body: dict = Body(...)):
    job_id = uuid.uuid4().hex[:8]
    sport = body.get("sport") or None
    t = threading.Thread(target=_run_generation, args=(job_id, sport), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})

@app.get("/api/schedule")
def get_schedule():
    return _load_schedule()

@app.post("/api/schedule")
def set_schedule(slots: list = Body(...)):
    _save_schedule(slots)
    return {"status": "ok"}

@app.post("/api/schedule/pause")
def pause_schedule(body: dict = Body({})):
    global _paused
    _paused = body.get("paused", not _paused)
    return {"paused": _paused}

@app.get("/api/videos")
def list_videos():
    videos = []
    for f in sorted(OUTPUT_DIR.glob("video_*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        meta_f = f.with_suffix(".json")
        meta = {}
        if meta_f.exists():
            try:
                meta = json.loads(meta_f.read_text(encoding="utf-8"))
            except Exception:
                pass
        videos.append({
            "filename": f.name,
            "size_mb":  round(f.stat().st_size / 1024 / 1024, 1),
            "created":  datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
            "title":    meta.get("title", f.stem),
            "sport":    meta.get("sport", ""),
            "uploaded": meta.get("uploaded", False),
        })
    return videos

@app.get("/")
def dashboard():
    html = """<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><title>SynCinSportDE Dashboard</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; background: #0a0a1a; color: #eee; }
  h1 { color: #00c8ff; }
  .card { background: #141428; border-radius: 12px; padding: 20px; margin: 16px 0; border: 1px solid #222; }
  button { background: #008cff; color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; margin: 5px; }
  button:hover { background: #0066cc; }
  select { background: #1a1a2e; color: #eee; border: 1px solid #333; padding: 8px; border-radius: 6px; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 12px; margin: 2px; }
  .fussball { background: #005a9e; } .nba { background: #c84b00; } .nfl { background: #004400; }
  .rage { background: #8b0000; } .news { background: #1a5276; }
  #log { background: #0a0a0a; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 13px; max-height: 200px; overflow-y: auto; }
</style></head>
<body>
<h1>🏆 SynCinSportDE Dashboard</h1>
<div class="card">
  <h3>Video generieren</h3>
  <select id="sport">
    <option value="">🎲 Zufällig</option>
    <option value="fussball">⚽ Fußball</option>
    <option value="nba">🏀 NBA</option>
    <option value="nfl">🏈 NFL</option>
  </select>
  <button onclick="generate()">▶ Generieren & Hochladen</button>
  <div id="log">Bereit.</div>
</div>
<div class="card">
  <h3>Letzte Videos</h3>
  <div id="videos">Lade...</div>
</div>
<script>
async function generate() {
  const sport = document.getElementById('sport').value;
  document.getElementById('log').textContent = 'Starte Generierung...';
  const r = await fetch('/api/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sport: sport || null})});
  const d = await r.json();
  pollJob(d.job_id);
}
async function pollJob(id) {
  const r = await fetch('/api/jobs/' + id);
  const d = await r.json();
  document.getElementById('log').textContent = `[${d.progress||0}%] ${d.message || d.status}`;
  if (d.status === 'running') setTimeout(() => pollJob(id), 3000);
  else if (d.status === 'done') { document.getElementById('log').textContent = '✅ ' + d.message; loadVideos(); }
  else document.getElementById('log').textContent = '❌ ' + d.message;
}
async function loadVideos() {
  const r = await fetch('/api/videos');
  const vs = await r.json();
  document.getElementById('videos').innerHTML = vs.map(v =>
    `<div style="padding:10px;border-bottom:1px solid #222">
      <b>${v.title}</b>
      <span class="badge ${v.sport}">${v.sport?.toUpperCase()}</span>
      ${v.uploaded ? '✅' : '⏳'}
      <small style="color:#888">${v.created} · ${v.size_mb}MB</small>
    </div>`
  ).join('') || 'Keine Videos';
}
loadVideos();
</script>
</body></html>"""
    return HTMLResponse(html)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
