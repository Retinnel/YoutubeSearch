"""
FastAPI application — YouTube Shorts Finder & Transcriber
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import JSONResponse

from app.config import settings
from app.models import (
    SearchShortsRequest, SearchShortsResponse,
    SearchChannelShortsRequest, SearchChannelShortsResponse,
    GetTranscriptRequest, TranscriptResponse,
    ProcessChannelRequest, ProcessChannelResponse, ShortWithTranscript,
)
from app.services.shorts_finder import search_shorts, get_channel_shorts, search_channel_outliers
from app.services.transcriber import get_transcript
from app.utils.logger import setup_logger, get_logger

# Initialise logger before app starts
setup_logger()
log = get_logger(__name__)

app = FastAPI(
    title="YouTube Shorts Finder",
    description="Search and transcribe YouTube Shorts without the YouTube Data API.",
    version="1.1.0",
)

# ── Auth ───────────────────────────────────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)


async def verify_api_key(key: Optional[str] = Security(api_key_header)) -> None:
    if not key or key != settings.api_key:
        log.warning(f"Unauthorized request (invalid or missing X-API-KEY)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


# ── Request timing middleware ──────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info(
        f"{request.method} {request.url.path} → {response.status_code} "
        f"({elapsed_ms:.1f} ms)"
    )
    return response


# ── Startup / Shutdown ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    log.info("Server starting up")
    log.info(
        f"Host: {settings.host}:{settings.port} | Log level: {settings.log_level} | "
        f"Whisper: {'enabled' if settings.whisper_enabled else 'disabled'}"
    )


@app.on_event("shutdown")
async def shutdown_event():
    log.info("Server shutting down")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    """Simple health check — no auth required."""
    return {"status": "ok"}


@app.post(
    "/api/v1/search_shorts",
    response_model=SearchShortsResponse,
    tags=["Shorts"],
    dependencies=[Depends(verify_api_key)],
    summary="Search YouTube Shorts by query (no API key needed)",
)
async def search_shorts_endpoint(body: SearchShortsRequest):
    """
    Search YouTube for Shorts matching the query.

    Uses yt-dlp internally — no YouTube Data API quota consumed.
    Returns metadata including outlier_score (views / batch_avg) and engagement_rate.
    """
    log.info(
        f"search_shorts | query='{body.query}' | max_results={body.max_results} | "
        f"min_views={body.min_views} | min_outlier_score={body.min_outlier_score}"
    )
    shorts = await search_shorts(body.query, body.max_results, body.min_views, body.min_outlier_score)
    return SearchShortsResponse(query=body.query, shorts=shorts, total=len(shorts))


@app.post(
    "/api/v1/search_channel_shorts",
    response_model=SearchChannelShortsResponse,
    tags=["Shorts"],
    dependencies=[Depends(verify_api_key)],
    summary="Find top viral Shorts from a specific channel by outlier score",
)
async def search_channel_shorts_endpoint(body: SearchChannelShortsRequest):
    """
    Fetch Shorts from a specific YouTube channel and return top outliers.

    Outlier score = views / channel_avg_views (real per-channel average, more accurate
    than keyword search batch average). No YouTube Data API quota consumed.

    Useful for analyzing curated channels to find their viral content.
    """
    log.info(
        f"search_channel_shorts | channel='{body.channel_url}' | max={body.max_results} | "
        f"top_n={body.top_n} | min_outlier={body.min_outlier_score} | min_views={body.min_views}"
    )
    result = await search_channel_outliers(
        body.channel_url,
        max_results=body.max_results,
        top_n=body.top_n,
        min_outlier_score=body.min_outlier_score,
        min_views=body.min_views,
    )
    return SearchChannelShortsResponse(
        channel_url=body.channel_url,
        channel_title=result["channel_title"],
        shorts_scanned=result["scanned"],
        avg_views_channel=result["avg_views"],
        shorts=result["shorts"],
        total=len(result["shorts"]),
    )


@app.post(
    "/api/v1/get_transcript",
    response_model=TranscriptResponse,
    tags=["Transcripts"],
    dependencies=[Depends(verify_api_key)],
    summary="Get transcript for a single video",
)
async def get_transcript_endpoint(body: GetTranscriptRequest):
    """
    Retrieve the transcript for a YouTube video.

    Tries `youtube-transcript-api` first; falls back to yt-dlp auto-subtitles.
    If `WHISPER_ENABLED=true` in .env, uses Whisper as a last resort.
    Returns `null` transcript (not an error) when no captions are available.
    """
    log.info(f"get_transcript | video_id={body.video_id} | language={body.language} | force_whisper={body.force_whisper}")
    result = await get_transcript(
        body.video_id,
        body.language,
        use_whisper=settings.whisper_enabled,
        force_whisper=body.force_whisper,
        cookies_file=settings.cookies_file,
    )
    return result


@app.post(
    "/api/v1/process_channel",
    response_model=ProcessChannelResponse,
    tags=["Channels"],
    dependencies=[Depends(verify_api_key)],
    summary="Get Shorts + transcripts for a YouTube channel",
)
async def process_channel_endpoint(body: ProcessChannelRequest):
    """
    Fetch up to `max_shorts` Shorts from the given channel, then retrieve
    transcripts for each one concurrently.

    `channel_id` accepts:
    - YouTube channel ID (`UC…`)
    - @handle (`@channelname`)
    - Full channel URL
    """
    log.info(f"process_channel | channel_id='{body.channel_id}' | max_shorts={body.max_shorts}")

    shorts_meta = await get_channel_shorts(body.channel_id, body.max_shorts)
    log.info(f"process_channel | shorts found: {len(shorts_meta)}")

    # Fetch all transcripts concurrently
    import asyncio
    transcript_tasks = [
            get_transcript(s.video_id, body.language, use_whisper=settings.whisper_enabled, cookies_file=settings.cookies_file)
            for s in shorts_meta
        ]
    transcripts = await asyncio.gather(*transcript_tasks)

    results: list[ShortWithTranscript] = []
    for meta, tr in zip(shorts_meta, transcripts):
        results.append(ShortWithTranscript(
            video_id=meta.video_id,
            url=meta.url,
            title=meta.title,
            duration=meta.duration,
            transcript=tr.transcript,
            transcript_source=tr.source,
            transcript_error=tr.error,
        ))

    return ProcessChannelResponse(
        channel_id=body.channel_id,
        shorts_found=len(results),
        shorts=results,
    )

