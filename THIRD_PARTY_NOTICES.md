# Third-party notices and compatibility review — v1.3.13

The combined WBSM release uses **AGPL-3.0-only**. Third-party components retain their original licenses. Preserved MIT and Apache-2.0 historical project notices in `third_party_licenses/historical/` are attribution for incorporated earlier code, not an alternative license for this combined release.

## Review scope and result

Repository commit authors at this review were shellsec (two email identities). No other commit authors appeared in the available Git history. This checks recorded provenance, not a guarantee that every historical line's origin can be established. The original MIT notice is retained. No dependency licensing conflict was identified in the components reviewed below. Optional components installed separately must still be reviewed for the versions actually deployed.

| Component | License / treatment |
| --- | --- |
| CPython 3.10.20 and bundled standard library | PSF license and component notices in `third_party_licenses/Python-LICENSE.txt` |
| Tcl/Tk 8.6 | Permissive Tcl/Tk terms, preserved separately |
| psutil 7.2.2 | BSD-3-Clause |
| Pillow 12.3.0 | MIT-CMU and bundled component notices; included conservatively if collected by the build |
| PyInstaller 6.22.3 | GPL with bootloader/bundling exception; the exception allows generated applications to use their own license |
| PyInstaller hooks and build tools | Original licenses retained; build dependencies are not all runtime components |
| FastAPI, Pydantic and optional API dependencies | MIT, BSD, PSF or dual permissive terms; exact installed versions and license files are listed in the manifest |
| OpenSSH Windows 10.4.0.0 | Separate unmodified Microsoft installer, with original LICENSE and NOTICE; BSD/ISC/MIT-style and other bundled notices apply |
| Microsoft VC/UCRT runtime libraries | Windows-only system runtime components; retain vendor notices and the Python Windows binary redistribution conditions in `Python-LICENSE.txt`. They are not relicensed under AGPL. |

`third_party_licenses/manifest.json` records reviewed Python package versions and retained license files. The API script is distributed as source and its Python packages are installed separately. The manifest covers installed core API/build dependencies, not every possible optional extra such as uvloop, watchfiles or websockets. The desktop build excludes FastAPI/uvicorn, windnd, matplotlib and platform-specific managers.

The offline OpenSSH installer is `OpenSSH-Win64-v10.4.0.0.msi`, SHA-256 `788d8643464a01eb63640a2fd746f9ed23d106b5a901a35f88dc8cd40e446ac9`, from upstream tag **10.4.0.0p1-Preview**. It remains a preview upstream component, reused from the previous release; this release does not install or update system OpenSSH merely by starting WBSM.

## Corresponding source and build

The GitHub release includes a source ZIP containing project source, tests, build configuration, license notices and instructions. See `PYINSTALLER.md`, `requirements-build.txt`, `build_exe.spec` and `docs/WINDOWS_REMOTE_ACCESS.md`. Build with Python 3.10 on Windows. Set `WBSM_ONEFILE=1`; the offline installer is optional and is verified using `WBSM_OPENSSH_MSI` and `WBSM_OPENSSH_SHA256`. Download the unchanged installer from the upstream release above to reproduce the offline payload. No production credentials or machine configuration are needed to build.

API `/source`, MCP `get_source_info`, and the release's `SOURCE.txt` provide corresponding-source links. Operators who modify the program and offer network interaction must provide their actual corresponding source to those users under AGPL section 13; an unchanged upstream URL alone is insufficient for a modified deployment. AGPL permits commercial use; it does not require every private modification to be published on GitHub.

References: [AGPL text](https://www.gnu.org/licenses/agpl-3.0.html), [PyInstaller bundling exception](https://pyinstaller.org/en/stable/license.html), [OpenSSH release](https://github.com/PowerShell/Win32-OpenSSH/releases/tag/10.4.0.0p1-Preview).
