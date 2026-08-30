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
from ..core.mpv import MpvController, PlaybackQueue
from ..core.ytdlp import YtdlpWrapper

logger = logging.getLogger(__name__)

_mpv: MpvController | None = None
_ytdlp: YtdlpWrapper | None = None
_config: Config | None = None
_yt_dlp_proc: subprocess.Popen | None = None
_format: str = "bestaudio/best"


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

    async def _play_single_url(url: str, fmt: str) -> dict[str, Any]:
        global _yt_dlp_proc, _format
        _format = fmt

        info = _ytdlp.extract_info(url, format=fmt)
        title = info.get("title", "Unknown")

        env = os.environ.copy()
        if _config and _config.pulse_server:
            env["PULSE_SERVER"] = _config.pulse_server

        cookie_args = []
        cookie_file = _ytdlp._resolve_cookie_file(url)
        if cookie_file:
            cookie_args = ["--cookies", cookie_file]

        _yt_dlp_proc = subprocess.Popen(
            [
                "yt-dlp",
                "-f", fmt,
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

        await asyncio.sleep(1.0)

        if _yt_dlp_proc.poll() is not None:
            stderr = _yt_dlp_proc.stderr.read() if _yt_dlp_proc.stderr else b""
            logger.error("yt-dlp exited early with code %d: %s",
                         _yt_dlp_proc.returncode, stderr.decode().strip() if stderr else "no output")
            raise RuntimeError(f"yt-dlp failed to start (exit code {_yt_dlp_proc.returncode})")

        stderr_thread = threading.Thread(target=_read_ytdlp_stderr, args=(_yt_dlp_proc,), daemon=True)
        stderr_thread.start()

        await _mpv.play(_yt_dlp_proc, title=title, url=url, format_info={
            "format_id": info.get("format_id"),
            "ext": info.get("ext"),
            "acodec": info.get("acodec"),
            "abr": info.get("abr"),
        })

        return {
            "title": title,
            "duration": info.get("duration"),
            "format_info": _mpv.state.format_info,
        }

    async def _on_next_url(url: str) -> None:
        try:
            await _play_single_url(url, _format)
        except Exception as e:
            logger.exception("auto-play next failed for %s", url)

    async def play(urls: list[str], format: str = "bestaudio/best", 
                   volume: int | None = None, loop: bool = False) -> str:
        """Play audio from one or more media URLs through PulseAudio.

        Args:
            urls: List of media page URLs to play. Single URL for one track, multiple for queue playback.
            format: yt-dlp format string for selecting media format. Defaults to bestaudio/best.
            volume: Volume level 0-100. If not specified, keeps current volume.
            loop: Whether to loop the queue. Defaults to false.
        """
        global _mpv
        if not _mpv:
            return json.dumps({"error": "Playback engine not initialized"})

        if not urls:
            return json.dumps({"error": "No URLs provided"})

        try:
            queue = PlaybackQueue(urls=urls, loop=loop)
            _mpv.set_queue(queue)
            _mpv.set_on_next_callback(_on_next_url)

            result = await _play_single_url(urls[0], format)

            if volume is not None:
                await _mpv.set_volume(float(volume))

            response = {
                "status": "playing",
                "queue_id": queue.queue_id,
                "total": queue.total,
                "current_index": 0,
                "loop": loop,
                "current": {
                    "title": result["title"],
                    "url": urls[0],
                    "duration": result["duration"],
                },
                "format_info": result["format_info"],
            }
            if queue.total > 1:
                response["next"] = {"url": urls[1]} if queue.total > 1 else None

            return json.dumps(response, ensure_ascii=False)
        except Exception as e:
            logger.exception("play failed")
            return json.dumps({"error": str(e)})

    async def playback_control(action: str, value: float | None = None) -> str:
        """Control current playback.

        Args:
            action: Control action: pause, resume, stop, seek, volume, next, previous, queue_status.
            value: For seek: position in seconds. For volume: level 0-100.
        """
        if not _mpv or not _mpv.is_running:
            if action != "queue_status":
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
        elif action == "next":
            result = await _mpv.play_next()
        elif action == "previous":
            result = await _mpv.play_previous()
        elif action == "queue_status":
            queue = _mpv.queue if _mpv else None
            if not queue:
                return json.dumps({"queue": None, "status": "no_queue"})
            return json.dumps({
                "queue": {
                    "queue_id": queue.queue_id,
                    "total": queue.total,
                    "current_index": queue.current_index,
                    "current_url": queue.current_url,
                    "has_next": queue.has_next,
                    "has_previous": queue.has_previous,
                    "loop": queue.loop,
                },
                "status": _mpv.state.status if _mpv else "unknown",
            }, ensure_ascii=False)
        else:
            return json.dumps({"error": f"Unknown action: {action}"})

        position = await _mpv.get_position()
        duration = await _mpv.get_duration()
        response = {
            "status": _mpv.state.status,
            "success": result,
            "position": position,
            "duration": duration,
        }
        queue = _mpv.queue
        if queue:
            response["queue_index"] = queue.current_index
            response["queue_total"] = queue.total
        return json.dumps(response)

    async def playback_status() -> str:
        """Get current playback status."""
        if not _mpv or not _mpv.is_running:
            return json.dumps({"status": "stopped"})

        position = await _mpv.get_position()
        duration = await _mpv.get_duration()
        response = {
            "status": _mpv.state.status,
            "title": _mpv.state.title,
            "url": _mpv.state.url,
            "position": position,
            "duration": duration,
            "volume": _mpv.state.volume,
            "format": _mpv.state.format_info,
        }
        queue = _mpv.queue
        if queue:
            response["queue_id"] = queue.queue_id
            response["queue_index"] = queue.current_index
            response["queue_total"] = queue.total
            response["loop"] = queue.loop
        return json.dumps(response, ensure_ascii=False)

    server.add_tool(play, description="Play audio from one or more media URLs through PulseAudio. Supports queue playback with multiple URLs.")
    server.add_tool(playback_control, description="Control current playback (pause/resume/stop/seek/volume/next/previous/queue_status).")
    server.add_tool(playback_status, description="Get current playback status.")
