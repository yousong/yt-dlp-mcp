from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import MCPServer

from ..core.ytdlp import YtdlpWrapper

logger = logging.getLogger(__name__)

SEARCH_PREFIXES = {
    "youtube": "ytsearch",
    "soundcloud": "scsearch",
    "bilibili": "bilisearch",
    "dailymotion": "dmsearch",
    "nicovideo": "nicosearch",
    "google": "google",
}


def register(server: MCPServer, ytdlp: YtdlpWrapper) -> None:

    async def search(query: str, source: str = "youtube", max_results: int = 10) -> str:
        """Search for media across various sources.

        Args:
            query: Search keywords.
            source: Search source: youtube (default), soundcloud, bilibili, dailymotion, nicovideo, google.
            max_results: Maximum number of results to return. Defaults to 10.
        """
        prefix = SEARCH_PREFIXES.get(source, "ytsearch")
        search_query = f"{prefix}{max_results}:{query}"
        try:
            info = ytdlp.extract_info_flat(search_query)
            entries = info.get("entries", [])
            results = []
            for entry in entries:
                results.append({
                    "title": entry.get("title"),
                    "url": entry.get("url") or entry.get("webpage_url"),
                    "id": entry.get("id"),
                    "duration": entry.get("duration"),
                    "uploader": entry.get("uploader") or entry.get("channel"),
                    "thumbnail": entry.get("thumbnail"),
                    "view_count": entry.get("view_count"),
                    "description": (entry.get("description") or "")[:200],
                })
            return json.dumps({"entries": results, "total": len(results)}, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("search failed for query=%s source=%s", query, source)
            return json.dumps({"error": str(e)})

    server.add_tool(search, description="Search for media across various sources (YouTube, SoundCloud, Bilibili, etc).")
