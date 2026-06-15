"""
Transcript retrieval service.

Strategy:
1. Try youtube-transcript-api (fast, no video download).
2. Fallback: yt-dlp with --write-auto-sub (slower, more reliable).
3. Optional fallback: Whisper transcription (off by default).
   - If GROQ_API_KEY is set: uses Groq cloud API (whisper-large-v3, free, best quality).
   - Otherwise: uses local faster-whisper (model size set by WHISPER_MODEL, default: small).
   Enable via WHISPER_ENABLED=true in .env.
4. On any failure: return error string, never raise.

Cookies:
Set COOKIES_FILE=/path/to/cookies.txt in .env to bypass YouTube bot-detection.
Export via browser extension: "Get cookies.txt LOCALLY" (Chrome) or "cookies.txt" (Firefox).

Rate limiting:
Both methods retry up to 3 times with exponential backoff on 429 / IP block errors.
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import os
import re
import shutil
import tempfile
import time
import glob as _glob
from collections import Counter
from typing import Optional

import yt_dlp
from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.models import TranscriptResponse
from app.utils.logger import get_logger

log = get_logger(__name__)

# ── Retry configuration ───────────────────────────────────────────────────────

_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 5, 10]  # seconds between retries

# Strings in exception messages that indicate a retryable rate-limit condition
_RATE_LIMIT_MARKERS = ["429", "too many requests", "ip", "blocked", "sign in to confirm"]

# ── Transcript quality check (ported from subproject analyzer.py) ─────────────

_MIN_CHARS = 70
_MIN_WORDS = 12
_MAX_REPEATED_WORD_SHARE = 0.35
_MIN_UNIQUE_WORD_SHARE = 0.45
_GARBAGE_MARKERS = ["traceback", "file \"<string>\"", "unicodeencodeerror", "exception", "error:"]


from concurrent.futures import ThreadPoolExecutor
import asyncio

# Создаем пул с 1 потоком. 
# Почему 1? Потому что Whisper (особенно на GPU) потребляет очень много памяти.
# Если запустить 2-3 задачи параллельно, вы получите OutOfMemory (OOM) ошибку.
transcript_executor = ThreadPoolExecutor(max_workers=1)


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
    """Build a requests Session pre-loaded with browser cookies and realistic headers."""
    session = Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    if cookies_file and os.path.exists(cookies_file):
        try:
            jar = http.cookiejar.MozillaCookieJar(cookies_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar  # type: ignore[assignment]
            log.debug(f"Loaded cookies from {cookies_file}")
        except Exception as exc:
            log.warning(f"Failed to load cookies file {cookies_file}: {exc}")
    return session


def _is_rate_limited(exc: Exception) -> bool:
    """Return True if the exception looks like a transient rate-limit / IP-block."""
    msg = str(exc).lower()
    return any(m in msg for m in _RATE_LIMIT_MARKERS)


# ── Core transcript methods ────────────────────────────────────────────────────

def _get_via_api(video_id: str, language: str, cookies_file: str = "") -> tuple[str, str] | None:
    """
    Try youtube-transcript-api (v1.x) with retry on rate-limit errors.
    Returns (transcript_text, language_code) or None on failure.
    """
    for attempt in range(_MAX_RETRIES):
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

            # 2. List all available transcripts and take any
            try:
                transcript_list = api.list(video_id)
                transcript = next(iter(transcript_list))
                fetched = transcript.fetch()
                text = _join_transcript(fetched)
                if text:
                    return text, transcript.language_code
            except Exception:
                pass

            # Video exists but has no transcripts — no point retrying
            break

        except Exception as exc:
            if _is_rate_limited(exc) and attempt < _MAX_RETRIES - 1:
                wait = _RETRY_DELAYS[attempt]
                log.warning(f"Transcript API rate-limited | video_id={video_id} | retry {attempt+1}/{_MAX_RETRIES} in {wait}s")
                time.sleep(wait)
            else:
                log.warning(f"Transcript API error | video_id={video_id} | {exc}")
                break

    log.debug(f"Transcript API: no transcript | video_id={video_id}")
    return None


def _parse_json3(path: str) -> str:
    """Parse YouTube's native json3 subtitle format into plain text."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        parts = []
        for event in data.get("events", []):
            segs = event.get("segs")
            if not segs:
                continue
            line = "".join(s.get("utf8", "") for s in segs).strip()
            line = re.sub(r"\s+", " ", line).strip()
            if line and line != "\n":
                parts.append(line)
        # Deduplicate adjacent identical lines
        deduped = []
        last = ""
        for p in parts:
            if p != last:
                deduped.append(p)
                last = p
        return " ".join(deduped)
    except Exception as exc:
        log.debug(f"json3 parse error for {path}: {exc}")
        return ""


def _parse_subtitle_file(path: str, lang_code: str) -> tuple[str, str] | None:
    """Parse a subtitle file (vtt or json3) and return (text, lang_code) or None."""
    ext = os.path.splitext(path)[-1].lower()
    try:
        if ext == ".vtt":
            text = _parse_vtt(path)
        elif ext == ".json3":
            text = _parse_json3(path)
        else:
            # Try VTT parser as generic fallback
            text = _parse_vtt(path)
        if text and len(text.strip()) > 10:
            return text, lang_code
    except Exception as exc:
        log.debug(f"Subtitle parse error {path}: {exc}")
    return None


def _get_via_ytdlp(video_id: str, language: str, cookies_file: str = "") -> tuple[str, str] | None:
    """
    Fallback: download auto-generated subtitles with yt-dlp (no video).
    Uses Node.js (via yt-dlp-ejs) to solve YouTube's n-challenge.
    Supports VTT and JSON3 subtitle formats.
    Returns (transcript_text, language_code) or None on failure.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    for attempt in range(_MAX_RETRIES):
        with tempfile.TemporaryDirectory() as tmp_dir:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "writeautomaticsub": True,
                "writesubtitles": True,
                "subtitleslangs": [language, "en"],
                # No subtitlesformat restriction — accept VTT or JSON3
                "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
                # Use Node.js runtime for n-challenge (yt-dlp-ejs package provides solver script)
                "js_runtimes": {"node": {}},
            }
            if cookies_file and os.path.exists(cookies_file):
                # yt-dlp updates cookie timestamps in-place, so copy to a writable temp file
                tmp_cookies = os.path.join(tmp_dir, "cookies.txt")
                shutil.copy2(cookies_file, tmp_cookies)
                opts["cookiefile"] = tmp_cookies

            error_msg = ""
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            except Exception as exc:
                error_msg = str(exc)
                if _is_rate_limited(exc) and attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_DELAYS[attempt]
                    log.warning(f"yt-dlp rate-limited | video_id={video_id} | retry {attempt+1}/{_MAX_RETRIES} in {wait}s")
                    time.sleep(wait)
                    continue
                elif "requested format is not available" in error_msg.lower():
                    # Video has no subtitles in any format — not retryable
                    log.debug(f"yt-dlp: no subtitles available | video_id={video_id}")
                    return None
                else:
                    log.warning(f"yt-dlp subtitle download error | video_id={video_id} | {exc}")
                    return None

            # Find any downloaded subtitle file (vtt or json3)
            subtitle_files = [
                f for f in _glob.glob(os.path.join(tmp_dir, "*"))
                if not f.endswith("cookies.txt") and os.path.splitext(f)[-1].lower() in (".vtt", ".json3", ".srv1", ".srv2", ".srv3", ".ttml")
            ]
            if not subtitle_files:
                log.debug(f"yt-dlp: no subtitle file found | video_id={video_id}")
                return None

            for sub_path in subtitle_files:
                # Extract language code from filename like video_id.en.vtt or video_id.en-orig.json3
                basename = os.path.basename(sub_path)
                parts = basename.rsplit(".", 2)
                detected_lang = parts[-2] if len(parts) >= 3 else language

                result = _parse_subtitle_file(sub_path, detected_lang)
                if result:
                    return result

        # If we got here without returning, something went wrong — retry
        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_DELAYS[attempt])

    return None


def _download_audio(video_id: str, tmp_dir: str, cookies_file: str = "") -> str | None:
    """Download audio from a YouTube Shorts video to tmp_dir. Returns file path or None."""
    url = f"https://www.youtube.com/shorts/{video_id}"
    dl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "worstaudio/worst",
        "outtmpl": os.path.join(tmp_dir, f"{video_id}.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "5",
        }],
        "socket_timeout": 30,
        "retries": 1,
        "js_runtimes": ["nodejs"],  # for n-challenge solving
    }
    if cookies_file and os.path.isfile(cookies_file):
        # Copy to writable temp path (source may be read-only in Docker)
        tmp_cookies = os.path.join(tmp_dir, "cookies.txt")
        shutil.copy2(cookies_file, tmp_cookies)
        dl_opts["cookiefile"] = tmp_cookies
    try:
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        log.warning(f"Whisper: audio download failed | video_id={video_id} | {exc}")
        return None

    # Find the actual output file (yt-dlp may change extension)
    for ext in [".mp3", ".m4a", ".wav", ".opus", ".webm", ".ogg"]:
        p = os.path.join(tmp_dir, f"{video_id}{ext}")
        if os.path.exists(p):
            return p
    candidates = [f for f in _glob.glob(os.path.join(tmp_dir, f"{video_id}.*"))
                  if not f.endswith(".txt")]
    return candidates[0] if candidates else None


def _get_via_groq(video_id: str, audio_path: str, language: Optional[str] = None) -> tuple[str, str] | None:
    """
    Transcribe audio using Groq cloud API (whisper-large-v3).
    Free tier: 7200 min/day. Best quality option.
    Returns (text, language) or None on failure.
    """
    from app.config import settings
    if not settings.groq_api_key:
        return None
    try:
        from groq import Groq
    except ImportError:
        log.warning("groq package not installed (pip install groq). Skipping Groq transcription.")
        return None

    try:
        client = Groq(api_key=settings.groq_api_key, timeout=90.0)
        transcribe_kwargs: dict = {
            "model": "whisper-large-v3",
            "response_format": "verbose_json",
        }
        if language:
            transcribe_kwargs["language"] = language

        log.info(f"Groq Whisper: transcribing | video_id={video_id} | model=whisper-large-v3")
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), f, "audio/mpeg"),
                **transcribe_kwargs,
            )
        text = response.text.strip() if hasattr(response, "text") else ""
        detected_lang = getattr(response, "language", None) or language or "unknown"
        if text:
            log.info(f"Groq Whisper: OK | video_id={video_id} | lang={detected_lang} | chars={len(text)}")
            return text, detected_lang
        log.warning(f"Groq Whisper: empty response | video_id={video_id}")
        return None
    except Exception as exc:
        log.warning(f"Groq Whisper: error | video_id={video_id} | {exc}")
        return None


def _get_via_local_whisper(video_id: str, audio_path: str, language: Optional[str] = None) -> tuple[str, str] | None:
    """
    Transcribe audio using local faster-whisper model.
    Model size controlled by WHISPER_MODEL env var (default: small).
    Returns (text, language) or None on failure.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper not installed. Skipping local Whisper transcription.")
        return None

    from app.config import settings
    model_name = settings.whisper_model or "small"

    try:
        model = None
        for compute_type in ("int8", "float32"):
            try:
                #model = WhisperModel(model_name, device="cpu", compute_type=compute_type)

                try:
                    # Теперь принудительно используем CUDA
                    model = WhisperModel(
                        model_name, 
                        device="cuda", 
                        compute_type="float16" # float16 работает быстрее и потребляет меньше VRAM на GPU
                        )
                    log.info(f"Whisper: loaded model={model_name} on GPU (cuda)")
                except Exception as load_exc:
                    log.warning(f"Whisper: GPU initialization failed ({load_exc}), falling back to CPU")
                    model = WhisperModel(model_name, device="cpu", compute_type=compute_type, num_workers=2)



                log.debug(f"Whisper: loaded model={model_name} compute_type={compute_type}")
                break
            except Exception as load_exc:
                log.debug(f"Whisper: compute_type={compute_type} failed ({load_exc}), trying next")

        if model is None:
            log.warning(f"Whisper: could not load model={model_name} | video_id={video_id}")
            return None

        log.info(f"Whisper: transcribing | video_id={video_id} | model={model_name} | file={audio_path}")
        transcribe_opts: dict = {"beam_size": 5}
        if language:
            transcribe_opts["language"] = language
        segments, info = model.transcribe(audio_path, **transcribe_opts)
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        if text:
            detected_lang = info.language or language or "unknown"
            return text, detected_lang
        return None
    except Exception as exc:
        log.warning(f"Whisper local transcription error | video_id={video_id} | {exc}")
        return None


def _get_via_whisper(video_id: str, language: Optional[str] = None, cookies_file: str = "") -> tuple[str, str] | None:
    """
    Transcribe YouTube Shorts audio.
    Priority: Groq cloud API (if GROQ_API_KEY set) → local faster-whisper.
    Returns (text, language) or None on failure.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = _download_audio(video_id, tmp_dir, cookies_file=cookies_file)
        if not audio_path:
            log.warning(f"Whisper: audio file not found after download | video_id={video_id}")
            return None

        # Try Groq first (best quality, free)
        from app.config import settings
        if settings.groq_api_key:
            result = _get_via_groq(video_id, audio_path, language)
            if result:
                return result
            log.info(f"Groq failed, falling back to local Whisper | video_id={video_id}")

        # Local faster-whisper fallback
        return _get_via_local_whisper(video_id, audio_path, language)


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


def _get_transcript_sync(video_id: str, language: str, use_whisper: bool = False, force_whisper: bool = False, cookies_file: str = "") -> TranscriptResponse:
    url = f"https://www.youtube.com/shorts/{video_id}"

    if not force_whisper:
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

    # 3. Whisper: forced (skip YouTube captions) or fallback
    if use_whisper or force_whisper:
        if force_whisper:
            log.info(f"Force Whisper transcription (skipping YouTube captions) | video_id={video_id}")
        else:
            log.info(f"Falling back to Whisper | video_id={video_id}")
        result = _get_via_whisper(video_id, language if language != "en" else None, cookies_file=cookies_file)
        if result:
            text, lang = result
            ok, reason = is_usable_transcript(text)
            log.info(f"Transcript OK (whisper) | video_id={video_id} | lang={lang} | chars={len(text)} | quality={reason}")
            return TranscriptResponse(video_id=video_id, url=url, transcript=text, language=lang, source="whisper")

    # All methods failed
    if force_whisper:
        msg = "No transcript available (Whisper failed — check WHISPER_ENABLED and openai-whisper installation)"
    elif use_whisper:
        msg = "No transcript available (API, yt-dlp, and Whisper all failed)"
    else:
        msg = "No transcript available (API and yt-dlp both failed)"
    log.warning(f"Transcript FAILED | video_id={video_id} | {msg}")
    return TranscriptResponse(video_id=video_id, url=url, transcript=None, error=msg)


#async def get_transcript(video_id: str, language: str = "en", use_whisper: bool = False, force_whisper: bool = False, cookies_file: str = "") -> TranscriptResponse:
    """
    Async entry point for transcript retrieval.
    Tries youtube-transcript-api first, falls back to yt-dlp subtitles,
    and optionally falls back to local Whisper transcription.
    If force_whisper=True, skips YouTube captions and uses Whisper directly.
    Never raises — returns error field on failure.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _get_transcript_sync, video_id, language, use_whisper, force_whisper, cookies_file
    )

async def get_transcript(video_id: str, language: str = "en", use_whisper: bool = False, force_whisper: bool = False, cookies_file: str = "") -> TranscriptResponse:
    """
    Async entry point for transcript retrieval.
    """
    loop = asyncio.get_running_loop() # Берем текущий запущенный цикл
    
    # Передаем наш `transcript_executor` в run_in_executor
    return await loop.run_in_executor(
        transcript_executor, 
        _get_transcript_sync, 
        video_id, language, use_whisper, force_whisper, cookies_file
    )

