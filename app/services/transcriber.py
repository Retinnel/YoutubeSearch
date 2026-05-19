"""
Transcript retrieval service.

Strategy:
1. Try youtube-transcript-api (fast, no video download).
2. Fallback: yt-dlp with --write-auto-sub (slower, more reliable).
3. On any failure: return error string, never raise.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import glob as _glob
from typing import Optional

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from app.models import TranscriptResponse
from app.utils.logger import get_logger

log = get_logger(__name__)


def _join_transcript(snippets: list[dict]) -> str:
    """Join transcript snippet dicts into a single text block."""
    return " ".join(s.get("text", "").strip() for s in snippets if s.get("text", "").strip())


def _get_via_api(video_id: str, language: str) -> tuple[str, str] | None:
    """
    Try youtube-transcript-api.
    Returns (transcript_text, language_code) or None on failure.
    """
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Try requested language first, then any manually-created, then auto-generated
        for lang_code in [language, None]:
            try:
                if lang_code:
                    transcript = transcript_list.find_transcript([lang_code])
                else:
                    # Get any available transcript
                    transcript = next(iter(transcript_list))
                snippets = transcript.fetch()
                text = _join_transcript(snippets)
                if text:
                    return text, transcript.language_code
            except Exception:
                continue

    except (TranscriptsDisabled, NoTranscriptFound):
        log.debug(f"Transcript API: no transcript | video_id={video_id}")
    except Exception as exc:
        log.warning(f"Transcript API error | video_id={video_id} | {exc}")

    return None


def _get_via_ytdlp(video_id: str, language: str) -> tuple[str, str] | None:
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

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            log.warning(f"yt-dlp subtitle download error | video_id={video_id} | {exc}")
            return None

        # Find downloaded .vtt file
        vtt_files = _glob.glob(os.path.join(tmp_dir, f"*.vtt"))
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


def _parse_vtt(path: str) -> str:
    """Parse a WebVTT subtitle file into plain text, deduplicating adjacent identical lines."""
    import re

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


def _get_transcript_sync(video_id: str, language: str) -> TranscriptResponse:
    url = f"https://www.youtube.com/shorts/{video_id}"

    # 1. Fast path via youtube-transcript-api
    result = _get_via_api(video_id, language)
    if result:
        text, lang = result
        log.info(f"Transcript OK (api) | video_id={video_id} | lang={lang} | chars={len(text)}")
        return TranscriptResponse(video_id=video_id, url=url, transcript=text, language=lang, source="api")

    # 2. Fallback: yt-dlp subtitles
    log.debug(f"Falling back to yt-dlp subtitles | video_id={video_id}")
    result = _get_via_ytdlp(video_id, language)
    if result:
        text, lang = result
        log.info(f"Transcript OK (yt-dlp) | video_id={video_id} | lang={lang} | chars={len(text)}")
        return TranscriptResponse(video_id=video_id, url=url, transcript=text, language=lang, source="yt-dlp")

    # 3. Both failed — return gracefully
    msg = "No transcript available (API and yt-dlp both failed)"
    log.warning(f"Transcript FAILED | video_id={video_id} | {msg}")
    return TranscriptResponse(video_id=video_id, url=url, transcript=None, error=msg)


async def get_transcript(video_id: str, language: str = "en") -> TranscriptResponse:
    """
    Async entry point for transcript retrieval.
    Tries youtube-transcript-api first, falls back to yt-dlp subtitles.
    Never raises — returns error field on failure.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_transcript_sync, video_id, language)
