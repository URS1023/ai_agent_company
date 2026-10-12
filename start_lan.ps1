[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8011,

    [switch]$ConfigureFirewall,

    [switch]$RemoveFirewallRule,

    [switch]$FirewallOnly
)

$ErrorActionPreference = "Stop"

$projectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $projectDirectory "backend\start_server.ps1"

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Backend launcher not found: $launcher"
}

& $launcher `
    -Port $Port `
    -BindAddress "0.0.0.0" `
    -ConfigureFirewall:$ConfigureFirewall `
    -RemoveFirewallRule:$RemoveFirewallRule `
    -FirewallOnly:$FirewallOnly
