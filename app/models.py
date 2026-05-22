from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ── Search Shorts ──────────────────────────────────────────────────────────────

class SearchShortsRequest(BaseModel):
    query: str = Field(..., min_length=1, description="YouTube search query")
    max_results: int = Field(20, ge=1, le=50, description="Max number of shorts to return")
    min_views: int = Field(0, ge=0, description="Minimum view count filter (0 = no filter)")
    min_outlier_score: float = Field(0.0, ge=0.0, description="Minimum outlier score (views/batch_avg). 0 = no filter")


class ShortMeta(BaseModel):
    video_id: str
    url: str
    title: str
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None
    duration: Optional[int] = None  # seconds
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    outlier_score: Optional[float] = None   # views / batch_avg (keyword search) or views / channel_avg (channel search)
    engagement_rate: Optional[float] = None  # (likes + comments) / views * 100


class SearchShortsResponse(BaseModel):
    query: str
    shorts: list[ShortMeta]
    total: int


# ── Search Channel Shorts (outlier mode) ──────────────────────────────────────

class SearchChannelShortsRequest(BaseModel):
    channel_url: str = Field(..., description="YouTube channel URL, @handle, or UC… ID")
    max_results: int = Field(50, ge=1, le=200, description="Max shorts to fetch from channel")
    top_n: int = Field(20, ge=1, le=100, description="How many top outliers to return")
    min_outlier_score: float = Field(1.5, ge=0.0, description="Minimum outlier score to include (default 1.5x = 50% above channel avg)")
    min_views: int = Field(0, ge=0, description="Minimum view count filter (0 = no filter)")


class SearchChannelShortsResponse(BaseModel):
    channel_url: str
    channel_title: Optional[str] = None
    shorts_scanned: int
    avg_views_channel: Optional[int] = None
    shorts: list[ShortMeta]
    total: int


# ── Get Transcript ─────────────────────────────────────────────────────────────

class GetTranscriptRequest(BaseModel):
    video_id: str = Field(..., description="YouTube video ID (e.g. dQw4w9WgXcQ)")
    language: str = Field("en", description="Preferred transcript language code")


class TranscriptResponse(BaseModel):
    video_id: str
    url: str
    transcript: Optional[str] = None
    language: Optional[str] = None
    source: Optional[str] = None  # "api" | "yt-dlp" | None
    error: Optional[str] = None


# ── Process Channel ────────────────────────────────────────────────────────────

class ProcessChannelRequest(BaseModel):
    channel_id: str = Field(..., description="YouTube channel ID (UC…) or @handle or full URL")
    max_shorts: int = Field(10, ge=1, le=30, description="Max shorts to process per channel")
    language: str = Field("en", description="Preferred transcript language code")


class ShortWithTranscript(BaseModel):
    video_id: str
    url: str
    title: str
    duration: Optional[int] = None
    transcript: Optional[str] = None
    transcript_source: Optional[str] = None
    transcript_error: Optional[str] = None


class ProcessChannelResponse(BaseModel):
    channel_id: str
    shorts_found: int
    shorts: list[ShortWithTranscript]
