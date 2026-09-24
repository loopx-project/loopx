#requires -Version 7.0

$ErrorActionPreference = "Stop"
$pointerPath = $env:LOOPX_CURRENT_RELEASE_FILE
if ([string]::IsNullOrWhiteSpace($pointerPath)) {
    $launcherPointer = Join-Path $PSScriptRoot "loopx-current-release.json"
    $pointerPath = if (Test-Path -LiteralPath $launcherPointer -PathType Leaf) {
        $launcherPointer
    } else {
        Join-Path $HOME ".local/share/loopx/current-release.json"
    }
}
if (-not (Test-Path -LiteralPath $pointerPath -PathType Leaf)) {
    throw "LoopX current release pointer was not found: $pointerPath"
}

$pointer = Get-Content -LiteralPath $pointerPath -Raw -Encoding UTF8 | ConvertFrom-Json
$releaseRoot = [string]$pointer.release_root
$python = [string]$pointer.python
if ([string]::IsNullOrWhiteSpace($releaseRoot) -or -not (Test-Path -LiteralPath $releaseRoot -PathType Container)) {
    throw "LoopX current release root is invalid: $releaseRoot"
}

$loopxCommand = ""
$loopxSubcommand = ""
$skipNextArg = $false
foreach ($argValue in $args) {
    if ($skipNextArg) {
        $skipNextArg = $false
        continue
    }
    if ($argValue -in @("--format", "--registry", "--runtime-root")) {
        $skipNextArg = $true
        continue
    }
    if ($argValue.StartsWith("--")) {
        continue
    }
    if ([string]::IsNullOrWhiteSpace($loopxCommand)) {
        $loopxCommand = $argValue
    } elseif ([string]::IsNullOrWhiteSpace($loopxSubcommand)) {
        $loopxSubcommand = $argValue
        break
    }
}
$hasNativeSchedulerFacts = @(
    $args | Where-Object {
        $_ -eq "--scheduler-host-facts-chunk" -or
        $_.StartsWith("--scheduler-host-facts-chunk=")
    }
).Count -gt 0
$hasNativeSchedulerTurn = @(
    $args | Where-Object {
        $_ -eq "--turn-instance-id" -or $_.StartsWith("--turn-instance-id=")
    }
).Count -gt 0
$isNativeSchedulerFollowup = (
    $loopxCommand -eq "quota" -and
    $loopxSubcommand -in @("scheduler-ack-current", "scheduler-fail-current") -and
    $hasNativeSchedulerFacts -and
    $hasNativeSchedulerTurn
)
if ($isNativeSchedulerFollowup) {
    $node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        throw "Native scheduler follow-up requires Node.js 22.22.3 or newer"
    }
    $nativeEntry = Join-Path $releaseRoot "loopx/control_plane/scheduler/heartbeat_followup_cli.ts"
    if (-not (Test-Path -LiteralPath $nativeEntry -PathType Leaf)) {
        throw "LoopX native scheduler follow-up entrypoint was not found: $nativeEntry"
    }
    $env:LOOPX_RELEASE_ROOT = $releaseRoot
    $env:LOOPX_COMMAND_PATH = $PSCommandPath
    & $node.Source --no-warnings --experimental-strip-types $nativeEntry @args
    exit $LASTEXITCODE
}

if ([string]::IsNullOrWhiteSpace($python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "LoopX configured Python executable is invalid: $python"
}
$entry = Join-Path $releaseRoot "scripts/loopx_entry.py"
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
    throw "LoopX Windows entry script was not found: $entry"
}

$env:LOOPX_RELEASE_ROOT = $releaseRoot
$env:LOOPX_COMMAND_PATH = $PSCommandPath
& $python -I $entry @args
exit $LASTEXITCODE
