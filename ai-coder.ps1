<#
.SYNOPSIS
    Local AI coder: Strata serving Qwen3.8-Flash-Next, with OpenCode working on the current folder.
    Everything installs per-user (no admin rights needed).

.DESCRIPTION
    1. Checks this PC (NVIDIA GPU, VRAM, driver, RAM, CPU) and picks the best Qwen3.8-Flash-Next variant
       it can run. If none fits, it says so and stops.
    2. Looks for what already exists (Strata install, configured model, model files, HF cache, OpenCode,
       a running server) and skips those steps.
    3. Makes sure the disk has room for the model before downloading anything.
    4. Installs Strata, the model and OpenCode as needed, starts the Strata server and opens OpenCode
       in the folder this script was started from.

.EXAMPLE
    ai-coder.cmd               # set up whatever is missing, start the server, open OpenCode here
    ai-coder.cmd -CheckOnly    # report what would happen, change nothing
    ai-coder.cmd -Update       # move Strata and OpenCode to their newest releases
    ai-coder.cmd -Stop         # stop the Strata server
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,                         # report specs, choice, disk and install state; change nothing
    [ValidateSet('qwen', 'coder')][string]$Family,
    [string]$Model,                             # e.g. IQ2_XS; with -Family, overrides the automatic choice
    [int]$Context,                              # context tokens; default: Strata's recommendation for this PC
    [string]$DataDir,                           # where the model files go (default: earlier choice, else roomiest drive)
    [int]$Port = 8080,
    [switch]$NoLaunch,                          # set up and start the server, but do not open OpenCode
    [switch]$Update,                            # move Strata and OpenCode to their newest releases
    [switch]$Stop,                              # stop the Strata server
    [switch]$Stats                              # live tokens/sec of the running server (Ctrl+C to quit)
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'        # Invoke-WebRequest is very slow with the progress bar on
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProjectDir = (Get-Location).ProviderPath       # the folder OpenCode works on

$AppDir      = Join-Path $env:LOCALAPPDATA 'ai-coder'
$OpenCodeDir = Join-Path $AppDir 'opencode'
$OpenCodeExe = Join-Path $OpenCodeDir 'opencode.exe'
$OpenCodeCfg = Join-Path $AppDir 'opencode.json'
$StatePath   = Join-Path $AppDir 'state.json'
$DownloadDir = Join-Path $AppDir 'downloads'
$LogDir      = Join-Path $AppDir 'logs'
$UnpackDir   = Join-Path $AppDir 'strata-unpack'
$StrataSettingsPath = Join-Path $env:APPDATA 'Strata\settings.json'   # written by Strata's own setup

# The Strata release this script was tested with. The script relies on Strata's setup flags, its config naming
# (strata-<tag>.json) and serve\server.py's arguments, so a fresh install uses this one; -Update moves to the newest.
$PinnedStrata = 'v0.1.40.3'

# Variants this script will pick, best first. Sizes are from Strata's setup.py (MODELS); MinRamGB sits ~2 GB
# under Strata's stated requirement because e.g. 32 GB of RAM reports as ~31.4 GB.
$Variants = @(
    @{ Family = 'qwen';  Model = 'IQ3_XXS'; MinRamGB = 58; DownloadGB = 75.8; ArenaGB = 42.9
       Title = 'Qwen3.8-Flash-Next IQ3_XXS (3-bit, best quality that fits 64 GB)'
       Repo = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF' }
    @{ Family = 'qwen';  Model = 'IQ2_XS';  MinRamGB = 46; DownloadGB = 68.0; ArenaGB = 35.5
       Title = 'Qwen3.8-Flash-Next IQ2_XS (2-bit, Strata''s recommended size for 48-64 GB)'
       Repo = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF' }
    @{ Family = 'coder'; Model = 'IQ1_M';   MinRamGB = 30; DownloadGB = 58.4; ArenaGB = 23.4
       Title = 'Qwen3.8-Flash-Next Coder IQ1_M (code-focused, half the experts, fits 32 GB)'
       Repo = 'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF' }
)
$MinVramMiB    = 11500      # Strata needs a 12 GB card or more
$MinDriver     = 580        # Strata's CUDA 13 engine
$MinComputeCap = 7.5        # Strata's ready-made engine covers RTX 20 (sm_75) to RTX 50 (sm_120)
$MtpGB         = 8          # MTP draft layer and its room while being prepared (Strata's own figure)
$Margin        = 1.15
$LoadMinutes   = 15         # how long a server may take to load the model

# ------------------------------------------------------------------------------------------------ output
$script:TranscriptPath = $null

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    ok    $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "          $msg" }
function Write-Skip($msg) { Write-Host "    skip  $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "    warn  $msg" -ForegroundColor Yellow }
function Fail($msg, $hint) {
    Write-Host ""
    Write-Host "STOPPED: $msg" -ForegroundColor Red
    if ($hint) { Write-Host "         $hint" -ForegroundColor Red }
    if ($script:TranscriptPath) { Write-Host "         (this run's log: $script:TranscriptPath)" -ForegroundColor DarkGray }
    exit 1
}

# anything not handled below ends here, in the same form as the script's own stops
trap {
    $at = $_.InvocationInfo
    $where = if ($at -and $at.ScriptLineNumber) { " (line $($at.ScriptLineNumber): $($at.Line.Trim()))" } else { '' }
    Fail "unexpected error: $($_.Exception.Message)$where" "run it again; if it keeps happening, the log below shows what led up to it"
}

# ------------------------------------------------------------------------------------------------ helpers
function Read-Json($path) {
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return $null }
    try { return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Write-Utf8File($path, $text) {
    [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false))
}

function Save-State($changes) {
    $state = @{}
    $old = Read-Json $StatePath
    if ($old) { $old.PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value } }
    foreach ($k in $changes.Keys) { $state[$k] = $changes[$k] }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Write-Utf8File $StatePath ($state | ConvertTo-Json -Depth 5)
}

# Runs a program and returns its output lines with stderr dropped. Windows PowerShell turns a program's stderr into
# errors that 'Stop' would throw on, so it is run under 'Continue'. The whole output is collected (cutting the
# pipeline short, e.g. with Select-Object -First, kills the program and spoils its exit code).
function Invoke-Native($exe, [string[]]$argList) {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& $exe @argList 2>$null) } finally { $ErrorActionPreference = $eap }
    return $out
}

function Get-FreeGB($path) {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
    return [math]::Round((New-Object IO.DriveInfo $root).AvailableFreeSpace / 1GB, 1)
}

function Get-FolderGB($path) {
    if (-not (Test-Path -LiteralPath $path)) { return 0 }
    $sum = (Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    if (-not $sum) { return 0 }
    return [math]::Round($sum / 1GB, 1)
}

function Invoke-Download($url, $out) {
    New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        # resumable; curl.exe ships with Windows 10 1803+
        & $curl.Source -L --fail --retry 3 --retry-delay 5 -C - -o $out $url
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn "curl failed (exit $LASTEXITCODE), retrying with Invoke-WebRequest"
        Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue
    }
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out }
    catch {
        Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue
        Fail "could not download $url ($($_.Exception.Message))" "check the internet connection and run this again"
    }
}

function Get-LatestRelease($repo) {
    try { return Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$repo/releases/latest" `
                                   -Headers @{ 'User-Agent' = 'ai-coder' } }
    catch { Fail "could not reach GitHub to look up $repo ($($_.Exception.Message))" "check the internet connection and try again" }
}

function Get-StrataHealth($port) {
    try {
        $h = Invoke-RestMethod -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec 5
        if ($h.service -eq 'strata') { return $h }
    } catch { }
    return $null
}

# Ready = the model is loaded, or the server lists it as loading on first use (Strata's idle unload).
function Test-StrataReady($port) {
    $h = Get-StrataHealth $port
    if (-not $h) { return $false }
    if ($h.loaded) { return $true }
    try {
        $m = Invoke-RestMethod -UseBasicParsing -Uri "http://127.0.0.1:$port/v1/models" -TimeoutSec 5
        return @($m.data).Count -gt 0
    } catch { return $false }
}

function Test-PortInUse($port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $port); return $true } catch { return $false } finally { $c.Close() }
}

function Get-StrataServerProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'server\.py' -and $_.CommandLine -match '--engine\s+"?strata' }
}

function Get-ServerPort($proc) {
    if ($proc.CommandLine -match '--port\s+"?(\d+)') { return [int]$Matches[1] }
    return 8080
}

function Stop-StrataServers {
    $procs = @(Get-StrataServerProcesses)
    foreach ($p in $procs) {
        [void](Invoke-Native taskkill.exe @('/T', '/F', '/PID', $p.ProcessId))
        Write-Ok "stopped the Strata server (PID $($p.ProcessId))"
    }
    return $procs.Count
}

function Show-LogTail($path, $lines = 25) {
    if ($path -and (Test-Path -LiteralPath $path)) {
        Write-Host "    --- last lines of $path ---" -ForegroundColor DarkGray
        Get-Content -LiteralPath $path -Tail $lines | ForEach-Object { Write-Host "    | $_" }
    }
}

# ------------------------------------------------------------------------------------------------ -Stop
if ($Stop) {
    Write-Step "Stopping the Strata server"
    if ((Stop-StrataServers) -eq 0) { Write-Skip "no Strata server is running" }
    exit 0
}

# ------------------------------------------------------------------------------------------------ -Stats
if ($Stats) {
    $proc = @(Get-StrataServerProcesses) | Select-Object -First 1
    if (-not (Get-StrataHealth $Port) -and $proc) { $Port = Get-ServerPort $proc }
    $base = "http://127.0.0.1:$Port"
    if (-not (Get-StrataHealth $Port)) { Fail "no Strata server is answering on port $Port." }
    Write-Step "Strata speed on $base (Ctrl+C to quit; the web Monitor tab at $base has more)"
    $lastShown = $null
    while ($true) {
        try {
            $now = Invoke-RestMethod -UseBasicParsing -Uri "$base/status" -TimeoutSec 5
            $v1  = Invoke-RestMethod -UseBasicParsing -Uri "$base/v1/status" -TimeoutSec 5
        } catch { Fail "lost the connection to the server ($($_.Exception.Message))" }
        $t = $v1.last_timings
        $last = if ($t -and $t.predicted_per_second) {
            $draft = if ($t.draft_n) { ", drafts accepted {0:P0}" -f ($t.draft_n_accepted / $t.draft_n) } else { '' }
            "last answer: {0} tokens at {1} tok/s, prompt {2} tokens at {3} tok/s ({4} cached){5}" -f `
                $t.predicted_n, $t.predicted_per_second, $t.prompt_n, $t.prompt_per_second, $t.cache_n, $draft
        } else { "last answer: none yet" }
        $live = if ($now.busy -and $now.tokens_per_s) { "generating: {0} tok/s now, {1} tok/s this answer" -f $now.tokens_per_s, $now.tokens_per_s_mean }
                elseif ($now.busy) { "busy: reading the prompt" } else { "idle" }
        $gpu = $v1.machine.gpu
        $hw = if ($gpu) { "GPU {0}% {1}/{2} MiB {3}C" -f $gpu.util_pct, $gpu.used_mib, $gpu.total_mib, $gpu.temp_c } else { '' }
        $line = "{0}  {1,-48} {2}" -f (Get-Date -Format 'HH:mm:ss'), $live, $hw
        Write-Host $line
        if ($last -ne $lastShown) { Write-Host "          $last" -ForegroundColor Green; $lastShown = $last }
        Start-Sleep -Seconds 2
    }
}

# ------------------------------------------------------------------------------------------------ run log
# every setup run keeps a transcript (the 20 newest are kept), so a failure on another PC can be looked at later
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Get-ChildItem -LiteralPath $LogDir -Filter 'run-*.log' | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 19 | Remove-Item -Force -ErrorAction SilentlyContinue
    $script:TranscriptPath = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Start-Transcript -LiteralPath $script:TranscriptPath | Out-Null
} catch { $script:TranscriptPath = $null }

# ------------------------------------------------------------------------------------------------ 1. this PC
function Get-SystemSpec {
    $spec = [ordered]@{}
    $spec.OS = (Get-CimInstance Win32_OperatingSystem).Caption
    $spec.RamGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $spec.Cpu = $cpu.Name.Trim()
    $spec.Cores = $cpu.NumberOfCores
    $spec.Avx2 = $null
    try {
        if (-not ('AiCoder.Native' -as [type])) {
            Add-Type -Namespace AiCoder -Name Native -MemberDefinition `
                '[DllImport("kernel32.dll")] public static extern bool IsProcessorFeaturePresent(uint feature);'
        }
        $spec.Avx2 = [AiCoder.Native]::IsProcessorFeaturePresent(40)   # PF_AVX2_INSTRUCTIONS_AVAILABLE
    } catch { }

    $spec.Gpu = $null
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    $smiPath = if ($smi) { $smi.Source } elseif (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe") { "$env:SystemRoot\System32\nvidia-smi.exe" }
    if ($smiPath) {
        $rows = Invoke-Native $smiPath @('--query-gpu=index,name,memory.total,driver_version,compute_cap', '--format=csv,noheader,nounits')
        $gpus = foreach ($row in @($rows)) {
            $f = $row -split '\s*,\s*'
            if ($f.Count -ge 5) {
                [pscustomobject]@{ Index = [int]$f[0]; Name = $f[1].Trim(); VramMiB = [int]$f[2]
                                   Driver = $f[3].Trim(); ComputeCap = [double]$f[4] }
            }
        }
        $spec.Gpu = $gpus | Sort-Object VramMiB -Descending | Select-Object -First 1
    }
    $spec.OtherGpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
    return $spec
}

function Get-Candidates {
    $candidates = $Variants
    if ($Family) { $candidates = @($candidates | Where-Object { $_.Family -eq $Family }) }
    if ($Model)  { $candidates = @($candidates | Where-Object { $_.Model -eq $Model.ToUpper() }) }
    if (-not $candidates) {
        $known = ($Variants | ForEach-Object { "$($_.Family)/$($_.Model)" }) -join ', '
        Fail "no variant matches -Family '$Family' -Model '$Model'" "choose one of: $known"
    }
    return $candidates
}

Write-Step "Checking this PC"
$spec = Get-SystemSpec
Write-Info "OS:   $($spec.OS)"
Write-Info "CPU:  $($spec.Cpu) ($($spec.Cores) cores, AVX2: $(if ($null -eq $spec.Avx2) { 'unknown' } elseif ($spec.Avx2) { 'yes' } else { 'no' }))"
Write-Info "RAM:  $($spec.RamGB) GB"
if ($spec.Gpu) {
    Write-Info ("GPU:  {0}, {1:N1} GB VRAM, driver {2}, compute {3}" -f $spec.Gpu.Name, ($spec.Gpu.VramMiB / 1024), $spec.Gpu.Driver, $spec.Gpu.ComputeCap)
} else {
    Write-Info "GPU:  $($spec.OtherGpus -join ', ') (no NVIDIA GPU found)"
}

$cannot = "This PC can't run any Qwen3.8-Flash-Next variant properly"
if ([Environment]::OSVersion.Version.Major -lt 10) { Fail "$cannot`: Windows 10 or 11 is required." }
if (-not $spec.Gpu) {
    Fail "$cannot`: an NVIDIA GPU (RTX 20 series or newer, 12 GB+) is required." `
         "AMD cards can work with Strata, but this script supports NVIDIA only."
}
if ($spec.Gpu.VramMiB -lt $MinVramMiB) {
    Fail ("$cannot`: the GPU has {0:N1} GB of VRAM, Strata needs 12 GB or more." -f ($spec.Gpu.VramMiB / 1024))
}
if ([int]($spec.Gpu.Driver -split '\.')[0] -lt $MinDriver) {
    Fail "the NVIDIA driver $($spec.Gpu.Driver) is too old: Strata needs $MinDriver or newer." `
         "update the driver from nvidia.com (on a managed PC, ask IT), then run this again."
}
if ($spec.Gpu.ComputeCap -lt $MinComputeCap) {
    Fail "$cannot`: the $($spec.Gpu.Name) is older than RTX 20; Strata's ready-made engine needs RTX 20 or newer." `
         "older cards need the engine compiled with Visual Studio and the CUDA Toolkit, which needs admin rights."
}
if ($spec.Avx2 -eq $false) { Write-Warn "this CPU has no AVX2: Strata runs, but slowly" }

$candidates = @(Get-Candidates)
$variant = $candidates | Where-Object { $spec.RamGB -ge $_.MinRamGB } | Select-Object -First 1
if (-not $variant) {
    $least = $candidates | Sort-Object { $_.MinRamGB } | Select-Object -First 1
    $msg = "$($least.Family)/$($least.Model) needs ~$($least.MinRamGB + 2) GB of RAM, this PC has $($spec.RamGB) GB."
    if ($Family -or $Model) { Fail "the requested variant won't run properly here: $msg" "run without -Family/-Model to pick what fits" }
    Fail "$cannot`: even the smallest, $msg"
}
$tag = if ($variant.Family -eq 'qwen') { $variant.Model } else { "$($variant.Family)-$($variant.Model)" }
$tag = $tag.ToLower()                                  # Strata's names: strata-<tag>.json, run-<tag>.bat
Write-Ok "chosen: $($variant.Title)"
if ($variant.Family -eq 'coder' -and -not $Family) {
    Write-Info "(the full-size variants need 48 GB+ of RAM; the Coder is the one that fits, and suits OpenCode)"
}

# ------------------------------------------------------------------------------------------------ one run at a time
# A second ai-coder started meanwhile waits here, then sees what the first one set up. The lock is released before
# OpenCode opens, so several OpenCode sessions can share one server.
$lock = $null
if (-not $CheckOnly) {
    $lock = New-Object Threading.Mutex($false, 'Local\ai-coder-setup')
    try {
        if (-not $lock.WaitOne(0)) {
            Write-Step "Another ai-coder is setting things up: waiting for it to finish (Ctrl+C to give up)"
            [void]$lock.WaitOne()
        }
    } catch [Threading.AbandonedMutexException] { }   # the other one was killed: the lock is ours now
}

# ------------------------------------------------------------------------------------------------ 2. what exists
Write-Step "Looking for what is already installed"
$strataSettings = Read-Json $StrataSettingsPath
$state = Read-Json $StatePath

function Test-StrataConfig($root) {
    $cfg = Read-Json (Join-Path $root "strata-$tag.json")
    if (-not $cfg -or -not $cfg.exe) { return $false }
    return (Test-Path -LiteralPath $cfg.exe) -and (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe'))
}

function Get-StrataVersion($root) {
    if ((Split-Path $root -Leaf) -match '^strata-(v[\d.]+)$') { return $Matches[1] }
    if ($state -and $state.StrataRoot -eq $root -and $state.StrataVersion) { return $state.StrataVersion }
    return 'unknown'
}

# Strata folders, most likely first: the one in use, ours, then any this user ran before (Strata records them).
# Only complete ones count: an unpack is moved into place whole, so a folder with setup.py is a whole copy.
$roots = @()
if ($state -and $state.StrataRoot) { $roots += $state.StrataRoot }
$roots += @(Get-ChildItem -LiteralPath $AppDir -Directory -Filter 'strata*' -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $UnpackDir } | Sort-Object LastWriteTime -Descending | ForEach-Object { $_.FullName })
if ($strataSettings -and $strataSettings.installs) { $roots += @($strataSettings.installs) }
$roots = @($roots | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'setup.py')) } | Select-Object -Unique)

$StrataRoot = $roots | Where-Object { Test-StrataConfig $_ } | Select-Object -First 1
$modelReady = [bool]$StrataRoot
if (-not $StrataRoot -and $roots) { $StrataRoot = $roots[0] }
$strataPresent = [bool]$StrataRoot
$strataVersion = if ($strataPresent) { Get-StrataVersion $StrataRoot } else { $null }

if ($strataPresent) { Write-Ok "Strata $strataVersion`: $StrataRoot" } else { Write-Info "Strata: not installed" }
if ($modelReady) { Write-Ok "model $tag is set up (strata-$tag.json)" } else { Write-Info "model ${tag}: not set up yet" }

# -Update: the newest Strata, set up beside the current one and switched to only when its setup succeeds
$strataTarget = $null
if (-not $strataPresent) {
    $strataTarget = $PinnedStrata
} elseif ($Update) {
    $latest = (Get-LatestRelease 'Niko1221/Strata').tag_name
    if ($latest -ne $strataVersion) { $strataTarget = $latest; Write-Info "Strata update available: $strataVersion -> $latest" }
    else { Write-Ok "Strata is up to date ($latest)" }
}

# GGUF files already in the Hugging Face cache: handed to Strata instead of downloading again
$ggufDir = $null
if (-not $modelReady) {
    $hfHub = if ($env:HF_HUB_CACHE) { $env:HF_HUB_CACHE } elseif ($env:HF_HOME) { Join-Path $env:HF_HOME 'hub' } `
             else { Join-Path $env:USERPROFILE '.cache\huggingface\hub' }
    $repoDir = Join-Path $hfHub ('models--' + ($variant.Repo -replace '/', '--'))
    $shards = 1..2 | ForEach-Object { "Qwen3.8-Flash-Next-GSQ-RCO-$($variant.Model)-0000$_-of-00002.gguf" }
    if (Test-Path -LiteralPath (Join-Path $repoDir 'snapshots')) {
        $ggufDir = Get-ChildItem -LiteralPath (Join-Path $repoDir 'snapshots') -Directory | ForEach-Object {
            Join-Path $_.FullName $variant.Model } | Where-Object {
            $d = $_; @($shards | Where-Object { Test-Path -LiteralPath (Join-Path $d $_) }).Count -eq 2 } |
            Select-Object -First 1
    }
    if ($ggufDir) { Write-Ok "model files found in the Hugging Face cache: $ggufDir" }
}

$openCodePresent = $false
$ocVersion = $null
if (Test-Path -LiteralPath $OpenCodeExe) {
    $ocOut = Invoke-Native $OpenCodeExe @('--version')
    $openCodePresent = $LASTEXITCODE -eq 0
    $ocVersion = "$($ocOut | Select-Object -First 1)".Trim()
}
if ($openCodePresent) { Write-Ok "OpenCode $ocVersion" } else { Write-Info "OpenCode: not installed (goes to $OpenCodeDir)" }
$openCodeRelease = $null
if ($openCodePresent -and $Update) {
    $openCodeRelease = Get-LatestRelease 'anomalyco/opencode'
    if ($openCodeRelease.tag_name.TrimStart('v') -eq "$ocVersion".Trim().TrimStart('v')) {
        Write-Ok "OpenCode is up to date"; $openCodeRelease = $null
    } else { Write-Info "OpenCode update available: $ocVersion -> $($openCodeRelease.tag_name)" }
}

# One server at a time: two copies of the model do not fit in memory. A Strata server already running - on any
# port, even one still loading - is used instead of starting another.
$serverRunning = $false
$health = Get-StrataHealth $Port
if ($health) {
    $serverRunning = $true
    Write-Ok "a Strata server is running on port $Port ($($health.model))"
} else {
    $existing = @(Get-StrataServerProcesses) | Select-Object -First 1
    if ($existing) {
        $serverRunning = $true
        $otherPort = Get-ServerPort $existing
        if ($otherPort -ne $Port) {
            Write-Warn "a Strata server is already running on port $otherPort`: using it rather than starting a second one"
            $Port = $otherPort
        } else {
            Write-Info "a Strata server is starting on port $Port"
        }
        $health = Get-StrataHealth $Port
    } elseif (Test-PortInUse $Port) {
        Fail "port $Port is used by something that is not Strata." "stop that program, or run again with -Port <another port>"
    }
}

# ------------------------------------------------------------------------------------------------ 3. disk space
Write-Step "Checking disk space"
$knownDataDir = if ($strataSettings -and $strataSettings.data_dir -and (Test-Path -LiteralPath $strataSettings.data_dir)) { $strataSettings.data_dir }
if ($DataDir) {
    $DataDir = [IO.Path]::GetFullPath($DataDir)
} elseif ($knownDataDir) {
    $DataDir = $knownDataDir
    Write-Info "using the model folder Strata used before"
} else {
    # the local drive with the most free space
    $best = [IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady } |
            Sort-Object AvailableFreeSpace -Descending | Select-Object -First 1
    $DataDir = Join-Path $best.RootDirectory.FullName 'ai-coder-models'
}

$needGB = 0
if (-not $modelReady) {
    # Strata's own formula (setup.py): the download, the MTP layer, and the low-RAM copy of the experts
    $download = if ($ggufDir) { 0 } else { $variant.DownloadGB }
    $full = ($download + $MtpGB + $variant.ArenaGB + 1) * $Margin
    $have = Get-FolderGB $DataDir                       # a resumed download or an earlier model counts
    $needGB = [math]::Max([math]::Round($full - $have, 1), 2)
    if ($have -gt 0) { Write-Info "$have GB already in $DataDir" }
}
$freeGB = Get-FreeGB $DataDir
$toolsNeed = 0
if ($strataTarget -or -not $modelReady) { $toolsNeed += 3 }        # Python packages, CUDA libraries, engine
if (-not $openCodePresent -or $openCodeRelease) { $toolsNeed += 1 }
$toolsFree = Get-FreeGB $AppDir.Substring(0, 3)
$sameDrive = [IO.Path]::GetPathRoot($DataDir) -eq [IO.Path]::GetPathRoot($AppDir)
if ($sameDrive) { $needGB += $toolsNeed }
Write-Info "model folder: $DataDir ($freeGB GB free, $needGB GB needed)"

if ($freeGB -lt $needGB) {
    $alt = [IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady -and
            ($_.AvailableFreeSpace / 1GB) -ge $needGB } | ForEach-Object { $_.Name }
    $hint = if ($alt) { "run again with -DataDir on a drive with room, e.g. -DataDir $($alt[0])ai-coder-models" }
            else { "free up space (no local drive has $needGB GB free)" }
    Fail "not enough disk space: $needGB GB needed in $DataDir, $freeGB GB free." $hint
}
if (-not $sameDrive -and $toolsFree -lt $toolsNeed) {
    Fail "not enough disk space for the tools in $AppDir`: $toolsNeed GB needed, $toolsFree GB free."
}
Write-Ok "enough space"

if ($CheckOnly) {
    Write-Step "Check only: nothing was changed"
    $strataLine = if (-not $strataPresent) { "would be installed ($PinnedStrata)" }
                  elseif ($strataTarget) { "would be updated to $strataTarget" } else { "installed ($strataVersion)" }
    Write-Info "Strata:   $strataLine"
    Write-Info "Model:    $(if ($modelReady) { 'ready' } elseif ($ggufDir) { 'would be set up from the HF cache' } else { "would be downloaded (~$($variant.DownloadGB) GB)" })"
    Write-Info "OpenCode: $(if ($openCodeRelease) { "would be updated to $($openCodeRelease.tag_name)" } elseif ($openCodePresent) { 'installed' } else { 'would be installed' })"
    Write-Info "Server:   $(if ($serverRunning) { "running on port $Port" } else { "would be started on port $Port" })"
    Write-Info "Project:  $ProjectDir"
    exit 0
}

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

# ------------------------------------------------------------------------------------------------ 4. Strata
# Unpacked into a scratch folder, checked, then moved into place whole: a broken download or an interrupted unpack
# never leaves a half copy that a later run would take for an install.
function Install-StrataCode($version) {
    $dest = Join-Path $AppDir "strata-$version"
    if (Test-Path -LiteralPath (Join-Path $dest 'setup.py')) { return $dest }   # unpacked by an earlier run
    $zip = Join-Path $DownloadDir "strata-$version.zip"
    Invoke-Download "https://github.com/Niko1221/Strata/archive/refs/tags/$version.zip" $zip
    Remove-Item -LiteralPath $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue
    try { Expand-Archive -LiteralPath $zip -DestinationPath $UnpackDir }
    catch {
        Remove-Item -LiteralPath $zip, $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue
        Fail "the Strata download was damaged ($($_.Exception.Message)); it has been deleted." "run this again to download it afresh"
    }
    $inner = Get-ChildItem -LiteralPath $UnpackDir -Directory | Select-Object -First 1
    if (-not $inner -or -not (Test-Path -LiteralPath (Join-Path $inner.FullName 'setup.py')) -or
        -not (Test-Path -LiteralPath (Join-Path $inner.FullName 'START-HERE.bat'))) {
        Remove-Item -LiteralPath $zip, $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue
        Fail "the Strata $version download does not contain Strata's setup files." "run this again; if it repeats, the release may have changed"
    }
    Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue   # a leftover without setup.py
    Move-Item -LiteralPath $inner.FullName -Destination $dest
    Remove-Item -LiteralPath $UnpackDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    return $dest
}

function Invoke-StrataSetup($root, $argList) {
    # START-HERE.bat installs Python (per-user) and the .venv if needed, then runs setup.py. stdin is NUL so a
    # 'pause' on failure cannot hang this script; output is streamed so a build-tools install (admin) can be stopped.
    $quoted = ($argList | ForEach-Object { if ("$_" -match '\s') { "`"$_`"" } else { "$_" } }) -join ' '
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = '/d /s /c ""' + (Join-Path $root 'START-HERE.bat') + '" ' + $quoted + ' < NUL 2>&1"'
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'
    $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $p = [Diagnostics.Process]::Start($psi)
    $lastProgress = [DateTime]::MinValue
    while ($null -ne ($line = $p.StandardOutput.ReadLine())) {
        if ($line -match 'has to be compiled|Install them now\?|Windows will ask for permission') {
            [void](Invoke-Native taskkill.exe @('/T', '/F', '/PID', $p.Id))
            Fail "Strata has no ready-made engine for this GPU and wants to install Visual Studio and the CUDA Toolkit (needs admin)." `
                 "on a PC where you have admin rights, run $root\START-HERE.bat once by hand to let it compile."
        }
        # download progress bars redraw constantly: show one every few seconds
        if ($line -match '\d+%\|' -or $line -match '[\d.]+\s*[MG]B/s') {
            if (((Get-Date) - $lastProgress).TotalSeconds -lt 5) { continue }
            $lastProgress = Get-Date
        }
        if ($line.Trim()) { Write-Host "    | $line" }
    }
    $p.WaitForExit()
    return $p.ExitCode
}

function Get-SetupArgs {
    $a = @('--family', $variant.Family, '--model', $variant.Model, '--vision', 'no', '--data-dir', $DataDir,
           '--port', $Port, '--no-browser', '--no-start', '--yes')
    if ($Context) { $a += @('--context', $Context) }
    if ($ggufDir) { $a += @('--gguf-dir', $ggufDir) }
    return $a
}

if ($strataTarget) {
    $isUpdate = $strataPresent
    Write-Step $(if ($isUpdate) { "Updating Strata $strataVersion -> $strataTarget" } else { "Installing Strata $strataTarget" })
    $oldRoot = $StrataRoot
    $newRoot = Install-StrataCode $strataTarget
    Write-Ok "Strata $strataTarget unpacked in $newRoot"
    if ($isUpdate) {
        # the new copy sets itself up beside the old one (the model files are reused, nothing big is downloaded);
        # the old one stays in use until that has worked
        if ($serverRunning) {
            Write-Info "stopping the running server so the new version can take over"
            [void](Stop-StrataServers); $serverRunning = $false; $health = $null
        }
        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
        $code = Invoke-StrataSetup $newRoot (Get-SetupArgs)
        if (-not (Test-StrataConfig $newRoot)) {
            Remove-Item -LiteralPath $newRoot -Recurse -Force -ErrorAction SilentlyContinue
            Fail "the Strata $strataTarget setup did not finish (exit code $code); its output is above. Strata $strataVersion is still installed and in use." `
                 "run without -Update to keep using it"
        }
        $StrataRoot = $newRoot
        Save-State @{ StrataRoot = $StrataRoot; StrataVersion = $strataTarget; Model = $tag; DataDir = $DataDir }
        $modelReady = $true
        if ($oldRoot -and $oldRoot.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) -and $oldRoot -ne $newRoot) {
            try { Remove-Item -LiteralPath $oldRoot -Recurse -Force; Write-Ok "removed the old version ($oldRoot)" }
            catch { Write-Warn "could not remove the old version in $oldRoot ($($_.Exception.Message)); it can be deleted by hand" }
        }
        Write-Ok "Strata updated to $strataTarget"
    } else {
        $StrataRoot = $newRoot
        Save-State @{ StrataRoot = $StrataRoot; StrataVersion = $strataTarget }
    }
} else {
    Write-Skip "Strata already installed"
}

# ------------------------------------------------------------------------------------------------ 5. the model
if (-not $modelReady) {
    Write-Step "Setting up $($variant.Title)"
    Write-Info "this downloads ~$(if ($ggufDir) { 6 } else { [math]::Round($variant.DownloadGB + 6) }) GB and prepares it; it can take a long while"
    Write-Info "(interrupted? just run this again - the download resumes)"
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $code = Invoke-StrataSetup $StrataRoot (Get-SetupArgs)
    if (-not (Test-StrataConfig $StrataRoot)) {
        Fail "Strata's setup did not finish (exit code $code); its output is above." `
             "fix what it reports and run this again - finished downloads are kept"
    }
    $modelReady = $true
    Save-State @{ StrataRoot = $StrataRoot; Model = $tag; DataDir = $DataDir }
    Write-Ok "model $tag is set up"
} else {
    Write-Skip "model already set up"
}

# ------------------------------------------------------------------------------------------------ 6. OpenCode
if (-not $openCodePresent -or $openCodeRelease) {
    Write-Step $(if ($openCodeRelease) { "Updating OpenCode" } else { "Installing OpenCode" })
    $rel = if ($openCodeRelease) { $openCodeRelease } else { Get-LatestRelease 'anomalyco/opencode' }
    $assetName = if ($spec.Avx2 -eq $false) { 'opencode-windows-x64-baseline.zip' } else { 'opencode-windows-x64.zip' }
    $asset = $rel.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
    if (-not $asset) { Fail "OpenCode release $($rel.tag_name) has no $assetName" }
    $zip = Join-Path $DownloadDir $assetName
    $stage = Join-Path $AppDir 'opencode-unpack'
    Invoke-Download $asset.browser_download_url $zip
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    try { Expand-Archive -LiteralPath $zip -DestinationPath $stage }
    catch {
        Remove-Item -LiteralPath $zip, $stage -Recurse -Force -ErrorAction SilentlyContinue
        Fail "the OpenCode download was damaged ($($_.Exception.Message)); it has been deleted." "run this again to download it afresh"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $stage 'opencode.exe'))) { Fail "opencode.exe was not in the OpenCode download" }
    New-Item -ItemType Directory -Force -Path $OpenCodeDir | Out-Null
    # a running exe cannot be overwritten but can be renamed: the old one steps aside, and comes back on failure
    $oldExe = "$OpenCodeExe.old"
    Remove-Item -LiteralPath $oldExe -Force -ErrorAction SilentlyContinue   # in use by an open OpenCode: then kept
    if (Test-Path -LiteralPath $OpenCodeExe) {
        if (Test-Path -LiteralPath $oldExe) { $oldExe = "$OpenCodeExe.$(Get-Date -Format 'yyyyMMddHHmmss').old" }
        Rename-Item -LiteralPath $OpenCodeExe -NewName (Split-Path $oldExe -Leaf)
    }
    try { Move-Item -LiteralPath (Join-Path $stage 'opencode.exe') -Destination $OpenCodeExe }
    catch {
        if (Test-Path -LiteralPath $oldExe) { Rename-Item -LiteralPath $oldExe -NewName (Split-Path $OpenCodeExe -Leaf) }
        Fail "could not put the new opencode.exe in place ($($_.Exception.Message))."
    }
    Remove-Item -LiteralPath $zip, $stage -Recurse -Force -ErrorAction SilentlyContinue
    Save-State @{ OpenCodeVersion = $rel.tag_name }
    Write-Ok "OpenCode $($rel.tag_name)"
} else {
    Write-Skip "OpenCode already installed"
}

# ------------------------------------------------------------------------------------------------ 7. the server
# Waits until the server is ready, the deadline passes, or the server is gone. $proc: the process we started.
function Wait-StrataReady($proc) {
    $deadline = (Get-Date).AddMinutes($LoadMinutes)
    while ((Get-Date) -lt $deadline) {
        if (Test-StrataReady $Port) { return $true }
        if ($proc) { if ($proc.HasExited) { return $false } }
        elseif (-not @(Get-StrataServerProcesses)) { return $false }
        Start-Sleep -Seconds 3
    }
    return $false
}

$serverLog = Join-Path $LogDir 'server.log'
if (-not $serverRunning) {
    Write-Step "Starting the Strata server"
    $cfgPath = Join-Path $StrataRoot "strata-$tag.json"
    $cfg = Read-Json $cfgPath
    $python = Join-Path $StrataRoot '.venv\Scripts\python.exe'
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    if (Test-Path -LiteralPath $serverLog) { Move-Item -LiteralPath $serverLog -Destination (Join-Path $LogDir 'server.prev.log') -Force }
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    # hidden, with everything it prints in logs\server.log (a crash's last words are kept there); ai-coder -Stop ends it
    $cmdArgs = '/d /s /c ""{0}" "{1}" --engine strata --config "{2}" --port {3} > "{4}" 2>&1"' -f `
               $python, (Join-Path $StrataRoot 'serve\server.py'), $cfgPath, $Port, $serverLog
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdArgs -WorkingDirectory $StrataRoot -WindowStyle Hidden -PassThru
    Save-State @{ Port = $Port }
    Write-Info "loading the model (usually 30-90 s; the first start can take a few minutes)"
    Write-Info "server output: $serverLog"
    if (-not (Wait-StrataReady $proc)) {
        $why = if ($proc.HasExited) { "it stopped" } else { "it was not ready after $LoadMinutes minutes" }
        Show-LogTail $serverLog
        if ($cfg -and $cfg.log) { Show-LogTail $cfg.log 15 }
        if (-not $proc.HasExited) { [void](Stop-StrataServers) }
        Fail "the Strata server did not come up: $why." "the logs are above; START-HERE.bat in $StrataRoot shows more"
    }
} elseif (-not (Test-StrataReady $Port)) {
    Write-Step "Waiting for the running Strata server to finish loading"
    if (-not (Wait-StrataReady $null)) {
        Show-LogTail $serverLog
        Fail "the running Strata server did not become ready within $LoadMinutes minutes." "stop it with: ai-coder -Stop, then run this again"
    }
} else {
    Write-Skip "server already running"
}
$health = Get-StrataHealth $Port
if (-not $health) { Fail "the Strata server stopped answering on port $Port." "see $serverLog" }
Write-Ok "Strata is serving $($health.model) on http://127.0.0.1:$Port (context $($health.max_context))"

# ------------------------------------------------------------------------------------------------ 8. OpenCode config
$modelId = $health.model
$displayName = ($variant.Title -replace ' \(.*$', '') + ' (local)'
$ctx = if ($health.max_context) { [int]$health.max_context } else { 131072 }
$config = [ordered]@{
    '$schema' = 'https://opencode.ai/config.json'
    provider = [ordered]@{
        strata = [ordered]@{
            npm = '@ai-sdk/openai-compatible'
            name = 'Strata (local)'
            options = [ordered]@{ baseURL = "http://127.0.0.1:$Port/v1"; apiKey = 'local' }
            models = [ordered]@{
                $modelId = [ordered]@{
                    name = $displayName
                    limit = [ordered]@{ context = $ctx; output = [math]::Min(32768, [int]($ctx / 4)) }
                }
            }
        }
    }
    model = "strata/$modelId"
    small_model = "strata/$modelId"
}
$json = $config | ConvertTo-Json -Depth 10
$old = if (Test-Path -LiteralPath $OpenCodeCfg) { Get-Content -LiteralPath $OpenCodeCfg -Raw -Encoding UTF8 } else { '' }
if ("$old".Trim() -ne $json.Trim()) { Write-Utf8File $OpenCodeCfg $json; Write-Ok "OpenCode config: $OpenCodeCfg" }

# ------------------------------------------------------------------------------------------------ 9. OpenCode
if ($lock) { $lock.ReleaseMutex(); $lock.Dispose(); $lock = $null }   # setup is done: other runs may go ahead
if ($script:TranscriptPath) { try { Stop-Transcript | Out-Null } catch { } }   # OpenCode's screen is not logged

if ($NoLaunch) {
    Write-Step "Ready"
    Write-Info "server: http://127.0.0.1:$Port/v1  (stop it with: ai-coder -Stop)"
    exit 0
}
Write-Step "Opening OpenCode in $ProjectDir"
$env:OPENCODE_CONFIG = $OpenCodeCfg
Push-Location -LiteralPath $ProjectDir
try { & $OpenCodeExe $ProjectDir } finally { Pop-Location }

Write-Host ""
Write-Info "The Strata server is still running (it keeps the model loaded for next time)."
Write-Info "Stop it with: ai-coder -Stop"
