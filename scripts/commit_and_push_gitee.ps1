param(
  [Parameter(Mandatory = $true)] [string]$Message,
  [switch]$NoPush = $false,
  [switch]$SkipTests = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent)

if (-not $SkipTests) {
  Write-Host "建议先运行关键测试：npm run ui:check / pytest...（如需可跳过此提示：-SkipTests）"
}

# 把工作区变更全部纳入暂存区
$changed = @(git status --short)
if (-not $changed -or $changed.Count -eq 0) {
  throw '当前工作区没有可提交变更。'
}

git add -A
git commit -m $Message

if ($NoPush) {
  Write-Host '已提交但未推送（-NoPush）。如需推送请执行: git push'
  exit 0
}

$branch = (git symbolic-ref --short HEAD).Trim()
if ([string]::IsNullOrWhiteSpace($branch)) {
  throw '无法识别当前分支名。'
}

git push origin $branch
Write-Host "已提交并推送到 origin/$branch"
