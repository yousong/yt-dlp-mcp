from __future__ import annotations

import pytest

from yt_dlp_mcp.core.download import DownloadManager


class TestDownloadManager:

    def test_validate_subdir_valid(self):
        assert DownloadManager.validate_subdir("music") is True
        assert DownloadManager.validate_subdir("music/lofi") is True
        assert DownloadManager.validate_subdir("podcast-ep1") is True
        assert DownloadManager.validate_subdir("my_folder") is True
        assert DownloadManager.validate_subdir("音乐") is True

    def test_validate_subdir_invalid(self):
        assert DownloadManager.validate_subdir("") is False
        assert DownloadManager.validate_subdir("../etc") is False
        assert DownloadManager.validate_subdir("foo/../bar") is False
        assert DownloadManager.validate_subdir("/absolute") is False
        assert DownloadManager.validate_subdir("foo//bar") is False

    def test_list_downloads_empty(self, tmp_path):
        manager = DownloadManager(store_dir=tmp_path)
        result = manager.list_downloads()
        assert result == []

    def test_get_download_info_not_found(self, tmp_path):
        manager = DownloadManager(store_dir=tmp_path)
        result = manager.get_download_info("subdir", "nonexistent")
        assert result is None
