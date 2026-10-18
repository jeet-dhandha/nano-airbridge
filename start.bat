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

if "%MODE%"=="agent" (
    if "!RELAY_URL!"=="" set "RELAY_URL=https://nano-airbridge.gt.tc/nano_relay.php"
    if "!SESSION_KEY!"=="" (
        for /f "delims=" %%i in ('"%PYTHON_CMD%" -c "import secrets; print(secrets.token_hex(8))"') do set "SESSION_KEY=%%i"
    )
    set "MCP_ENDPOINT=!RELAY_URL!?op=mcp&s=!SESSION_KEY!"
) else if "%MODE%"=="gateway" (
    set "MCP_ENDPOINT=http://localhost:%PORT%/mcp"
)

echo ================================================================================
echo  nano-airbridge — Zero-Dependency MCP Runtime
echo ================================================================================
echo  Workspace:    %ROOT_DIR%
echo  Mode:         %MODE%
echo  OS:           Windows
if "%MODE%"=="agent" (
echo  Relay URL:    !RELAY_URL!
echo  Session Key:  !SESSION_KEY!
echo  MCP Endpoint: !MCP_ENDPOINT!
) else if "%MODE%"=="gateway" (
echo  MCP Endpoint: !MCP_ENDPOINT!
echo  Local Port:   %PORT%
)
echo ================================================================================
echo.
if not "%MODE%"=="local" (
echo 📋 COPY-PASTE SYSTEM INSTRUCTION FOR CHATGPT / CLAUDE / KIMI:
echo --------------------------------------------------------------------------------
echo [SYSTEM INSTRUCTION: NANO-AIRBRIDGE WORKSPACE INTEGRATION]
echo You are connected to the operator's local project workspace via nano-airbridge.
echo - Bridge Endpoint: !MCP_ENDPOINT!
if not "!SESSION_KEY!"=="" echo - Session Key: !SESSION_KEY!
echo - Protocol: Model Context Protocol (MCP) JSON-RPC 2.0 with SHA-256 CAS verification.
echo - Concurrency Invariant: Optimistic concurrency control. Every `read_file` returns the file's current `sha256`. Every `write_file` and `edit_file` MUST supply `expected_sha256`. If the file was changed concurrently, the bridge aborts with `source_changed`.
echo - Idempotency: Retries with the same `execution_id` or `change_id` observe the original receipt and never replay.
echo.
echo Active Tools:
echo 1. `get_capabilities`: Inspect active grants, limits, and workspace identity.
echo 2. `inspect_workspace(depth=2, include_hashes=True)`: List directories or search contents.
echo 3. `read_file(path, offset=0, limit=1000)`: Read file page and retrieve SHA-256 hash.
echo 4. `write_file(path, content, expected_sha256)`: Overwrite file (use expected_sha256="" for new file).
echo 5. `edit_file(path, old_text, new_text, expected_sha256)`: Contiguous string replacement.
echo 6. `run_command(command, execution_id)`: Execute shell command if granted.
echo.
echo Workflow:
echo 1. First, inspect workspace using `inspect_workspace` to discover project structure autonomously.
echo 2. Read target files with `read_file` to verify content and obtain the SHA-256 checksum.
echo 3. Perform edits or create files using `edit_file` / `write_file` with verified `expected_sha256`.
echo 4. Execute required test or build commands with `run_command` and verify execution receipt.
echo 5. Provide an audited nano-airbridge execution receipt in your final response.
echo --------------------------------------------------------------------------------
echo.
)

if "%MODE%"=="gateway" (
    echo Starting HTTP/MCP Gateway on port %PORT%...
    "%PYTHON_CMD%" "%SCRIPT_DIR%nano_http_server.py" --root "%ROOT_DIR%" --port %PORT% --enable-writes --enable-execution
    goto :eof
)

if "%MODE%"=="agent" (
    echo Connecting to Cloud Relay via outbound long-poll:
    echo  Relay:   !RELAY_URL!
    echo  Session: !SESSION_KEY!
    echo  No open ports or ngrok needed.
    "%PYTHON_CMD%" "%SCRIPT_DIR%relay\nano_agent.py" --relay "!RELAY_URL!" --session "!SESSION_KEY!" --token "!AGENT_TOKEN!" --command "%PYTHON_CMD% %SCRIPT_DIR%nano_airbridge.py --root %ROOT_DIR% --enable-writes --enable-execution"
    goto :eof
)

if "%MODE%"=="local" (
    echo Starting stdio MCP server for Cursor / Claude Desktop / Windsurf...
    "%PYTHON_CMD%" "%SCRIPT_DIR%nano_airbridge.py" --root "%ROOT_DIR%" --enable-writes --enable-execution
    goto :eof
)

echo Usage: start.bat [path_to_workspace] [--relay <URL>]
pause
