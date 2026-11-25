import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cache_cleanup


class CacheCleanupTests(unittest.TestCase):
    def test_scan_preserves_files_changed_or_added_after_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / "changed"
            changed.write_bytes(b"before")
            unchanged = root / "unchanged"
            unchanged.write_bytes(b"cache")
            snapshot = cache_cleanup.scan_cache("test", root)
            changed.write_bytes(b"changed after scan")
            added = root / "added"
            added.write_bytes(b"keep")
            self.assertEqual(cache_cleanup.remove(snapshot), (1, 5, 1))
            self.assertTrue(changed.exists())
            self.assertTrue(added.exists())

    def test_log_scan_excludes_running_log_and_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("old.log", "running.log", "settings.json"):
                (root / name).write_bytes(b"data")
            snapshot = cache_cleanup.scan_cache(
                "Task logs", root, kind="logs", excluded=[root / "running.log"],
            )
            self.assertEqual([path.name for path, _ in snapshot.files], ["old.log"])

    def hibernation_item(self):
        windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        path = Path(windows.anchor) / "hiberfil.sys"
        item = cache_cleanup.Cache(
            "Hibernation", path, [(path, SimpleNamespace(st_size=4096))], kind="hibernate",
        )
        return windows, item

    def test_hibernation_uses_powercfg_and_verifies_file_disappeared(self):
        windows, item = self.hibernation_item()
        with patch.object(Path, "exists", side_effect=[True, False]), \
                patch.object(cache_cleanup.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(cache_cleanup.remove(item), (1, 4096, 0))
        self.assertEqual(
            run.call_args.args[0],
            [str(windows / "System32/powercfg.exe"), "/hibernate", "off"],
        )
        self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_hibernation_reports_permission_failure(self):
        _, item = self.hibernation_item()
        with patch.object(Path, "exists", return_value=True), \
                patch.object(cache_cleanup.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=1, stderr=b"Access denied", stdout=b"")
            with self.assertRaisesRegex(OSError, "Access denied"):
                cache_cleanup.remove(item)

    def test_hibernation_retries_with_windows_storage_api(self):
        _, item = self.hibernation_item()
        with patch.object(Path, "exists", side_effect=[True, True, False]), \
                patch.object(cache_cleanup.subprocess, "run") as run, \
                patch.object(cache_cleanup, "release_hibernation_storage") as release:
            run.return_value.returncode = 0
            self.assertEqual(cache_cleanup.remove(item), (1, 4096, 0))
        release.assert_called_once_with()

    def test_hibernation_does_not_report_space_before_release(self):
        _, item = self.hibernation_item()
        with patch.object(Path, "exists", return_value=True), \
                patch.object(cache_cleanup.subprocess, "run") as run, \
                patch.object(cache_cleanup, "release_hibernation_storage"), \
                patch.object(cache_cleanup.time, "monotonic", side_effect=[0, 6]):
            run.return_value.returncode = 0
            with self.assertRaisesRegex(OSError, "not released"):
                cache_cleanup.remove(item)

    def test_hibernation_rejects_arbitrary_file(self):
        item = cache_cleanup.Cache("Hibernation", Path("personal.sys"), [], kind="hibernate")
        with patch.object(cache_cleanup.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                cache_cleanup.remove(item)
        run.assert_not_called()
