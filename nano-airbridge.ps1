# nano-airbridge.ps1 — Zero-dependency Pure PowerShell runtime for Windows 10 & 11.
# Requires NO Python, NO PHP, NO Go, NO Node. Built directly on Windows .NET HttpListener.

param(
    [string]$Root = ".\demo_workspace",
    [int]$Port = 8899,
    [switch]$EnableWrites = $false,
    [switch]$EnableExecution = $false
)

$ResolvedRoot = (Resolve-Path $Root).Path
if (-not (Test-Path $ResolvedRoot -PathType Container)) {
    Write-Error "Workspace path does not exist: $Root"
    exit 1
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " nano-airbridge (Pure PowerShell Windows Edition)" -ForegroundColor Cyan
Write-Host " Zero Python • Zero PHP • Zero Node • Pure Windows" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Workspace: $ResolvedRoot"
Write-Host " Port:      $Port"
Write-Host " Writes:    $EnableWrites"
Write-Host " Exec:      $EnableExecution"
Write-Host "=================================================="

$listener = New-Object System.Net.HttpListener
$prefix = "http://localhost:$Port/"
$listener.Prefixes.Add($prefix)

try {
    $listener.Start()
    Write-Host "Listening on $prefix (Press Ctrl+C to stop)..." -ForegroundColor Green
} catch {
    Write-Error "Failed to start listener on port $Port : $_"
    exit 1
}

while ($listener.IsListening) {
    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response

    $response.Headers.Add("Access-Control-Allow-Origin", "*")
    $response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    $response.Headers.Add("Access-Control-Allow-Headers", "Content-Type, Authorization")

    $path = $request.Url.AbsolutePath

    if ($path -eq "/healthz") {
        $json = '{"status":"ok","server":"nano-airbridge-powershell","version":"0.1.0"}'
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $response.ContentType = "application/json"
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        $response.Close()
        continue
    }

    if ($path -eq "/inspect") {
        $files = Get-ChildItem -Path $ResolvedRoot -File -Recurse | Where-Object {
            $_.Name -notmatch "^\." -and $_.Name -notmatch "\.(pem|key)$"
        }
        $entries = @()
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($ResolvedRoot.Length).TrimStart("\", "/")
            $entries += @{ path = $rel.Replace("\", "/"); kind = "file"; size_bytes = $f.Length }
        }
        $res = @{ action = "list"; entries = $entries; total_scanned = $entries.Count }
        $json = $res | ConvertTo-Json -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $response.ContentType = "application/json"
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        $response.Close()
        continue
    }

    if ($path.StartsWith("/read/")) {
        $rel = $path.Substring(6).TrimStart("/")
        $full = Join-Path $ResolvedRoot $rel
        if (Test-Path $full -PathType Leaf) {
            $content = [System.IO.File]::ReadAllText($full, [System.Text.Encoding]::UTF8)
            $hash = (Get-FileHash -Path $full -Algorithm SHA256).Hash.ToLower()
            $res = @{
                path = $rel
                exists = $true
                size = $content.Length
                sha256 = $hash
                content = $content
            }
            $json = $res | ConvertTo-Json -Compress
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
            $response.ContentType = "application/json"
            $response.OutputStream.Write($bytes, 0, $bytes.Length)
        } else {
            $response.StatusCode = 404
        }
        $response.Close()
        continue
    }

    $defaultMsg = [System.Text.Encoding]::UTF8.GetBytes("nano-airbridge PowerShell Server active.")
    $response.OutputStream.Write($defaultMsg, 0, $defaultMsg.Length)
    $response.Close()
}
