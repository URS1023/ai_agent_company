# 实时查看后端节点日志；可按会话或运行标识筛选，日志内容来自本机文件。
param(
    [ValidateSet('ai', 'backend')][string]$Kind = 'ai',
    [string]$ConversationId = '',
    [string]$RunId = '',
    [string]$LogDirectory = '',
    [ValidateRange(1, 10000)][int]$Tail = 80,
    [switch]$Follow,
    [switch]$Structured
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $LogDirectory) {
    # 与启动器保持一致：优先读取本地配置中的 LOCAL_STORAGE_ROOT，避免日志实际写到 E: 盘时查看器仍找项目内空目录。
    $storageRoot = 'storage'
    $envFile = Join-Path $projectRoot '.env.local'
    if (Test-Path -LiteralPath $envFile) {
        $line = Get-Content -LiteralPath $envFile -Encoding UTF8 |
            Where-Object { $_ -match '^\s*LOCAL_STORAGE_ROOT\s*=' } | Select-Object -First 1
        if ($line -and $line -match '^\s*LOCAL_STORAGE_ROOT\s*=\s*(.+?)\s*$') { $storageRoot = $Matches[1].Trim().Trim('"', "'") }
    }
    if (-not [System.IO.Path]::IsPathRooted($storageRoot)) { $storageRoot = Join-Path $projectRoot $storageRoot }
    $LogDirectory = Join-Path $storageRoot 'logs'
}
# AI 默认查看给人看的中文正文；指定会话或运行 ID 时自动切换到机器 JSONL，保留精确检索能力。
if ($Kind -eq 'ai' -and ($ConversationId -or $RunId)) { $Structured = $true }
$humanAi = $Kind -eq 'ai' -and -not $Structured
$prefix = if ($humanAi) { 'ai-chat-readable' } elseif ($Kind -eq 'ai') { 'ai-chat' } else { 'backend' }
$pattern = if ($humanAi) { "$prefix-*.log*" } else { "$prefix-*.jsonl*" }
$files = @(Get-ChildItem -LiteralPath $LogDirectory -Filter $pattern -File | Sort-Object LastWriteTime -Descending)
if ($files.Count -eq 0) { throw "未找到日志：$LogDirectory。请先启动已更新的后端。" }

function Show-LogLine([string]$Line) {
    if ($humanAi) {
        if ($Line.Trim()) { Write-Host $Line }
        return
    }
    try { $entry = $Line | ConvertFrom-Json } catch { return }
    if ($ConversationId -and $entry.conversation_id -ne $ConversationId) { return }
    if ($RunId -and $entry.run_id -ne $RunId -and $entry.chat_run_id -ne $RunId) { return }
    $parts = @("$($entry.time) [$($entry.level)] $($entry.message)", "event=$($entry.event)")
    foreach ($key in @('conversation_id', 'run_id', 'chat_run_id', 'node_id', 'node_title', 'step_name', 'tool_name', 'model', 'status', 'error_code', 'error_type', 'duration_ms', 'data_count', 'row_count', 'output_characters', 'progress_message', 'artifact_id')) {
        $value = $entry.$key
        if ($null -ne $value -and "$value" -ne '') { $parts += "$key=$value" }
    }
    $color = switch ($entry.level) { 'ERROR' { 'Red' }; 'WARNING' { 'Yellow' }; default { 'Gray' } }
    Write-Host ($parts -join '  ') -ForegroundColor $color
}

if ($Follow) {
    # 每轮关闭文件句柄，避免 Windows 日志轮转时被查看器占用。
    # 半行 UTF-8 内容只保留字节位置，下轮读到换行后再显示。
    $knownPath = ''
    $position = 0L
    $oldCreation = $null
    while ($true) {
        $latest = Get-ChildItem -LiteralPath $LogDirectory -Filter $pattern -File |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($null -eq $latest) { Start-Sleep -Seconds 1; continue }
        $switched = $knownPath -ne $latest.FullName -or $latest.Length -lt $position -or $latest.CreationTimeUtc -ne $oldCreation
        if ($switched) {
            $knownPath = $latest.FullName
            $oldCreation = $latest.CreationTimeUtc
            $position = 0L
            Write-Host "正在查看：$knownPath（Ctrl+C 退出）" -ForegroundColor Cyan
        }
        try {
            $stream = [System.IO.FileStream]::new($knownPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
        } catch [System.IO.IOException] { Start-Sleep -Seconds 1; continue }
        try {
            $null = $stream.Seek($position, [System.IO.SeekOrigin]::Begin)
            $buffer = [byte[]]::new([int]($stream.Length - $position))
            $read = 0
            while ($read -lt $buffer.Length) {
                $count = $stream.Read($buffer, $read, $buffer.Length - $read)
                if ($count -eq 0) { break }
                $read += $count
            }
            $lastNewline = -1
            for ($index = $read - 1; $index -ge 0; $index--) {
                if ($buffer[$index] -eq 10) { $lastNewline = $index; break }
            }
            if ($lastNewline -ge 0) {
                $text = [System.Text.Encoding]::UTF8.GetString($buffer, 0, $lastNewline + 1)
                $lines = @($text -split "`n" | Where-Object { $_.Trim() })
                if ($switched) { $lines = @($lines | Select-Object -Last $Tail) }
                $lines | ForEach-Object { Show-LogLine $_ }
                $position += $lastNewline + 1
            }
        } finally { $stream.Dispose() }
        Start-Sleep -Seconds 1
    }
} elseif ($ConversationId -or $RunId) {
    # 检索全部仍保留的分卷，避免当前服务重启后查不到上一进程的会话。
    Get-ChildItem -LiteralPath $LogDirectory -Filter $pattern -File |
        Sort-Object LastWriteTime | ForEach-Object {
            Get-Content -LiteralPath $_.FullName -Encoding UTF8 | ForEach-Object { Show-LogLine $_ }
        }
} else {
    Write-Host "正在查看：$($files[0].FullName)" -ForegroundColor Cyan
    Get-Content -LiteralPath $files[0].FullName -Encoding UTF8 -Tail $Tail | ForEach-Object { Show-LogLine $_ }
}
