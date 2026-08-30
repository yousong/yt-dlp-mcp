from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
from typing import Any

from mcp.server import MCPServer

from ..config import Config
from ..core.mpv import MpvController
from ..core.ytdlp import YtdlpWrapper

logger = logging.getLogger(__name__)

_mpv: MpvController | None = None
_ytdlp: YtdlpWrapper | None = None
_config: Config | None = None
_yt_dlp_proc: subprocess.Popen | None = None


def _read_ytdlp_stderr(proc: subprocess.Popen) -> None:
    """Read and log yt-dlp stderr output in a background thread."""
    if not proc.stderr:
        return
    try:
        for line in iter(proc.stderr.readline, b''):
            if line:
                logger.info("yt-dlp: %s", line.decode().strip())
    except Exception as e:
        logger.debug("yt-dlp stderr reader stopped: %s", e)


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

            # Start yt-dlp with stdout=subprocess.PIPE
            _yt_dlp_proc = subprocess.Popen(
                [
                    "yt-dlp",
                    "-f", format,
                    "-o", "-",
                    "--quiet",
                    "--no-warnings",
                    *cookie_args,
                    url,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            logger.info("yt-dlp process started with PID: %d", _yt_dlp_proc.pid)

            # Wait for yt-dlp to start producing data
            await asyncio.sleep(1.0)
            
            # Check if yt-dlp is still running
            if _yt_dlp_proc.poll() is not None:
                stderr = _yt_dlp_proc.stderr.read() if _yt_dlp_proc.stderr else b""
                logger.error("yt-dlp exited early with code %d: %s", 
                           _yt_dlp_proc.returncode, stderr.decode().strip() if stderr else "no output")
                return json.dumps({"error": f"yt-dlp failed to start (exit code {_yt_dlp_proc.returncode})"})

            # Start reading yt-dlp stderr in background thread
            stderr_thread = threading.Thread(target=_read_ytdlp_stderr, args=(_yt_dlp_proc,), daemon=True)
            stderr_thread.start()

            # Start mpv and pass yt-dlp process for data copying
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
            if _yt_dlp_proc and _yt_dlp_proc.poll() is None:
                _yt_dlp_proc.terminate()
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _yt_dlp_proc.wait(timeout=5.0)
                    )
                except subprocess.TimeoutExpired:
                    _yt_dlp_proc.kill()
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
