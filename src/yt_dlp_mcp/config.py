from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    pulse_server: str = ""
    mcp_transport: str = "streamable-http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8080
    cookie_dir: str = "/data/cookies"
    store_dir: str = "/data/store"
    mpv_socket: str = "/tmp/mpv-socket"
    mpv_audio_device: str = ""  # Empty means auto-detect, or use "null" for testing

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            pulse_server=os.environ.get("PULSE_SERVER", ""),
            mcp_transport=os.environ.get("YTDLP_MCP_TRANSPORT", "streamable-http"),
            mcp_host=os.environ.get("YTDLP_MCP_HOST", "0.0.0.0"),
            mcp_port=int(os.environ.get("YTDLP_MCP_PORT", "8080")),
            cookie_dir=os.environ.get("YTDLP_MCP_COOKIE_DIR", "/data/cookies"),
            store_dir=os.environ.get("YTDLP_MCP_STORE_DIR", "/data/store"),
            mpv_socket=os.environ.get("MPV_SOCKET", "/tmp/mpv-socket"),
            mpv_audio_device=os.environ.get("YTDLP_MCP_MPV_AUDIO_DEVICE", ""),
        )
