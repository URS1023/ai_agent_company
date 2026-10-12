param(
    [string]$LynxRoot = (Join-Path $PSScriptRoot '../../lynx-ai-master'),
    [string]$TargetRoot = (Join-Path $PSScriptRoot '../apps/analytics')
)
$ErrorActionPreference = 'Stop'
$taskSourceRoot = (Resolve-Path -LiteralPath $LynxRoot).Path
$taskSourceUi = Join-Path $taskSourceRoot 'lynx-ai-ui'
$taskTarget = [IO.Path]::GetFullPath($TargetRoot)
$taskWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $taskTarget.StartsWith($taskWorkspace + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Migration target must be inside Test5.' }
$taskFiles = [Collections.Generic.List[string]]::new()
foreach ($taskComponent in @('DataSourceManage','IntelligentQuery','DataAnalysisDashboard','QueryTemplates','SmartChart','ReportManage','ReportDesigner','ReportViewer','JimuBigScreenReplica')) {
    $taskFiles.Add("src/components/$taskComponent.vue")
}
foreach ($taskUtility in @('widgetSizing','reportWidgetData','reportPromptLibrary','reportAiContext','dashboardData','dashboardAdjustment','dashboardDrilldown','dashboardTheme','jimuReportAdapter','aiChartOption','aiGenerationProgress')) {
    $taskFiles.Add("src/utils/$taskUtility.js")
}
foreach ($taskDirectory in @('src/components/jimu-screen', 'src/data', 'public/jimu-screen', 'public/jimu-screen-reference')) {
    foreach ($taskFile in Get-ChildItem -LiteralPath (Join-Path $taskSourceUi $taskDirectory) -File -Recurse) {
        $taskFiles.Add([IO.Path]::GetRelativePath($taskSourceUi, $taskFile.FullName))
    }
}
foreach ($taskPath in @('src/styles/datasource-manage.css','src/api/report.js','src/api/reportPrompt.js')) { $taskFiles.Add($taskPath) }
$taskManifest = @()
foreach ($taskPath in $taskFiles) {
    $taskSource = Join-Path $taskSourceUi $taskPath
    $taskDestination = Join-Path $taskTarget $taskPath
    $taskHash = (Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash
    if (Test-Path -LiteralPath $taskDestination) {
        if ((Get-FileHash -LiteralPath $taskDestination -Algorithm SHA256).Hash -ne $taskHash) { throw "Target already differs; preserve it and review: $taskPath" }
    } else {
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($taskDestination)) -Force | Out-Null
        Copy-Item -LiteralPath $taskSource -Destination $taskDestination
    }
    $taskManifest += [PSCustomObject]@{ Path=$taskPath.Replace('\','/'); SourceSha256=$taskHash }
}
New-Item -ItemType Directory -Path (Join-Path $taskTarget 'licenses') -Force | Out-Null
$taskLicenseTarget = Join-Path $taskTarget 'licenses/Lynx-LICENSE.txt'
if (-not (Test-Path -LiteralPath $taskLicenseTarget)) { Copy-Item -LiteralPath (Join-Path $taskSourceRoot 'LICENSE') -Destination $taskLicenseTarget }
$taskManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskTarget 'source-manifest.json') -Encoding utf8
Write-Output "Imported $($taskFiles.Count) frontend/assets files. Source unchanged; customer, approval and Java runtime excluded."
