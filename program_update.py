"""Check official releases and replace the Windows single-file executable."""
import base64
from dataclasses import dataclass
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile

REPO = "shellsec/WindowsBatchScriptManager"
RELEASES = f"https://github.com/{REPO}/releases"
EXE_NAME = "WindowsBatchScriptManager.exe"
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
UPDATE_MIRRORS = (
    "https://gh-proxy.com",
    "https://ghfast.top",
    "https://gh.monlor.com",
    "https://gh.acmsz.top",
)


@dataclass(frozen=True)
class Release:
    version: str
    package_url: str
    checksum_url: str


class _GitHubRedirect(HTTPRedirectHandler):
    def __init__(self, mirror_host=None):
        self.mirror_host = mirror_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        host = target.hostname or ""
        if target.scheme != "https" or not (
            host == "github.com" or host.endswith(".githubusercontent.com") or host == self.mirror_host
        ):
            raise ValueError("The update was redirected outside GitHub.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    request = Request(url, headers={"User-Agent": "WindowsBatchScriptManager"})
    host = urlsplit(url).hostname
    accelerated = host in {urlsplit(mirror).hostname for mirror in UPDATE_MIRRORS}
    return build_opener(_GitHubRedirect(host if accelerated else None)).open(
        request, timeout=6 if accelerated else 15,
    )


def _version(value):
    return tuple(map(int, value.split(".")))


def check_release(current):
    with _open(f"https://api.github.com/repos/{REPO}/releases/latest") as response:
        data = json.loads(response.read(1024 * 1024 + 1))
    tag = data["tag_name"]
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Unsupported release tag.")
    version = tag[1:]
    if data.get("draft") or data.get("prerelease") or _version(version) <= _version(current):
        return None
    name = f"WindowsBatchScriptManager-{version}-windows-x64.zip"
    base = f"{RELEASES}/download/{tag}"
    assets = {asset["name"]: asset["browser_download_url"] for asset in data["assets"]}
    package = f"{base}/{name}"
    checksum = package + ".sha256"
    if assets.get(name) != package or assets.get(name + ".sha256") != checksum:
        raise ValueError("The release is missing the Windows ZIP or its checksum.")
    return Release(version, package, checksum)


def _download_package(url, payload, expected_digest):
    digest = hashlib.sha256()
    total = 0
    with _open(url) as response:
        while chunk := response.read(128 * 1024):
            total += len(chunk)
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("Update package is too large.")
            digest.update(chunk)
            payload.write(chunk)
    if digest.hexdigest() != expected_digest:
        raise ValueError("SHA-256 verification failed.")


def stage_update(release, executable, *, language="en-US"):
    name = release.package_url.rsplit("/", 1)[-1]
    with _open(release.checksum_url) as response:
        checksum = response.read(4096).decode("ascii").split()
    if len(checksum) != 2 or checksum[1].lstrip("*") != name or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum[0]):
        raise ValueError("Invalid SHA-256 checksum file.")

    with tempfile.TemporaryFile() as payload:
        expected = checksum[0].lower()
        urls = [release.package_url]
        if language in ("zh-CN", "zh-TW"):
            urls = [f"{mirror}/{release.package_url}" for mirror in UPDATE_MIRRORS] + urls
        for index, url in enumerate(urls):
            payload.seek(0)
            payload.truncate()
            try:
                _download_package(url, payload, expected)
                break
            except (OSError, HTTPException, ValueError):
                if index == len(urls) - 1:
                    raise
        with ZipFile(payload) as archive:
            entries = [entry for entry in archive.infolist() if entry.filename == EXE_NAME]
            if len(entries) != 1 or not 2 <= entries[0].file_size <= MAX_PACKAGE_BYTES:
                raise ValueError("Invalid executable in the update package.")
            with archive.open(entries[0]) as source:
                if source.read(2) != b"MZ":
                    raise ValueError("The update is not a Windows executable.")
                with tempfile.NamedTemporaryFile(
                    dir=executable.parent, prefix=".wbsm-update-", suffix=".exe", delete=False,
                ) as output:
                    staged = Path(output.name)
                    try:
                        output.write(b"MZ")
                        shutil.copyfileobj(source, output)
                        output.close()
                    except BaseException:
                        output.close()
                        staged.unlink(missing_ok=True)
                        raise
    return staged


def task_snapshot_path(staged):
    return staged.with_suffix(".tasks.json")


def write_running_tasks(staged, running):
    task_snapshot_path(staged).write_text(json.dumps(running), encoding="utf-8")


def create_restart_snapshot(executable, running):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=executable.resolve().parent,
        prefix=".wbsm-update-", suffix=".tasks.json", delete=False,
    ) as output:
        json.dump(running, output)
        return Path(output.name)


def read_running_tasks(executable):
    value = os.environ.pop("WBSM_UPDATE_TASKS", "")
    if not value:
        return None
    snapshot = Path(value)
    if snapshot.parent != executable.resolve().parent or not re.fullmatch(r"\.wbsm-update-[a-z0-9_]+\.tasks\.json", snapshot.name):
        return None
    try:
        records = json.loads(snapshot.read_text(encoding="utf-8"))
        return [item for item in records if isinstance(item, dict)] if isinstance(records, list) else []
    except (OSError, ValueError):
        return []
    finally:
        try:
            snapshot.unlink(missing_ok=True)
        except OSError:
            pass


def launch_installer(launch_process, executable, staged):
    """Wait for GUI exit, replace atomically, and retain the old EXE for recovery."""
    powershell = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32/WindowsPowerShell/v1.0/powershell.exe"
    args = base64.b64encode(json.dumps([
        str(executable), str(staged), os.getpid(), str(task_snapshot_path(staged)),
    ]).encode()).decode("ascii")
    script = r"""
$ErrorActionPreference = 'Stop'
$paths = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__ARGS__')))
$target = $paths[0]
$staged = $paths[1]
$guiPid = $paths[2]
$backup = $staged + '.bak'
$replaced = $false
try {
    $gui = Get-Process -Id $guiPid -ErrorAction SilentlyContinue
    if ($gui -and -not $gui.WaitForExit(30000)) { throw 'The application did not exit.' }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            [IO.File]::Replace($staged, $target, $backup)
            $replaced = $true
            break
        } catch [IO.IOException] {
            if ($attempt -eq 29) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    Remove-Item Env:_PYI_APPLICATION_HOME_DIR -ErrorAction SilentlyContinue
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    $env:WBSM_UPDATE_TASKS = $paths[3]
    Start-Process -FilePath $target -WorkingDirectory ([IO.Path]::GetDirectoryName($target))
} catch {
    $reason = $_.Exception.Message
    if ($replaced) {
        try { [IO.File]::Replace($backup, $target, $null) }
        catch { $reason += " (Backup: $backup)" }
    }
    Remove-Item -LiteralPath $staged -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $paths[3] -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.Windows.Forms
    [void][Windows.Forms.MessageBox]::Show($reason, 'WindowsBatchScriptManager Update')
}
""".replace("__ARGS__", args)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return launch_process(
        [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=executable.parent, creationflags=subprocess.CREATE_NO_WINDOW,
    )
