from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import MCPServer

from ..core.cast import airplay, chromecast, dlna
from ..core.ytdlp import YtdlpWrapper

logger = logging.getLogger(__name__)

_device_cache: dict[str, dict[str, Any]] = {}


def register(server: MCPServer, ytdlp: YtdlpWrapper) -> None:

    async def list_devices(protocol: str = "all", timeout: float = 5.0) -> str:
        """Discover casting devices on the local network.

        Args:
            protocol: Filter by protocol: dlna, airplay, chromecast, or all (default).
            timeout: Discovery timeout in seconds. Defaults to 5.
        """
        global _device_cache
        _device_cache.clear()

        tasks = []
        if protocol in ("all", "dlna"):
            tasks.append(dlna.discover_devices(timeout=timeout))
        if protocol in ("all", "airplay"):
            tasks.append(airplay.discover_devices(timeout=timeout))
        if protocol in ("all", "chromecast"):
            tasks.append(chromecast.discover_devices(timeout=timeout))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        devices = []
        for r in results:
            if isinstance(r, list):
                for d in r:
                    dev_id = str(d.get("id", ""))
                    _device_cache[dev_id] = d
                    devices.append(d)

        return json.dumps({"devices": devices, "total": len(devices)}, ensure_ascii=False, indent=2)

    async def cast(url: str, device_id: str, format: str = "bestaudio/best") -> str:
        """Cast media to a device on the local network.

        Args:
            url: Media page URL to cast.
            device_id: Target device ID (from list_devices).
            format: Format selection for extracting the media URL.
        """
        try:
            info = ytdlp.extract_info(url, format=format)
            media_url = info.get("url")
            title = info.get("title", "")

            if not media_url:
                return json.dumps({"error": "Could not extract media URL from the given URL"})

            device = _device_cache.get(str(device_id))
            if not device:
                return json.dumps({"error": f"Device not found: {device_id}. Run list_devices first."})

            proto = device.get("protocol", "")
            if proto == "dlna":
                result = await dlna.cast(device, media_url, title=title)
            elif proto == "airplay":
                result = await airplay.cast(device, media_url, title=title)
            elif proto == "chromecast":
                result = await chromecast.cast(device, media_url, title=title)
            else:
                return json.dumps({"error": f"Unknown protocol: {proto}"})

            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("cast failed for url=%s device=%s", url, device_id)
            return json.dumps({"error": str(e)})

    server.add_tool(list_devices, description="Discover casting devices on the local network (DLNA/AirPlay/Chromecast).")
    server.add_tool(cast, description="Cast media to a device on the local network.")
