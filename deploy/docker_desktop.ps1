<#
本机 Docker Desktop 的 D 盘安装入口。
仅安装经官方 SHA256 与 Windows 签名双重核验的固定版本；不启动业务容器或重启电脑。
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Install', 'PrepareWSL', 'Start')]
    [string]$Action = 'Check',
    [switch]$AcknowledgeResourceIncident
)

$ErrorActionPreference = 'Stop'
$dockerRoot = 'D:\Docker'
$applicationPath = Join-Path $dockerRoot 'Desktop'
$dataPath = Join-Path $dockerRoot 'wsl-data'
$installerPath = Join-Path $dockerRoot 'installers\4.96.0-243774\DockerDesktopInstaller.exe'
# 来源：https://desktop.docker.com/win/main/amd64/243774/checksums.txt
$expectedHash = '3F75D46A07FF7C7ADCC42E55B2BAB862E25A459351723AC2633DE9AD22A5B4B6'
$resultPath = Join-Path $dockerRoot 'installers\installation-result.json'

function Write-InstallResult {
    param([hashtable]$Values)
    $Values['recorded_at'] = [DateTimeOffset]::UtcNow.ToString('o')
    $Values['application_path'] = $applicationPath
    $Values['wsl_data_path'] = $dataPath
    $Values | ConvertTo-Json -Depth 6 -Compress | Add-Content -LiteralPath (Join-Path (Split-Path -Parent $resultPath) 'installation-events.jsonl') -Encoding utf8
    $Values | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding utf8
    $Values | ConvertTo-Json -Depth 6
}

# 使用固定本机磁盘与互相独立的程序、数据目录，避免更新安装包时覆盖数据盘。
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'"
if (-not $drive -or $drive.DriveType -ne 3) { throw 'D盘不是可核验的本机固定磁盘。' }
if (Test-Path -LiteralPath $dockerRoot) {
    $rootItem = Get-Item -LiteralPath $dockerRoot
    if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'D盘Docker根目录是重定向目录，停止安装。' }
}
foreach ($path in @($applicationPath, $dataPath, (Split-Path -Parent $installerPath))) {
    if (Test-Path -LiteralPath $path) {
        if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw '安装或数据路径存在重定向，停止安装。'
        }
    }
}
$check = @{
    status = 'checked'; free_gib = [math]::Round($drive.FreeSpace / 1GB, 2)
    installer_present = (Test-Path -LiteralPath $installerPath)
    application_present = (Test-Path -LiteralPath (Join-Path $applicationPath 'Docker Desktop.exe'))
    automatic_reboot = $false; business_containers_started = $false
}
if ($Action -eq 'Check') { $check | ConvertTo-Json; exit 0 }
if ($Action -eq 'Start') {
    if (-not $check.application_present) { throw 'D盘Docker程序尚未安装。' }
    $incidentPath = Join-Path $dockerRoot 'installers\engine-resource-incident.json'
    if ((Test-Path -LiteralPath $incidentPath) -and -not $AcknowledgeResourceIncident) {
        throw '曾发生本次引擎资源异常；须用户明确允许单次重启后再给确认参数。'
    }
    $desktopProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('Docker Desktop.exe', 'com.docker.backend.exe', 'docker-desktop.exe')
    })
    if ($desktopProcesses.Count -ne 0) { throw '已有Docker进程，不接管或重复启动。' }
    $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
    if ([uint64]$memory.AvailableBytes -lt 5GB -or
        ([uint64]$memory.CommitLimit - [uint64]$memory.CommittedBytes) -lt 7GB) {
        throw '引擎启动前物理可用须至少5GiB、提交余量至少7GiB。'
    }
    $profileRoot = Join-Path $dockerRoot 'desktop-profile-20261020-02'
    if (Test-Path -LiteralPath $profileRoot) { throw '独立配置目录已存在，本次引擎启动不可重放。' }
    $profileRoaming = Join-Path $profileRoot 'Roaming'
    $profileSettings = Join-Path $profileRoaming 'Docker'
    $profileLocal = Join-Path $profileRoot 'Local'
    $profileLocalDocker = Join-Path $profileLocal 'Docker'
    New-Item -ItemType Directory -Path $profileSettings | Out-Null
    New-Item -ItemType Directory -Path $profileLocalDocker | Out-Null
    # 两类用户目录均隔离到D盘并保持加密，不改变全局环境和原用户配置。
    foreach ($encryptedDirectory in @($profileRoaming, $profileSettings, $profileLocal, $profileLocalDocker)) {
        [IO.File]::Encrypt($encryptedDirectory)
        if (-not ((Get-Item -LiteralPath $encryptedDirectory).Attributes -band [IO.FileAttributes]::Encrypted)) {
            throw '独立Docker配置目录未保持EFS加密，停止启动。'
        }
        $probeFrom = Join-Path $encryptedDirectory 'atomic-probe.tmp'
        $probeTo = Join-Path $encryptedDirectory 'atomic-probe.json'
        [IO.File]::WriteAllText($probeFrom, '{"probe":true}')
        [IO.File]::Move($probeFrom, $probeTo)
        if (-not ((Get-Item -LiteralPath $probeTo).Attributes -band [IO.FileAttributes]::Encrypted)) {
            throw '独立配置的原子写入探针未保持加密。'
        }
    }
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = Join-Path $applicationPath 'resources\bin\docker.exe'
    $startInfo.Arguments = 'desktop start --timeout 120'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables['APPDATA'] = $profileRoaming
    $startInfo.EnvironmentVariables['LOCALAPPDATA'] = $profileLocal
    $startedAt = Get-Date
    $desktopStarter = [Diagnostics.Process]::Start($startInfo)
    $stdoutTask = $desktopStarter.StandardOutput.ReadToEndAsync()
    $stderrTask = $desktopStarter.StandardError.ReadToEndAsync()
    $deadline = $startedAt.AddSeconds(125)
    $resourceViolation = $false
    try {
        while (-not $desktopStarter.WaitForExit(1000) -and (Get-Date) -lt $deadline) {
            $currentMemory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
            if ([uint64]$currentMemory.AvailableBytes -lt 2GB -or
                ([uint64]$currentMemory.CommitLimit - [uint64]$currentMemory.CommittedBytes) -lt 3GB) {
                $resourceViolation = $true
                @{active=$true; observed_at=[DateTimeOffset]::UtcNow.ToString('o'); automatic_restart=$false;
                    physical_available_bytes=[uint64]$currentMemory.AvailableBytes;
                    available_commit_bytes=([uint64]$currentMemory.CommitLimit - [uint64]$currentMemory.CommittedBytes)} |
                    ConvertTo-Json | Set-Content -LiteralPath $incidentPath -Encoding utf8
                break
            }
        }
    } catch {
        # 资源状态无法核验时也停止本次启动，不因监测器异常留下失控进程。
        $resourceViolation = $true
    }
    $timedOut = -not $desktopStarter.HasExited -and -not $resourceViolation
    if (-not $desktopStarter.HasExited) { $desktopStarter.Kill() }
    $desktopStarter.WaitForExit()
    $startExit = $desktopStarter.ExitCode
    $distributionStopped = $null
    $remainingOwnedPids = @()
    if ($timedOut -or $resourceViolation -or $startExit -ne 0) {
        $newOwned = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($applicationPath + '\', [StringComparison]::OrdinalIgnoreCase) -and
            $_.Name -in @('Docker Desktop.exe', 'com.docker.backend.exe') -and $_.CreationDate -ge $startedAt
        })
        foreach ($candidate in $newOwned) {
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($candidate.ProcessId)" -ErrorAction SilentlyContinue
            if ($current -and $current.ExecutablePath -eq $candidate.ExecutablePath -and $current.CreationDate -eq $candidate.CreationDate) {
                Stop-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
            }
        }
        # 此发行版的注册位置必须精确匹配本次新D盘数据；不关闭其他WSL实例。
        $ownedDistribution = @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
            Where-Object { $_.DistributionName -eq 'docker-desktop' -and $_.BasePath -eq '\\?\D:\Docker\wsl-data\main' })
        if ($ownedDistribution.Count -eq 1) {
            & (Join-Path $env:SystemRoot 'System32\wsl.exe') --terminate docker-desktop | Out-Null
            $distributionStopped = $LASTEXITCODE -eq 0
        }
        $remainingOwnedPids = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($applicationPath + '\', [StringComparison]::OrdinalIgnoreCase) -and
            $_.Name -in @('Docker Desktop.exe', 'com.docker.backend.exe') -and $_.CreationDate -ge $startedAt
        } | Select-Object -ExpandProperty ProcessId)
    }
    # 子进程可能继承输出句柄，异步捕获并限时读取，避免收尾被管道阻塞。
    $startOutput = if ($stdoutTask.Wait(500)) { $stdoutTask.Result } else { 'Docker CLI output capture incomplete' }
    $startError = if ($stderrTask.Wait(500)) { $stderrTask.Result } else { 'Docker CLI error capture incomplete' }
    Write-InstallResult @{status='engine_start_finished'; installer_exit_code=$startExit; timed_out=$timedOut;
        process_appdata=$profileRoaming; process_localappdata=$profileLocal;
        resource_violation=$resourceViolation; settings_encrypted=$true; global_environment_changed=$false;
        cleanup_remaining_owned_pids=$remainingOwnedPids; owned_distribution_stopped=$distributionStopped;
        automatic_reboot=$false; business_containers_started=$false; engine_verified=$false}
    if ($startOutput) { Write-Output $startOutput.Trim() }
    if ($startError) { Write-Output $startError.Trim() }
    $desktopStarter.Dispose()
    if ($timedOut -or $resourceViolation -or $startExit -ne 0) { exit 1 }
    exit 0
}
if ($Action -eq 'PrepareWSL') {
    if (-not $check.application_present) { throw '先完成本项目D盘Docker安装，再准备其WSL运行时。' }
    # 旧Windows安装入口拒绝完整参数时，使用微软官方离线MSI；不创建Ubuntu或发起重启。
    $wslMsiPath = Join-Path $dockerRoot 'installers\wsl-3.0.3\wsl.3.0.3.0.x64.msi'
    $wslExpectedHash = 'AE977286974255BD26BF925F00F40F5692C6B2084155CBC0563C09AEA091F211'
    if (-not (Test-Path -LiteralPath $wslMsiPath)) { throw '尚未完成微软官方WSL3.0.3 MSI下载。' }
    if ((Get-FileHash -LiteralPath $wslMsiPath -Algorithm SHA256).Hash -ne $wslExpectedHash) { throw 'WSL官方摘要不符。' }
    $wslSignature = Get-AuthenticodeSignature -LiteralPath $wslMsiPath
    if ($wslSignature.Status -ne 'Valid' -or $wslSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation[, ]') {
        throw 'WSL安装包没有有效的微软签名。'
    }
    $msiExecPath = Join-Path $env:SystemRoot 'System32\msiexec.exe'
    $msiLogPath = Join-Path $dockerRoot 'installers\wsl-3.0.3\install.log'
    $msiArguments = @('/i', $wslMsiPath, '/qn', '/norestart', 'REBOOT=ReallySuppress', '/L*v', $msiLogPath)
    $wslInstaller = Start-Process -FilePath $msiExecPath -Verb RunAs -ArgumentList $msiArguments -WindowStyle Hidden -PassThru
    Write-InstallResult @{status='preparing_wsl'; installer_pid=$wslInstaller.Id; automatic_reboot=$false; standalone_distribution=$false}
    $wslInstaller.WaitForExit()
    $wslInstaller.Refresh()
    $wslExit = $wslInstaller.ExitCode
    Write-InstallResult @{status='wsl_command_finished'; installer_exit_code=$wslExit;
        automatic_reboot=$false; reboot_requested=($wslExit -eq 3010); standalone_distribution=$false; engine_verified=$false}
    if ($wslExit -notin @(0, 3010)) { exit 1 }
    exit 0
}
if ($check.application_present) { throw 'D盘已存在Docker Desktop，先核查现有安装，禁止直接覆盖。' }
if ($drive.FreeSpace -lt 12GB) { throw 'D盘可用空间不足12GiB，停止安装。' }
if (-not $check.installer_present) { throw '尚未完成固定版本官方安装包下载。' }
if ((Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash -ne $expectedHash) {
    throw '官方安装包SHA256不匹配，禁止执行。'
}
$signature = Get-AuthenticodeSignature -LiteralPath $installerPath
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Docker Inc\.?[, ]') {
    throw '安装器没有有效的Docker Inc签名，禁止执行。'
}
New-Item -ItemType Directory -Path (Split-Path -Parent $resultPath) -Force | Out-Null
$installArguments = @('install', '--quiet', '--accept-license', '--backend=wsl-2',
    '--no-windows-containers', "--installation-dir=$applicationPath", "--wsl-default-data-root=$dataPath")
try {
    # UAC由Windows呈现；只提权已验签安装器，不提权任意拼接的脚本或静默操作用户应用。
    $installer = Start-Process -FilePath $installerPath -Verb RunAs -ArgumentList $installArguments -WindowStyle Hidden -PassThru
} catch {
    Write-InstallResult @{status='not_started'; error_type=$_.Exception.GetType().Name; automatic_reboot=$false}
    throw
}
Write-InstallResult @{status='installing'; installer_pid=$installer.Id; version='4.96.0'; sha256=$expectedHash; automatic_reboot=$false}
$installer.WaitForExit()
$installer.Refresh()
$exitCode = $installer.ExitCode
$applicationPresent = Test-Path -LiteralPath (Join-Path $applicationPath 'Docker Desktop.exe')
$result = @{status='failed'; installer_exit_code=$exitCode; application_present=$applicationPresent;
    reboot_requested=($exitCode -eq 3010 -or $exitCode -eq 1641); automatic_reboot=$false;
    engine_verified=$false; business_containers_started=$false}
if ($exitCode -eq 0 -and $applicationPresent) { $result.status='installed_engine_unverified' }
elseif ($exitCode -eq 3010 -and $applicationPresent) { $result.status='installed_reboot_required' }
Write-InstallResult $result
if ($result.status -eq 'failed') { exit 1 }
