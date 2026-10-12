param(
  [switch]$Disable = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent)

if ($Disable) {
  git config --unset-all core.hooksPath | Out-Null
  Write-Host '已取消自动 Hook；当前提交将不再自动推送（除非你手动执行推送）。'
  exit 0
}

$hookPath = '.githooks'
if (-not (Test-Path $hookPath)) {
  New-Item -ItemType Directory -Path $hookPath | Out-Null
}

git config core.hooksPath $hookPath

if (-not (Test-Path (Join-Path $hookPath 'post-commit'))) {
  throw "未检测到 $hookPath/post-commit，请确认文件已存在。"
}

git update-index --chmod=+x (Join-Path $hookPath 'post-commit') 2>$null | Out-Null

Write-Host "已启用自动推送：每次 commit 后执行 post-commit 钩子并自动尝试推送至 origin/$((git symbolic-ref --short HEAD).Trim())"
Write-Host '可用环境变量 GIT_AUTO_PUSH=0 临时关闭自动推送；可通过 -Disable 取消 hooksPath 配置。'