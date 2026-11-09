@echo off
setlocal
set "PYTHONNOUSERSITE=1"
cd /d "%~dp0"

rem Use one interpreter for dependency collection and packaging.
set "BUILD_PY=%~dp0.venv-build\Scripts\python.exe"
if not exist "%BUILD_PY%" (
    py -3.10 -m venv "%~dp0.venv-build"
    if errorlevel 1 goto :failed
)
echo [1/3] Install build dependencies...
"%BUILD_PY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed

rem Default: package the application and its runtime in one executable.
set "WBSM_ONEFILE=1"
if /i "%~1"=="onedir" set "WBSM_ONEFILE=0"
rem Always use a fresh output directory; existing releases may contain user data.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "BUILD_STAMP=%%i"
set "BUILD_OUTPUT=dist\release-%BUILD_STAMP%"
if exist "%BUILD_OUTPUT%" (
    echo Output directory already exists. Please retry in a second.
    exit /b 1
)
echo [2/3] PyInstaller build...
"%BUILD_PY%" -m PyInstaller --clean --noconfirm --distpath "%BUILD_OUTPUT%" build_exe.spec
if errorlevel 1 goto :failed

echo [3/3] Done.
if "%WBSM_ONEFILE%"=="1" (
    echo Output: %BUILD_OUTPUT%\WindowsBatchScriptManager.exe
) else (
    echo Output: %BUILD_OUTPUT%\WindowsBatchScriptManager\WindowsBatchScriptManager.exe
    echo Distribute the entire WindowsBatchScriptManager folder, including _internal.
)
exit /b 0

:failed
echo BUILD FAILED. Check the error above.
exit /b 1
