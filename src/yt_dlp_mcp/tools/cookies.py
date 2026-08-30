from __future__ import annotations

import json
import logging

from mcp.server import MCPServer

from ..core.cookies import CookieStore

logger = logging.getLogger(__name__)


def register(server: MCPServer, store: CookieStore) -> None:

    async def set_cookies(site: str, cookies: str) -> str:
        """Set cookies for a site in Netscape format.

        Args:
            site: Site domain, e.g. youtube.com.
            cookies: Cookie text in Netscape format.
        """
        try:
            result = store.set_cookies(site, cookies)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("set_cookies failed for site=%s", site)
            return json.dumps({"error": str(e)})

    async def get_cookies(site: str) -> str:
        """Get cookie status for a site.

        Args:
            site: Site domain, e.g. youtube.com.
        """
        try:
            result = store.get_cookies(site)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("get_cookies failed for site=%s", site)
            return json.dumps({"error": str(e)})

    async def delete_cookies(site: str) -> str:
        """Delete cookies for a site.

        Args:
            site: Site domain, e.g. youtube.com.
        """
        try:
            result = store.delete_cookies(site)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.exception("delete_cookies failed for site=%s", site)
            return json.dumps({"error": str(e)})

    async def list_cookie_sites() -> str:
        """List all sites with stored cookies."""
        try:
            result = store.list_sites()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("list_cookie_sites failed")
            return json.dumps({"error": str(e)})

    server.add_tool(set_cookies, description="Set cookies for a site in Netscape format.")
    server.add_tool(get_cookies, description="Get cookie status for a site.")
    server.add_tool(delete_cookies, description="Delete cookies for a site.")
    server.add_tool(list_cookie_sites, description="List all sites with stored cookies.")
