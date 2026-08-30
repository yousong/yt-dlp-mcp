from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import MCPServer

from .config import Config
from .core.cookies import CookieStore
from .core.download import DownloadManager
from .core.mpv import MpvController
from .core.ytdlp import YtdlpWrapper
from .tools import cookies as cookie_tools
from .tools import cast as cast_tools
from .tools import download as download_tools
from .tools import playback as playback_tools
from .tools import search as search_tools

logger = logging.getLogger(__name__)


def create_server(config: Config) -> tuple[MCPServer, MpvController]:
    ytdlp = YtdlpWrapper(cookie_dir=config.cookie_dir)
    mpv = MpvController(socket_path=config.mpv_socket, pulse_server=config.pulse_server)
    cookie_store = CookieStore(config.cookie_dir)
    download_manager = DownloadManager(store_dir=config.store_dir, cookie_dir=config.cookie_dir)

    server = MCPServer(
        name="yt-dlp-mcp",
        version="0.1.0",
        description="MCP service for audio playback, search, casting, and downloading via yt-dlp",
    )

    _register_info_tools(server, ytdlp)
    playback_tools.register(server, config, ytdlp, mpv)
    search_tools.register(server, ytdlp)
    cookie_tools.register(server, cookie_store)
    download_tools.register(server, download_manager)
    cast_tools.register(server, ytdlp)

    return server, mpv


def _register_info_tools(server: MCPServer, ytdlp: YtdlpWrapper) -> None:

    async def get_info(url: str) -> str:
        """Get media metadata from a URL without downloading.

        Args:
            url: The media page URL to extract information from.
        """
        try:
            info = ytdlp.extract_info(url)
            fields = {
                "title": info.get("title"),
                "id": info.get("id"),
                "url": info.get("webpage_url") or info.get("url"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
                "upload_date": info.get("upload_date"),
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "description": (info.get("description") or "")[:500],
                "tags": info.get("tags"),
                "thumbnail": info.get("thumbnail"),
                "ext": info.get("ext"),
                "acodec": info.get("acodec"),
                "vcodec": info.get("vcodec"),
                "abr": info.get("abr"),
                "format": info.get("format"),
                "is_live": info.get("is_live"),
                "live_status": info.get("live_status"),
                "chapters": info.get("chapters"),
            }
            fields = {k: v for k, v in fields.items() if v is not None}
            return json.dumps(fields, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("get_info failed for %s", url)
            return json.dumps({"error": str(e)})

    async def list_formats(url: str) -> str:
        """List all available formats for a media URL.

        Args:
            url: The media page URL to list formats for.
        """
        try:
            formats = ytdlp.list_formats(url)
            return json.dumps(formats, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("list_formats failed for %s", url)
            return json.dumps({"error": str(e)})

    server.add_tool(get_info, description="Get media metadata from a URL without downloading.")
    server.add_tool(list_formats, description="List all available formats for a media URL.")


def run_server(config: Config) -> None:
    server, mpv = create_server(config)

    async def _run() -> None:
        try:
            if config.mcp_transport == "streamable-http":
                await server.run_streamable_http_async(
                    host=config.mcp_host,
                    port=config.mcp_port,
                    stateless_http=True,
                )
            elif config.mcp_transport == "stdio":
                await server.run_stdio_async()
            else:
                raise ValueError(f"Unsupported transport: {config.mcp_transport}")
        finally:
            await mpv.cleanup()

    logger.info("Starting yt-dlp-mcp server (transport=%s)", config.mcp_transport)
    asyncio.run(_run())
