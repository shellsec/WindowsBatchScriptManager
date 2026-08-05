@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Clean build cache...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
for /d /r %%i in (__pycache__) do @if exist "%%i" rmdir /s /q "%%i"
del /q *.pyc 2>nul

echo [2/3] PyInstaller build...
pyinstaller --clean --noconfirm build_exe.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED.
    exit /b 1
)

echo.
echo [3/3] Done: dist\WindowsBatchScriptManager.exe
endlocal
