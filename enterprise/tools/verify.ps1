param(
    [string]$Uv = 'uv',
    [string]$Node = 'node',
    [ValidateRange(1, 8)]
    [int]$WebMaxWorkers = 2,
    [switch]$IncludeWeb
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$api = Join-Path $root 'enterprise/api'
$previousDirectory = Get-Location
$previousPluginSetting = $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
$previousPythonPath = $env:PYTHONPATH

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Verification failed: $Program $($Arguments -join ' ') (exit $LASTEXITCODE)"
    }
}

try {
    Set-Location -LiteralPath $root
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/tools/native-baseline.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/tools/reviewed-integrations.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/tools/github-push-policy.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/workflows/default-workflows.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/dashboard/template-assets.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/dashboard/renderer-assets.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/dashboard/viewer/preview.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/dashboard/viewer/renderer-assets.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/dashboard/publish-renderer.test.mjs')
    Invoke-Checked -Program $Node -Arguments @('enterprise/tools/native-baseline.mjs')

    Set-Location -LiteralPath $api
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    Invoke-Checked -Program $Uv -Arguments @('lock', '--project', $api, '--check')
    Invoke-Checked -Program $Uv -Arguments @('run', '--project', $api, '--no-sync', 'ruff', 'check', 'src', 'tests')
    Invoke-Checked -Program $Uv -Arguments @('run', '--project', $api, '--no-sync', 'ruff', 'format', '--check', 'src', 'tests')
    Invoke-Checked -Program $Uv -Arguments @('run', '--project', $api, '--no-sync', 'mypy', 'src')
    Invoke-Checked -Program $Uv -Arguments @(
        'run', '--project', $api, '--no-sync', 'python', '-m', 'pytest',
        '-c', 'pyproject.toml', '--confcutdir', '.', 'tests/unit', '-q'
    )

    Set-Location -LiteralPath $root
    $env:PYTHONPATH = Join-Path $root 'api'
    Invoke-Checked -Program $Uv -Arguments @(
        'run', '--project', $api, '--no-sync', 'python', '-m', 'pytest',
        '-c', 'enterprise/api/pyproject.toml', '--confcutdir', 'api/tests/unit_tests/core/workflow',
        'api/tests/unit_tests/core/workflow/test_enterprise_execution.py', '-q'
    )
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONPATH = (Join-Path $root 'enterprise/api/src') + [IO.Path]::PathSeparator + (Join-Path $root 'enterprise/plugin')
    Invoke-Checked -Program $Uv -Arguments @('run', '--project', $api, '--no-sync', 'ruff', 'check', 'enterprise/plugin')
    Invoke-Checked -Program $Uv -Arguments @('run', '--project', $api, '--no-sync', 'ruff', 'format', '--check', 'enterprise/plugin')
    Invoke-Checked -Program $Uv -Arguments @(
        'run', '--project', $api, '--no-sync', 'mypy', '--config-file',
        'enterprise/plugin/pyproject.toml', 'enterprise/plugin/managed_device_plugin'
    )
    Invoke-Checked -Program $Uv -Arguments @(
        'run', '--project', $api, '--no-sync', 'python', '-m', 'pytest',
        '-c', 'enterprise/plugin/pyproject.toml', '--confcutdir', 'enterprise/plugin',
        'enterprise/plugin/tests/test_client.py', 'enterprise/plugin/tests/test_bridge.py',
        'enterprise/plugin/tests/test_backend_contract.py', 'enterprise/plugin/tests/test_package.py', '-q'
    )
    $env:PYTHONPATH = $previousPythonPath
    Invoke-Checked -Program $Uv -Arguments @(
        'run', '--project', $api, '--no-sync', 'python', '-m', 'enterprise_platform.export_openapi',
        '--output', 'enterprise/contracts/business-openapi.json', '--check'
    )
    Invoke-Checked -Program $Node -Arguments @('--test', 'enterprise/contracts/contract.test.mjs')
    Invoke-Checked -Program $Node -Arguments @(
        'node_modules/@typescript/native/bin/tsc', '-p', 'enterprise/contracts/tsconfig.json'
    )
    Invoke-Checked -Program $Node -Arguments @(
        'node_modules/@typescript/native/bin/tsc', '-p', 'enterprise/tools/tsconfig.business.json'
    )

    if ($IncludeWeb) {
        Set-Location -LiteralPath (Join-Path $root 'packages/dev-proxy')
        Invoke-Checked -Program $Node -Arguments @('../../node_modules/vite-plus/bin/vp', 'pack')
        Set-Location -LiteralPath (Join-Path $root 'web')
        Invoke-Checked -Program $Node -Arguments @(
            '../node_modules/vite-plus/bin/vp', 'test', 'run',
            'app/components/enterprise', 'app/(commonLayout)/enterprise/api/_proxy/__tests__',
            'app/(commonLayout)/enterprise/workflow-templates',
            'service/enterprise-business/__tests__', 'service/client.spec.ts',
            'service/console-router-loader.spec.ts', '--reporter=dot', "--maxWorkers=$WebMaxWorkers"
        )
        Invoke-Checked -Program $Node -Arguments @(
            '../node_modules/vite-plus/bin/vp', 'test', 'run',
            'app/components/main-nav/__tests__/index.spec.tsx', '-t', 'enterprise', '--reporter=dot', "--maxWorkers=$WebMaxWorkers"
        )
        # Route validators must exist even on a checkout that has never run Next dev.
        Invoke-Checked -Program $Node -Arguments @('node_modules/next/dist/bin/next', 'typegen')
        # Delivery verification must read current JSON locale types, not a prior incremental snapshot.
        Invoke-Checked -Program $Node -Arguments @(
            '../node_modules/@typescript/native/bin/tsc', '--noEmit', '--pretty', 'false', '--incremental', 'false'
        )
    }
    Write-Output 'Enterprise focused verification passed. Native browser regression and service integration are separate gates.'
}
finally {
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = $previousPluginSetting
    $env:PYTHONPATH = $previousPythonPath
    Set-Location -LiteralPath $previousDirectory.Path
}
