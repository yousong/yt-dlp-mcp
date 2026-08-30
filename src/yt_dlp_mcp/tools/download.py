from __future__ import annotations

import json
import logging

from mcp.server import MCPServer

from ..core.download import DownloadManager

logger = logging.getLogger(__name__)


def register(server: MCPServer, manager: DownloadManager) -> None:

    async def download(url: str, subdir: str, format: str = "bestaudio/best",
                       playlist_items: str | None = None,
                       sponsorblock: str | list[str] | None = "default") -> str:
        """Download media to the store directory.

        Args:
            url: Media page URL (supports single video or playlist).
            subdir: Subdirectory name within the store directory.
            format: yt-dlp format string. Defaults to bestaudio/best.
            playlist_items: Playlist item selection, e.g. "1,3,5-10".
            sponsorblock: SponsorBlock categories to remove. "default" removes
                sponsor, selfpromo, interaction, intro, outro, preview.
                None disables. Or pass a list of categories.
        """
        try:
            task_id = await manager.start_download(url, subdir, format, playlist_items, sponsorblock)
            return json.dumps({"task_id": task_id, "status": "pending", "subdir": subdir})
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            logger.exception("download failed for url=%s subdir=%s", url, subdir)
            return json.dumps({"error": str(e)})

    async def download_status(task_id: str) -> str:
        """Query download task status.

        Args:
            task_id: Download task ID returned by the download tool.
        """
        task = manager.get_task(task_id)
        if not task:
            return json.dumps({"error": f"Task not found: {task_id}"})
        result = {
            "task_id": task.task_id,
            "status": task.status.value,
            "progress": task.progress,
            "subdir": task.subdir,
            "url": task.url,
        }
        if task.files:
            result["files"] = task.files
        if task.error:
            result["error"] = task.error
        return json.dumps(result, ensure_ascii=False, indent=2)

    async def list_downloads(subdir: str | None = None) -> str:
        """List downloaded content in the store directory.

        Args:
            subdir: Filter by subdirectory. If not specified, lists all.
        """
        try:
            items = manager.list_downloads(subdir)
            return json.dumps({"items": items}, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("list_downloads failed")
            return json.dumps({"error": str(e)})

    async def get_download_info(subdir: str, filename: str) -> str:
        """Read metadata info.json for a downloaded item.

        Args:
            subdir: Subdirectory name.
            filename: Filename without extension.
        """
        try:
            info = manager.get_download_info(subdir, filename)
            if info is None:
                return json.dumps({"error": f"Info not found: {subdir}/{filename}"})
            return json.dumps(info, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("get_download_info failed")
            return json.dumps({"error": str(e)})

    server.add_tool(download, description="Download media to the store directory.")
    server.add_tool(download_status, description="Query download task status.")
    server.add_tool(list_downloads, description="List downloaded content in the store directory.")
    server.add_tool(get_download_info, description="Read metadata info.json for a downloaded item.")
