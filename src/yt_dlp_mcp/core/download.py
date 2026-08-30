from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUBDIR_PATTERN = re.compile(r'^[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]*(/[\w\u4e00-\u9fff][\w\u4e00-\u9fff\-]*)*$')


class TaskStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


SPONSORBLOCK_DEFAULT_CATEGORIES = "sponsor,selfpromo,interaction,intro,outro,preview"


@dataclass
class DownloadTask:
    task_id: str
    url: str
    subdir: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    files: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class DownloadManager:
    def __init__(self, store_dir: str | Path, cookie_dir: str | Path | None = None):
        self._store_dir = Path(store_dir)
        self._cookie_dir = Path(cookie_dir) if cookie_dir else None
        self._tasks: dict[str, DownloadTask] = {}
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            logger.warning("Cannot create store directory %s", self._store_dir)

    @staticmethod
    def validate_subdir(subdir: str) -> bool:
        if not subdir:
            return False
        if ".." in subdir:
            return False
        return bool(SUBDIR_PATTERN.match(subdir))

    def _target_dir(self, subdir: str) -> Path:
        return self._store_dir / subdir

    async def start_download(self, url: str, subdir: str, format: str = "bestaudio/best",
                             playlist_items: str | None = None,
                             sponsorblock: str | list[str] | None = "default") -> str:
        if not self.validate_subdir(subdir):
            raise ValueError(f"Invalid subdir: {subdir}")

        task_id = uuid.uuid4().hex[:12]
        task = DownloadTask(task_id=task_id, url=url, subdir=subdir)
        self._tasks[task_id] = task

        asyncio.create_task(self._run_download(task, format, playlist_items, sponsorblock))
        return task_id

    async def _run_download(self, task: DownloadTask, format: str,
                            playlist_items: str | None,
                            sponsorblock: str | list[str] | None = "default") -> None:
        task.status = TaskStatus.DOWNLOADING
        target_dir = self._target_dir(task.subdir)
        target_dir.mkdir(parents=True, exist_ok=True)

        outtmpl = str(target_dir / "%(title)s-%(id)s.%(ext)s")

        import subprocess

        env_args = []
        if self._cookie_dir and self._cookie_dir.is_dir():
            from urllib.parse import urlparse
            host = urlparse(task.url).hostname
            if host:
                for f in self._cookie_dir.glob("*.txt"):
                    site = f.stem
                    if host == site or host.endswith("." + site):
                        env_args = ["--cookies", str(f)]
                        break

        cmd = [
            "yt-dlp",
            "-f", format,
            "-o", outtmpl,
            "--write-info-json",
            "--write-thumbnail",
            "--quiet",
            "--no-warnings",
            *env_args,
        ]
        if playlist_items:
            cmd.extend(["--playlist-items", playlist_items])
        if sponsorblock is not None:
            if sponsorblock == "default":
                cats = SPONSORBLOCK_DEFAULT_CATEGORIES
            elif isinstance(sponsorblock, list):
                cats = ",".join(sponsorblock)
            else:
                cats = str(sponsorblock)
            if cats:
                cmd.extend(["--sponsorblock-remove", cats])
        cmd.append(task.url)

        def progress_hook(d: dict) -> None:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    task.progress = round(downloaded / total * 100, 1)
            elif d["status"] == "finished":
                task.progress = 100.0

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                task.status = TaskStatus.FAILED
                task.error = stderr.decode(errors="replace").strip()[-500:]
                return

            task.status = TaskStatus.COMPLETED
            task.progress = 100.0
            task.files = self._scan_files(target_dir)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)

    def _scan_files(self, directory: Path) -> list[dict[str, Any]]:
        files = []
        for f in sorted(directory.iterdir()):
            if f.is_file():
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "has_info_json": False,
                    "downloaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
        for f in files:
            info_path = directory / (Path(f["name"]).stem + ".info.json")
            if info_path.exists():
                f["has_info_json"] = True
        return files

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def list_downloads(self, subdir: str | None = None) -> list[dict[str, Any]]:
        results = []
        dirs_to_scan = [self._target_dir(subdir)] if subdir else [
            d for d in self._store_dir.iterdir() if d.is_dir()
        ] if self._store_dir.exists() else []

        for d in dirs_to_scan:
            if not d.exists():
                continue
            rel = str(d.relative_to(self._store_dir))
            items = []
            for f in sorted(d.iterdir()):
                if f.is_file() and not f.name.endswith(".info.json") and not f.name.endswith(".jpg"):
                    info_path = d / (f.stem + ".info.json")
                    stat = f.stat()
                    items.append({
                        "name": f.name,
                        "size": stat.st_size,
                        "has_info_json": info_path.exists(),
                        "downloaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
            results.append({"subdir": rel, "files": items})
        return results

    def get_download_info(self, subdir: str, filename: str) -> dict[str, Any] | None:
        target_dir = self._target_dir(subdir)
        info_path = target_dir / f"{filename}.info.json"
        if not info_path.exists():
            return None
        return json.loads(info_path.read_text())
