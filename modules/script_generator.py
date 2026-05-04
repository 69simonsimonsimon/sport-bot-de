"""
Script Generator — Sport Bot DE
================================
Generiert viral aufgeladene Sport-Kommentare via Claude.
Zwei Modi: News-Recap oder Rage-Bait Opinion Piece.
"""

import logging
import os
import random
import re

import anthropic

logger = logging.getLogger("syncin")

_CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def _llm_call(prompt: str, system: str = "", max_tokens: int = 900) -> str:
    """Ruft Anthropic Claude auf — fällt auf OpenAI GPT-4o-mini zurück wenn Credits aufgebraucht."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        try:
            client = anthropic.Anthropic(api_key=anthropic_key)
            kwargs = {"model": _CLAUDE_MODEL, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]}
            if system:
                kwargs["system"] = system
            msg = client.messages.create(**kwargs)
            return msg.content[0].text.strip()
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower():
                logger.warning("[llm] Anthropic Credits aufgebraucht — OpenAI Fallback")
            else:
                raise
    import openai
    oai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not oai_key:
        raise RuntimeError("Weder Anthropic noch OpenAI API-Key verfügbar")
    oai = openai.OpenAI(api_key=oai_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = oai.chat.completions.create(model="gpt-4o-mini", max_tokens=max_tokens, messages=messages)
    return resp.choices[0].message.content.strip()


_RAGE_BAIT_TRIGGERS = [
    "überschätzt", "der schlechteste", "muss sofort weg",
    "totaler Flop", "hat komplett versagt", "niemand will die Wahrheit hören",
    "das ist eine Katastrophe", "wie konnte das passieren",
    "der größte Fehler", "blamiert sich", "peinlich",
]

_SYSTEM_PROMPT = """Du bist ein viraler Sport-Content-Creator für TikTok/YouTube Shorts.
Dein Stil: direkt, leidenschaftlich, kontrovers, manchmal provokant.
Du nimmst IMMER eine klare Meinung ein — nie neutral.
Du sprichst die Zuschauer direkt an.
Du baust Spannung auf und endest mit einem Aufruf zur Meinung.
Schreib NUR den Sprechtext, keine Regieanweisungen oder Formatierung."""


def generate_script(article: dict, mode: str = "auto") -> dict:
    """
    Generiert ein TTS-Script basierend auf einem Artikel.

    mode: 'news' = sachliche News mit Spin
          'rage' = volle Rage-Bait Meinung
          'auto' = zufällig (40% rage, 60% news)

    Gibt zurück: {title, tts_text, player_name, team_name, sport, hashtags, caption}
    """
    if mode == "auto":
        mode = random.choices(["news", "rage"], weights=[60, 40], k=1)[0]

    sport = article.get("sport", "fussball")

    # Artikelinhalt zusammenbauen — Volltext bevorzugen
    fulltext = article.get("fulltext", "").strip()
    summary  = article.get("summary", "").strip()
    content  = fulltext if len(fulltext) > len(summary) else summary
    if not content:
        content = "(nur Überschrift verfügbar)"

    facts_warning = """
⚠️ WICHTIGE REGEL: Erfinde KEINE Fakten, Zahlen, Zitate oder Details die nicht explizit im Artikel stehen!
Wenn du eine Meinung äußerst, mach deutlich dass es DEINE Einschätzung ist ("Ich glaube...", "Meiner Meinung nach...").
Halte dich an die Fakten aus dem Artikel — du darfst sie kommentieren und bewerten, aber nicht erfinden."""

    if mode == "rage":
        prompt = f"""Basierend auf dieser Sport-News:
TITEL: {article['title']}
ARTIKELINHALT: {content}
SPORTART: {sport.upper()}
{facts_warning}

Schreib ein KONTROVERSES TikTok-Voiceover-Script auf Deutsch. Das Script MUSS genau 140-160 Wörter haben.
STRUKTUR (diese Reihenfolge einhalten!):
1. HOOK (Satz 1-2): Die schockierendste oder provokanteste Aussage aus dem Artikel ZUERST. Muss so stark sein dass man aufhört zu scrollen. Direkte Ansprache: "Das glaubst du nicht..." / "Alle reden davon..." / "Niemand will zugeben..."
2. AUFBAU (Satz 3-5): Kontext der den Schock noch größer macht. Konkrete Zahlen/Fakten aus dem Artikel.
3. MEINUNG (Satz 6-8): Deine harte, klare Einschätzung — provokant aber faktenbasiert. Darf spaltend sein.
4. CLIFFHANGER (letzter Satz): Endet mit "Schreibt eure Meinung in die Kommentare!"

Danach diese Metadaten:
TITEL: (klickbarer Titel mit Emojis, max 60 Zeichen)
SPIELER: (Hauptperson/Team aus dem Artikel für Clip-Suche)
HASHTAGS: (6 relevante Hashtags)
CAPTION: (1 Satz + Hashtags)

Antworte GENAU in diesem Format:
SCRIPT: [dein Script hier]
TITEL: ...
SPIELER: ...
HASHTAGS: ...
CAPTION: ..."""
    else:
        prompt = f"""Basierend auf dieser Sport-News:
TITEL: {article['title']}
ARTIKELINHALT: {content}
SPORTART: {sport.upper()}
{facts_warning}

Schreib einen SPANNENDEN TikTok-Voiceover-Kommentar auf Deutsch. Das Script MUSS genau 140-160 Wörter haben.
STRUKTUR (diese Reihenfolge einhalten!):
1. HOOK (Satz 1-2): Die überraschendste oder unglaublichste Tatsache aus dem Artikel ZUERST. Scroll-stopper: "Das hat niemand kommen sehen..." / "Jetzt ist es offiziell..." / "Das ändert alles..."
2. AUFBAU (Satz 3-5): Was ist passiert? Erkläre den Hintergrund mit konkreten Fakten aus dem Artikel.
3. ANALYSE (Satz 6-8): Deine Einschätzung was das bedeutet — mutig, direkt, als Meinung gekennzeichnet.
4. CALL TO ACTION (letzter Satz): Endet mit "Was denkt ihr? Kommentiert!"

Danach diese Metadaten:
TITEL: (klickbarer Titel mit Emojis, max 60 Zeichen)
SPIELER: (Hauptperson/Team aus dem Artikel für Clip-Suche)
HASHTAGS: (6 relevante Hashtags)
CAPTION: (1 Satz + Hashtags)

Antworte GENAU in diesem Format:
SCRIPT: [dein Script hier]
TITEL: ...
SPIELER: ...
HASHTAGS: ...
CAPTION: ..."""

    raw = _llm_call(prompt, system=_SYSTEM_PROMPT, max_tokens=900)
    logger.info(f"[script] Claude Response ({mode}): {raw[:100]}")

    def extract(key: str) -> str:
        m = re.search(rf"^{key}:\s*(.+)$", raw, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    tts_text = extract("SCRIPT")
    if not tts_text:
        # Fallback: alles nach dem letzten Feld als Script
        parts = raw.split("SCRIPT:")
        tts_text = parts[-1].strip() if len(parts) > 1 else raw

    title      = extract("TITEL") or article["title"][:60]
    player     = extract("SPIELER") or ""
    hashtags   = extract("HASHTAGS") or "#sport #fussball #bundesliga #nba #nfl #fyp"
    caption    = extract("CAPTION") or f"{title}\n{hashtags}"

    # Wortanzahl checken
    word_count = len(tts_text.split())
    logger.info(f"[script] Script: {word_count} Wörter, Modus: {mode}, Spieler: {player}")

    if word_count < 120:
        raise ValueError(f"Script zu kurz ({word_count} Wörter)")
    if word_count > 170:
        words = tts_text.split()[:165]
        tts_text = " ".join(words) + "."

    return {
        "title":      title,
        "tts_text":   tts_text,
        "player":     player,
        "sport":      sport,
        "hashtags":   hashtags,
        "caption":    caption,
        "mode":       mode,
        "source_url": article.get("link", ""),
    }
