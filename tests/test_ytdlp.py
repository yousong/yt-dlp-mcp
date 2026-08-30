from __future__ import annotations

import json

import pytest

from yt_dlp_mcp.config import Config
from yt_dlp_mcp.core.ytdlp import YtdlpWrapper


@pytest.fixture
def ytdlp(tmp_path):
    return YtdlpWrapper(cookie_dir=tmp_path)


class TestYtdlpWrapper:

    def test_extract_info(self, ytdlp):
        info = ytdlp.extract_info("https://www.youtube.com/watch?v=BaW_jenozKc")
        assert info.get("title")
        assert info.get("id") == "BaW_jenozKc"
        assert info.get("duration") is not None

    def test_list_formats(self, ytdlp):
        formats = ytdlp.list_formats("https://www.youtube.com/watch?v=BaW_jenozKc")
        assert isinstance(formats, list)
        assert len(formats) > 0
        f = formats[0]
        assert "format_id" in f
        assert "ext" in f

    def test_cookie_resolution(self, tmp_path):
        cookie_file = tmp_path / "youtube.com.txt"
        cookie_file.write_text("# Netscape cookie file\n")
        ytdlp = YtdlpWrapper(cookie_dir=tmp_path)
        resolved = ytdlp._resolve_cookie_file("https://www.youtube.com/watch?v=test")
        assert resolved == str(cookie_file)

    def test_cookie_resolution_no_match(self, tmp_path):
        ytdlp = YtdlpWrapper(cookie_dir=tmp_path)
        resolved = ytdlp._resolve_cookie_file("https://www.youtube.com/watch?v=test")
        assert resolved is None


class TestConfig:

    def test_from_env_defaults(self, monkeypatch):
        for key in ("PULSE_SERVER", "YTDLP_MCP_TRANSPORT", "YTDLP_MCP_HOST", "YTDLP_MCP_PORT",
                     "YTDLP_MCP_COOKIE_DIR", "YTDLP_MCP_STORE_DIR", "MPV_SOCKET"):
            monkeypatch.delenv(key, raising=False)
        config = Config.from_env()
        assert config.mcp_transport == "streamable-http"
        assert config.mcp_host == "0.0.0.0"
        assert config.mcp_port == 8080
        assert config.cookie_dir == "/data/cookies"
        assert config.store_dir == "/data/store"

    def test_from_env_custom(self, monkeypatch):
        monkeypatch.setenv("YTDLP_MCP_PORT", "9090")
        monkeypatch.setenv("YTDLP_MCP_STORE_DIR", "/custom/store")
        config = Config.from_env()
        assert config.mcp_port == 9090
        assert config.store_dir == "/custom/store"
