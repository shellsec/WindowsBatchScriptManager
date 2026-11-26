"""Run PowerShell on an enrolled Windows host using an OpenSSH alias."""

import argparse
import base64
import re
import subprocess
import sys
from pathlib import Path


PROBE = r"""
$tools = [ordered]@{}
foreach ($name in 'python.exe', 'pwsh.exe', 'winget.exe', 'docker.exe') {
    $tool = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
    $tools[$name] = if ($tool) { @($tool)[0].Path } else { $null }
}
$docker = $null
if ($tools['docker.exe']) {
    $ErrorActionPreference = 'Continue'
    $details = & $tools['docker.exe'] info --format '{{.ServerVersion}}' 2>&1
    $engineCode = $LASTEXITCODE
    $compose = & $tools['docker.exe'] compose version 2>&1
    $composeCode = $LASTEXITCODE
    $docker = @{
        engineExitCode = $engineCode; engine = ($details | Out-String).Trim()
        composeExitCode = $composeCode; compose = ($compose | Out-String).Trim()
    }
}
[ordered]@{
    host = $env:COMPUTERNAME; user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    powershell = $PSVersionTable.PSVersion.ToString(); directory = (Get-Location).Path
    tools = $tools; docker = $docker
} | ConvertTo-Json -Depth 4
exit 0
"""


def command_arguments(target, script, directory=None, allow_password=False):
    script = script.lstrip("\ufeff")
    script = "$ErrorActionPreference = 'Stop'\n& {\n" + script + "\n}\n"
    if directory:
        literal = directory.replace("'", "''")
        script = f"Set-Location -LiteralPath '{literal}' -ErrorAction Stop\n" + script
    script = "[Console]::OutputEncoding = New-Object Text.UTF8Encoding\n" + script
    script += "\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }; exit 0"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "ssh", "-T", "-o", "BatchMode=no" if allow_password else "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", target,
        "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Host alias already configured in ssh_config")
    parser.add_argument("--directory", help="Absolute Windows working directory")
    parser.add_argument("--timeout", type=int, help="Stop waiting after this many seconds")
    parser.add_argument("--password-prompt", action="store_true", help="Allow OpenSSH to ask for a password in the local terminal")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="Inspect runtimes and Docker connectivity")
    mode.add_argument("--script", type=Path, help="UTF-8 PowerShell file on the local machine; use - for stdin")
    mode.add_argument("--interactive", action="store_true", help="Open a PowerShell terminal")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.target):
        parser.error("target must be an OpenSSH host alias")
    if args.timeout is not None and args.timeout < 1:
        parser.error("timeout must be a positive number of seconds")
    try:
        if args.interactive:
            if args.directory or args.timeout:
                parser.error("interactive mode does not accept --directory or --timeout")
            return subprocess.call([
                "ssh", "-t", "-o", "StrictHostKeyChecking=yes", args.target,
                "powershell.exe", "-NoProfile",
            ])
        script = PROBE
        if args.script:
            script = (sys.stdin.buffer.read().decode("utf-8-sig") if str(args.script) == "-"
                      else args.script.read_text(encoding="utf-8-sig"))
        command = command_arguments(args.target, script, args.directory, args.password_prompt)
        if len(command[-1]) > 6500:
            parser.error("script is too large for Windows SSH command-line limits; upload it over SFTP and invoke it with a short script")
        timeout = args.timeout if args.timeout is not None else (60 if args.probe else None)
        stdin = None if args.password_prompt else subprocess.DEVNULL
        return subprocess.run(command, stdin=stdin, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print("SSH timed out. Check remote state before retrying.", file=sys.stderr)
        return 124
    except (OSError, UnicodeError) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
