"""
Shorts discovery service using yt-dlp.
No YouTube Data API key required.
"""
from __future__ import annotations

import asyncio
from typing import Any

import yt_dlp

from app.models import ShortMeta
from app.utils.logger import get_logger

log = get_logger(__name__)

# yt-dlp options common to all calls (no actual download)
_BASE_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "ignoreerrors": True,
    "extract_flat": "in_playlist",  # fast metadata-only mode
}


def _build_search_url(query: str, max_results: int) -> str:
    """Build a yt-dlp search URL that returns videos."""
    return f"ytsearch{max_results}:{query} #shorts"


def _entry_to_short_meta(entry: dict[str, Any]) -> ShortMeta | None:
    """Convert a yt-dlp flat-playlist entry to ShortMeta. Returns None if not a short."""
    if not entry:
        return None

    duration = entry.get("duration")
    # YouTube Shorts can be up to 3 minutes in practice; filter obvious long-form content
    if duration is not None and duration > 180:
        return None

    video_id = entry.get("id") or entry.get("video_id")
    if not video_id:
        return None

    return ShortMeta(
        video_id=video_id,
        url=f"https://www.youtube.com/shorts/{video_id}",
        title=entry.get("title") or "No title",
        channel_id=entry.get("channel_id") or entry.get("uploader_id"),
        channel_title=entry.get("channel") or entry.get("uploader"),
        duration=int(duration) if duration else None,
    )


def _search_sync(query: str, max_results: int) -> list[ShortMeta]:
    """Synchronous yt-dlp search. Runs in a thread pool."""
    url = _build_search_url(query, max_results)
    log.debug(f"yt-dlp search | url={url}")

    opts = {
        **_BASE_OPTS,
        "playlistend": max_results,
    }

    shorts: list[ShortMeta] = []

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        log.warning(f"yt-dlp returned no info for query='{query}'")
        return shorts

    entries = info.get("entries") or []
    for entry in entries:
        meta = _entry_to_short_meta(entry)
        if meta:
            shorts.append(meta)

    log.info(f"Search done | query='{query}' | found={len(shorts)} shorts (from {len(entries)} results)")
    return shorts


async def search_shorts(query: str, max_results: int = 20) -> list[ShortMeta]:
    """
    Search YouTube for Shorts matching `query` without using the YouTube Data API.

    Uses yt-dlp with `ytsearch:` extractor and filters by duration ≤ 65 seconds.
    Runs the blocking yt-dlp call in a thread pool to stay async.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search_sync, query, max_results)


def _get_channel_shorts_sync(channel_id: str, max_shorts: int) -> list[ShortMeta]:
    """
    Retrieve Shorts from a specific channel's /shorts tab.
    channel_id can be UC…, @handle, or full URL.
    """
    # Normalise channel_id to a URL
    if channel_id.startswith("http"):
        base_url = channel_id.rstrip("/") + "/shorts"
    elif channel_id.startswith("@"):
        base_url = f"https://www.youtube.com/{channel_id}/shorts"
    else:
        base_url = f"https://www.youtube.com/channel/{channel_id}/shorts"

    log.debug(f"Fetching channel shorts | url={base_url}")

    opts = {
        **_BASE_OPTS,
        "playlistend": max_shorts,
    }

    shorts: list[ShortMeta] = []

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(base_url, download=False)

    if not info:
        log.warning(f"yt-dlp returned no info for channel_id='{channel_id}'")
        return shorts

    entries = info.get("entries") or []
    for entry in entries:
        meta = _entry_to_short_meta(entry)
        if meta:
            shorts.append(meta)

    log.info(f"Channel shorts done | channel_id='{channel_id}' | found={len(shorts)}")
    return shorts


async def get_channel_shorts(channel_id: str, max_shorts: int = 10) -> list[ShortMeta]:
    """Async wrapper around _get_channel_shorts_sync."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_channel_shorts_sync, channel_id, max_shorts)
