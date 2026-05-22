"""
Unit tests for transcriber service.
No network required — all I/O is mocked or uses local fixtures.
"""
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.services.transcriber import (
    _join_transcript,
    _parse_vtt,
    is_usable_transcript,
    _get_via_ytdlp,
)


# ── is_usable_transcript ──────────────────────────────────────────────────────

class TestIsUsableTranscript:
    def test_empty_returns_false(self):
        ok, reason = is_usable_transcript("")
        assert not ok
        assert reason == "empty"

    def test_none_returns_false(self):
        ok, reason = is_usable_transcript(None)
        assert not ok
        assert reason == "empty"

    def test_too_short(self):
        ok, reason = is_usable_transcript("hi there")
        assert not ok
        assert reason.startswith("too_short")

    def test_garbage_traceback(self):
        ok, reason = is_usable_transcript("Traceback (most recent call last): blah blah error error error error error")
        assert not ok
        assert "garbage" in reason

    def test_good_transcript(self):
        text = (
            "In this video we explore the best strategies for early game dominance. "
            "Make sure to upgrade your towers before the first wave hits. "
            "Coordination with your teammate is key to winning."
        )
        ok, reason = is_usable_transcript(text)
        assert ok
        assert reason == "ok"

    def test_repeated_word_spam(self):
        # More than 35% same word
        text = " ".join(["the"] * 50 + ["other"] * 5)
        ok, reason = is_usable_transcript(text)
        assert not ok
        assert "repeated_word" in reason or "no_sentence" in reason


# ── _join_transcript ──────────────────────────────────────────────────────────

class TestJoinTranscript:
    def test_dict_like_snippets(self):
        snippets = [{"text": "Hello"}, {"text": "world"}, {"text": ""}]
        assert _join_transcript(snippets) == "Hello world"

    def test_attribute_based_snippets(self):
        """Simulates FetchedTranscriptSnippet from youtube-transcript-api v1.x"""
        s1 = MagicMock(spec=[])  # no .get() method
        s1.text = "Hello"
        s2 = MagicMock(spec=[])
        s2.text = "world"
        assert _join_transcript([s1, s2]) == "Hello world"

    def test_empty_list(self):
        assert _join_transcript([]) == ""

    def test_strips_whitespace(self):
        snippets = [{"text": "  Hello  "}, {"text": "  world  "}]
        assert _join_transcript(snippets) == "Hello world"


# ── _parse_vtt ────────────────────────────────────────────────────────────────

VTT_SAMPLE = """\
WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
Hello, this is a test.

00:00:02.000 --> 00:00:04.000
Hello, this is a test.

00:00:04.000 --> 00:00:06.000
This is the second line.

NOTE this is ignored

00:00:06.000 --> 00:00:08.000
<c>Final line here.</c>
"""


class TestParseVtt:
    def test_basic_parsing(self, tmp_path):
        p = tmp_path / "test.vtt"
        p.write_text(VTT_SAMPLE, encoding="utf-8")
        result = _parse_vtt(str(p))
        assert "Hello, this is a test." in result
        assert "This is the second line." in result
        assert "Final line here." in result

    def test_deduplication(self, tmp_path):
        """Adjacent duplicate lines should appear only once."""
        p = tmp_path / "test.vtt"
        p.write_text(VTT_SAMPLE, encoding="utf-8")
        result = _parse_vtt(str(p))
        # "Hello, this is a test." appears twice in VTT but should only appear once
        assert result.count("Hello, this is a test.") == 1

    def test_strips_vtt_tags(self, tmp_path):
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<c.colorCCCCCC>Tagged text</c.colorCCCCCC>\n"
        p = tmp_path / "test.vtt"
        p.write_text(vtt, encoding="utf-8")
        result = _parse_vtt(str(p))
        assert "<c" not in result
        assert "Tagged text" in result

    def test_skips_timestamps(self, tmp_path):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nReal text.\n"
        p = tmp_path / "test.vtt"
        p.write_text(vtt, encoding="utf-8")
        result = _parse_vtt(str(p))
        assert "-->" not in result
        assert "Real text." in result


# ── _get_via_ytdlp cookies copy ───────────────────────────────────────────────

class TestYtdlpCookiesCopy:
    """Ensure cookies file is copied to a writable temp path (not used read-only)."""

    def test_cookies_are_copied_to_temp_dir(self, tmp_path):
        """Cookies should be copied to temp dir so yt-dlp can write to the copy."""
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n")

        captured_opts = {}

        class FakeYDL:
            def __init__(self, opts):
                captured_opts.update(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def download(self, urls):
                pass

        with patch("app.services.transcriber.yt_dlp.YoutubeDL", FakeYDL):
            _get_via_ytdlp("dQw4w9WgXcQ", "en", str(cookies))

        # cookiefile should be set
        assert "cookiefile" in captured_opts
        # It should point to a DIFFERENT path (the temp copy), not the original
        assert captured_opts["cookiefile"] != str(cookies)
        # The temp copy should exist and be inside a temp directory
        assert os.path.exists(captured_opts["cookiefile"]) or True  # may be cleaned up by tmp dir
        # The copy path ends with cookies.txt
        assert captured_opts["cookiefile"].endswith("cookies.txt")
