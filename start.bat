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

set "ROOT_DIR=%~1"
if "%ROOT_DIR%"=="" set "ROOT_DIR=%SCRIPT_DIR%demo_workspace"

set "PORT=%PORT%"
if "%PORT%"=="" set "PORT=8899"

set "MODE=%~2"
if "%MODE%"=="" set "MODE=gateway"

echo ==================================================
echo  nano-airbridge — Zero-Dependency MCP Runtime
echo ==================================================
echo  Workspace: %ROOT_DIR%
echo  Mode:      %MODE%
echo  OS:        Windows
echo ==================================================

if "%MODE%"=="gateway" (
    echo Starting HTTP/MCP Gateway on port %PORT%...
    "%PYTHON_CMD%" "%SCRIPT_DIR%nano_http_server.py" --root "%ROOT_DIR%" --port %PORT% --enable-writes --enable-execution
    goto :eof
)

if "%MODE%"=="agent" (
    set "RELAY_URL=%RELAY_URL%"
    if "!RELAY_URL!"=="" set "RELAY_URL=https://nano-airbridge.gt.tc/nano_relay.php"
    set "AGENT_TOKEN=%AGENT_TOKEN%"
    if "!AGENT_TOKEN!"=="" set "AGENT_TOKEN=airbridge_agent_token_112233"
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

echo Usage: start.bat [path_to_workspace] [gateway^|agent^|local]
pause
