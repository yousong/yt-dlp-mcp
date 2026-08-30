from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CookieEntry:
    domain: str
    name: str
    value: str
    path: str = "/"
    expires: str = ""
    secure: bool = False

    def to_netscape_line(self) -> str:
        flag = "TRUE" if self.domain.startswith(".") else "FALSE"
        include = "TRUE" if self.secure else "FALSE"
        expires_ts = self.expires if self.expires else "0"
        return f"{self.domain}\t{flag}\t{self.path}\t{include}\t{expires_ts}\t{self.name}\t{self.value}"

    @classmethod
    def from_netscape_line(cls, line: str) -> CookieEntry | None:
        parts = line.strip().split("\t")
        if len(parts) < 7:
            return None
        domain, flag, path, secure, expires, name, value = parts[:7]
        return cls(
            domain=domain,
            name=name,
            value=value,
            path=path,
            expires=expires if expires != "0" else "",
            secure=secure == "TRUE",
        )


class CookieStore:
    def __init__(self, cookie_dir: str | Path):
        self._dir = Path(cookie_dir)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            logger.warning("Cannot create cookie directory %s, cookie operations will fail", self._dir)

    def _cookie_path(self, site: str) -> Path:
        safe_site = re.sub(r'[^\w.\-]', '_', site)
        return self._dir / f"{safe_site}.txt"

    def set_cookies(self, site: str, cookies_text: str) -> dict[str, Any]:
        path = self._cookie_path(site)
        path.write_text(cookies_text)
        entries = self._parse_file(path)
        return {
            "site": site,
            "cookie_count": len(entries),
            "path": str(path),
        }

    def get_cookies(self, site: str) -> dict[str, Any]:
        path = self._cookie_path(site)
        if not path.exists():
            return {"site": site, "cookies": [], "cookie_count": 0}
        entries = self._parse_file(path)
        return {
            "site": site,
            "cookies": [
                {"domain": e.domain, "name": e.name, "expires": e.expires}
                for e in entries
            ],
            "cookie_count": len(entries),
        }

    def delete_cookies(self, site: str) -> dict[str, Any]:
        path = self._cookie_path(site)
        if path.exists():
            path.unlink()
        return {"site": site, "deleted": True}

    def list_sites(self) -> dict[str, Any]:
        sites = []
        for f in sorted(self._dir.glob("*.txt")):
            site = f.stem
            entries = self._parse_file(f)
            stat = f.stat()
            sites.append({
                "site": site,
                "cookie_count": len(entries),
                "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return {"sites": sites}

    def get_cookie_file_path(self, url: str) -> str | None:
        from urllib.parse import urlparse
        host = urlparse(url).hostname
        if not host:
            return None
        for f in self._dir.glob("*.txt"):
            site = f.stem
            if host == site or host.endswith("." + site):
                return str(f)
        return None

    def _parse_file(self, path: Path) -> list[CookieEntry]:
        entries = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entry = CookieEntry.from_netscape_line(line)
            if entry:
                entries.append(entry)
        return entries
