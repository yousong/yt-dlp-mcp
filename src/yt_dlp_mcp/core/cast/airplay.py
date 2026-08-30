from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def discover_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    try:
        from pyatv import scan as atv_scan
        from pyatv.const import Protocol
    except ImportError:
        logger.warning("pyatv not installed, AirPlay discovery disabled")
        return []

    devices = []
    try:
        results = await atv_scan(loop=asyncio.get_event_loop(), timeout=timeout, protocol=Protocol.AirPlay)
        for atv in results:
            devices.append({
                "id": atv.identifier,
                "name": atv.name,
                "protocol": "airplay",
                "ip": str(atv.address),
                "deep_sleep": atv.deep_sleep,
            })
    except Exception as e:
        logger.warning("AirPlay discovery failed: %s", e)

    return devices


async def cast(device: dict[str, Any], media_url: str, title: str = "") -> dict[str, Any]:
    try:
        from pyatv import connect
        from pyatv.conf import ManualService
        from pyatv.const import Protocol
    except ImportError:
        return {"error": "pyatv not installed"}

    device_id = device.get("id", "")
    device_name = device.get("name", "")
    device_ip = device.get("ip", "")

    if not device_id or not device_ip:
        return {"error": "Missing device id or ip"}

    try:
        from pyatv.conf import AppleTV
        conf = AppleTV(device_ip, device_name)
        conf.add_service(ManualService(device_id, Protocol.AirPlay, 7000, {}))

        atv = await connect(conf, loop=asyncio.get_event_loop())
        try:
            if atv.audio and hasattr(atv.audio, "set_volume"):
                pass
            await atv.streaming.play_url(media_url)
            return {
                "status": "playing",
                "device": device_name,
                "protocol": "airplay",
                "media_url": media_url,
            }
        finally:
            await atv.close()
    except Exception as e:
        return {"error": f"AirPlay cast failed: {e}"}
