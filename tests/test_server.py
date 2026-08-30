from __future__ import annotations

import pytest

from yt_dlp_mcp.config import Config
from yt_dlp_mcp.server import create_server


@pytest.fixture
def config(tmp_path):
    return Config(
        cookie_dir=str(tmp_path / "cookies"),
        store_dir=str(tmp_path / "store"),
    )


class TestServer:

    def test_create_server(self, config):
        server, mpv = create_server(config)
        assert server is not None
        assert server.name == "yt-dlp-mcp"
        assert mpv is not None
