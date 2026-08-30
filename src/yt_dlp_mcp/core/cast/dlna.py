from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def discover_devices(timeout: float = 5.0) -> list[dict[str, Any]]:
    try:
        from async_upnp_client.search import async_search
    except ImportError:
        logger.warning("async-upnp-client not installed, DLNA discovery disabled")
        return []

    devices = []
    try:
        async def on_response(headers: dict, source: tuple) -> None:
            if "media_renderer" in headers.get("ST", "").lower() or \
               "urn:schemas-upnp-org:service:AVTransport" in headers.get("ST", ""):
                devices.append({
                    "id": headers.get("USN", source[0]),
                    "name": headers.get("SERVER", "Unknown DLNA Device"),
                    "protocol": "dlna",
                    "ip": source[0],
                    "location": headers.get("LOCATION", ""),
                })

        await async_search(
            search_target="urn:schemas-upnp-org:device:MediaRenderer:1",
            timeout=timeout,
            async_callback=on_response,
        )
    except Exception as e:
        logger.warning("DLNA discovery failed: %s", e)

    return devices


async def cast(device: dict[str, Any], media_url: str, title: str = "",
               mime_type: str = "audio/mpeg") -> dict[str, Any]:
    try:
        from async_upnp_client.client import UpnpRequester
        from async_upnp_client.client_factory import UpnpFactory
        from async_upnp_client.aiohttp import AiohttpRequester
    except ImportError:
        return {"error": "async-upnp-client not installed"}

    location = device.get("location", "")
    if not location:
        return {"error": "Device has no location"}

    try:
        requester = AiohttpRequester()
        factory = UpnpFactory(requester)
        upnp_device = await factory.async_create_device(location)

        av_transport = None
        for service in upnp_device.services.values():
            if "AVTransport" in service.service_type:
                av_transport = service
                break

        if not av_transport:
            return {"error": "Device does not support AVTransport"}

        await av_transport.async_call_action(
            "SetAVTransportURI",
            InstanceID=0,
            CurrentURI=media_url,
            CurrentURIMetaData="",
        )
        await av_transport.async_call_action("Play", InstanceID=0, Speed="1")

        return {
            "status": "playing",
            "device": device.get("name"),
            "protocol": "dlna",
            "media_url": media_url,
        }
    except Exception as e:
        return {"error": f"DLNA cast failed: {e}"}
