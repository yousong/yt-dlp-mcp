from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)

DEFAULT_FORMAT = "bestaudio/best"


class YtdlpWrapper:
    def __init__(self, cookie_dir: str | Path | None = None):
        self._cookie_dir = Path(cookie_dir) if cookie_dir else None

    def _resolve_cookie_file(self, url: str) -> str | None:
        if not self._cookie_dir or not self._cookie_dir.is_dir():
            return None
        from urllib.parse import urlparse

        host = urlparse(url).hostname
        if not host:
            return None
        for cookie_file in self._cookie_dir.glob("*.txt"):
            site = cookie_file.stem
            if host == site or host.endswith("." + site):
                return str(cookie_file)
        return None

    def _base_opts(self, url: str, **overrides: Any) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
        }
        cookie_file = self._resolve_cookie_file(url)
        if cookie_file:
            opts["cookiefile"] = cookie_file
        opts.update(overrides)
        return opts

    def extract_info(self, url: str, *, format: str = DEFAULT_FORMAT, **extra_opts: Any) -> dict[str, Any]:
        opts = self._base_opts(url, format=format, **extra_opts)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info) if info else {}

    def extract_info_flat(self, search_query: str, **extra_opts: Any) -> dict[str, Any]:
        opts = self._base_opts(search_query, extract_flat=True, **extra_opts)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            return ydl.sanitize_info(info) if info else {}

    def download(self, urls: list[str], *, outtmpl: str, format: str = DEFAULT_FORMAT,
                 writeinfojson: bool = True, writethumbnail: bool = True,
                 playlist_items: str | None = None,
                 progress_hook: Any = None, **extra_opts: Any) -> int:
        opts = self._base_opts(urls[0] if urls else "", format=format,
                               outtmpl=outtmpl, writeinfojson=writeinfojson,
                               writethumbnail=writethumbnail, **extra_opts)
        if playlist_items:
            opts["playlist_items"] = playlist_items
        if progress_hook:
            opts["progress_hooks"] = [progress_hook]
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.download(urls)

    def list_formats(self, url: str) -> list[dict[str, Any]]:
        info = self.extract_info(url)
        formats = info.get("formats", [])
        result = []
        for f in formats:
            result.append({
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "acodec": f.get("acodec"),
                "vcodec": f.get("vcodec"),
                "abr": f.get("abr"),
                "vbr": f.get("vbr"),
                "asr": f.get("asr"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "protocol": f.get("protocol"),
                "format_note": f.get("format_note"),
            })
        return result
