"""
Transcript retrieval service.

Strategy:
1. Try youtube-transcript-api (fast, no video download).
2. Fallback: yt-dlp with --write-auto-sub (slower, more reliable).
3. Optional fallback: OpenAI Whisper (local audio transcription, off by default).
   Enable via WHISPER_ENABLED=true in .env. Requires openai-whisper + ffmpeg.
4. On any failure: return error string, never raise.

Cookies:
Set COOKIES_FILE=/path/to/cookies.txt in .env to bypass YouTube bot-detection.
Export via browser extension: "Get cookies.txt LOCALLY" (Chrome) or "cookies.txt" (Firefox).
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import os
import re
import tempfile
import glob as _glob
from collections import Counter
from typing import Optional

import yt_dlp
from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.models import TranscriptResponse
from app.utils.logger import get_logger

log = get_logger(__name__)

# ── Transcript quality check (ported from subproject analyzer.py) ─────────────

_MIN_CHARS = 70
_MIN_WORDS = 12
_MAX_REPEATED_WORD_SHARE = 0.35
_MIN_UNIQUE_WORD_SHARE = 0.45
_GARBAGE_MARKERS = ["traceback", "file \"<string>\"", "unicodeencodeerror", "exception", "error:"]


def _transcript_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", text.lower())


def is_usable_transcript(transcript: Optional[str]) -> tuple[bool, str]:
    """
    Validate transcript quality before sending to LLM.
    Returns (is_ok, reason) where reason is 'ok' or a short failure code.
    Ported from subproject/yt/analyzer.py.
    """
    text = (transcript or "").strip()
    if not text:
        return False, "empty"

    normalized = re.sub(r"\s+", " ", text)
    lowered = normalized.lower()
    for marker in _GARBAGE_MARKERS:
        if marker in lowered:
            return False, f"garbage:{marker}"

    if len(normalized) < _MIN_CHARS:
        return False, f"too_short:{len(normalized)}<{_MIN_CHARS}"

    words = _transcript_words(normalized)
    if len(words) < _MIN_WORDS:
        return False, f"too_few_words:{len(words)}<{_MIN_WORDS}"

    counts = Counter(words)
    most_common_count = counts.most_common(1)[0][1] if counts else 0
    repeated_share = most_common_count / len(words) if words else 0
    unique_share = len(counts) / len(words) if words else 0

    if repeated_share > _MAX_REPEATED_WORD_SHARE:
        return False, f"repeated_word:{repeated_share:.2f}"
    if unique_share < _MIN_UNIQUE_WORD_SHARE:
        return False, f"low_unique:{unique_share:.2f}"

    sentence_like = re.search(r"[A-Za-zА-Яа-яЁё][^.!?]{18,}[.!?]", normalized)
    long_phrase = re.search(r"(?:[A-Za-zА-Яа-яЁё]{2,}\W+){4,}[A-Za-zА-Яа-яЁё]{2,}", normalized)
    if not sentence_like and not long_phrase:
        return False, "no_sentence"

    return True, "ok"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _join_transcript(snippets) -> str:
    """Join transcript snippet objects (dicts or TypedDicts) into plain text."""
    parts = []
    for s in snippets:
        # FetchedTranscriptSnippet in v1.x is a TypedDict (dict-like)
        if hasattr(s, "get"):
            text = (s.get("text") or "").strip()
        elif hasattr(s, "text"):
            text = (s.text or "").strip()
        else:
            text = str(s).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _build_requests_session(cookies_file: str) -> Session:
    """Build a requests Session pre-loaded with browser cookies."""
    session = Session()
    if cookies_file and os.path.exists(cookies_file):
        try:
            jar = http.cookiejar.MozillaCookieJar(cookies_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar  # type: ignore[assignment]
            log.debug(f"Loaded cookies from {cookies_file}")
        except Exception as exc:
            log.warning(f"Failed to load cookies file {cookies_file}: {exc}")
    return session


# ── Core transcript methods ────────────────────────────────────────────────────

def _get_via_api(video_id: str, language: str, cookies_file: str = "") -> tuple[str, str] | None:
    """
    Try youtube-transcript-api (v1.x).
    Returns (transcript_text, language_code) or None on failure.
    """
    try:
        session = _build_requests_session(cookies_file)
        api = YouTubeTranscriptApi(http_client=session)

        # 1. Try the requested language directly (fastest path)
        try:
            fetched = api.fetch(video_id, languages=[language, "en"])
            text = _join_transcript(fetched)
            if text:
                return text, language
        except Exception:
            pass

        # 2. Try listing all available transcripts and take any
        try:
            transcript_list = api.list(video_id)
            transcript = next(iter(transcript_list))
            fetched = transcript.fetch()
            text = _join_transcript(fetched)
            if text:
                return text, transcript.language_code
        except Exception:
            pass

    except Exception as exc:
        log.warning(f"Transcript API error | video_id={video_id} | {exc}")

    log.debug(f"Transcript API: no transcript | video_id={video_id}")
    return None


def _get_via_ytdlp(video_id: str, language: str, cookies_file: str = "") -> tuple[str, str] | None:
    """
    Fallback: download auto-generated subtitles with yt-dlp (no video).
    Returns (transcript_text, language_code) or None on failure.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": [language, "en"],
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
        }
        if cookies_file and os.path.exists(cookies_file):
            opts["cookiefile"] = cookies_file

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            log.warning(f"yt-dlp subtitle download error | video_id={video_id} | {exc}")
            return None

        # Find downloaded .vtt file
        vtt_files = _glob.glob(os.path.join(tmp_dir, "*.vtt"))
        if not vtt_files:
            log.debug(f"yt-dlp: no .vtt file found | video_id={video_id}")
            return None

        vtt_path = vtt_files[0]
        lang_code = os.path.basename(vtt_path).rsplit(".", 2)[-2] if "." in vtt_path else language

        try:
            text = _parse_vtt(vtt_path)
            if text:
                return text, lang_code
        except Exception as exc:
            log.warning(f"VTT parse error | video_id={video_id} | {exc}")

    return None


def _get_via_whisper(video_id: str, language: Optional[str] = None) -> tuple[str, str] | None:
    """
    Optional 3rd fallback: download audio and transcribe with OpenAI Whisper locally.
    Requires: pip install openai-whisper + ffmpeg in PATH.
    Much slower than subtitle-based methods — use only as last resort.
    """
    try:
        import whisper
    except ImportError:
        log.warning("Whisper not installed (pip install openai-whisper). Skipping Whisper fallback.")
        return None

    url = f"https://www.youtube.com/shorts/{video_id}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = os.path.join(tmp_dir, f"{video_id}.mp3")

        # Download audio only
        dl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "worstaudio/worst",
            "outtmpl": audio_path,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "5",
            }],
        }
        try:
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            log.warning(f"Whisper: audio download failed | video_id={video_id} | {exc}")
            return None

        # Find the actual output file (yt-dlp may append extension)
        found_path = None
        for candidate in [audio_path, audio_path + ".mp3"]:
            if os.path.exists(candidate):
                found_path = candidate
                break
        if not found_path:
            for ext in [".mp3", ".m4a", ".wav", ".opus", ".webm"]:
                p = os.path.splitext(audio_path)[0] + ext
                if os.path.exists(p):
                    found_path = p
                    break
        if not found_path:
            log.warning(f"Whisper: audio file not found after download | video_id={video_id}")
            return None

        try:
            log.info(f"Whisper: transcribing | video_id={video_id} | model=base")
            model = whisper.load_model("base")
            opts_w = {"fp16": False}
            if language:
                opts_w["language"] = language
            result = model.transcribe(found_path, **opts_w)
            text = result.get("text", "").strip()
            if text:
                detected_lang = result.get("language") or language or "unknown"
                return text, detected_lang
        except Exception as exc:
            log.warning(f"Whisper transcription error | video_id={video_id} | {exc}")

    return None


def _parse_vtt(path: str) -> str:
    """Parse a WebVTT subtitle file into plain text, deduplicating adjacent identical lines."""
    # Metadata/header prefixes to skip
    _SKIP_PREFIXES = ("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE", "REGION")

    lines: list[str] = []
    last_line = ""

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            # Skip empty lines, timestamps, cue IDs (numbers), and header metadata
            if not line:
                continue
            if "-->" in line:
                continue
            if line.isdigit():
                continue
            if any(line.startswith(p) for p in _SKIP_PREFIXES):
                continue
            # Remove VTT inline tags: <c>, </c>, <00:00:00.000>, <b>, etc.
            line = re.sub(r"<[^>]+>", "", line).strip()
            if line and line != last_line:
                lines.append(line)
                last_line = line

    return " ".join(lines)


def _get_transcript_sync(video_id: str, language: str, use_whisper: bool = False, cookies_file: str = "") -> TranscriptResponse:
    url = f"https://www.youtube.com/shorts/{video_id}"

    # 1. Fast path via youtube-transcript-api
    result = _get_via_api(video_id, language, cookies_file)
    if result:
        text, lang = result
        ok, reason = is_usable_transcript(text)
        log.info(f"Transcript OK (api) | video_id={video_id} | lang={lang} | chars={len(text)} | quality={reason}")
        return TranscriptResponse(video_id=video_id, url=url, transcript=text, language=lang, source="api")

    # 2. Fallback: yt-dlp subtitles
    log.debug(f"Falling back to yt-dlp subtitles | video_id={video_id}")
    result = _get_via_ytdlp(video_id, language, cookies_file)
    if result:
        text, lang = result
        ok, reason = is_usable_transcript(text)
        log.info(f"Transcript OK (yt-dlp) | video_id={video_id} | lang={lang} | chars={len(text)} | quality={reason}")
        return TranscriptResponse(video_id=video_id, url=url, transcript=text, language=lang, source="yt-dlp")

    # 3. Optional Whisper fallback
    if use_whisper:
        log.info(f"Falling back to Whisper | video_id={video_id}")
        result = _get_via_whisper(video_id, language if language != "en" else None)
        if result:
            text, lang = result
            ok, reason = is_usable_transcript(text)
            log.info(f"Transcript OK (whisper) | video_id={video_id} | lang={lang} | chars={len(text)} | quality={reason}")
            return TranscriptResponse(video_id=video_id, url=url, transcript=text, language=lang, source="whisper")

    # All methods failed
    msg = "No transcript available (API and yt-dlp both failed)"
    if use_whisper:
        msg = "No transcript available (API, yt-dlp, and Whisper all failed)"
    log.warning(f"Transcript FAILED | video_id={video_id} | {msg}")
    return TranscriptResponse(video_id=video_id, url=url, transcript=None, error=msg)


async def get_transcript(video_id: str, language: str = "en", use_whisper: bool = False, cookies_file: str = "") -> TranscriptResponse:
    """
    Async entry point for transcript retrieval.
    Tries youtube-transcript-api first, falls back to yt-dlp subtitles,
    and optionally falls back to local Whisper transcription.
    Never raises — returns error field on failure.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _get_transcript_sync, video_id, language, use_whisper, cookies_file
    )



