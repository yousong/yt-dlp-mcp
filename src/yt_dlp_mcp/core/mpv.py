from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


class MpvController:
    def __init__(self, socket_path: str = "/tmp/mpv-socket", pulse_server: str = ""):
        self._socket_path = socket_path
        self._pulse_server = pulse_server
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._state = PlaybackState()
        self._request_id = 0
        self._monitor_task: asyncio.Task | None = None
        self._on_end_callback: Any = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def play(self, pipe_read_fd: int, title: str = "",
                   url: str = "", format_info: dict | None = None) -> None:
        if self.is_running:
            await self.stop()

        socket = Path(self._socket_path)
        if socket.exists():
            socket.unlink()

        env = os.environ.copy()
        if self._pulse_server:
            env["PULSE_SERVER"] = self._pulse_server

        logger.info("Starting mpv process with socket: %s", self._socket_path)
        self._process = await asyncio.create_subprocess_exec(
            "mpv",
            "--no-video",
            f"--input-ipc-server={self._socket_path}",
            "-",
            stdin=pipe_read_fd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        os.close(pipe_read_fd)
        logger.info("mpv process started with PID: %d", self._process.pid)

        # Wait a bit and check stderr for immediate errors
        await asyncio.sleep(0.2)
        if self._process.returncode is not None:
            # Process already exited, read stderr
            stderr_output = await self._process.stderr.read()
            if stderr_output:
                logger.error("mpv exited immediately with code %d: %s", 
                           self._process.returncode, stderr_output.decode().strip())
            else:
                logger.error("mpv exited immediately with code %d (no stderr output)", 
                           self._process.returncode)
            self._process = None
            self._state.status = "stopped"
            raise RuntimeError(f"mpv failed to start (exit code {self._process.returncode if self._process else 'unknown'})")

        self._state = PlaybackState(
            status="playing",
            title=title,
            url=url,
            format_info=format_info or {},
        )

        asyncio.create_task(self._read_stderr())
        asyncio.create_task(self._connect_ipc())

    async def _read_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            # Log at INFO level to see mpv errors
            logger.info("mpv: %s", line.decode().strip())

    async def _connect_ipc(self) -> None:
        socket_path = Path(self._socket_path)
        for attempt in range(50):
            await asyncio.sleep(0.1)
            
            # Check if mpv process is still running
            if self._process and self._process.returncode is not None:
                logger.error("mpv process exited with code %d before IPC connection", self._process.returncode)
                return
            
            # Check if socket exists
            if not socket_path.exists():
                if attempt % 10 == 9:  # Log every 10 attempts
                    logger.debug("mpv socket not yet created (attempt %d/50)", attempt + 1)
                continue
            
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
                logger.info("mpv IPC connected successfully")
                self._monitor_task = asyncio.create_task(self._monitor_events())
                return
            except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
                if attempt % 10 == 9:  # Log every 10 attempts
                    logger.debug("mpv IPC connection attempt %d failed: %s", attempt + 1, e)
                continue
        
        logger.error("Failed to connect to mpv IPC socket after 50 attempts. Socket exists: %s, Process running: %s",
                     socket_path.exists(),
                     self._process.returncode is None if self._process else False)

    async def _monitor_events(self) -> None:
        if not self._reader:
            return
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    data = json.loads(line)
                    if data.get("event") == "end-file":
                        self._state.status = "stopped"
                        if self._on_end_callback:
                            await self._on_end_callback()
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

    async def _send_command(self, command: list) -> dict | None:
        if not self._writer or not self._reader:
            return None
        self._request_id += 1
        msg = {"command": command, "request_id": self._request_id}
        try:
            self._writer.write((json.dumps(msg) + "\n").encode())
            await self._writer.drain()
            while True:
                line = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
                if not line:
                    return None
                data = json.loads(line)
                if data.get("request_id") == self._request_id:
                    return data
        except (asyncio.TimeoutError, ConnectionResetError, json.JSONDecodeError) as e:
            logger.warning("mpv IPC command failed: %s", e)
            return None

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
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None
        if self._writer:
            self._writer.close()
            self._writer = None
        self._reader = None
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
        self._process = None
        self._state.status = "stopped"

    async def cleanup(self) -> None:
        await self.stop()
        socket = Path(self._socket_path)
        if socket.exists():
            socket.unlink()
