"""
Shorts discovery service using yt-dlp.
No YouTube Data API key required.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

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
    # Using "shorts" as a keyword (not hashtag) works more reliably across niches.
    # #shorts hashtag causes YouTube to return 0 results for many topics.
    return f"ytsearch{max_results}:{query} shorts"


def _compute_outlier_score(view_count: Optional[int], avg_views: float) -> Optional[float]:
    """Compute outlier score = views / avg_views. Returns None if data is missing."""
    if view_count is None or avg_views <= 0:
        return None
    return round(view_count / avg_views, 2)


def _entry_to_short_meta(entry: dict[str, Any], avg_views: float = 0.0) -> ShortMeta | None:
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

    view_count = entry.get("view_count")
    view_int = int(view_count) if view_count is not None else None

    like_count = entry.get("like_count")
    like_int = int(like_count) if like_count is not None else None

    comment_count_raw = entry.get("comment_count")
    comment_int = int(comment_count_raw) if comment_count_raw is not None else None

    # Engagement rate: (likes + comments) / views * 100
    engagement_rate: Optional[float] = None
    if view_int and view_int > 0:
        numerator = (like_int or 0) + (comment_int or 0)
        if numerator > 0:
            engagement_rate = round(numerator / view_int * 100, 2)

    outlier_score = _compute_outlier_score(view_int, avg_views)

    return ShortMeta(
        video_id=video_id,
        url=f"https://www.youtube.com/shorts/{video_id}",
        title=entry.get("title") or "No title",
        channel_id=entry.get("channel_id") or entry.get("uploader_id"),
        channel_title=entry.get("channel") or entry.get("uploader"),
        duration=int(duration) if duration else None,
        view_count=view_int,
        like_count=like_int,
        outlier_score=outlier_score,
        engagement_rate=engagement_rate,
    )


def _search_sync(query: str, max_results: int, min_views: int = 0, min_outlier_score: float = 0.0) -> list[ShortMeta]:
    """Synchronous yt-dlp search. Runs in a thread pool."""
    url = _build_search_url(query, max_results)
    log.debug(f"yt-dlp search | url={url}")

    opts = {
        **_BASE_OPTS,
        "playlistend": max_results,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        log.warning(f"yt-dlp returned no info for query='{query}'")
        return []

    entries = info.get("entries") or []

    # First pass: collect view counts to compute batch average for outlier_score
    view_counts = [
        int(e.get("view_count")) for e in entries
        if e and e.get("view_count") is not None and e.get("duration", 0) <= 180
    ]
    avg_views = sum(view_counts) / len(view_counts) if view_counts else 0.0

    shorts: list[ShortMeta] = []
    for entry in entries:
        meta = _entry_to_short_meta(entry, avg_views=avg_views)
        if not meta:
            continue
        if min_views > 0 and (meta.view_count is None or meta.view_count < min_views):
            log.debug(f"Skipped (views={meta.view_count} < min={min_views}) | {meta.video_id}")
            continue
        if min_outlier_score > 0 and (meta.outlier_score is None or meta.outlier_score < min_outlier_score):
            log.debug(f"Skipped (outlier_score={meta.outlier_score} < min={min_outlier_score}) | {meta.video_id}")
            continue
        shorts.append(meta)

    log.info(
        f"Search done | query='{query}' | found={len(shorts)} shorts "
        f"(from {len(entries)} results, min_views={min_views}, min_outlier_score={min_outlier_score}, batch_avg_views={avg_views:.0f})"
    )
    return shorts


async def search_shorts(query: str, max_results: int = 20, min_views: int = 0, min_outlier_score: float = 0.0) -> list[ShortMeta]:
    """
    Search YouTube for Shorts matching `query` without using the YouTube Data API.

    Uses yt-dlp with `ytsearch:` extractor and filters by duration ≤ 180 seconds.
    Outlier score is computed as views / batch_avg (approximate).
    Runs the blocking yt-dlp call in a thread pool to stay async.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search_sync, query, max_results, min_views, min_outlier_score)


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

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(base_url, download=False)

    if not info:
        log.warning(f"yt-dlp returned no info for channel_id='{channel_id}'")
        return []

    entries = info.get("entries") or []
    shorts: list[ShortMeta] = []
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


def _search_channel_outliers_sync(
    channel_url: str,
    max_results: int = 50,
    top_n: int = 20,
    min_outlier_score: float = 1.5,
    min_views: int = 0,
) -> dict:
    """
    Fetch Shorts from a channel, compute real outlier scores (views / channel_avg),
    and return top N by outlier score. No YouTube API key required.
    """
    # Normalise to /shorts tab URL
    if channel_url.startswith("http"):
        base_url = channel_url.rstrip("/") + "/shorts"
    elif channel_url.startswith("@"):
        base_url = f"https://www.youtube.com/{channel_url}/shorts"
    else:
        base_url = f"https://www.youtube.com/channel/{channel_url}/shorts"

    log.info(f"Fetching channel outliers | url={base_url} | max={max_results}")

    opts = {
        **_BASE_OPTS,
        "playlistend": max_results,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(base_url, download=False)

    if not info:
        log.warning(f"yt-dlp returned no info for channel='{channel_url}'")
        return {"channel_title": None, "avg_views": 0, "shorts": [], "scanned": 0}

    channel_title = info.get("channel") or info.get("uploader") or info.get("title")
    entries = info.get("entries") or []

    # First pass: collect view counts from all valid Shorts for accurate channel avg
    valid_view_counts = [
        int(e.get("view_count"))
        for e in entries
        if e and e.get("view_count") is not None and (e.get("duration") or 0) <= 180
    ]
    avg_views = sum(valid_view_counts) / len(valid_view_counts) if valid_view_counts else 0.0

    log.info(f"Channel '{channel_title}' | scanned={len(entries)} | avg_views={avg_views:.0f}")

    # Second pass: build ShortMeta with real outlier scores
    all_shorts: list[ShortMeta] = []
    for entry in entries:
        meta = _entry_to_short_meta(entry, avg_views=avg_views)
        if not meta:
            continue
        if min_views > 0 and (meta.view_count is None or meta.view_count < min_views):
            continue
        all_shorts.append(meta)

    # Filter by outlier score and sort descending
    filtered = [s for s in all_shorts if s.outlier_score is not None and s.outlier_score >= min_outlier_score]
    filtered.sort(key=lambda s: s.outlier_score or 0, reverse=True)
    top_shorts = filtered[:top_n]

    log.info(
        f"Channel outliers done | '{channel_title}' | scanned={len(entries)} | "
        f"filtered(>={min_outlier_score}x)={len(filtered)} | returning top {len(top_shorts)}"
    )

    return {
        "channel_title": channel_title,
        "avg_views": round(avg_views),
        "shorts": top_shorts,
        "scanned": len(entries),
    }


async def search_channel_outliers(
    channel_url: str,
    max_results: int = 50,
    top_n: int = 20,
    min_outlier_score: float = 1.5,
    min_views: int = 0,
) -> dict:
    """Async wrapper: fetch channel Shorts and return top outliers by viral coefficient."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _search_channel_outliers_sync, channel_url, max_results, top_n, min_outlier_score, min_views
    )

