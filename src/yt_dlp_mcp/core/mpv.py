from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class PlaybackState:
    status: str = "stopped"
    title: str = ""
    url: str = ""
    position: float = 0.0
    duration: float = 0.0
    volume: float = 100.0
    format_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlaybackQueue:
    urls: list[str] = field(default_factory=list)
    current_index: int = 0
    queue_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    loop: bool = False

    @property
    def total(self) -> int:
        return len(self.urls)

    @property
    def current_url(self) -> str | None:
        if 0 <= self.current_index < len(self.urls):
            return self.urls[self.current_index]
        return None

    @property
    def has_next(self) -> bool:
        if self.loop and self.total > 0:
            return True
        return self.current_index < self.total - 1

    @property
    def has_previous(self) -> bool:
        if self.loop and self.total > 0:
            return True
        return self.current_index > 0


class MpvController:
    def __init__(self, socket_path: str = "/tmp/mpv-socket", pulse_server: str = "", audio_device: str = ""):
        self._socket_path = socket_path
        self._pulse_server = pulse_server
        self._audio_device = audio_device
        self._popen: subprocess.Popen | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._state = PlaybackState()
        self._request_id = 0
        self._reader_task: asyncio.Task | None = None
        self._pending_responses: dict[int, asyncio.Future] = {}
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._queue: PlaybackQueue | None = None
        self._on_next_callback: Callable[[str], Awaitable[None]] | None = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def is_running(self) -> bool:
        if self._popen is None:
            return False
        return self._popen.poll() is None

    @property
    def queue(self) -> PlaybackQueue | None:
        return self._queue

    def set_queue(self, queue: PlaybackQueue) -> None:
        self._queue = queue

    def set_on_next_callback(self, callback: Callable[[str], Awaitable[None]] | None) -> None:
        self._on_next_callback = callback

    async def play_next(self) -> bool:
        if not self._queue or not self._queue.has_next:
            return False
        if self._queue.current_index < self._queue.total - 1:
            self._queue.current_index += 1
        elif self._queue.loop:
            self._queue.current_index = 0
        else:
            return False
        url = self._queue.current_url
        if url and self._on_next_callback:
            await self._on_next_callback(url)
            return True
        return False

    async def play_previous(self) -> bool:
        if not self._queue or not self._queue.has_previous:
            return False
        if self._queue.current_index > 0:
            self._queue.current_index -= 1
        elif self._queue.loop:
            self._queue.current_index = self._queue.total - 1
        else:
            return False
        url = self._queue.current_url
        if url and self._on_next_callback:
            await self._on_next_callback(url)
            return True
        return False

    def clear_queue(self) -> None:
        self._queue = None

    async def play(self, ytdlp_proc: subprocess.Popen, title: str = "",
                   url: str = "", format_info: dict | None = None) -> None:
        if self.is_running:
            await self.stop()

        socket = Path(self._socket_path)
        if socket.exists():
            socket.unlink()

        env = os.environ.copy()
        if self._pulse_server:
            env["PULSE_SERVER"] = self._pulse_server

        cmd = [
            "mpv",
            "--no-video",
            f"--input-ipc-server={self._socket_path}",
        ]
        if self._audio_device:
            cmd.append(f"--audio-device={self._audio_device}")
        cmd.append("-")

        logger.info("Starting mpv: %s", " ".join(cmd))

        self._stderr_lines.clear()
        self._popen = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )

        stderr_thread = threading.Thread(target=self._collect_stderr, daemon=True)
        stderr_thread.start()

        copy_thread = threading.Thread(
            target=self._copy_data, args=(ytdlp_proc,), daemon=True
        )
        copy_thread.start()

        await asyncio.sleep(0.5)
        if self._popen.poll() is not None:
            exit_code = self._popen.returncode
            stderr_text = "\n".join(self._stderr_lines) or "no output"
            logger.error("mpv exited with code %d: %s", exit_code, stderr_text)
            self._popen = None
            self._state.status = "stopped"
            raise RuntimeError(f"mpv failed to start (exit code {exit_code}): {stderr_text}")

        logger.info("mpv running with PID: %d", self._popen.pid)

        self._state = PlaybackState(
            status="playing",
            title=title,
            url=url,
            format_info=format_info or {},
        )

        await self._connect_ipc()
        asyncio.create_task(self._monitor_process())

    def _collect_stderr(self) -> None:
        if not self._popen or not self._popen.stderr:
            return
        try:
            for line in iter(self._popen.stderr.readline, b''):
                if line:
                    text = line.decode(errors="replace").strip()
                    with self._stderr_lock:
                        self._stderr_lines.append(text)
                    logger.info("mpv: %s", text)
        except Exception:
            pass

    def _copy_data(self, ytdlp_proc: subprocess.Popen) -> None:
        try:
            if not ytdlp_proc.stdout or not self._popen or not self._popen.stdin:
                return
            total = 0
            while True:
                chunk = ytdlp_proc.stdout.read(8192)
                if not chunk:
                    break
                try:
                    self._popen.stdin.write(chunk)
                    self._popen.stdin.flush()
                    total += len(chunk)
                except BrokenPipeError:
                    break
            logger.info("data copy done, %d bytes total", total)
        except Exception as e:
            logger.error("data copy error: %s", e)
        finally:
            if self._popen and self._popen.stdin:
                try:
                    self._popen.stdin.close()
                except Exception:
                    pass

    async def _monitor_process(self) -> None:
        loop = asyncio.get_event_loop()
        while self._popen and self._popen.poll() is None:
            await asyncio.sleep(0.5)
        if self._popen:
            logger.info("mpv exited with code %d", self._popen.returncode)
        self._state.status = "stopped"

    async def _connect_ipc(self) -> None:
        socket_path = Path(self._socket_path)
        for attempt in range(50):
            await asyncio.sleep(0.1)
            if self._popen and self._popen.poll() is not None:
                logger.error("mpv exited before IPC connection")
                return
            if not socket_path.exists():
                continue
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
                logger.info("mpv IPC connected")
                self._reader_task = asyncio.create_task(self._reader_loop())
                return
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                continue
        logger.error("Failed to connect to mpv IPC socket")

    async def _reader_loop(self) -> None:
        if not self._reader:
            return
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    data = json.loads(line)
                    request_id = data.get("request_id")
                    if request_id is not None and request_id in self._pending_responses:
                        self._pending_responses[request_id].set_result(data)
                    else:
                        if data.get("event") == "end-file":
                            reason = data.get("reason", "")
                            if reason == "eof" and self._queue and self._queue.has_next:
                                asyncio.create_task(self._auto_play_next())
                            else:
                                self._state.status = "stopped"
                        elif data.get("event") == "playback-restart":
                            self._state.status = "playing"
                        elif data.get("event") == "pause":
                            self._state.status = "paused"
                except json.JSONDecodeError:
                    pass
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._state.status = "stopped"
            for future in self._pending_responses.values():
                if not future.done():
                    future.set_result(None)
            self._pending_responses.clear()

    async def _auto_play_next(self) -> None:
        await self.play_next()

    async def _send_command(self, command: list) -> dict | None:
        if not self._writer or not self._reader:
            return None
        self._request_id += 1
        msg = {"command": command, "request_id": self._request_id}
        future = asyncio.get_event_loop().create_future()
        self._pending_responses[self._request_id] = future
        try:
            self._writer.write((json.dumps(msg) + "\n").encode())
            await self._writer.drain()
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except (asyncio.TimeoutError, ConnectionResetError, json.JSONDecodeError) as e:
            logger.warning("mpv IPC command failed: %s", e)
            return None
        finally:
            self._pending_responses.pop(self._request_id, None)

    async def pause(self) -> bool:
        if not self.is_running:
            return False
        result = await self._send_command(["set_property", "pause", True])
        if result and result.get("error") == "success":
            self._state.status = "paused"
            return True
        return False

    async def resume(self) -> bool:
        if not self.is_running:
            return False
        result = await self._send_command(["set_property", "pause", False])
        if result and result.get("error") == "success":
            self._state.status = "playing"
            return True
        return False

    async def seek(self, position: float) -> bool:
        if not self.is_running:
            return False
        result = await self._send_command(["seek", position, "absolute"])
        if result and result.get("error") == "success":
            self._state.position = position
            return True
        return False

    async def set_volume(self, volume: float) -> bool:
        if not self.is_running:
            return False
        result = await self._send_command(["set_property", "volume", volume])
        if result and result.get("error") == "success":
            self._state.volume = volume
            return True
        return False

    async def get_position(self) -> float:
        if not self.is_running:
            return 0.0
        result = await self._send_command(["get_property", "time-pos"])
        if result and result.get("error") == "success":
            self._state.position = result.get("data", 0.0)
        return self._state.position

    async def get_duration(self) -> float:
        if not self.is_running:
            return 0.0
        result = await self._send_command(["get_property", "duration"])
        if result and result.get("error") == "success":
            self._state.duration = result.get("data", 0.0)
        return self._state.duration

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._writer:
            self._writer.close()
            self._writer = None
        self._reader = None
        if self._popen and self._popen.poll() is None:
            self._popen.terminate()
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._popen.wait(timeout=5.0)
                )
            except subprocess.TimeoutExpired:
                self._popen.kill()
        self._popen = None
        self._state.status = "stopped"
        self.clear_queue()

    async def cleanup(self) -> None:
        await self.stop()
        socket = Path(self._socket_path)
        if socket.exists():
            socket.unlink()
