param([string]$CompilerPath = '')

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($CompilerPath)) {
    $compilerCommand = Get-Command gcc.exe -ErrorAction SilentlyContinue
    if ($compilerCommand) {
        $CompilerPath = $compilerCommand.Source
    } elseif (Test-Path -LiteralPath 'D:\QT\Tools\mingw1120_64\bin\gcc.exe') {
        $CompilerPath = 'D:\QT\Tools\mingw1120_64\bin\gcc.exe'
    } else {
        throw 'Pass -CompilerPath with a MinGW GCC executable.'
    }
}

$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$testSource = Join-Path $PSScriptRoot 'test_host_control.c'
$testExe = Join-Path $PSScriptRoot 'host_control_test.exe'
$savedPath = $env:PATH
try {
    $env:PATH = (Split-Path -Parent $CompilerPath) + ';' + $env:PATH
    & $CompilerPath -std=c99 -Wall -Wextra -Werror -Wno-unused-parameter -O0 -g -static `
        ('-I' + (Join-Path $PSScriptRoot 'stubs')) `
        ('-I' + (Join-Path $workspaceRoot 'Core\Inc')) $testSource -o $testExe
    if ($LASTEXITCODE -ne 0) { throw 'Native firmware test compilation failed.' }
    & $testExe
    if ($LASTEXITCODE -ne 0) { throw 'Native firmware tests failed.' }
} finally {
    $env:PATH = $savedPath
}
