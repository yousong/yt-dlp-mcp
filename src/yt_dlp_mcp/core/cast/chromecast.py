from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def discover_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    try:
        import pychromecast
    except ImportError:
        logger.warning("pychromecast not installed, Chromecast discovery disabled")
        return []

    devices = []
    try:
        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        for cc in chromecasts:
            devices.append({
                "id": cc.uuid,
                "name": cc.name,
                "protocol": "chromecast",
                "ip": str(cc.host),
                "port": cc.port,
                "model": cc.model_name,
            })
        pychromecast.discovery.stop_discovery(browser)
    except Exception as e:
        logger.warning("Chromecast discovery failed: %s", e)

    return devices


async def cast(device: dict[str, Any], media_url: str, title: str = "",
               mime_type: str = "audio/mpeg") -> dict[str, Any]:
    try:
        import pychromecast
    except ImportError:
        return {"error": "pychromecast not installed"}

    device_ip = device.get("ip", "")
    device_port = device.get("port", 8009)
    device_name = device.get("name", "")

    if not device_ip:
        return {"error": "Missing device ip"}

    try:
        def _cast_sync() -> dict[str, Any]:
            cast = pychromecast.Chromecast(device_ip, port=device_port)
            cast.wait()
            mc = cast.media_controller
            mc.play_media(media_url, mime_type, title=title or "yt-dlp-mcp")
            mc.block_until_active(timeout=10)
            return {
                "status": "playing",
                "device": device_name or cast.name,
                "protocol": "chromecast",
                "media_url": media_url,
            }

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _cast_sync)
        return result
    except Exception as e:
        return {"error": f"Chromecast cast failed: {e}"}
