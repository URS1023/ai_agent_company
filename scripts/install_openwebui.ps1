# 安装完整上游后端到独立环境，避免影响企业业务服务的依赖版本。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv_py312\Scripts\python.exe'
$targetPython = Join-Path $projectRoot '.venv_openwebui\Scripts\python.exe'
$dependencyFile = Join-Path $projectRoot 'services\openwebui\upstream\backend\requirements.txt'
$cachePath = Join-Path $projectRoot '.implementation\openwebui_dependency_cache'
if (-not (Test-Path -LiteralPath $targetPython)) {
    uv venv (Join-Path $projectRoot '.venv_openwebui') --python $pythonPath
    if ($LASTEXITCODE -ne 0) { throw '创建 Open WebUI 环境失败' }
}
uv pip install --python $targetPython --cache-dir $cachePath -r $dependencyFile
if ($LASTEXITCODE -ne 0) { throw '安装 Open WebUI 完整依赖失败' }
uv pip install --python $pythonPath --cache-dir $cachePath 'python-socketio[client]==5.16.2'
if ($LASTEXITCODE -ne 0) { throw '安装业务适配客户端失败' }
Write-Host '安装完成。start.bat 将自动启动完整 Open WebUI 后端及现有业务应用。'
