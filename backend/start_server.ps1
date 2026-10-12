# 启动参数说明：
#   -DisableScheduler：验收时只暂停本次服务进程的自动派发，不修改已保存的任务或数据源绑定。
#   -DisableOpenCodeProxy：OpenCode/zen 头注入代理由 run.py 按需自动拉起；此开关显式关闭
#     （例如改用已支持 x-opencode-session 的其它模型端点时）。
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8011,

    [string]$BindAddress = "0.0.0.0",

    [switch]$ConfigureFirewall,

    [switch]$RemoveFirewallRule,

    [switch]$FirewallOnly,

    [switch]$DisableScheduler,

    [switch]$DisableOpenCodeProxy
)

$ErrorActionPreference = "Stop"

# 启动器向控制台转发 Python/Node 的中文节点日志时统一使用 UTF-8。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDirectory = [System.IO.Path]::GetFullPath((Join-Path $scriptDirectory ".."))
$pythonExecutable = Join-Path $projectDirectory ".venv_py312\Scripts\python.exe"
$runFile = Join-Path $scriptDirectory "run.py"
$webScript = Join-Path $projectDirectory "apps\web\app.js"
$firewallRuleName = "Cloud Brain TCP $Port (LocalSubnet)"

function Get-LanIPv4Addresses {
    try {
        @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.AddressState -eq "Preferred" -and
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                ($_.IPAddress -like "10.*" -or
                 $_.IPAddress -like "192.168.*" -or
                 $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.')
            } |
            Sort-Object InterfaceMetric, SkipAsSource |
            Select-Object -ExpandProperty IPAddress -Unique)
    }
    catch {
        @()
    }
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required to configure Windows Firewall."
    }
}

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Python runtime not found: $pythonExecutable"
}

if (-not (Test-Path -LiteralPath $runFile -PathType Leaf)) {
    throw "Backend launcher not found: $runFile"
}

if (-not (Test-Path -LiteralPath $webScript -PathType Leaf)) {
    throw "Web frontend bundle not found: $webScript"
}

# 启动时记录当前前端指纹，确认服务使用的是工作区最新文件；调用项目 Python 兼容 Windows PowerShell。
$webHash = "unavailable"
try {
    $hashOutput = & $pythonExecutable -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" $webScript 2>$null
    if ($hashOutput) { $webHash = [string]($hashOutput | Select-Object -Last 1) }
}
catch {
    $webHash = "unavailable"
}
Write-Host "Frontend bundle: $webScript"
Write-Host ("Frontend SHA256: " + $webHash)

if ($ConfigureFirewall -and $RemoveFirewallRule) {
    throw "-ConfigureFirewall and -RemoveFirewallRule cannot be used together."
}

if ($FirewallOnly -and -not ($ConfigureFirewall -or $RemoveFirewallRule)) {
    throw "-FirewallOnly requires -ConfigureFirewall or -RemoveFirewallRule."
}

if ($ConfigureFirewall) {
    Assert-Administrator
    $existingRule = Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue
    if ($null -eq $existingRule) {
        New-NetFirewallRule -DisplayName $firewallRuleName `
            -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
            -Profile Private,Public -RemoteAddress LocalSubnet | Out-Null
        Write-Host "Firewall rule added: TCP $Port, remote scope LocalSubnet." -ForegroundColor Green
    }
    else {
        Write-Host "Firewall rule already exists: $firewallRuleName" -ForegroundColor DarkGreen
    }
    if ($FirewallOnly) {
        return
    }
}

if ($RemoveFirewallRule) {
    Assert-Administrator
    $existingRule = Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue
    if ($null -ne $existingRule) {
        $existingRule | Remove-NetFirewallRule
        Write-Host "Firewall rule removed: $firewallRuleName" -ForegroundColor Yellow
    }
    else {
        Write-Host "Firewall rule not found: $firewallRuleName"
    }
    return
}

$existingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -ne $existingListener) {
    # 重复点击启动入口时，先确认占用端口的是本项目且服务健康，避免把正常运行误报成失败。
    $healthyExistingService = $false
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -ErrorAction Stop
        $healthyExistingService = $health.ok -eq $true -and $health.app -eq "native-dev"
    }
    catch {
        $healthyExistingService = $false
    }
    if ($healthyExistingService) {
        Write-Host "Cloud Brain is already running on port $Port (PID $($existingListener.OwningProcess))." -ForegroundColor Green
        Write-Host "No new process was started or stopped."
        return
    }

    $ownerProc = Get-Process -Id $existingListener.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($ownerProc) { $ownerProc.ProcessName } else { "unknown" }
    Write-Host "Port $Port is occupied by external process '$ownerName' (PID $($existingListener.OwningProcess))." -ForegroundColor Yellow

    $fallbackPort = $null
    foreach ($candidate in @(8000, 8010, 8012, 8015, 8088)) {
        $candidateListener = Get-NetTCPConnection -LocalPort $candidate -State Listen -ErrorAction SilentlyContinue
        if ($null -eq $candidateListener) {
            $fallbackPort = $candidate
            break
        }
    }

    if ($null -ne $fallbackPort) {
        Write-Host "Automatically switching to available port $fallbackPort..." -ForegroundColor Cyan
        $Port = $fallbackPort
    }
    else {
        throw "Port $Port is already used by process $($existingListener.OwningProcess) ($ownerName); no process was started or stopped."
    }
}

$previousPort = $env:PORT
$previousHost = $env:HOST
$previousScheduler = $env:SCHEDULER_ENABLED
$previousOpenCodeProxy = $env:OPCODE_PROXY_ENABLED
try {
    $env:PORT = [string]$Port
    $env:HOST = $BindAddress
    $env:SCHEDULER_ENABLED = if ($DisableScheduler) { "0" } else { "1" }
    if ($DisableScheduler) {
        Write-Host "Automatic scheduling is paused for this process; saved jobs and data sources are unchanged." -ForegroundColor Yellow
    }
    if ($DisableOpenCodeProxy) {
        $env:OPCODE_PROXY_ENABLED = "0"
        Write-Host "OpenCode header proxy is disabled for this process." -ForegroundColor Yellow
    }
    else {
        Write-Host "OpenCode header proxy: auto (http://127.0.0.1:8901, see run.py/OPCODE_PROXY_*)." -ForegroundColor DarkGray
    }
    Write-Host "Local URL: http://127.0.0.1:$Port" -ForegroundColor Cyan
    $lanAddresses = Get-LanIPv4Addresses
    if ($BindAddress -in @("0.0.0.0", "::")) {
        foreach ($address in $lanAddresses) {
            Write-Host "LAN URL: http://$address`:$Port" -ForegroundColor Green
        }
        if (-not $ConfigureFirewall) {
            Write-Host "If another LAN device cannot connect, run as Administrator:" -ForegroundColor Yellow
            Write-Host "  & '$projectDirectory\start_lan.ps1' -Port $Port -ConfigureFirewall -FirewallOnly" -ForegroundColor Yellow
        }
    }
    Write-Host "Press Ctrl+C to stop the server."
    Push-Location $scriptDirectory
    try {
        & $pythonExecutable $runFile
        if ($LASTEXITCODE -ne 0) {
            throw "Server process exited with code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -eq $previousPort) {
        Remove-Item Env:PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:PORT = $previousPort
    }

    if ($null -eq $previousHost) {
        Remove-Item Env:HOST -ErrorAction SilentlyContinue
    }
    else {
        $env:HOST = $previousHost
    }

    if ($null -eq $previousScheduler) {
        Remove-Item Env:SCHEDULER_ENABLED -ErrorAction SilentlyContinue
    }
    else {
        $env:SCHEDULER_ENABLED = $previousScheduler
    }

    if ($null -eq $previousOpenCodeProxy) {
        Remove-Item Env:OPCODE_PROXY_ENABLED -ErrorAction SilentlyContinue
    }
    else {
        $env:OPCODE_PROXY_ENABLED = $previousOpenCodeProxy
    }
}
