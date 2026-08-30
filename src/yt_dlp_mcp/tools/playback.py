from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from mcp.server import MCPServer

from ..config import Config
from ..core.mpv import MpvController
from ..core.ytdlp import YtdlpWrapper

logger = logging.getLogger(__name__)

_mpv: MpvController | None = None
_ytdlp: YtdlpWrapper | None = None
_config: Config | None = None
_yt_dlp_proc: asyncio.subprocess.Process | None = None


def register(server: MCPServer, config: Config, ytdlp: YtdlpWrapper, mpv: MpvController) -> None:
    global _mpv, _ytdlp, _config
    _mpv = mpv
    _ytdlp = ytdlp
    _config = config

    async def play(url: str, format: str = "bestaudio/best", volume: int | None = None) -> str:
        """Play audio from a media URL through PulseAudio.

        Args:
            url: The media page URL to play.
            format: yt-dlp format string for selecting media format. Defaults to bestaudio/best.
            volume: Volume level 0-100. If not specified, keeps current volume.
        """
        global _yt_dlp_proc
        if not _mpv:
            return json.dumps({"error": "Playback engine not initialized"})

        try:
            info = _ytdlp.extract_info(url, format=format)
            title = info.get("title", "Unknown")

            env = os.environ.copy()
            if _config and _config.pulse_server:
                env["PULSE_SERVER"] = _config.pulse_server

            cookie_args = []
            cookie_file = _ytdlp._resolve_cookie_file(url)
            if cookie_file:
                cookie_args = ["--cookies", cookie_file]

            _yt_dlp_proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "-f", format,
                "-o", "-",
                "--quiet",
                "--no-warnings",
                *cookie_args,
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            await _mpv.play(_yt_dlp_proc, title=title, url=url, format_info={
                "format_id": info.get("format_id"),
                "ext": info.get("ext"),
                "acodec": info.get("acodec"),
                "abr": info.get("abr"),
            })

            if volume is not None:
                await _mpv.set_volume(float(volume))

            return json.dumps({
                "status": "playing",
                "title": title,
                "duration": info.get("duration"),
                "format_info": _mpv.state.format_info,
            }, ensure_ascii=False)
        except Exception as e:
            logger.exception("play failed for %s", url)
            return json.dumps({"error": str(e)})

    async def playback_control(action: str, value: float | None = None) -> str:
        """Control current playback.

        Args:
            action: Control action: pause, resume, stop, seek, volume.
            value: For seek: position in seconds. For volume: level 0-100.
        """
        if not _mpv or not _mpv.is_running:
            return json.dumps({"error": "Nothing is playing"})

        result = False
        if action == "pause":
            result = await _mpv.pause()
        elif action == "resume":
            result = await _mpv.resume()
        elif action == "stop":
            await _mpv.stop()
            result = True
        elif action == "seek" and value is not None:
            result = await _mpv.seek(value)
        elif action == "volume" and value is not None:
            result = await _mpv.set_volume(value)
        else:
            return json.dumps({"error": f"Unknown action: {action}"})

        position = await _mpv.get_position()
        duration = await _mpv.get_duration()
        return json.dumps({
            "status": _mpv.state.status,
            "success": result,
            "position": position,
            "duration": duration,
        })

    async def playback_status() -> str:
        """Get current playback status."""
        if not _mpv or not _mpv.is_running:
            return json.dumps({"status": "stopped"})

        position = await _mpv.get_position()
        duration = await _mpv.get_duration()
        return json.dumps({
            "status": _mpv.state.status,
            "title": _mpv.state.title,
            "url": _mpv.state.url,
            "position": position,
            "duration": duration,
            "volume": _mpv.state.volume,
            "format": _mpv.state.format_info,
        }, ensure_ascii=False)

    server.add_tool(play, description="Play audio from a media URL through PulseAudio.")
    server.add_tool(playback_control, description="Control current playback (pause/resume/stop/seek/volume).")
    server.add_tool(playback_status, description="Get current playback status.")
