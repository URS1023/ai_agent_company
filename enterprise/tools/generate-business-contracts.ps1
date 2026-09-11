param(
    [string]$Uv = 'uv',
    [string]$Node = 'node'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$previousDirectory = Get-Location

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Business contract generation failed (exit $LASTEXITCODE)."
    }
}

try {
    Set-Location -LiteralPath $root
    Invoke-Checked -Program $Uv -Arguments @(
        'run', '--project', 'enterprise/api', '--no-sync', 'python', '-m', 'enterprise_platform.export_openapi',
        '--output', 'enterprise/contracts/business-openapi.json'
    )
    Invoke-Checked -Program $Node -Arguments @(
        'packages/contracts/node_modules/@hey-api/openapi-ts/bin/run.js',
        '-f', 'enterprise/tools/openapi-ts.business.config.ts'
    )
    Invoke-Checked -Program $Node -Arguments @('node_modules/vite-plus/bin/vp', 'fmt', 'enterprise/contracts/generated')
    Invoke-Checked -Program $Node -Arguments @(
        'node_modules/@typescript/native/bin/tsc', '-p', 'enterprise/contracts/tsconfig.json'
    )
    Invoke-Checked -Program $Node -Arguments @(
        'node_modules/@typescript/native/bin/tsc', '-p', 'enterprise/tools/tsconfig.business.json'
    )
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/contracts/contract.test.mjs')
    Write-Output 'Business OpenAPI and generated contracts verified. No runtime service or database was contacted.'
}
finally {
    Set-Location -LiteralPath $previousDirectory
}
