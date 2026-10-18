@echo off
REM nano-airbridge — One-click launcher for Windows (cmd / PowerShell)
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PYTHON_CMD=python"

where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=py"
    ) else (
        echo [ERROR] Python 3 was not found in PATH.
        echo Please install Python 3 from https://python.org and check "Add to PATH".
        pause
        exit /b 1
    )
)

set "ROOT_DIR="
set "RELAY_URL="
set "AGENT_TOKEN=airbridge_agent_token_112233"
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
if /i "%~1"=="local" (
    set "MODE=local"
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
    if not "%RELAY_URL%"=="" (
        set "MODE=agent"
    ) else (
        set "MODE=gateway"
    )
)

echo ==================================================
echo  nano-airbridge — Zero-Dependency MCP Runtime
echo ==================================================
echo  Workspace: %ROOT_DIR%
echo  Mode:      %MODE%
echo  OS:        Windows
if "%MODE%"=="agent" (
echo  Relay URL: %RELAY_URL%
) else (
echo  HTTP Port: %PORT%
)
echo ==================================================

if "%MODE%"=="gateway" (
    echo Starting HTTP/MCP Gateway on port %PORT%...
    "%PYTHON_CMD%" "%SCRIPT_DIR%nano_http_server.py" --root "%ROOT_DIR%" --port %PORT% --enable-writes --enable-execution
    goto :eof
)

if "%MODE%"=="agent" (
    if "!RELAY_URL!"=="" set "RELAY_URL=https://nano-airbridge.gt.tc/nano_relay.php"
    echo Connecting to Cloud Relay via outbound long-poll:
    echo  Relay: !RELAY_URL!
    echo  No open ports or ngrok needed.
    "%PYTHON_CMD%" "%SCRIPT_DIR%relay\nano_agent.py" --relay "!RELAY_URL!" --token "!AGENT_TOKEN!" --command "%PYTHON_CMD% %SCRIPT_DIR%nano_airbridge.py --root %ROOT_DIR% --enable-writes --enable-execution"
    goto :eof
)

if "%MODE%"=="local" (
    echo Starting stdio MCP server for Cursor / Claude Desktop / Windsurf...
    "%PYTHON_CMD%" "%SCRIPT_DIR%nano_airbridge.py" --root "%ROOT_DIR%" --enable-writes --enable-execution
    goto :eof
)

echo Usage: start.bat [path_to_workspace] [--relay <URL>]
pause
