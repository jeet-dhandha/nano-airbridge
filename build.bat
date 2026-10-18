@echo off
REM build.bat — Compile nano-airbridge Go binary on Windows
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if not exist "%SCRIPT_DIR%bin" mkdir "%SCRIPT_DIR%bin"

where go >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Go compiler was not found in PATH.
    echo Please install Go from https://go.dev/dl/ and add it to PATH.
    exit /b 1
)

echo ==================================================
echo  Building nano-airbridge.exe for Windows (x64)
echo ==================================================
set CGO_ENABLED=0
set GOOS=windows
set GOARCH=amd64
go build -ldflags="-s -w" -o "%SCRIPT_DIR%bin\nano-airbridge.exe" "%SCRIPT_DIR%src\go\main.go"

if %errorlevel% equ 0 (
    echo [SUCCESS] Binary compiled to bin\nano-airbridge.exe
) else (
    echo [FAIL] Compilation failed.
    exit /b 1
)
