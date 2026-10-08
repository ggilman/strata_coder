<#
.SYNOPSIS
    Local AI coder: Strata serving Qwen3.8-Flash-Next, with OpenCode working
    in the current directory. Installs per-user; no administrator rights are
    required for supported NVIDIA systems.

.EXAMPLE
    .\strata-coder.ps1
    .\strata-coder.ps1 -CheckOnly
    .\strata-coder.ps1 -Update
    .\strata-coder.ps1 -Stop
    .\strata-coder.ps1 -Stats
    .\strata-coder.ps1 -Setup
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [ValidateSet('qwen', 'coder')][string]$Family,
    [string]$Model,
    [int]$Context,
    [string]$DataDir,
    [int]$Port = 8080,
    [switch]$NoLaunch,
    [switch]$Update,
    [switch]$Stop,
    [switch]$Stats,
    [switch]$Setup
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
}

# ----------------------------------------------------------------------------------------------------
# Paths, version pins, and model definitions
# ----------------------------------------------------------------------------------------------------

$ProjectDir = (Get-Location).ProviderPath

$AppDir      = Join-Path $env:LOCALAPPDATA 'strata-coder'
$OpenCodeDir = Join-Path $AppDir 'opencode'
$OpenCodeExe = Join-Path $OpenCodeDir 'opencode.exe'
$OpenCodeCfg = Join-Path $AppDir 'opencode.json'
$StatePath   = Join-Path $AppDir 'state.json'
$SetupPath   = Join-Path $AppDir 'setup.json'
$DownloadDir = Join-Path $AppDir 'downloads'
$LogDir      = Join-Path $AppDir 'logs'
$UnpackDir   = Join-Path $AppDir 'strata-unpack'

$StrataSettingsPath = Join-Path $env:APPDATA 'Strata\settings.json'

$PinnedStrata = 'v0.1.40.3'

$Variants = @(
    @{
        Family     = 'qwen'
        Model      = 'IQ3_S'
        MinRamGB   = 60
        DownloadGB = 83.6
        ArenaGB    = 50.3
        Title      = 'Qwen3.8-Flash-Next IQ3_S (3.5-bit, best quality; needs about 62 GB RAM)'
        Repo       = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF'
    },
    @{
        Family     = 'qwen'
        Model      = 'IQ3_XXS'
        MinRamGB   = 58
        DownloadGB = 75.8
        ArenaGB    = 42.9
        Title      = 'Qwen3.8-Flash-Next IQ3_XXS (3-bit)'
        Repo       = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF'
    },
    @{
        Family     = 'qwen'
        Model      = 'IQ2_XS'
        MinRamGB   = 46
        DownloadGB = 68.0
        ArenaGB    = 35.5
        Title      = 'Qwen3.8-Flash-Next IQ2_XS (2-bit; recommended for 48-64 GB RAM)'
        Repo       = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF'
    },
    @{
        Family     = 'coder'
        Model      = 'IQ1_M'
        MinRamGB   = 30
        DownloadGB = 58.4
        ArenaGB    = 23.4
        Title      = 'Qwen3.8-Flash-Next Coder IQ1_M (code-focused; fits 32 GB RAM)'
        Repo       = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF'
    }
)

$MinVramMiB    = 11500
$MinDriver     = 580
$MinComputeCap = 7.5
$MtpGB         = 8
$Margin        = 1.15
$LoadMinutes   = 15

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

function Save-State([hashtable]$Changes) {
    $state = @{}
    $existing = Read-Json $StatePath

    if ($existing) {
        $existing.PSObject.Properties | ForEach-Object {
            $state[$_.Name] = $_.Value
        }
    }

    foreach ($key in $Changes.Keys) {
        $state[$key] = $Changes[$key]
    }

    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Write-Utf8File $StatePath ($state | ConvertTo-Json -Depth 8)
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
        "$esc[?1005l",
        "$esc[?1006l",
        "$esc[?1015l"
    )

    [Console]::Out.Write(($sequences -join ''))
    [Console]::Out.Flush()
}

function Get-FreeGB([string]$Path) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    $drive = New-Object IO.DriveInfo($root)

    return [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
}

function Get-FolderGB([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }

    $sum = (
        Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum
    ).Sum

    if (-not $sum) {
        return 0
    }

    return [math]::Round($sum / 1GB, 1)
}

function Invoke-Download([string]$Url, [string]$OutFile) {
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
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile
    } catch {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue

        Fail "could not download $Url ($($_.Exception.Message))" `
            'Check network access and run the script again.'
    }
}

function Get-LatestRelease([string]$Repository) {
    try {
        return Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "https://api.github.com/repos/$Repository/releases/latest" `
            -Headers @{ 'User-Agent' = 'strata-coder' } `
            -ErrorAction Stop
    } catch {
        Fail "could not contact GitHub for $Repository ($($_.Exception.Message))" `
            'Check network access and try again.'
    }
}

function Show-LogTail([string]$Path, [int]$Lines = 25) {
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Write-Host "    --- last $Lines lines of $Path ---" -ForegroundColor DarkGray

        Get-Content -LiteralPath $Path -Tail $Lines | ForEach-Object {
            Write-Host "    | $_"
        }
    }
}

# ----------------------------------------------------------------------------------------------------
# Strata server helpers
# ----------------------------------------------------------------------------------------------------

function Get-StrataHealth([int]$ServerPort) {
    try {
        $health = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$ServerPort/health" `
            -TimeoutSec 5 `
            -ErrorAction Stop

        if ($health.service -eq 'strata') {
            return $health
        }
    } catch {
    }

    return $null
}

function Get-StrataModels([int]$ServerPort) {
    try {
        $response = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$ServerPort/v1/models" `
            -TimeoutSec 10 `
            -ErrorAction Stop

        return @(
            $response.data | Where-Object {
                $_ -and $_.id -and "$($_.id)".Trim()
            }
        )
    } catch {
        return @()
    }
}

function Test-StrataReady([int]$ServerPort) {
    $health = Get-StrataHealth $ServerPort

    if (-not $health) {
        return $false
    }

    if ($health.loaded) {
        return $true
    }

    return (Get-StrataModels $ServerPort).Count -gt 0
}

function Test-StrataChatCompletion([int]$ServerPort, [string]$ModelId) {
    $body = @{
        model       = $ModelId
        messages    = @(
            @{
                role    = 'user'
                content = 'Reply with exactly: OK'
            }
        )
        temperature = 0
        max_tokens  = 4
        stream      = $false
    } | ConvertTo-Json -Depth 8

    try {
        $response = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$ServerPort/v1/chat/completions" `
            -Method Post `
            -ContentType 'application/json' `
            -Headers @{ Authorization = 'Bearer local' } `
            -Body $body `
            -TimeoutSec 180 `
            -ErrorAction Stop

        return [pscustomobject]@{
            Ok    = $true
            Error = $null
            Reply = $response
        }
    } catch {
        return [pscustomobject]@{
            Ok    = $false
            Error = $_.Exception.Message
            Reply = $null
        }
    }
}

function Test-PortInUse([int]$ServerPort) {
    $client = New-Object Net.Sockets.TcpClient

    try {
        $client.Connect('127.0.0.1', $ServerPort)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-StrataServerProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match 'server\.py' -and
            $_.CommandLine -match '--engine\s+"?strata'
        }
}

function Get-ServerPort($Process) {
    if ($Process.CommandLine -match '--port\s+"?(\d+)') {
        return [int]$Matches[1]
    }

    return 8080
}

function Stop-StrataServers {
    $processes = @(Get-StrataServerProcesses)

    foreach ($process in $processes) {
        [void](Invoke-Native taskkill.exe @('/T', '/F', '/PID', $process.ProcessId))
        Write-Ok "stopped the Strata server (PID $($process.ProcessId))"
    }

    return $processes.Count
}

function Wait-StrataReady($Process) {
    $deadline = (Get-Date).AddMinutes($LoadMinutes)

    while ((Get-Date) -lt $deadline) {
        if (Test-StrataReady $Port) {
            return $true
        }

        if ($Process) {
            if ($Process.HasExited) {
                return $false
            }
        } elseif (-not @(Get-StrataServerProcesses)) {
            return $false
        }

        Start-Sleep -Seconds 3
    }

    return $false
}

# ----------------------------------------------------------------------------------------------------
# Setup, candidates, and system discovery
# ----------------------------------------------------------------------------------------------------

function Get-Candidates {
    $candidates = $Variants

    if ($Family) {
        $candidates = @(
            $candidates | Where-Object {
                $_.Family -eq $Family
            }
        )
    }

    if ($Model) {
        $candidates = @(
            $candidates | Where-Object {
                $_.Model -eq $Model.ToUpper()
            }
        )
    }

    if (-not $candidates) {
        $known = ($Variants | ForEach-Object {
            "$($_.Family)/$($_.Model)"
        }) -join ', '

        Fail "no model variant matches -Family '$Family' -Model '$Model'" `
            "Choose one of: $known"
    }

    return $candidates
}

function Prompt-SetupParams($Spec) {
    $saved = Read-Json $SetupPath

    $defaultDataDir = $null

    if ($saved -and $saved.dataDir) {
        $defaultDataDir = $saved.dataDir
    }

    if (-not $defaultDataDir) {
        $priorSettings = Read-Json $StrataSettingsPath

        if (
            $priorSettings -and
            $priorSettings.data_dir -and
            (Test-Path -LiteralPath $priorSettings.data_dir)
        ) {
            $defaultDataDir = $priorSettings.data_dir
        }
    }

    if (-not $defaultDataDir) {
        $defaultDataDir = Join-Path $env:USERPROFILE 'ai-models\models'
    }

    $defaultContext = 0
    $defaultPort = 8080
    $defaultNoLaunch = $false

    if ($saved) {
        try {
            if ($null -ne $saved.context) {
                $defaultContext = [int]$saved.context
            }
        } catch {
        }

        try {
            if ($saved.port) {
                $defaultPort = [int]$saved.port
            }
        } catch {
        }

        if ($saved.noLaunch -eq $true) {
            $defaultNoLaunch = $true
        }
    }

    $selected = $null

    if ($Family -or $Model) {
        $requested = @(Get-Candidates)

        $selected = @(
            $requested | Where-Object {
                $Spec.RamGB -ge $_.MinRamGB
            }
        ) | Select-Object -First 1

        if (-not $selected) {
            $selected = $requested | Select-Object -First 1
        }
    }

    if (-not $selected -and $saved -and $saved.family -and $saved.model) {
        $selected = @(
            $Variants | Where-Object {
                $_.Family -eq $saved.family -and
                $_.Model -eq $saved.model
            }
        ) | Select-Object -First 1
    }

    if (-not $selected) {
        $selected = @(
            $Variants | Where-Object {
                $Spec.RamGB -ge $_.MinRamGB
            }
        ) | Select-Object -First 1
    }

    if (-not $selected) {
        Fail 'this PC does not have enough RAM for any configured Strata model variant.'
    }

    Write-Step 'Asking for setup parameters (Enter keeps the displayed default)'

    if (-not ($Family -or $Model)) {
        $number = 0

        foreach ($item in $Variants) {
            $number++
            Write-Info "$number. $($item.Title) (needs $($item.MinRamGB) GB RAM)"
        }

        $defaultIndex = [array]::IndexOf($Variants, $selected) + 1
        Write-Info "Default: $defaultIndex. $($selected.Title)"

        while ($true) {
            $answer = Read-Host 'Variant'

            if ($answer.Trim() -eq '') {
                break
            }

            $index = 0

            if (
                [int]::TryParse($answer.Trim(), [ref]$index) -and
                $index -ge 1 -and
                $index -le $Variants.Count
            ) {
                $selected = $Variants[$index - 1]
                break
            }

            $byName = @(
                $Variants | Where-Object {
                    $_.Model.ToUpper() -eq $answer.Trim().ToUpper()
                }
            ) | Select-Object -First 1

            if ($byName) {
                $selected = $byName
                break
            }

            Write-Warn 'Enter a model number, a model name such as IQ2_XS, or leave the field blank.'
        }
    }

    $selectedDataDir = $defaultDataDir

    if (-not $DataDir) {
        $answer = Read-Host "Model folder [$defaultDataDir]"

        if ($answer.Trim() -ne '') {
            $selectedDataDir = [IO.Path]::GetFullPath($answer.Trim())
        }
    }

    $contextChoices = @(4096, 8192, 16384, 32768, 65536, 131072, 262144)
    $selectedContext = $defaultContext

    if (-not $Context) {
        $contextNumber = 0

        foreach ($tokens in $contextChoices) {
            $contextNumber++
            Write-Info "$contextNumber. $([int]($tokens / 1024))k ($tokens tokens)"
        }

        if ($defaultContext -eq 0) {
            Write-Info 'Default: 0. Strata recommendation'
        } else {
            $defaultContextIndex = [array]::IndexOf($contextChoices, $defaultContext) + 1

            if ($defaultContextIndex -ge 1) {
                Write-Info "Default: $defaultContextIndex. $([int]($defaultContext / 1024))k"
            } else {
                Write-Info "Default: $defaultContext tokens"
            }
        }

        while ($true) {
            $answer = Read-Host "Context [0 = Strata recommendation, 1-$($contextChoices.Count) = 4k-256k]"

            if ($answer.Trim() -eq '') {
                break
            }

            $index = 0

            if (
                [int]::TryParse($answer.Trim(), [ref]$index) -and
                $index -ge 0 -and
                $index -le $contextChoices.Count
            ) {
                if ($index -eq 0) {
                    $selectedContext = 0
                } else {
                    $selectedContext = $contextChoices[$index - 1]
                }

                break
            }

            Write-Warn "Enter a context number from 0 through $($contextChoices.Count), or leave the field blank."
        }
    }

    $selectedPort = $defaultPort

    if ($Port -eq 8080) {
        while ($true) {
            $answer = Read-Host "Server port [$defaultPort]"

            if ($answer.Trim() -eq '') {
                break
            }

            $number = 0

            if (
                [int]::TryParse($answer.Trim(), [ref]$number) -and
                $number -ge 1 -and
                $number -le 65535
            ) {
                $selectedPort = $number
                break
            }

            Write-Warn 'Enter a port number from 1 through 65535 or leave the field blank.'
        }
    }

    # Correct logic:
    # "yes, open OpenCode" => noLaunch = false
    # "no, do not open it" => noLaunch = true
    $selectedNoLaunch = $defaultNoLaunch

    if ($NoLaunch) {
        $selectedNoLaunch = $true
    } else {
        $defaultAnswer = if ($selectedNoLaunch) { 'n' } else { 'y' }
        $answer = Read-Host "Open OpenCode after setup? (y/n, default $defaultAnswer)"

        if ($answer.Trim() -ne '') {
            $selectedNoLaunch = ($answer.Trim().ToUpper() -ne 'Y')
        }
    }

    $config = [ordered]@{
        family   = $selected.Family
        model    = $selected.Model
        context  = $selectedContext
        dataDir  = $selectedDataDir
        port     = $selectedPort
        noLaunch = $selectedNoLaunch
    }

    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Write-Utf8File $SetupPath ($config | ConvertTo-Json -Depth 5)
    Write-Ok "setup saved to $SetupPath"

    return $config
}

function Get-SystemSpec {
    $spec = [ordered]@{}

    $spec.OS = (Get-CimInstance Win32_OperatingSystem).Caption
    $spec.RamGB = [math]::Round(
        (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB,
        1
    )

    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1

    $spec.Cpu = $cpu.Name.Trim()
    $spec.Cores = $cpu.NumberOfCores
    $spec.Avx2 = $null

    try {
        if (-not ('AiCoder.Native' -as [type])) {
            Add-Type `
                -Namespace AiCoder `
                -Name Native `
                -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool IsProcessorFeaturePresent(uint feature);'
        }

        $spec.Avx2 = [AiCoder.Native]::IsProcessorFeaturePresent(40)
    } catch {
    }

    $spec.Gpu = $null

    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    $smiPath = $null

    if ($smi) {
        $smiPath = $smi.Source
    } elseif (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe") {
        $smiPath = "$env:SystemRoot\System32\nvidia-smi.exe"
    }

    if ($smiPath) {
        $rows = Invoke-Native $smiPath @(
            '--query-gpu=index,name,memory.total,driver_version,compute_cap',
            '--format=csv,noheader,nounits'
        )

        $gpus = foreach ($row in @($rows)) {
            $fields = $row -split '\s*,\s*'

            if ($fields.Count -ge 5) {
                [pscustomobject]@{
                    Index      = [int]$fields[0]
                    Name       = $fields[1].Trim()
                    VramMiB    = [int]$fields[2]
                    Driver     = $fields[3].Trim()
                    ComputeCap = [double]$fields[4]
                }
            }
        }

        $spec.Gpu = $gpus |
            Sort-Object VramMiB -Descending |
            Select-Object -First 1
    }

    $spec.OtherGpus = @(
        Get-CimInstance Win32_VideoController |
            ForEach-Object {
                $_.Name
            }
    )

    return $spec
}

# ----------------------------------------------------------------------------------------------------
# Installation detection and setup helpers
# ----------------------------------------------------------------------------------------------------

function Test-StrataConfig([string]$Root) {
    $config = Read-Json (Join-Path $Root "strata-$tag.json")

    if (-not $config -or -not $config.exe) {
        return $false
    }

    return (
        (Test-Path -LiteralPath $config.exe) -and
        (Test-Path -LiteralPath (Join-Path $Root '.venv\Scripts\python.exe'))
    )
}

function Get-StrataVersion([string]$Root) {
    if ((Split-Path $Root -Leaf) -match '^strata-(v[\d.]+)$') {
        return $Matches[1]
    }

    if ($state -and $state.StrataRoot -eq $Root -and $state.StrataVersion) {
        return $state.StrataVersion
    }

    return 'unknown'
}

function Install-StrataCode([string]$Version) {
    $destination = Join-Path $AppDir "strata-$Version"

    if (Test-Path -LiteralPath (Join-Path $destination 'setup.py')) {
        return $destination
    }

    $zip = Join-Path $DownloadDir "strata-$Version.zip"

    Invoke-Download `
        "https://github.com/Niko1221/Strata/archive/refs/tags/$Version.zip" `
        $zip

    Remove-Item -LiteralPath $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue

    try {
        Expand-Archive -LiteralPath $zip -DestinationPath $UnpackDir
    } catch {
        Remove-Item -LiteralPath $zip, $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue

        Fail "the Strata archive is damaged ($($_.Exception.Message)); it was removed." `
            'Run the script again to download a fresh copy.'
    }

    $inner = Get-ChildItem -LiteralPath $UnpackDir -Directory | Select-Object -First 1

    if (
        -not $inner -or
        -not (Test-Path -LiteralPath (Join-Path $inner.FullName 'setup.py')) -or
        -not (Test-Path -LiteralPath (Join-Path $inner.FullName 'START-HERE.bat'))
    ) {
        Remove-Item -LiteralPath $zip, $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue

        Fail "the Strata $Version archive does not contain expected setup files." `
            'Run the script again. If it repeats, verify the Strata release.'
    }

    Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue

    Move-Item -LiteralPath $inner.FullName -Destination $destination

    Remove-Item -LiteralPath $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue

    return $destination
}

function Invoke-StrataSetup([string]$Root, [string[]]$ArgumentList) {
    $quotedArguments = (
        $ArgumentList | ForEach-Object {
            if ("$_" -match '\s') {
                "`"$_`""
            } else {
                "$_"
            }
        }
    ) -join ' '

    $processInfo = New-Object Diagnostics.ProcessStartInfo

    $processInfo.FileName = $env:ComSpec
    $processInfo.Arguments = '/d /s /c ""' +
        (Join-Path $Root 'START-HERE.bat') +
        '" ' +
        $quotedArguments +
        ' < NUL 2>&1"'

    $processInfo.WorkingDirectory = $Root
    $processInfo.UseShellExecute = $false
    $processInfo.RedirectStandardOutput = $true
    $processInfo.StandardOutputEncoding = [Text.Encoding]::UTF8

    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $process = [Diagnostics.Process]::Start($processInfo)
    $lastProgress = [DateTime]::MinValue

    while ($null -ne ($line = $process.StandardOutput.ReadLine())) {
        if ($line -match 'has to be compiled|Install them now\?|Windows will ask for permission') {
            [void](Invoke-Native taskkill.exe @('/T', '/F', '/PID', $process.Id))

            Fail 'Strata has no ready-made engine for this GPU and is requesting build tools that require administrator rights.' `
                "On a system where you have administrator rights, run $Root\START-HERE.bat manually."
        }

        if ($line -match '\d+%\|' -or $line -match '[\d.]+\s*[MG]B/s') {
            if (((Get-Date) - $lastProgress).TotalSeconds -lt 5) {
                continue
            }

            $lastProgress = Get-Date
        }

        if ($line.Trim()) {
            Write-Host "    | $line"
        }
    }

    $process.WaitForExit()

    return $process.ExitCode
}

function Get-SetupArgs {
    $arguments = @(
        '--family', $variant.Family,
        '--model', $variant.Model,
        '--vision', 'no',
        '--data-dir', $DataDir,
        '--port', $Port,
        '--no-browser',
        '--no-start',
        '--yes'
    )

    if ($Context) {
        $arguments += @('--context', $Context)
    }

    if ($ggufDir) {
        $arguments += @('--gguf-dir', $ggufDir)
    }

    return $arguments
}

# ----------------------------------------------------------------------------------------------------
# Stop and statistics modes
# ----------------------------------------------------------------------------------------------------

if ($Stop) {
    Write-Step 'Stopping the Strata server'

    if ((Stop-StrataServers) -eq 0) {
        Write-Skip 'no Strata server process was found'
    }

    exit 0
}

if ($Stats) {
    $process = @(Get-StrataServerProcesses) | Select-Object -First 1

    if (-not (Get-StrataHealth $Port) -and $process) {
        $Port = Get-ServerPort $process
    }

    $base = "http://127.0.0.1:$Port"

    if (-not (Get-StrataHealth $Port)) {
        Fail "no Strata server is answering on port $Port."
    }

    Write-Step "Strata speed on $base (Ctrl+C to quit)"

    $lastShown = $null

    while ($true) {
        try {
            $status = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "$base/status" `
                -TimeoutSec 5

            $v1Status = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "$base/v1/status" `
                -TimeoutSec 5
        } catch {
            Fail "lost the connection to the server ($($_.Exception.Message))"
        }

        $timing = $v1Status.last_timings

        if ($timing -and $timing.predicted_per_second) {
            $last = (
                'last answer: {0} tokens at {1} tok/s; prompt {2} tokens at {3} tok/s' -f
                $timing.predicted_n,
                $timing.predicted_per_second,
                $timing.prompt_n,
                $timing.prompt_per_second
            )
        } else {
            $last = 'last answer: none yet'
        }

        if ($status.busy -and $status.tokens_per_s) {
            $live = 'generating: {0} tok/s now' -f $status.tokens_per_s
        } elseif ($status.busy) {
            $live = 'busy'
        } else {
            $live = 'idle'
        }

        Write-Host (
            '{0}  {1,-48} {2}' -f
            (Get-Date -Format 'HH:mm:ss'),
            $live,
            $last
        )

        if ($last -ne $lastShown) {
            $lastShown = $last
        }

        Start-Sleep -Seconds 2
    }
}

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
# Hardware check
# ----------------------------------------------------------------------------------------------------

Write-Step 'Checking this PC'

$spec = Get-SystemSpec

Write-Info "OS:   $($spec.OS)"
Write-Info "CPU:  $($spec.Cpu) ($($spec.Cores) cores, AVX2: $(if ($null -eq $spec.Avx2) { 'unknown' } elseif ($spec.Avx2) { 'yes' } else { 'no' }))"
Write-Info "RAM:  $($spec.RamGB) GB"

if ($spec.Gpu) {
    Write-Info (
        'GPU:  {0}, {1:N1} GB VRAM, driver {2}, compute {3}' -f
        $spec.Gpu.Name,
        ($spec.Gpu.VramMiB / 1024),
        $spec.Gpu.Driver,
        $spec.Gpu.ComputeCap
    )
} else {
    Write-Info "GPU:  $($spec.OtherGpus -join ', ') (no NVIDIA GPU found)"
}

$cannotRun = 'this PC cannot run any configured Qwen3.8-Flash-Next variant properly'

if ([Environment]::OSVersion.Version.Major -lt 10) {
    Fail "$cannotRun`: Windows 10 or Windows 11 is required."
}

if (-not $spec.Gpu) {
    Fail "$cannotRun`: an NVIDIA GPU with at least 12 GB VRAM is required." `
        'This script supports NVIDIA GPUs only.'
}

if ($spec.Gpu.VramMiB -lt $MinVramMiB) {
    Fail (
        "$cannotRun`: the GPU has {0:N1} GB VRAM; at least 12 GB is required." -f
        ($spec.Gpu.VramMiB / 1024)
    )
}

$driverMajor = [int](($spec.Gpu.Driver -split '\.')[0])

if ($driverMajor -lt $MinDriver) {
    Fail "NVIDIA driver $($spec.Gpu.Driver) is too old; Strata requires driver $MinDriver or newer." `
        'Update the NVIDIA driver and run this script again.'
}

if ($spec.Gpu.ComputeCap -lt $MinComputeCap) {
    Fail "$cannotRun`: the GPU is older than the supported RTX 20-series baseline." `
        'This script requires compute capability 7.5 or newer.'
}

if ($spec.Avx2 -eq $false) {
    Write-Warn 'this CPU has no AVX2 support; Strata may run slowly'
}

# ----------------------------------------------------------------------------------------------------
# Saved setup settings
# ----------------------------------------------------------------------------------------------------

$savedSetup = Read-Json $SetupPath

if ($Setup -and -not $CheckOnly) {
    $savedSetup = Prompt-SetupParams $spec
}

if ($savedSetup) {
    if (-not $Family -and $savedSetup.family) {
        $Family = $savedSetup.family
    }

    if (-not $Model -and $savedSetup.model) {
        $Model = $savedSetup.model
    }

    if (-not $Context) {
        try {
            if ($null -ne $savedSetup.context) {
                $Context = [int]$savedSetup.context
            }
        } catch {
        }
    }

    if (-not $DataDir -and $savedSetup.dataDir) {
        $DataDir = $savedSetup.dataDir
    }

    if ($Port -eq 8080) {
        try {
            if ($savedSetup.port) {
                $Port = [int]$savedSetup.port
            }
        } catch {
        }
    }

    if (-not $NoLaunch -and ($savedSetup.noLaunch -eq $true)) {
        $NoLaunch = $true
    }
}

$candidates = @(Get-Candidates)

$variant = @(
    $candidates | Where-Object {
        $spec.RamGB -ge $_.MinRamGB
    }
) | Select-Object -First 1

if (-not $variant) {
    $smallest = $candidates | Sort-Object MinRamGB | Select-Object -First 1

    $message = (
        '{0}/{1} needs about {2} GB RAM; this PC has {3} GB.' -f
        $smallest.Family,
        $smallest.Model,
        ($smallest.MinRamGB + 2),
        $spec.RamGB
    )

    if ($Family -or $Model) {
        Fail "the requested variant will not run properly here: $message" `
            'Run without -Family or -Model to select a fitting variant automatically.'
    }

    Fail "$cannotRun`: $message"
}

$tag = if ($variant.Family -eq 'qwen') {
    $variant.Model
} else {
    "$($variant.Family)-$($variant.Model)"
}

$tag = $tag.ToLower()

Write-Ok "chosen: $($variant.Title)"

# ----------------------------------------------------------------------------------------------------
# Setup mutex
# ----------------------------------------------------------------------------------------------------

$lock = $null

if (-not $CheckOnly) {
    $lock = New-Object Threading.Mutex($false, 'Local\strata-coder-setup')

    try {
        if (-not $lock.WaitOne(0)) {
            Write-Step 'Another strata-coder setup is running; waiting for it to finish'
            [void]$lock.WaitOne()
        }
    } catch [Threading.AbandonedMutexException] {
    }
}

# ----------------------------------------------------------------------------------------------------
# Existing Strata and OpenCode installations
# ----------------------------------------------------------------------------------------------------

Write-Step 'Looking for existing installations'

$strataSettings = Read-Json $StrataSettingsPath
$state = Read-Json $StatePath

$roots = @()

if ($state -and $state.StrataRoot) {
    $roots += $state.StrataRoot
}

$roots += @(
    Get-ChildItem -LiteralPath $AppDir -Directory -Filter 'strata*' -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -ne $UnpackDir
        } |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object {
            $_.FullName
        }
)

if ($strataSettings -and $strataSettings.installs) {
    $roots += @($strataSettings.installs)
}

$roots = @(
    $roots | Where-Object {
        $_ -and (Test-Path -LiteralPath (Join-Path $_ 'setup.py'))
    } | Select-Object -Unique
)

$StrataRoot = @(
    $roots | Where-Object {
        Test-StrataConfig $_
    }
) | Select-Object -First 1

$modelReady = [bool]$StrataRoot

if (-not $StrataRoot -and $roots) {
    $StrataRoot = $roots[0]
}

$strataPresent = [bool]$StrataRoot
$strataVersion = if ($strataPresent) {
    Get-StrataVersion $StrataRoot
} else {
    $null
}

if ($strataPresent) {
    Write-Ok "Strata $strataVersion`: $StrataRoot"
} else {
    Write-Info 'Strata: not installed'
}

if ($modelReady) {
    Write-Ok "model $tag is configured"
} else {
    Write-Info "model $tag is not configured"
}

$strataTarget = $null

if (-not $strataPresent) {
    $strataTarget = $PinnedStrata
} elseif ($Update) {
    $latest = (Get-LatestRelease 'Niko1221/Strata').tag_name

    if ($latest -ne $strataVersion) {
        $strataTarget = $latest
        Write-Info "Strata update available: $strataVersion -> $latest"
    } else {
        Write-Ok "Strata is up to date ($latest)"
    }
}

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
    Write-Ok "OpenCode $openCodeVersion"
} else {
    Write-Info "OpenCode: not installed (target: $OpenCodeDir)"
}

$openCodeRelease = $null

if ($openCodePresent -and $Update) {
    $openCodeRelease = Get-LatestRelease 'anomalyco/opencode'

    if (
        $openCodeRelease.tag_name.TrimStart('v') -eq
        "$openCodeVersion".Trim().TrimStart('v')
    ) {
        Write-Ok 'OpenCode is up to date'
        $openCodeRelease = $null
    } else {
        Write-Info "OpenCode update available: $openCodeVersion -> $($openCodeRelease.tag_name)"
    }
}

$serverRunning = $false
$health = Get-StrataHealth $Port

if ($health) {
    $serverRunning = $true
    Write-Ok "a Strata server is running on port $Port ($($health.model))"
} else {
    $existingServer = @(Get-StrataServerProcesses) | Select-Object -First 1

    if ($existingServer) {
        $serverRunning = $true
        $otherPort = Get-ServerPort $existingServer

        if ($otherPort -ne $Port) {
            Write-Warn "a Strata server is already running on port $otherPort; using it"
            $Port = $otherPort
        } else {
            Write-Info "a Strata server is starting on port $Port"
        }

        $health = Get-StrataHealth $Port
    } elseif (Test-PortInUse $Port) {
        Fail "port $Port is already in use by a process other than Strata." `
            'Stop that process or run this script again with -Port <another-port>.'
    }
}

# ----------------------------------------------------------------------------------------------------
# Disk-space validation
# ----------------------------------------------------------------------------------------------------

Write-Step 'Checking disk space'

$knownDataDir = $null

if (
    $strataSettings -and
    $strataSettings.data_dir -and
    (Test-Path -LiteralPath $strataSettings.data_dir)
) {
    $knownDataDir = $strataSettings.data_dir
}

if ($DataDir) {
    $DataDir = [IO.Path]::GetFullPath($DataDir)
} elseif ($knownDataDir) {
    $DataDir = $knownDataDir
    Write-Info 'using the model folder previously used by Strata'
} else {
    $DataDir = Join-Path $env:USERPROFILE 'ai-models\models'
}

$ggufDir = $null

if (-not $modelReady) {
    $shards = 1..2 | ForEach-Object {
        "Qwen3.8-Flash-Next-GSQ-RCO-$($variant.Model)-0000$_-of-00002.gguf"
    }

    $hfHub = if ($env:HF_HUB_CACHE) {
        $env:HF_HUB_CACHE
    } elseif ($env:HF_HOME) {
        Join-Path $env:HF_HOME 'hub'
    } else {
        Join-Path $env:USERPROFILE '.cache\huggingface\hub'
    }

    $repoDir = Join-Path $hfHub (
        'models--' + ($variant.Repo -replace '/', '--')
    )

    if (Test-Path -LiteralPath (Join-Path $repoDir 'snapshots')) {
        $ggufDir = Get-ChildItem `
            -LiteralPath (Join-Path $repoDir 'snapshots') `
            -Directory `
            -ErrorAction SilentlyContinue |
            ForEach-Object {
                Join-Path $_.FullName $variant.Model
            } |
            Where-Object {
                $candidate = $_

                @(
                    $shards | Where-Object {
                        Test-Path -LiteralPath (Join-Path $candidate $_)
                    }
                ).Count -eq $shards.Count
            } |
            Select-Object -First 1
    }

    if (-not $ggufDir -and (Test-Path -LiteralPath $DataDir)) {
        $firstShard = Get-ChildItem `
            -LiteralPath $DataDir `
            -Recurse `
            -File `
            -Filter $shards[0] `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1

        if (
            $firstShard -and
            (Test-Path -LiteralPath (Join-Path $firstShard.DirectoryName $shards[1]))
        ) {
            $ggufDir = $firstShard.DirectoryName
        }
    }

    if ($ggufDir) {
        Write-Ok "model files already downloaded: $ggufDir"
    }
}

$needGB = 0

if (-not $modelReady) {
    $downloadGB = if ($ggufDir) {
        0
    } else {
        $variant.DownloadGB
    }

    $fullRequirement = (
        $downloadGB +
        $MtpGB +
        $variant.ArenaGB +
        1
    ) * $Margin

    $existingGB = Get-FolderGB $DataDir

    $needGB = [math]::Max(
        [math]::Round($fullRequirement - $existingGB, 1),
        2
    )

    if ($existingGB -gt 0) {
        Write-Info "$existingGB GB already exists in $DataDir"
    }
}

$freeGB = Get-FreeGB $DataDir

$toolsNeedGB = 0

if ($strataTarget -or -not $modelReady) {
    $toolsNeedGB += 3
}

if (-not $openCodePresent -or $openCodeRelease) {
    $toolsNeedGB += 1
}

$sameDrive = (
    [IO.Path]::GetPathRoot($DataDir) -eq
    [IO.Path]::GetPathRoot($AppDir)
)

$toolsFreeGB = Get-FreeGB $AppDir

if ($sameDrive) {
    $needGB += $toolsNeedGB
}

Write-Info "model folder: $DataDir ($freeGB GB free; $needGB GB required)"

if ($freeGB -lt $needGB) {
    $alternateDrives = @(
        [IO.DriveInfo]::GetDrives() |
            Where-Object {
                $_.DriveType -eq 'Fixed' -and
                $_.IsReady -and
                ($_.AvailableFreeSpace / 1GB) -ge $needGB
            } |
            ForEach-Object {
                $_.Name
            }
    )

    $hint = if ($alternateDrives) {
        "Run again with -DataDir $($alternateDrives[0])ai-models\models"
    } else {
        "Free disk space. No fixed local drive has $needGB GB free."
    }

    Fail "not enough free disk space: $needGB GB required in $DataDir; $freeGB GB available." $hint
}

if (-not $sameDrive -and $toolsFreeGB -lt $toolsNeedGB) {
    Fail "not enough disk space for tools in ${AppDir}: $toolsNeedGB GB required; $toolsFreeGB GB available."
}

Write-Ok 'enough disk space'

if ($CheckOnly) {
    Write-Step 'Check only: no changes were made'

    $strataLine = if (-not $strataPresent) {
        "would be installed ($PinnedStrata)"
    } elseif ($strataTarget) {
        "would be updated to $strataTarget"
    } else {
        "installed ($strataVersion)"
    }

    $modelLine = if ($modelReady) {
        'ready'
    } elseif ($ggufDir) {
        'would be configured from existing GGUF files'
    } else {
        "would be downloaded (about $($variant.DownloadGB) GB)"
    }

    $openCodeLine = if ($openCodeRelease) {
        "would be updated to $($openCodeRelease.tag_name)"
    } elseif ($openCodePresent) {
        'installed'
    } else {
        'would be installed'
    }

    $serverLine = if ($serverRunning) {
        "running on port $Port"
    } else {
        "would be started on port $Port"
    }

    Write-Info "Strata:   $strataLine"
    Write-Info "Model:    $modelLine"
    Write-Info "OpenCode: $openCodeLine"
    Write-Info "Server:   $serverLine"
    Write-Info "Project:  $ProjectDir"

    exit 0
}

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

# ----------------------------------------------------------------------------------------------------
# Strata installation
# ----------------------------------------------------------------------------------------------------

if ($strataTarget) {
    $isUpdate = $strataPresent

    Write-Step $(if ($isUpdate) {
        "Updating Strata $strataVersion -> $strataTarget"
    } else {
        "Installing Strata $strataTarget"
    })

    $oldRoot = $StrataRoot
    $newRoot = Install-StrataCode $strataTarget

    Write-Ok "Strata $strataTarget unpacked in $newRoot"

    if ($isUpdate) {
        if ($serverRunning) {
            Write-Info 'stopping the running server so the updated version can take over'
            [void](Stop-StrataServers)
            $serverRunning = $false
            $health = $null
        }

        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

        $setupExitCode = Invoke-StrataSetup $newRoot (Get-SetupArgs)

        if (-not (Test-StrataConfig $newRoot)) {
            Remove-Item -LiteralPath $newRoot -Recurse -Force -ErrorAction SilentlyContinue

            Fail "Strata $strataTarget setup did not finish successfully (exit code $setupExitCode)." `
                'The previous version remains installed. Run without -Update to use it.'
        }

        $StrataRoot = $newRoot
        $modelReady = $true

        Save-State @{
            StrataRoot    = $StrataRoot
            StrataVersion = $strataTarget
            Model         = $tag
            DataDir       = $DataDir
        }

        if (
            $oldRoot -and
            $oldRoot.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) -and
            $oldRoot -ne $newRoot
        ) {
            try {
                Remove-Item -LiteralPath $oldRoot -Recurse -Force
                Write-Ok "removed prior Strata version: $oldRoot"
            } catch {
                Write-Warn "could not remove old Strata folder $oldRoot ($($_.Exception.Message))"
            }
        }

        Write-Ok "Strata updated to $strataTarget"
    } else {
        $StrataRoot = $newRoot

        Save-State @{
            StrataRoot    = $StrataRoot
            StrataVersion = $strataTarget
        }
    }
} else {
    Write-Skip 'Strata already installed'
}

# ----------------------------------------------------------------------------------------------------
# Model configuration
# ----------------------------------------------------------------------------------------------------

if (-not $modelReady) {
    Write-Step "Setting up $($variant.Title)"

    $downloadEstimate = if ($ggufDir) {
        6
    } else {
        [math]::Round($variant.DownloadGB + 6)
    }

    Write-Info "this may download about $downloadEstimate GB and can take a long time"
    Write-Info 'If interrupted, run the script again; supported downloads resume.'

    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

    $setupExitCode = Invoke-StrataSetup $StrataRoot (Get-SetupArgs)

    if (-not (Test-StrataConfig $StrataRoot)) {
        Fail "Strata model setup did not finish successfully (exit code $setupExitCode)." `
            'Review the setup output, correct the reported issue, and run the script again.'
    }

    $modelReady = $true

    Save-State @{
        StrataRoot = $StrataRoot
        Model      = $tag
        DataDir    = $DataDir
    }

    Write-Ok "model $tag is configured"
} else {
    Write-Skip 'model already configured'
}

# ----------------------------------------------------------------------------------------------------
# OpenCode installation
# ----------------------------------------------------------------------------------------------------

if (-not $openCodePresent -or $openCodeRelease) {
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

    $assetName = if ($spec.Avx2 -eq $false) {
        'opencode-windows-x64-baseline.zip'
    } else {
        'opencode-windows-x64.zip'
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

    Save-State @{
        OpenCodeVersion = $release.tag_name
    }

    Write-Ok "OpenCode $($release.tag_name)"
} else {
    Write-Skip 'OpenCode already installed'
}

# ----------------------------------------------------------------------------------------------------
# Start Strata
# ----------------------------------------------------------------------------------------------------

$serverLog = Join-Path $LogDir 'server.log'

if (-not $serverRunning) {
    Write-Step 'Starting the Strata server'

    $strataConfigPath = Join-Path $StrataRoot "strata-$tag.json"
    $strataConfig = Read-Json $strataConfigPath
    $python = Join-Path $StrataRoot '.venv\Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $python)) {
        Fail "Strata Python environment was not found: $python"
    }

    if (-not (Test-Path -LiteralPath $strataConfigPath)) {
        Fail "Strata model configuration was not found: $strataConfigPath"
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    if (Test-Path -LiteralPath $serverLog) {
        Move-Item `
            -LiteralPath $serverLog `
            -Destination (Join-Path $LogDir 'server.prev.log') `
            -Force
    }

    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $commandArguments = (
        '/d /s /c ""{0}" "{1}" --engine strata --config "{2}" --port {3} > "{4}" 2>&1"' -f
        $python,
        (Join-Path $StrataRoot 'serve\server.py'),
        $strataConfigPath,
        $Port,
        $serverLog
    )

    $serverProcess = Start-Process `
        -FilePath $env:ComSpec `
        -ArgumentList $commandArguments `
        -WorkingDirectory $StrataRoot `
        -WindowStyle Hidden `
        -PassThru

    Save-State @{
        Port = $Port
    }

    Write-Info 'loading the model; the first start may take several minutes'
    Write-Info "server output: $serverLog"

    if (-not (Wait-StrataReady $serverProcess)) {
        $reason = if ($serverProcess.HasExited) {
            'it stopped'
        } else {
            "it was not ready after $LoadMinutes minutes"
        }

        Show-LogTail $serverLog

        if ($strataConfig -and $strataConfig.log) {
            Show-LogTail $strataConfig.log 15
        }

        if (-not $serverProcess.HasExited) {
            [void](Stop-StrataServers)
        }

        Fail "the Strata server did not come up: $reason." `
            "Review the logs above and $StrataRoot\START-HERE.bat."
    }
} elseif (-not (Test-StrataReady $Port)) {
    Write-Step 'Waiting for the running Strata server to finish loading'

    if (-not (Wait-StrataReady $null)) {
        Show-LogTail $serverLog

        Fail "the running Strata server did not become ready within $LoadMinutes minutes." `
            'Stop it with -Stop and run the script again.'
    }
} else {
    Write-Skip 'server already running'
}

$health = Get-StrataHealth $Port

if (-not $health) {
    Fail "the Strata server stopped answering on port $Port." `
        "See $serverLog"
}

Write-Ok "Strata is serving $($health.model) on http://127.0.0.1:$Port (context $($health.max_context))"

# ----------------------------------------------------------------------------------------------------
# Verify Strata API and build OpenCode config
# ----------------------------------------------------------------------------------------------------

Write-Step 'Verifying the Strata API for OpenCode'

$servedModels = @(Get-StrataModels $Port)

if ($servedModels.Count -eq 0) {
    Fail "Strata is healthy on port $Port, but /v1/models returned no usable model IDs." `
        "Review $serverLog and check http://127.0.0.1:$Port/v1/models."
}

$modelInfo = @(
    $servedModels | Where-Object {
        "$($_.id)" -eq "$($health.model)"
    }
) | Select-Object -First 1

if (-not $modelInfo -and $servedModels.Count -eq 1) {
    $modelInfo = $servedModels[0]
}

if (-not $modelInfo) {
    $availableIds = ($servedModels | ForEach-Object {
        $_.id
    }) -join ', '

    Fail "could not match Strata health model '$($health.model)' to a /v1/models model ID." `
        "Available model IDs: $availableIds"
}

$modelId = "$($modelInfo.id)".Trim()

Write-Info "Strata health model: $($health.model)"
Write-Info "OpenAI API model ID: $modelId"

$apiTest = Test-StrataChatCompletion $Port $modelId

if (-not $apiTest.Ok) {
    Fail "Strata is healthy, but /v1/chat/completions rejected model '$modelId'." `
        "Request error: $($apiTest.Error)"
}

Write-Ok "Strata accepted an OpenAI-compatible chat request for '$modelId'"

$displayName = ($variant.Title -replace ' \(.*$', '') + ' (local)'

$contextLimit = 131072

try {
    if ($health.max_context) {
        $reportedContext = [int]$health.max_context

        if ($reportedContext -gt 0) {
            $contextLimit = $reportedContext
        }
    }
} catch {
}

$outputLimit = [math]::Min(
    32768,
    [math]::Max(256, [int]($contextLimit / 4))
)

$openCodeConfig = [ordered]@{
    '$schema' = 'https://opencode.ai/config.json'

    provider = [ordered]@{
        strata = [ordered]@{
            npm  = '@ai-sdk/openai-compatible'
            name = 'Strata (local)'

            options = [ordered]@{
                baseURL = "http://127.0.0.1:$Port/v1"
                apiKey  = 'local'
            }

            models = [ordered]@{
                $modelId = [ordered]@{
                    name = $displayName

                    limit = [ordered]@{
                        context = $contextLimit
                        output  = $outputLimit
                    }
                }
            }
        }
    }

    model             = "strata/$modelId"
    small_model       = "strata/$modelId"
    enabled_providers = @('strata')
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

if ($lock) {
    $lock.ReleaseMutex()
    $lock.Dispose()
    $lock = $null
}

if ($script:TranscriptPath) {
    try {
        Stop-Transcript | Out-Null
    } catch {
    }
}

if ($NoLaunch) {
    Write-Step 'Ready'
    Write-Info "server: http://127.0.0.1:$Port/v1"
    Write-Info "OpenCode config: $OpenCodeCfg"
    Write-Info "OpenCode model: strata/$modelId"
    Write-Info 'stop the Strata server with: .\strata-coder.ps1 -Stop'
    exit 0
}

Write-Step "Opening OpenCode in $ProjectDir"

# OPENCODE_CONFIG allows inspection of the generated file.
$env:OPENCODE_CONFIG = $OpenCodeCfg

# OPENCODE_CONFIG_CONTENT is an inline runtime override. It prevents another
# OpenCode config in the project tree from replacing the local Strata provider
# or the selected model for this OpenCode session.
$env:OPENCODE_CONFIG_CONTENT = $openCodeJson

Write-Info "OpenCode config: $OpenCodeCfg"
Write-Info 'OpenCode provider: strata'
Write-Info "OpenCode model: strata/$modelId"
Write-Info "Strata endpoint: http://127.0.0.1:$Port/v1"

if (-not (Test-Path -LiteralPath $OpenCodeCfg)) {
    Fail "the generated OpenCode configuration file does not exist: $OpenCodeCfg"
}

$debugConfigPath = Join-Path $LogDir 'opencode-resolved-config.log'

try {
    $debugOutput = @(
        & $OpenCodeExe debug config 2>&1
    )

    $debugText = $debugOutput -join [Environment]::NewLine
    Write-Utf8File $debugConfigPath $debugText

    if ($debugText -notmatch [regex]::Escape('"strata"')) {
        Write-Warn 'OpenCode resolved configuration did not show the Strata provider.'
        Write-Warn "resolved config log: $debugConfigPath"
    } elseif ($debugText -notmatch [regex]::Escape("strata/$modelId")) {
        Write-Warn "OpenCode resolved configuration did not show strata/$modelId."
        Write-Warn "resolved config log: $debugConfigPath"
    } else {
        Write-Ok 'OpenCode resolved the Strata provider and selected model'
    }
} catch {
    Write-Warn "could not run 'opencode debug config' ($($_.Exception.Message))"
}

Push-Location -LiteralPath $ProjectDir

try {
    & $OpenCodeExe $ProjectDir
} finally {
    Pop-Location
    Reset-TerminalMouse

    Remove-Item Env:OPENCODE_CONFIG_CONTENT -ErrorAction SilentlyContinue
    Remove-Item Env:OPENCODE_CONFIG -ErrorAction SilentlyContinue
}

Write-Step 'Stopping the Strata server'

if ((Stop-StrataServers) -eq 0) {
    Write-Skip 'no Strata server process was found'
}