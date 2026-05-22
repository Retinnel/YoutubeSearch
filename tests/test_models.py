"""
Unit tests for Pydantic models — request validation and defaults.
"""
import pytest
from pydantic import ValidationError

from app.models import (
    SearchShortsRequest,
    GetTranscriptRequest,
    SearchChannelShortsRequest,
    ShortMeta,
)


class TestSearchShortsRequest:
    def test_defaults(self):
        r = SearchShortsRequest(query="gaming")
        assert r.max_results == 20
        assert r.min_views == 0
        assert r.min_outlier_score == 0.0

    def test_empty_query_rejected(self):
        with pytest.raises(ValidationError):
            SearchShortsRequest(query="")

    def test_max_results_capped(self):
        with pytest.raises(ValidationError):
            SearchShortsRequest(query="gaming", max_results=100)

    def test_negative_min_views_rejected(self):
        with pytest.raises(ValidationError):
            SearchShortsRequest(query="gaming", min_views=-1)


class TestGetTranscriptRequest:
    def test_default_language(self):
        r = GetTranscriptRequest(video_id="abc123")
        assert r.language == "en"

    def test_custom_language(self):
        r = GetTranscriptRequest(video_id="abc123", language="ru")
        assert r.language == "ru"


class TestShortMeta:
    def test_optional_fields_default_none(self):
        s = ShortMeta(video_id="abc", url="https://youtube.com/shorts/abc", title="Test")
        assert s.outlier_score is None
        assert s.engagement_rate is None
        assert s.like_count is None
        assert s.view_count is None

    def test_all_fields(self):
        s = ShortMeta(
            video_id="abc",
            url="https://youtube.com/shorts/abc",
            title="Test",
            outlier_score=3.5,
            engagement_rate=12.1,
            view_count=100000,
            like_count=5000,
        )
        assert s.outlier_score == 3.5
        assert s.engagement_rate == 12.1


class TestSearchChannelShortsRequest:
    def test_defaults(self):
        r = SearchChannelShortsRequest(channel_url="@testchannel")
        assert r.max_results == 50
        assert r.top_n == 20
        assert r.min_outlier_score == 1.5
        assert r.min_views == 0
