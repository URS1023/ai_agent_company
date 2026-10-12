[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$LanIp,
    [ValidateRange(1, 65535)][int]$Port = 8443,
    [switch]$PrepareOnly
)
$ErrorActionPreference = "Stop"
$projectDirectory = $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv_py312\Scripts\python.exe'
$backendLauncher = Join-Path $projectDirectory 'backend\start_server.ps1'
$certificateDirectory = Join-Path $projectDirectory '.implementation\lan-https'
New-Item -ItemType Directory -Path $certificateDirectory -Force | Out-Null
# Private signing keys stay accessible only to the current account and SYSTEM.
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $certificateDirectory /inheritance:r /grant:r "*$($currentSid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect the certificate directory.' }
& $pythonExecutable (Join-Path $projectDirectory 'scripts\create_lan_certificate.py') --ip $LanIp --directory $certificateDirectory
if ($LASTEXITCODE -ne 0) { throw 'LAN certificate preparation failed.' }
Copy-Item -LiteralPath (Join-Path $projectDirectory 'scripts\install_lan_certificate.ps1') -Destination (Join-Path $certificateDirectory 'client\install_lan_certificate.ps1') -Force
Copy-Item -LiteralPath (Join-Path $projectDirectory 'scripts\install_lan_certificate.cmd') -Destination (Join-Path $certificateDirectory 'client\install_lan_certificate.cmd') -Force
Set-Content -LiteralPath (Join-Path $certificateDirectory 'client\server_url.txt') -Value "https://$($LanIp):$Port/ai" -Encoding ascii
Write-Host "HTTPS address: https://$($LanIp):$Port/ai"
Write-Host "Client certificate bundle: $certificateDirectory\client"
Write-Host 'Client computers must explicitly trust the supplied CA before using the microphone.'
if ($PrepareOnly) { return }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Port $Port is already in use; existing processes were not stopped." }
$launcherCheck = Test-Path -LiteralPath $backendLauncher -PathType Leaf
if (-not $launcherCheck) { throw "Backend launcher not found: $backendLauncher" }
$previous = @{}
foreach ($name in @('HOST','PORT','SSL_CERTFILE','SSL_KEYFILE','SCHEDULER_ENABLED')) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:HOST = '0.0.0.0'
    $env:PORT = [string]$Port
    $env:SSL_CERTFILE = Join-Path $certificateDirectory 'server.crt'
    $env:SSL_KEYFILE = Join-Path $certificateDirectory 'server.key'
    # Both listeners may start the worker; a kernel lease selects one owner.
    $env:SCHEDULER_ENABLED = '1'
    # HTTPS 仅负责证书环境，实际服务统一走启动器，确保前端指纹和调度设置一致。
    & $backendLauncher -Port $Port -BindAddress '0.0.0.0'
    if ($LASTEXITCODE -ne 0) { throw 'HTTPS listener stopped with an error.' }
}
finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
