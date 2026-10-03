<#
.SYNOPSIS
    Stops only the Cloudflare tunnel that belongs to this project.

.DESCRIPTION
    Several projects run side by side, each with its own cloudflared
    process. Killing them by image name would tear down every other
    project's tunnel, so this matches on the local port the tunnel
    serves - which is unique per project because it matches that
    project's own web server.

    Harmless when this project never started a tunnel: it simply
    matches nothing.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass `
        -File scripts\stop_tunnel.ps1 -Port 8001
#>
param(
    [Parameter(Mandatory = $true)]
    [int]$Port
)

$ErrorActionPreference = 'SilentlyContinue'

# Note: a WMI filter such as "CommandLine LIKE '%localhost:8000%'" is
# unreliable here - the WMI provider on this machine matches nothing
# even for a bare wildcard - so the filtering is done in PowerShell.
$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq 'cloudflared.exe' -and
        $_.CommandLine -like "*localhost:$Port*"
    }

if (-not $processes) {

    Write-Host "No tunnel running for port $Port."

    exit 0
}

foreach ($process in $processes) {

    Write-Host "Stopping this project's tunnel (port $Port, PID $($process.ProcessId))."

    Stop-Process -Id $process.ProcessId -Force
}