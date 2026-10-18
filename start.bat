@echo off
REM nano-airbridge — One-click launcher for Windows (Pure Go Standalone Binary)
REM Zero Python • Zero PHP • Zero Node Runtime Dependencies
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "BIN_EXE=%SCRIPT_DIR%bin\nano-airbridge.exe"

if not exist "!BIN_EXE!" (
    where go >nul 2>nul
    if %errorlevel% equ 0 (
        echo ⚡ Building native standalone binary with Go...
        call "%SCRIPT_DIR%build.bat"
    )
)

if not exist "!BIN_EXE!" (
    echo ================================================================================
    echo  [!] Precompiled standalone binary not found in bin\nano-airbridge.exe
    echo --------------------------------------------------------------------------------
    echo  To compile your own binary with Go:
    echo    call build.bat
    echo  Or download Go from https://go.dev/dl/
    echo ================================================================================
    pause
    exit /b 1
)

set "ROOT_DIR="
set "RELAY_URL="
set "AGENT_TOKEN=airbridge_agent_token_112233"
set "SESSION_KEY="
set "PORT=8899"
set "MODE="

:parse_loop
if "%~1"=="" goto end_parse
if /i "%~1"=="--relay" (
    set "RELAY_URL=%~2"
    set "MODE=agent"
    shift
    shift
    goto parse_loop
)
if /i "%~1"=="--session" (
    set "SESSION_KEY=%~2"
    shift
    shift
    goto parse_loop
)
if /i "%~1"=="--token" (
    set "AGENT_TOKEN=%~2"
    shift
    shift
    goto parse_loop
)
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift
    shift
    goto parse_loop
)
if /i "%~1"=="gateway" (
    set "MODE=gateway"
    shift
    goto parse_loop
)
if /i "%~1"=="agent" (
    set "MODE=agent"
    shift
    goto parse_loop
)
if "%ROOT_DIR%"=="" (
    set "ROOT_DIR=%~1"
)
shift
goto parse_loop
:end_parse

if "%ROOT_DIR%"=="" set "ROOT_DIR=%SCRIPT_DIR%demo_workspace"
if "%MODE%"=="" (
    if not "!RELAY_URL!"=="" (
        set "MODE=agent"
    ) else (
        set "MODE=gateway"
    )
)

if "%MODE%"=="agent" (
    set "MCP_ENDPOINT=!RELAY_URL!?op=mcp&s=!SESSION_KEY!"
) else if "%MODE%"=="gateway" (
    set "TUNNEL_URL="

    REM Auto-discover npx in standard Windows locations
    if exist "%ProgramFiles%\nodejs\npx.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"
    if exist "%ProgramFiles(x86)%\nodejs\npx.cmd" set "PATH=%ProgramFiles(x86)%\nodejs;%PATH%"
    if exist "%LOCALAPPDATA%\Programs\node\npx.cmd" set "PATH=%LOCALAPPDATA%\Programs\node;%PATH%"
    if exist "%APPDATA%\npm\npx.cmd" set "PATH=%APPDATA%\npm;%PATH%"

    where npx >nul 2>&1
    if errorlevel 1 (
        echo ================================================================================
        echo  [!] Node.js & npx are not installed (needed to launch free localtunnel).
        echo --------------------------------------------------------------------------------
        echo  To enable free public tunneling for ChatGPT / Claude without any accounts:
        where winget >nul 2>&1
        if !errorlevel! equ 0 (
            echo    Windows (winget):   winget install OpenJS.NodeJS
        ) else (
            echo    Official Download:  https://nodejs.org/
        )
        echo --------------------------------------------------------------------------------
        echo  Continuing with local gateway on http://localhost:%PORT%/mcp
        echo ================================================================================
        echo.
        set "MCP_ENDPOINT=http://localhost:%PORT%/mcp"
    ) else (
        echo ⚡ Launching free localtunnel (zero accounts, no sign-up)...
        start /b "" cmd /c "npx -y localtunnel --port %PORT% > %TEMP%\airbridge_lt.log 2>&1"
        timeout /t 3 /nobreak >nul
        for /f "tokens=4" %%u in ('findstr "your url is:" %TEMP%\airbridge_lt.log 2^>nul') do set "TUNNEL_URL=%%u"
        if not "!TUNNEL_URL!"=="" (
            set "MCP_ENDPOINT=!TUNNEL_URL!/mcp"
        ) else (
            set "MCP_ENDPOINT=http://localhost:%PORT%/mcp"
        )
    )
)

echo ================================================================================
echo  nano-airbridge — Standalone Go MCP Runtime
echo  Zero Python • Zero PHP • Zero Node Dependencies
echo ================================================================================
echo  Workspace:     %ROOT_DIR%
echo  Engine:        Native Standalone Binary (nano-airbridge.exe)
if not "!TUNNEL_URL!"=="" echo  Public Tunnel: !TUNNEL_URL! (localtunnel)
echo  MCP Endpoint:  !MCP_ENDPOINT!
echo  Local Port:    %PORT%
echo ================================================================================
echo.
echo 📋 COPY-PASTE SYSTEM INSTRUCTION FOR CHATGPT / CLAUDE / KIMI:
echo --------------------------------------------------------------------------------
echo [SYSTEM INSTRUCTION: NANO-AIRBRIDGE WORKSPACE INTEGRATION]
echo You are connected to the operator's local project workspace via nano-airbridge.
echo - Bridge Endpoint: !MCP_ENDPOINT!
echo - Protocol: Model Context Protocol (MCP) JSON-RPC 2.0 with SHA-256 CAS verification.
echo.
echo Active Tools:
echo 1. inspect_workspace: List workspace files.
echo 2. read_file(path): Read file contents and retrieve whole-file SHA-256 checksum.
echo 3. write_file(path, content, expected_sha256): Create or overwrite file under CAS.
echo 4. edit_file(path, old_text, new_text, expected_sha256): Contiguous text replacement.
echo 5. run_command(command): Execute shell command inside workspace root.
echo --------------------------------------------------------------------------------
echo.

if "%MODE%"=="agent" (
    "!BIN_EXE!" -root "%ROOT_DIR%" -relay "!RELAY_URL!" -token "!AGENT_TOKEN!" -enable-writes -enable-execution
) else (
    "!BIN_EXE!" -root "%ROOT_DIR%" -port %PORT% -enable-writes -enable-execution
)
