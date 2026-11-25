"""Scan known rebuildable caches for the current Windows user."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import psutil
import json
import subprocess
import time


@dataclass
class Cache:
    name: str
    path: Path
    files: list
    skipped: int = 0
    kind: str = "cache"
    restore_id: int | None = None

    @property
    def size(self):
        return sum(info.st_size for _, info in self.files)


def fixed_drives():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    mask = kernel.GetLogicalDrives()
    return [Path(f"{chr(65 + n)}:\\") for n in range(26)
            if mask & (1 << n) and kernel.GetDriveTypeW(f"{chr(65 + n)}:\\") == 3]


def plain_path(path):
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return False
        if not stat.S_ISDIR(info.st_mode):
            return False
    return True


def cache_roots():
    local = Path(os.environ["LOCALAPPDATA"])
    profile = Path(os.environ["USERPROFILE"])
    roots = [local]
    for drive in fixed_drives():
        # Only the current user's standard profile layout is recognized.
        roots.append(drive / "Users" / profile.name / "AppData" / "Local")
    seen = set()
    for root in roots:
        identity = os.path.normcase(str(root))
        if identity in seen:
            continue
        seen.add(identity)
        try:
            if not plain_path(root):
                continue
            for name, relative in (
                ("DirectX", "D3DSCache"),
                ("NVIDIA", "NVIDIA/DXCache"),
                ("NVIDIA", "NVIDIA/GLCache"),
                ("AMD", "AMD/DxCache"),
                ("AMD", "AMD/GLCache"),
            ):
                yield name, root / relative
            for browser, relative in (
                ("Chrome", "Google/Chrome/User Data"),
                ("Edge", "Microsoft/Edge/User Data"),
            ):
                base = root / relative
                if not base.is_dir() or not plain_path(base):
                    continue
                for child in base.iterdir():
                    if child.name != "Default" and not child.name.startswith("Profile "):
                        continue
                    for folder in ("Cache", "Code Cache", "GPUCache"):
                        yield browser, child / folder
            firefox = root / "Mozilla/Firefox/Profiles"
            if firefox.is_dir() and plain_path(firefox):
                for child in firefox.iterdir():
                    yield "Firefox", child / "cache2"
        except OSError:
            continue


def scan_cache(name, root, *, kind="cache", excluded=()):
    cache = Cache(name, root, [], kind=kind)
    excluded = {os.path.normcase(str(Path(path).absolute())) for path in excluded}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            if not plain_path(directory):
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        info = Path(entry.path).lstat()
                        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode):
                            if os.path.normcase(str(Path(entry.path).absolute())) in excluded:
                                continue
                            if kind == "logs" and not entry.name.lower().endswith((".log", ".log.bak", ".log.gz")):
                                continue
                            if kind == "dumps" and not entry.name.lower().endswith(".dmp"):
                                continue
                            if kind == "prefetch" and not entry.name.lower().endswith(".pf"):
                                continue
                            cache.files.append((Path(entry.path), info))
                    except OSError:
                        cache.skipped += 1
        except FileNotFoundError:
            continue
        except OSError:
            cache.skipped += 1
    return cache


def scan_restore_points():
    windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = windows / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$ErrorActionPreference='Stop';"
        "@(Get-CimInstance -Namespace root/default -ClassName SystemRestore | "
        "Select-Object SequenceNumber,Description,CreationTime) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
    records = json.loads(result.stdout.decode("utf-8-sig") or "[]")
    if isinstance(records, dict):
        records = [records]
    return [
        Cache(f"{record['SequenceNumber']} — {record['Description']} — {record['CreationTime']}",
              windows, [], kind="restore", restore_id=int(record["SequenceNumber"]))
        for record in records
    ]


def scan(*, drive=None, include_optional=False, log_directory=None, excluded=(), include_system_optional=False):
    results = [cache for name, path in cache_roots() if drive is None or path.drive.upper() == drive.upper()
               if (cache := scan_cache(name, path)).files or cache.skipped]
    if include_optional:
        optional = []
        if log_directory is not None:
            optional.append(("Task logs", Path(log_directory), "logs"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            optional.append(("Crash dumps", Path(local) / "CrashDumps", "dumps"))
        windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        optional.append(("Crash dumps", windows / "Minidump", "dumps"))
        for name, path, kind in optional:
            if drive is not None and path.drive.upper() != drive.upper():
                continue
            cache = scan_cache(name, path, kind=kind, excluded=excluded)
            if cache.files or cache.skipped:
                results.append(cache)
        try:
            if (drive is None or windows.drive.upper() == drive.upper()) and plain_path(windows):
                dump = windows / "MEMORY.DMP"
                info = dump.lstat()
                if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    results.append(Cache("Crash dumps", dump, [(dump, info)], kind="dumps"))
        except OSError:
            pass
    if include_system_optional:
        windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        hibernation = Path(windows.anchor) / "hiberfil.sys"
        try:
            if plain_path(hibernation.parent):
                info = hibernation.lstat()
                if stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    results.append(Cache("Hibernation", hibernation, [(hibernation, info)], kind="hibernate"))
        except FileNotFoundError:
            pass
        except OSError as exc:
            results.append(Cache(str(exc), hibernation, [], kind="notice"))
        prefetch = scan_cache("Prefetch", windows / "Prefetch", kind="prefetch")
        if prefetch.files or prefetch.skipped:
            results.append(prefetch)
        try:
            results.extend(scan_restore_points())
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            results.append(Cache(str(exc), windows, [], kind="notice"))
    return results


def release_hibernation_storage():
    """Ask Windows to release storage reserved for the hibernation image."""
    import ctypes
    from ctypes import wintypes
    release = ctypes.WinDLL("PowrProf.dll").CallNtPowerInformation
    release.argtypes = [
        ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    ]
    release.restype = wintypes.LONG
    reserve = ctypes.c_ubyte(0)
    status = release(10, ctypes.byref(reserve), ctypes.sizeof(reserve), None, 0)
    if status:
        raise OSError(f"Windows hibernation storage release failed: NTSTATUS 0x{status & 0xffffffff:08X}")


def remove(cache):
    if cache.kind == "notice":
        return 0, 0, 1
    if cache.kind == "hibernate":
        windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        expected = Path(windows.anchor) / "hiberfil.sys"
        if cache.path != expected:
            raise ValueError("Invalid hibernation file path.")
        if not cache.path.exists():
            return 0, 0, 0
        # Windows manages this file. Disabling hibernation also disables Fast Startup.
        result = subprocess.run(
            [str(windows / "System32/powercfg.exe"), "/hibernate", "off"],
            capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            message = (result.stderr or result.stdout).decode("oem", errors="replace").strip()
            raise OSError(message or f"powercfg failed ({result.returncode}).")
        if cache.path.exists():
            release_hibernation_storage()
            deadline = time.monotonic() + 5
            while cache.path.exists():
                if time.monotonic() >= deadline:
                    raise OSError("Windows has not released hiberfil.sys. Space was not counted as reclaimed. Check administrator permissions and powercfg /a.")
                time.sleep(0.2)
        return 1, cache.size, 0
    if cache.kind == "restore":
        import ctypes
        from ctypes import wintypes
        if not isinstance(cache.restore_id, int) or not 0 < cache.restore_id <= 0xffffffff:
            raise ValueError("Invalid restore point ID.")
        remove_point = ctypes.WinDLL("SrClient.dll").SRRemoveRestorePoint
        remove_point.argtypes = [wintypes.DWORD]
        remove_point.restype = wintypes.DWORD
        code = remove_point(cache.restore_id)
        if code:
            raise ctypes.WinError(code)
        return 1, 0, 0
    browser_processes = {"Chrome": "chrome.exe", "Edge": "msedge.exe", "Firefox": "firefox.exe"}
    browser = browser_processes.get(cache.name)
    if browser:
        for process in psutil.process_iter(["name"]):
            try:
                if (process.info["name"] or "").lower() == browser:
                    return 0, 0, len(cache.files) + cache.skipped
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    removed = size = skipped = 0
    for path, before in cache.files:
        try:
            if not plain_path(path.parent):
                skipped += 1
                continue
            current = path.lstat()
            if (not stat.S_ISREG(current.st_mode)
                    or getattr(current, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or (current.st_ino, current.st_size, current.st_mtime_ns) !=
                    (before.st_ino, before.st_size, before.st_mtime_ns)):
                skipped += 1
                continue
            path.unlink()
            removed += 1
            size += before.st_size
        except FileNotFoundError:
            continue
        except OSError:
            skipped += 1
    return removed, size, skipped
