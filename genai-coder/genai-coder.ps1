<#
.SYNOPSIS
    OpenCode working in the current directory, backed by the hosted GenAI.mil
    API instead of a local Strata server. Installs per-user; no administrator
    rights are required.

.DESCRIPTION
    The API key is read from api-key.txt beside this script. On the first run
    (or with -m) the script calls GET <baseURL>/models with the key and
    lets you choose a model; the choice is saved to setup.json and reused.

.EXAMPLE
    .\genai-coder.ps1
    .\genai-coder.ps1 -CheckOnly
    .\genai-coder.ps1 -Update
    .\genai-coder.ps1 -m
    .\genai-coder.ps1 -NoLaunch
    .\genai-coder.ps1 -Model gpt-4.1
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$Update,
    [switch]$m,
    [switch]$NoLaunch,
    [string]$Model,
    [string]$BaseUrl
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
}

# ----------------------------------------------------------------------------------------------------
# Paths and defaults
# ----------------------------------------------------------------------------------------------------

$ProjectDir = (Get-Location).ProviderPath

$AppDir      = Join-Path $env:LOCALAPPDATA 'genai-coder'
$OpenCodeDir = Join-Path $AppDir 'opencode'
$OpenCodeExe = Join-Path $OpenCodeDir 'opencode.exe'
$OpenCodeCfg = Join-Path $AppDir 'opencode.json'
$SetupPath   = Join-Path $AppDir 'setup.json'
$DownloadDir = Join-Path $AppDir 'downloads'
$LogDir      = Join-Path $AppDir 'logs'

$ApiKeyPath = Join-Path $PSScriptRoot 'api-key.txt'

if (-not $BaseUrl) {
    $BaseUrl = 'https://api.genai.mil/v1'
}

$SetupLockWaitMs = 300000

$script:TranscriptPath = $null

# ----------------------------------------------------------------------------------------------------
# Output and error helpers
# ----------------------------------------------------------------------------------------------------

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "    ok    $Message" -ForegroundColor Green
}

function Write-Info([string]$Message) {
    Write-Host "          $Message"
}

function Write-Skip([string]$Message) {
    Write-Host "    skip  $Message" -ForegroundColor DarkGray
}

function Write-Warn([string]$Message) {
    Write-Host "    warn  $Message" -ForegroundColor Yellow
}

function Fail([string]$Message, [string]$Hint = $null) {
    Write-Host ''
    Write-Host "STOPPED: $Message" -ForegroundColor Red

    if ($Hint) {
        Write-Host "         $Hint" -ForegroundColor Red
    }

    if ($script:TranscriptPath) {
        Write-Host "         Log: $script:TranscriptPath" -ForegroundColor DarkGray
    }

    Stop-ScriptTranscript
    exit 1
}

trap {
    $where = ''

    try {
        if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
            $where = " (line $($_.InvocationInfo.ScriptLineNumber))"
        }
    } catch {
    }

    Fail "unexpected error: $($_.Exception.Message)$where" `
        'Run the script again. If the issue repeats, inspect the run log.'
}

# ----------------------------------------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------------------------------------

function Read-Json([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Write-Utf8File([string]$Path, [string]$Text) {
    $parent = Split-Path -Parent $Path

    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    [IO.File]::WriteAllText(
        $Path,
        $Text,
        (New-Object Text.UTF8Encoding($false))
    )
}

function Invoke-Native([string]$Exe, [string[]]$ArgumentList) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        $output = @(& $Exe @ArgumentList 2>$null)
    } finally {
        $ErrorActionPreference = $oldPreference
    }

    return $output
}

function Reset-TerminalMouse {
    $esc = [char] 27
    $sequences = @(
        "$esc[?1000l",
        "$esc[?1002l",
        "$esc[?1003l",
        "$esc[?1004l",
        "$esc[?1005l",
        "$esc[?1006l",
        "$esc[?1015l",
        "$esc[?2004l"
    )
    $payload = $sequences -join ''
    $bytes = [Text.Encoding]::ASCII.GetBytes($payload)

    try {
        if (-not ('GenAiCoder.ConsoleNative' -as [type])) {
            Add-Type `
                -Namespace GenAiCoder `
                -Name ConsoleNative `
                -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern IntPtr GetStdHandle(int nStdHandle);

[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);

[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
'@
        }

        $native = [GenAiCoder.ConsoleNative]

        $out = $native::GetStdHandle(-11)
        [uint32] $outMode = 0

        if ($native::GetConsoleMode($out, [ref] $outMode)) {
            [void] $native::SetConsoleMode($out, [uint32] ($outMode -bor 0x0004))
        }

        $stdout = [Console]::OpenStandardOutput()
        $stdout.Write($bytes, 0, $bytes.Length)
        $stdout.Flush()

        $in = $native::GetStdHandle(-10)
        [uint32] $inMode = 0

        if ($native::GetConsoleMode($in, [ref] $inMode)) {
            $clear = [uint32] (0x0010 -bor 0x0200)
            [void] $native::SetConsoleMode($in, [uint32] ($inMode -band (-bnot $clear)))
        }
    } catch {
        [Console]::Out.Write($payload)
        [Console]::Out.Flush()
    }
}

function Invoke-Download([string]$Url, [string]$OutFile, [int]$Timeout = 300) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue

    if ($curl) {
        & $curl.Source -L --fail --retry 3 --retry-delay 5 -C - -o $OutFile $Url

        if ($LASTEXITCODE -eq 0) {
            return
        }

        Write-Warn "curl failed with exit code $LASTEXITCODE; retrying with Invoke-WebRequest"
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
    }

    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile -TimeoutSec $Timeout
    } catch {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue

        Fail "could not download $Url ($($_.Exception.Message))" `
            'Check network access and run the script again.'
    }
}

function Get-LatestRelease([string]$Repository, [int]$Attempts = 3, [int]$Timeout = 30) {
    $lastError = $null

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "https://api.github.com/repos/$Repository/releases/latest" `
                -Headers @{ 'User-Agent' = 'genai-coder' } `
                -TimeoutSec $Timeout `
                -ErrorAction Stop
        } catch {
            $lastError = $_.Exception.Message

            if ($attempt -lt $Attempts) {
                Write-Warn "GitHub release attempt $attempt of $Attempts failed ($lastError); retrying"
                Start-Sleep -Seconds (3 * $attempt)
            }
        }
    }

    Fail "could not contact GitHub for $Repository ($lastError)" `
        'Check network access and try again.'
}

function Test-Avx2 {
    try {
        if (-not ('GenAiCoder.Native' -as [type])) {
            Add-Type `
                -Namespace GenAiCoder `
                -Name Native `
                -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool IsProcessorFeaturePresent(uint feature);'
        }

        return [GenAiCoder.Native]::IsProcessorFeaturePresent(40)
    } catch {
        return $null
    }
}

function Get-VersionNumber([string]$Text) {
    $match = [regex]::Match("$Text", '\d+(\.\d+)*')

    if ($match.Success) {
        return $match.Value
    }

    return "$Text".Trim()
}

function Get-ComparableVersion([string]$Text) {
    $match = [regex]::Match("$Text", '\d+(\.\d+)*')

    if (-not $match.Success) {
        return $null
    }

    $parts = $match.Value.Split('.')

    if ($parts.Count -lt 2) {
        $parts += '0'
    } elseif ($parts.Count -gt 4) {
        $parts = $parts[0..3]
    }

    $version = $null

    if ([version]::TryParse(($parts -join '.'), [ref]$version)) {
        return $version
    }

    return $null
}

function Stop-ScriptTranscript {
    if ($script:TranscriptPath) {
        try {
            Stop-Transcript | Out-Null
        } catch {
        }

        $script:TranscriptPath = $null
    }
}

# ----------------------------------------------------------------------------------------------------
# API key and GenAI.mil model list
# ----------------------------------------------------------------------------------------------------

function Get-ApiKey {
    if (-not (Test-Path -LiteralPath $ApiKeyPath)) {
        Write-Utf8File $ApiKeyPath (
            @(
                '# GenAI.mil API key',
                '# Paste your key on the next line (no quotes), save this file,',
                '# and run genai-coder again.',
                ''
            ) -join [Environment]::NewLine
        )

        Fail "the API key file was created: $ApiKeyPath" `
            'Edit the file, paste your GenAI.mil API key, save it, and run the script again.'
    }

    $lines = @(
        Get-Content -LiteralPath $ApiKeyPath -Encoding UTF8 |
            ForEach-Object { "$_".Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') }
    )

    if (-not $lines) {
        Fail "no API key found in $ApiKeyPath" `
            'Paste your GenAI.mil API key into the file (a line that does not start with #) and run again.'
    }

    return $lines[0].Trim('"', "'")
}

function Get-GenAiModels([string]$Key, [string]$ApiBase, [int]$Attempts = 3, [int]$Timeout = 30) {
    $lastError = $null

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "$ApiBase/models" `
                -Headers @{ Authorization = "Bearer $Key" } `
                -TimeoutSec $Timeout `
                -ErrorAction Stop

            return @(
                $response.data |
                    Where-Object {
                        $_ -and $_.id -and "$($_.id)".Trim()
                    } |
                    ForEach-Object {
                        "$($_.id)".Trim()
                    } |
                    Sort-Object -Unique
            )
        } catch {
            $lastError = $_.Exception.Message

            if ($attempt -lt $Attempts) {
                Write-Warn "model list attempt $attempt of $Attempts failed ($lastError); retrying"
                Start-Sleep -Seconds (3 * $attempt)
            }
        }
    }

    return $null
}

function Select-GenAiModel([string]$Key) {
    Write-Step 'Getting the model list from GenAI.mil'

    $models = @(Get-GenAiModels -Key $Key -ApiBase $BaseUrl)

    if (-not $models) {
        Fail "could not get a usable model list from $BaseUrl/models." `
            "Check the key in $ApiKeyPath, network access to $BaseUrl, and -BaseUrl."
    }

    $script:DiscoveredModels = $models

    Write-Ok "$($models.Count) models available"

    $number = 0

    foreach ($id in $models) {
        $number++
        Write-Info "$number. $id"
    }

    $emptyTries = 0

    while ($true) {
        $answer = Read-Host 'Model (number or exact ID)'

        if ($null -eq $answer -or $answer.Trim() -eq '') {
            $emptyTries++

            if ($emptyTries -ge 2) {
                Fail 'no model was chosen.' `
                    'Run again with -Model <id>, or use -m in an interactive terminal.'
            }

            Write-Warn 'Enter a model number or an exact model ID from the list.'
            continue
        }

        $index = 0

        if (
            [int]::TryParse($answer.Trim(), [ref]$index) -and
            $index -ge 1 -and
            $index -le $models.Count
        ) {
            return $models[$index - 1]
        }

        $byName = @(
            $models | Where-Object {
                $_ -eq $answer.Trim()
            }
        ) | Select-Object -First 1

        if ($byName) {
            return $byName
        }

        Write-Warn 'Enter a model number or an exact model ID from the list.'
    }
}

# ----------------------------------------------------------------------------------------------------
# Saved setup
# ----------------------------------------------------------------------------------------------------

$savedSetup = Read-Json $SetupPath

if ($savedSetup) {
    if (-not $Model -and $savedSetup.model) {
        $Model = [string]$savedSetup.model
    }

    if (-not $PSBoundParameters.ContainsKey('BaseUrl') -and $savedSetup.baseUrl) {
        $BaseUrl = [string]$savedSetup.baseUrl
    }
}

if ($m) {
    $Model = $null
}

$BaseUrl = $BaseUrl.TrimEnd('/')

# ----------------------------------------------------------------------------------------------------
# Transcript
# ----------------------------------------------------------------------------------------------------

try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    Get-ChildItem -LiteralPath $LogDir -Filter 'run-*.log' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 19 |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $script:TranscriptPath = Join-Path $LogDir (
        'run-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    )

    Start-Transcript -LiteralPath $script:TranscriptPath | Out-Null
} catch {
    $script:TranscriptPath = $null
}

# ----------------------------------------------------------------------------------------------------
# Existing OpenCode installation
# ----------------------------------------------------------------------------------------------------

Write-Step 'Looking for existing installations'

$avx2 = Test-Avx2

$openCodePresent = $false
$openCodeVersion = $null

if (Test-Path -LiteralPath $OpenCodeExe) {
    $versionOutput = Invoke-Native $OpenCodeExe @('--version')

    if ($LASTEXITCODE -eq 0) {
        $openCodePresent = $true
        $openCodeVersion = "$($versionOutput | Select-Object -First 1)".Trim()
    }
}

if ($openCodePresent) {
    Write-Ok "OpenCode $openCodeVersion ($OpenCodeExe)"
} else {
    Write-Info "OpenCode: not installed (target: $OpenCodeExe)"
}

$openCodeRelease = $null

if ($openCodePresent -and $Update) {
    $openCodeRelease = Get-LatestRelease 'anomalyco/opencode'

    if (-not "$($openCodeRelease.tag_name)") {
        $openCodeRelease = $null
        Write-Warn 'GitHub returned no release tag; skipping the update check.'
    } else {
        $installedVersion = Get-ComparableVersion "$openCodeVersion"
        $latestVersion = Get-ComparableVersion $openCodeRelease.tag_name

        if ($installedVersion -and $latestVersion) {
            if ($latestVersion -gt $installedVersion) {
                Write-Info "OpenCode update available: $openCodeVersion -> $($openCodeRelease.tag_name)"
            } elseif ($latestVersion -lt $installedVersion) {
                Write-Ok "installed OpenCode $openCodeVersion is newer than the latest release ($($openCodeRelease.tag_name)); leaving it"
                $openCodeRelease = $null
            } else {
                Write-Ok 'OpenCode is up to date'
                $openCodeRelease = $null
            }
        } elseif (
            (Get-VersionNumber $openCodeRelease.tag_name) -eq
            (Get-VersionNumber "$openCodeVersion")
        ) {
            Write-Ok 'OpenCode is up to date'
            $openCodeRelease = $null
        } else {
            Write-Info "OpenCode update available: $openCodeVersion -> $($openCodeRelease.tag_name)"
        }
    }
}

# ----------------------------------------------------------------------------------------------------
# Model selection
# ----------------------------------------------------------------------------------------------------

$apiKey = $null
$modelId = $null
$script:DiscoveredModels = $null

if ($CheckOnly) {
    Write-Step 'Check only: no changes were made'

    $keyLine = if (Test-Path -LiteralPath $ApiKeyPath) {
        'present'
    } else {
        'missing (would be created)'
    }

    $modelLine = if ($Model) {
        $Model
    } else {
        'not chosen yet (first run asks after reading the key)'
    }

    $openCodeLine = if ($openCodeRelease) {
        "would be updated to $($openCodeRelease.tag_name)"
    } elseif ($openCodePresent) {
        'installed'
    } else {
        'would be installed'
    }

    Write-Info "Endpoint:   $BaseUrl"
    Write-Info "API key:    $keyLine ($ApiKeyPath)"
    Write-Info "Model:      $modelLine"
    Write-Info "OpenCode:   $openCodeLine"
    Write-Info "Config:     $OpenCodeCfg"
    Write-Info "Project:    $ProjectDir"

    Stop-ScriptTranscript
    exit 0
}

if ($m -or -not $Model) {
    $apiKey = Get-ApiKey
    $modelId = Select-GenAiModel $apiKey
} else {
    $modelId = $Model

    Write-Step 'Checking that the saved model is still offered'

    $apiKey = Get-ApiKey
    $models = Get-GenAiModels -Key $apiKey -ApiBase $BaseUrl -Attempts 1 -Timeout 15

    if ($null -eq $models) {
        Write-Warn 'could not reach the model list; keeping the saved model'
    } elseif ($models -notcontains $modelId) {
        Write-Warn "the saved model '$modelId' is no longer offered by $BaseUrl"
        $Model = $null
        $modelId = Select-GenAiModel $apiKey
    } else {
        $script:DiscoveredModels = $models
        Write-Ok "'$modelId' is still offered"
    }
}

$savedBaseUrl = if ($savedSetup -and $savedSetup.baseUrl) {
    ([string]$savedSetup.baseUrl).TrimEnd('/')
} else {
    $null
}

if ($modelId -ne $Model -or -not $savedSetup -or $BaseUrl -ne $savedBaseUrl) {
    $config = [ordered]@{
        model   = $modelId
        baseUrl = $BaseUrl
    }

    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Write-Utf8File $SetupPath ($config | ConvertTo-Json -Depth 5)
    Write-Ok "setup saved to $SetupPath"
}

Write-Ok "GenAI.mil model: $modelId"

# ----------------------------------------------------------------------------------------------------
# OpenCode installation
# ----------------------------------------------------------------------------------------------------

$lock = $null
$lockHeld = $false

if (-not $openCodePresent -or $openCodeRelease) {
    $lock = New-Object Threading.Mutex($false, 'Local\genai-coder-setup')

    try {
        try {
            if (-not $lock.WaitOne(0)) {
                Write-Step 'Another genai-coder setup is running; waiting for it to finish'

                if (-not $lock.WaitOne($SetupLockWaitMs)) {
                    Fail 'another genai-coder setup is still running.' `
                        'Wait for it to finish and run the script again.'
                }
            }

            $lockHeld = $true
        } catch [Threading.AbandonedMutexException] {
            $lockHeld = $true
        }

        Write-Step $(if ($openCodeRelease) {
            'Updating OpenCode'
        } else {
            'Installing OpenCode'
        })

        $release = if ($openCodeRelease) {
            $openCodeRelease
        } else {
            Get-LatestRelease 'anomalyco/opencode'
        }

        $assetName = if ($avx2 -eq $true) {
            'opencode-windows-x64.zip'
        } else {
            'opencode-windows-x64-baseline.zip'
        }

        $asset = $release.assets | Where-Object {
            $_.name -eq $assetName
        } | Select-Object -First 1

        if (-not $asset) {
            Fail "OpenCode release $($release.tag_name) does not contain $assetName."
        }

        $zip = Join-Path $DownloadDir $assetName
        $stage = Join-Path $AppDir 'opencode-unpack'

        Invoke-Download $asset.browser_download_url $zip

        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue

        try {
            Expand-Archive -LiteralPath $zip -DestinationPath $stage
        } catch {
            Remove-Item -LiteralPath $zip, $stage -Recurse -Force -ErrorAction SilentlyContinue

            Fail "the OpenCode archive is damaged ($($_.Exception.Message)); it was removed." `
                'Run the script again to download a fresh copy.'
        }

        $stagedExe = Join-Path $stage 'opencode.exe'

        if (-not (Test-Path -LiteralPath $stagedExe)) {
            Fail 'opencode.exe was not found in the OpenCode archive.'
        }

        New-Item -ItemType Directory -Force -Path $OpenCodeDir | Out-Null

        $oldExe = "$OpenCodeExe.old"

        Remove-Item -LiteralPath $oldExe -Force -ErrorAction SilentlyContinue

        if (Test-Path -LiteralPath $OpenCodeExe) {
            if (Test-Path -LiteralPath $oldExe) {
                $oldExe = "$OpenCodeExe.$(Get-Date -Format 'yyyyMMddHHmmss').old"
            }

            Rename-Item `
                -LiteralPath $OpenCodeExe `
                -NewName (Split-Path $oldExe -Leaf)
        }

        try {
            Move-Item -LiteralPath $stagedExe -Destination $OpenCodeExe
        } catch {
            if (Test-Path -LiteralPath $oldExe) {
                Rename-Item `
                    -LiteralPath $oldExe `
                    -NewName (Split-Path $OpenCodeExe -Leaf)
            }

            Fail "could not install opencode.exe ($($_.Exception.Message))."
        }

        Remove-Item -LiteralPath $zip, $stage -Recurse -Force -ErrorAction SilentlyContinue

        Write-Ok "OpenCode $($release.tag_name)"
    } finally {
        if ($lockHeld) {
            $lock.ReleaseMutex()
        }

        $lock.Dispose()
        $lock = $null
    }
} else {
    Write-Skip 'OpenCode already installed'
}

# ----------------------------------------------------------------------------------------------------
# Build OpenCode config
# ----------------------------------------------------------------------------------------------------

Write-Step 'Building the OpenCode config for GenAI.mil'

if ($null -eq $apiKey) {
    $apiKey = Get-ApiKey
}

$allModels = if ($script:DiscoveredModels) {
    @($script:DiscoveredModels)
} else {
    @()
}

if ($allModels -notcontains $modelId) {
    $allModels = @($modelId) + $allModels
}

$modelTable = [ordered]@{}

foreach ($id in $allModels) {
    $modelTable[$id] = [ordered]@{
        name = "$id (GenAI.mil)"
    }
}

$openCodeConfig = [ordered]@{
    '$schema' = 'https://opencode.ai/config.json'

    provider = [ordered]@{
        genai = [ordered]@{
            npm  = '@ai-sdk/openai-compatible'
            name = 'GenAI.mil'

            options = [ordered]@{
                baseURL = $BaseUrl
                apiKey  = '{env:GENAI_CODER_API_KEY}'
            }

            models = $modelTable
        }
    }

    model             = "genai/$modelId"
    small_model       = "genai/$modelId"
    enabled_providers = @('genai')
    share             = 'disabled'
    autoupdate        = $false
}

$openCodeJson = $openCodeConfig | ConvertTo-Json -Depth 10

$oldOpenCodeJson = if (Test-Path -LiteralPath $OpenCodeCfg) {
    Get-Content -LiteralPath $OpenCodeCfg -Raw -Encoding UTF8
} else {
    ''
}

if ("$oldOpenCodeJson".Trim() -ne $openCodeJson.Trim()) {
    Write-Utf8File $OpenCodeCfg $openCodeJson
    Write-Ok "OpenCode config written: $OpenCodeCfg"
} else {
    Write-Skip 'OpenCode config is already current'
}

# ----------------------------------------------------------------------------------------------------
# Launch OpenCode
# ----------------------------------------------------------------------------------------------------

Stop-ScriptTranscript

if ($NoLaunch) {
    Write-Step 'Ready'
    Write-Info "endpoint: $BaseUrl"
    Write-Info "OpenCode config: $OpenCodeCfg"
    Write-Info "OpenCode model: genai/$modelId"
    exit 0
}

Write-Step "Opening OpenCode in $ProjectDir"

$env:GENAI_CODER_API_KEY = $apiKey

# OPENCODE_CONFIG allows inspection of the generated file.
$env:OPENCODE_CONFIG = $OpenCodeCfg

# OPENCODE_CONFIG_CONTENT is an inline runtime override. It prevents another
# OpenCode config in the project tree from replacing the GenAI.mil provider
# or the selected model for this OpenCode session.
$env:OPENCODE_CONFIG_CONTENT = $openCodeJson

Write-Info "OpenCode config: $OpenCodeCfg"
Write-Info 'OpenCode provider: genai'
Write-Info "OpenCode model: genai/$modelId"
Write-Info "Endpoint: $BaseUrl"

if (-not (Test-Path -LiteralPath $OpenCodeCfg)) {
    Fail "the generated OpenCode configuration file does not exist: $OpenCodeCfg"
}

$debugConfigPath = Join-Path $LogDir 'opencode-resolved-config.log'

try {
    $oldDebugPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        $debugOutput = @(
            & $OpenCodeExe debug config 2>&1
        )
    } finally {
        $ErrorActionPreference = $oldDebugPreference
    }

    $debugText = $debugOutput -join [Environment]::NewLine

    if ($apiKey) {
        $debugText = $debugText.Replace($apiKey, '{redacted-api-key}')
    }

    Write-Utf8File $debugConfigPath $debugText

    if ($debugText -notmatch [regex]::Escape('"genai"')) {
        Write-Warn 'OpenCode resolved configuration did not show the GenAI.mil provider.'
        Write-Warn "resolved config log: $debugConfigPath"
    } elseif ($debugText -notmatch [regex]::Escape("genai/$modelId")) {
        Write-Warn "OpenCode resolved configuration did not show genai/$modelId."
        Write-Warn "resolved config log: $debugConfigPath"
    } else {
        Write-Ok 'OpenCode resolved the GenAI.mil provider and selected model'
    }
} catch {
    Write-Warn "could not run 'opencode debug config' ($($_.Exception.Message))"
}

Push-Location -LiteralPath $ProjectDir

try {
    & $OpenCodeExe
} finally {
    Pop-Location
    Reset-TerminalMouse

    Remove-Item Env:GENAI_CODER_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:OPENCODE_CONFIG_CONTENT -ErrorAction SilentlyContinue
    Remove-Item Env:OPENCODE_CONFIG -ErrorAction SilentlyContinue
}

Write-Step 'Done'
Write-Info 'no local server was used; nothing to stop'
