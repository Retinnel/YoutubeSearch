"""
API integration tests — uses FastAPI TestClient (no real HTTP).
All external services (yt-dlp, youtube-transcript-api) are mocked.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings


API_KEY = settings.api_key
HEADERS = {"X-API-KEY": API_KEY}


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"


class TestAuthMiddleware:
    def test_no_key_returns_401(self, client):
        r = client.post("/api/v1/get_transcript", json={"video_id": "abc123"})
        assert r.status_code == 401

    def test_wrong_key_returns_401(self, client):
        r = client.post(
            "/api/v1/get_transcript",
            json={"video_id": "abc123"},
            headers={"X-API-KEY": "wrong-key"},
        )
        assert r.status_code == 401

    def test_valid_key_passes(self, client):
        with patch("app.services.transcriber.get_transcript") as mock_t:
            mock_t.return_value = {
                "video_id": "abc123",
                "transcript": "test text",
                "language": "en",
                "source": "api",
            }
            r = client.post(
                "/api/v1/get_transcript",
                json={"video_id": "abc123"},
                headers=HEADERS,
            )
        # Should be 200, not 403
        assert r.status_code == 200


class TestGetTranscriptEndpoint:
    def test_success_response_shape(self, client):
        mock_result = {
            "video_id": "abc123",
            "url": "https://youtube.com/watch?v=abc123",
            "transcript": "Hello world this is the video",
            "language": "en",
            "source": "api",
        }
        with patch("app.main.get_transcript", return_value=mock_result):
            r = client.post(
                "/api/v1/get_transcript",
                json={"video_id": "abc123"},
                headers=HEADERS,
            )
        assert r.status_code == 200
        data = r.json()
        assert data["video_id"] == "abc123"
        assert "transcript" in data

    def test_transcript_failure_returns_200_with_null(self, client):
        """Failed transcript should return 200 with null transcript, not 500."""
        mock_result = {
            "video_id": "abc123",
            "url": "https://youtube.com/watch?v=abc123",
            "transcript": None,
            "language": "en",
            "source": "failed",
            "error": "No transcript available",
        }
        with patch("app.main.get_transcript", return_value=mock_result):
            r = client.post(
                "/api/v1/get_transcript",
                json={"video_id": "abc123"},
                headers=HEADERS,
            )
        assert r.status_code == 200
        data = r.json()
        assert data["transcript"] is None

    def test_force_whisper_param_is_accepted(self, client):
        """force_whisper=true should be accepted and passed to get_transcript."""
        mock_result = {
            "video_id": "abc123",
            "url": "https://youtube.com/shorts/abc123",
            "transcript": "Whisper transcription text",
            "language": "en",
            "source": "whisper",
        }
        with patch("app.main.get_transcript", return_value=mock_result) as mock_t:
            r = client.post(
                "/api/v1/get_transcript",
                json={"video_id": "abc123", "force_whisper": True},
                headers=HEADERS,
            )
        assert r.status_code == 200
        # Verify force_whisper=True was passed to the service
        _, kwargs = mock_t.call_args
        assert kwargs.get("force_whisper") is True


class TestSearchShortsEndpoint:
    def test_success_response_shape(self, client):
        mock_shorts = [
            {
                "video_id": "abc123",
                "url": "https://youtube.com/shorts/abc123",
                "title": "Test Short",
                "channel_id": "UCtest",
                "channel_title": "Test Channel",
                "duration": 45,
                "view_count": 10000,
                "like_count": 500,
                "outlier_score": 2.5,
                "engagement_rate": 5.0,
            }
        ]
        with patch("app.main.search_shorts", return_value=mock_shorts):
            r = client.post(
                "/api/v1/search_shorts",
                json={"query": "gaming shorts"},
                headers=HEADERS,
            )
        assert r.status_code == 200
        data = r.json()
        assert "shorts" in data
        assert len(data["shorts"]) == 1
        assert data["shorts"][0]["video_id"] == "abc123"

    def test_empty_query_rejected(self, client):
        r = client.post(
            "/api/v1/search_shorts",
            json={"query": ""},
            headers=HEADERS,
        )
        assert r.status_code == 422  # Pydantic validation error
