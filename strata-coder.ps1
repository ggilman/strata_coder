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
    .\strata-coder.ps1 -Status
    .\strata-coder.ps1 -Setup
    .\strata-coder.ps1 -Offline
    .\strata-coder.ps1 -Alias
    .\strata-coder.ps1 -Help
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [ValidateSet('qwen', 'coder')][string]$Family,
    [string]$Model,
    [int]$Context,
    [ValidateSet('int8', 'k8v4', 'q4_0')][string]$Kv,
    [string]$DataDir,
    [ValidateRange(1, 65535)][int]$Port = 8080,
    [switch]$NoLaunch,
    [switch]$Update,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Setup,
    [switch]$Offline,
    [switch]$Help,
    [switch]$Alias,
    [ValidateSet('auto', 'cmd', 'powershell', 'ps', 'pwsh', 'bash', 'git-bash', 'gitbash')][string]$AliasShell
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$FamilySpecified = $PSBoundParameters.ContainsKey('Family')
$ModelSpecified = $PSBoundParameters.ContainsKey('Model')
$ContextSpecified = $PSBoundParameters.ContainsKey('Context')
$KvSpecified = $PSBoundParameters.ContainsKey('Kv')
$DataDirSpecified = $PSBoundParameters.ContainsKey('DataDir')
$PortSpecified = $PSBoundParameters.ContainsKey('Port')
$OfflineSpecified = $PSBoundParameters.ContainsKey('Offline')
$AliasSpecified = $PSBoundParameters.ContainsKey('Alias')

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
$GumDir      = Join-Path $AppDir 'gum'
$GumExe      = Join-Path $GumDir 'gum.exe'

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
$SlackGB       = 1
$SetupMinutes  = 6

# KV cache bytes per cached weight per quantization: q8_0 = 1.0625,
# q4_0 = 0.5625, k8v4 averages the two.
$KvBytesPerWeight = [ordered]@{
    int8 = 1.0625
    k8v4 = 0.8125
    q4_0 = 0.5625
}
$ComfortableHeadroomGB = 8
$TightHeadroomGB       = 2
$LogKeep       = 20
$StatusRetries = 3
$DefaultContextLimit = 131072
$MaxOutputLimit      = 32768
$MinOutputLimit      = 256

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
        Write-Warn "could not parse $Path ($($_.Exception.Message)); ignoring its contents"
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

function Update-ProfileBlock {
    param(
        [string]$Path,
        [string]$Begin,
        [string]$End,
        [string]$Block,
        [string]$StripPattern = $null
    )

    $parent = Split-Path -Parent $Path

    if ($parent) {
        [IO.Directory]::CreateDirectory($parent) | Out-Null
    }

    $text = if (Test-Path -LiteralPath $Path) {
        [IO.File]::ReadAllText($Path)
    } else {
        ''
    }

    $pattern = '(?ms)^\s*' + [regex]::Escape($Begin) + '.*?^\s*' + [regex]::Escape($End) + '\s*\r?\n?'
    $text = [regex]::Replace($text, $pattern, '')

    if ($StripPattern) {
        $text = [regex]::Replace($text, $StripPattern, '')
    }

    if ($text.Length -gt 0 -and -not $text.EndsWith([Environment]::NewLine)) {
        $text += [Environment]::NewLine
    }

    Write-Utf8File $Path ($text + $Block + [Environment]::NewLine)
}

function Get-IntSetting($Value, [int]$Fallback) {
    $number = 0

    if ($null -ne $Value -and [int]::TryParse("$Value", [ref] $number)) {
        return $number
    }

    return $Fallback
}

function Select-FittingVariant([object[]]$Candidates, [double]$RamGB) {
    return @(
        $Candidates | Where-Object {
            $RamGB -ge $_.MinRamGB
        }
    ) | Select-Object -First 1
}

function Close-SessionResources {
    if ($script:lock) {
        $script:lock.ReleaseMutex()
        $script:lock.Dispose()
        $script:lock = $null
    }

    if ($script:TranscriptPath) {
        try {
            Stop-Transcript | Out-Null
        } catch {
        }
    }
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

function Get-MouseOffSequences {
    $esc = [char] 27

    return @(
        "$esc[?1000l",
        "$esc[?1002l",
        "$esc[?1003l",
        "$esc[?1004l",
        "$esc[?1005l",
        "$esc[?1006l",
        "$esc[?1015l",
        "$esc[?2004l"
    )
}

function Reset-TerminalMouse {
    $payload = (Get-MouseOffSequences) -join ''
    $bytes = [Text.Encoding]::ASCII.GetBytes($payload)

    try {
        if (-not ('StrataCoder.ConsoleNative' -as [type])) {
            Add-Type `
                -Namespace StrataCoder `
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

        $native = [StrataCoder.ConsoleNative]

        # Output: when stdout is a real console (conhost), turn on virtual-terminal
        # processing first so the disable sequences are interpreted instead of printed.
        # When stdout is a pipe (Git Bash / MinTTY via conpty), there is no console
        # mode to set; the raw bytes still travel down the pipe to the terminal.
        $out = $native::GetStdHandle(-11)
        [uint32] $outMode = 0

        if ($native::GetConsoleMode($out, [ref] $outMode)) {
            [void] $native::SetConsoleMode($out, [uint32] ($outMode -bor 0x0004))
        }

        # Write the raw bytes straight to stdout. This reaches the terminal in both
        # cases: a console interprets them as VT, and a pipe carries them to the
        # terminal emulator (MinTTY) that is still reporting mouse events.
        $stdout = [Console]::OpenStandardOutput()
        $stdout.Write($bytes, 0, $bytes.Length)
        $stdout.Flush()

        # Input: clear the flags that make the console deliver mouse and raw VT
        # input, which is what leaks mouse events into the shell as text.
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

    $localSize = 0

    if (Test-Path -LiteralPath $OutFile) {
        $localSize = (Get-Item -LiteralPath $OutFile).Length

        try {
            $head = Invoke-WebRequest -UseBasicParsing -Method Head -Uri $Url
            $remoteSize = @($head.Headers['Content-Length'])[0]

            if ($remoteSize -and $localSize -gt 0 -and [int64]$remoteSize -eq $localSize) {
                Write-Info 'the file is already fully downloaded'
                return
            }
        } catch {
        }
    }

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue

    if ($curl) {
        $httpCode = "$(& $curl.Source -L --fail --retry 3 --retry-delay 5 -C - -o $OutFile -w '%{http_code}' $Url)".Trim()
        $curlExit = $LASTEXITCODE

        if ($curlExit -eq 0) {
            return
        }

        # 416 means the resume offset is already at or past the end of the
        # remote file: the local copy is complete even when HEAD gave no size.
        if ($curlExit -eq 22 -and $httpCode -eq '416' -and $localSize -gt 0) {
            Write-Info 'the server reports the file is already fully downloaded'
            return
        }

        Write-Warn "curl failed with exit code $curlExit; retrying with Invoke-WebRequest"
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
    } else {
        Write-Warn 'curl.exe was not found; the built-in downloader is much slower for large files'
    }

    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile
    } catch {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue

        throw "could not download $Url ($($_.Exception.Message))"
    }
}

function Get-DownloadedFile {
    param(
        [string]$Url,
        [string]$Zip,
        [string]$Stage,
        [string]$FileFilter
    )

    Invoke-Download $Url $Zip

    Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue

    try {
        Expand-Archive -LiteralPath $Zip -DestinationPath $Stage
    } catch {
        Remove-Item -LiteralPath $Zip, $Stage -Recurse -Force -ErrorAction SilentlyContinue

        throw "the archive is damaged ($($_.Exception.Message)); it was removed"
    }

    $staged = @(
        Get-ChildItem -LiteralPath $Stage -Filter $FileFilter -Recurse -ErrorAction SilentlyContinue
    ) | Select-Object -First 1

    if (-not $staged) {
        Remove-Item -LiteralPath $Zip, $Stage -Recurse -Force -ErrorAction SilentlyContinue

        throw "$FileFilter was not found in the archive"
    }

    return $staged.FullName
}

function Get-LatestRelease([string]$Repository) {
    try {
        return Invoke-RestMethod `
            -UseBasicParsing `
            -Uri "https://api.github.com/repos/$Repository/releases/latest" `
            -Headers @{ 'User-Agent' = 'strata-coder' } `
            -ErrorAction Stop
    } catch {
        Write-Warn "could not contact GitHub for $Repository ($($_.Exception.Message))"
        return $null
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
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
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

function Stop-StrataServers([int]$OnPort = 0) {
    $processes = @(Get-StrataServerProcesses)

    if ($OnPort -gt 0) {
        $processes = @(
            $processes | Where-Object {
                (Get-ServerPort $_) -eq $OnPort
            }
        )
    }

    $found = 0

    foreach ($process in $processes) {
        if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
            continue
        }

        $found += 1

        [void](Invoke-Native taskkill.exe @('/T', '/F', '/PID', $process.ProcessId))

        if ($LASTEXITCODE -eq 0) {
            Write-Ok "stopped the Strata server (PID $($process.ProcessId))"
        } elseif ($LASTEXITCODE -ne 128) {
            # 128 means the process already exited between discovery and the kill.
            Write-Warn "could not stop the Strata server (PID $($process.ProcessId)); taskkill exit code $LASTEXITCODE"
        }
    }

    return $found
}

function Wait-StrataReady($Process, [int]$ServerPort) {
    $deadline = (Get-Date).AddMinutes($LoadMinutes)

    while ((Get-Date) -lt $deadline) {
        if (Test-StrataReady $ServerPort) {
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

function Get-Candidates([string]$FamilyFilter, [string]$ModelFilter) {
    $candidates = $Variants

    if ($FamilyFilter) {
        $candidates = @(
            $candidates | Where-Object {
                $_.Family -eq $FamilyFilter
            }
        )
    }

    if ($ModelFilter) {
        $candidates = @(
            $candidates | Where-Object {
                $_.Model -eq $ModelFilter.ToUpper()
            }
        )
    }

    if (-not $candidates) {
        $known = ($Variants | ForEach-Object {
            "$($_.Family)/$($_.Model)"
        }) -join ', '

        Fail "no model variant matches -Family '$FamilyFilter' -Model '$ModelFilter'" `
            "Choose one of: $known"
    }

    return $candidates
}

function Get-CallingShell {
    if ($env:MSYSTEM) {
        return 'bash'
    }

    try {
        $procId = $PID

        for ($hop = 0; $hop -lt 6; $hop++) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue

            if (-not $proc) {
                break
            }

            $name = "$($proc.Name)".ToLowerInvariant()

            if ($name -eq 'bash.exe' -or $name -eq 'sh.exe') {
                return 'bash'
            }

            if (
                ($name -eq 'powershell.exe' -or $name -eq 'pwsh.exe') -and
                $procId -ne $PID
            ) {
                return 'powershell'
            }

            $procId = $proc.ParentProcessId
        }
    } catch {
    }

    return 'cmd'
}

function Install-ScodeAlias([string]$Shell) {
    $scriptFile = $PSCommandPath
    $launcher = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$scriptFile`""

    Write-Info "adding the scode shortcut to $Shell"

    if ($Shell -eq 'cmd') {
        $macroDir = Join-Path $env:USERPROFILE 'bin'
        $macroFile = Join-Path $macroDir 'cmd_macros.doskey'

        New-Item -ItemType Directory -Force -Path $macroDir | Out-Null

        $lines = @()

        if (Test-Path -LiteralPath $macroFile) {
            $lines = @(
                Get-Content -LiteralPath $macroFile |
                    Where-Object { $_ -notmatch '^\s*scode=' }
            )
        }

        $lines += "scode=$launcher `$*"

        Write-Utf8File $macroFile (($lines -join [Environment]::NewLine) + [Environment]::NewLine)
        Write-Ok "updated the DOSKEY macro: $macroFile"

        $key = 'HKCU:\Software\Microsoft\Command Processor'
        $loader = "doskey /macrofile=`"$macroFile`""
        $old = (Get-ItemProperty -Path $key -Name AutoRun -ErrorAction SilentlyContinue).AutoRun

        if ([string]::IsNullOrWhiteSpace($old)) {
            $new = $loader
        } elseif ($old.Contains($macroFile)) {
            $new = $old
        } else {
            $new = $old + ' & ' + $loader
        }

        try {
            New-ItemProperty -Path $key -Name AutoRun -Value $new -PropertyType ExpandString -Force | Out-Null
            Write-Ok 'preserved or updated the CMD AutoRun entry'
        } catch {
            Write-Warn "the DOSKEY macro was created, but the CMD AutoRun entry could not be updated ($($_.Exception.Message))."
        }
    } elseif ($Shell -eq 'bash') {
        $homeDir = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }
        $profileFile = Join-Path $homeDir '.bash_profile'

        $begin = '# BEGIN strata-coder scode'
        $end = '# END strata-coder scode'

        $esc = [string][char] 27
        $printfOff = ((Get-MouseOffSequences) | ForEach-Object { $_.Replace($esc, '\033') }) -join ''

        $block = @(
            $begin
            "SCODE_CMD='$scriptFile'"
            'unset -f scode 2>/dev/null'
            'scode() {'
            '    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SCODE_CMD" "$@"'
            '    local rc=$?'
            "    printf `"$printfOff`" >/dev/tty"
            '    return $rc'
            '}'
            $end
        ) -join [Environment]::NewLine

        Update-ProfileBlock $profileFile $begin $end $block '(?m)^\s*alias scode=.*\r?\n?'
        Write-Ok "updated the scode function in $profileFile"
    } else {
        $profileFile = $PROFILE.CurrentUserCurrentHost

        if (-not $profileFile) {
            $profileFile = $PROFILE
        }

        $begin = '# >>> strata-coder scode >>>'
        $end = '# <<< strata-coder scode <<<'

        $block = @(
            $begin
            "function scode { & $launcher @args }"
            $end
        ) -join [Environment]::NewLine

        Update-ProfileBlock $profileFile $begin $end $block
        Write-Ok "updated the scode function in $profileFile"
    }

    Write-Info 'open a new shell window to pick up the shortcut'
}

function Show-Help {
    $text = @'
strata-coder - OpenCode launch and setup utility

Usage:
  .\strata-coder.ps1 [options]
  scode [options] after the shortcut is set up

Options:
  -Help, --help, -h          Show this help.
  -Setup                     Interactive setup menu, then install and stop.
  -CheckOnly                 Show specs and installs; change nothing.
  -NoLaunch                  Set up and start the server without OpenCode.
  -Stop                      Stop the Strata server.
  -Status                    Live tokens/sec panel, refreshing in place.
  -Update                    Update Strata and OpenCode to newest releases.
  -Alias                     Add the scode shortcut. Pick the shell with
                             -AliasShell: auto (default), cmd, powershell,
                             or bash.
  -Family qwen|coder         Model family.
  -Model <name>              Model variant, e.g. IQ3_S or IQ1_M.
  -Context <tokens>          Context size; 0 = Strata recommendation.
  -Kv int8|k8v4|q4_0         KV cache quantization.
  -DataDir <path>            Model folder.
  -Port <port>               Server port, default 8080.
  -Offline                   Block OpenCode web access;
                             -Offline:$false turns the block off.
'@

    Write-Host $text
}

function Get-Gum {
    if (Test-Path -LiteralPath $GumExe) {
        return $GumExe
    }

    Write-Step 'Getting gum for the setup menus'

    $release = $null

    try {
        $release = Invoke-RestMethod `
            -UseBasicParsing `
            -Uri 'https://api.github.com/repos/charmbracelet/gum/releases/latest' `
            -Headers @{ 'User-Agent' = 'strata-coder' } `
            -ErrorAction Stop
    } catch {
        Write-Warn "could not contact GitHub for gum ($($_.Exception.Message)); using plain prompts"
        return $null
    }

    $asset = @(
        $release.assets | Where-Object {
            $_.name -like 'gum_*_Windows_x86_64.zip'
        }
    ) | Select-Object -First 1

    if (-not $asset) {
        Write-Warn "gum release $($release.tag_name) has no Windows x86_64 zip; using plain prompts"
        return $null
    }

    $zip = Join-Path $DownloadDir $asset.name
    $stage = Join-Path $AppDir 'gum-unpack'

    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null

    try {
        $stagedExe = Get-DownloadedFile $asset.browser_download_url $zip $stage 'gum.exe'
    } catch {
        Write-Warn "could not prepare gum ($($_.Exception.Message)); using plain prompts"
        return $null
    }

    New-Item -ItemType Directory -Force -Path $GumDir | Out-Null
    Move-Item -LiteralPath $stagedExe -Destination $GumExe -Force
    Remove-Item -LiteralPath $zip, $stage -Recurse -Force -ErrorAction SilentlyContinue

    Write-Ok "gum is ready at $GumExe"

    return $GumExe
}

function Invoke-GumChoose([string]$Header, [string[]]$OrderedLabels) {
    $choice = & $GumExe choose --header $Header --height 12 --cursor '>' $OrderedLabels

    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'setup cancelled.'
        exit 1
    }

    return [array]::IndexOf($OrderedLabels, $choice)
}

function Invoke-GumInput([string]$Header, [string]$Value) {
    $answer = & $GumExe input --header $Header --value $Value

    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'setup cancelled.'
        exit 1
    }

    return "$answer".Trim()
}

function Invoke-GumConfirm([string]$Message, [bool]$Default) {
    if ($Default) {
        & $GumExe confirm --default $Message
    } else {
        & $GumExe confirm $Message
    }

    return ($LASTEXITCODE -eq 0)
}

function Select-OrderedDefault([object[]]$Options, $DefaultValue) {
    $defaultOption = @(
        $Options | Where-Object {
            $_.Value -eq $DefaultValue
        }
    ) | Select-Object -First 1

    if (-not $defaultOption) {
        $defaultOption = $Options[0]
    }

    return @(
        @($defaultOption) + @(
            $Options | Where-Object {
                $_.Value -ne $defaultOption.Value
            }
        )
    )
}

# Options are hashtables with Value, Name (for typed matching), and Label.
function Prompt-Choice {
    param(
        [string]$Header,
        [object[]]$Options,
        $DefaultValue,
        $UseGum
    )

    if ($UseGum) {
        $ordered = Select-OrderedDefault $Options $DefaultValue
        $index = Invoke-GumChoose $Header @($ordered | ForEach-Object { $_.Label })

        if ($index -lt 0 -or $index -ge $ordered.Count) {
            return $DefaultValue
        }

        return $ordered[$index].Value
    }

    $number = 0

    foreach ($opt in $Options) {
        $number++
        Write-Info "$number. $($opt.Label)"
    }

    $values = @($Options | ForEach-Object { $_.Value })
    $defaultIndex = [array]::IndexOf($values, $DefaultValue) + 1

    if ($defaultIndex -ge 1) {
        $defaultLabel = @(
            $Options | Where-Object { $_.Value -eq $DefaultValue }
        ) | Select-Object -First 1 | ForEach-Object { $_.Label }
        Write-Info "Default: $defaultIndex. $defaultLabel"
    } else {
        Write-Info "Default: $DefaultValue"
    }

    while ($true) {
        $answer = Read-Host "$Header [1-$($Options.Count), Enter keeps the default]"

        if ($answer.Trim() -eq '') {
            break
        }

        $index = 0

        if (
            [int]::TryParse($answer.Trim(), [ref] $index) -and
            $index -ge 1 -and
            $index -le $Options.Count
        ) {
            return $Options[$index - 1].Value
        }

        $byName = @(
            $Options | Where-Object {
                "$($_.Name)".ToLowerInvariant() -eq $answer.Trim().ToLowerInvariant()
            }
        ) | Select-Object -First 1

        if ($byName) {
            return $byName.Value
        }

        Write-Warn "Enter a number from 1 through $($Options.Count), a name such as $($Options[0].Name), or leave the field blank."
    }

    return $DefaultValue
}

function Prompt-Text([string]$Header, [string]$Default, $UseGum) {
    if ($UseGum) {
        return Invoke-GumInput $Header $Default
    }

    return (Read-Host "$Header [$Default]").Trim()
}

function Prompt-Port([string]$Header, [int]$DefaultPort, $UseGum) {
    while ($true) {
        if ($UseGum) {
            $answer = Invoke-GumInput $Header "$DefaultPort"
        } else {
            $answer = (Read-Host "$Header [$DefaultPort]").Trim()
        }

        if ($answer -eq '') {
            return $DefaultPort
        }

        $number = 0

        if (
            [int]::TryParse($answer, [ref] $number) -and
            $number -ge 1 -and
            $number -le 65535
        ) {
            return $number
        }

        Write-Warn 'Enter a port number from 1 through 65535 or leave the field blank.'
    }
}

function Prompt-YesNo([string]$Message, [bool]$Default, $UseGum) {
    if ($UseGum) {
        return Invoke-GumConfirm $Message $Default
    }

    $defaultAnswer = if ($Default) { 'y' } else { 'n' }
    $answer = Read-Host "$Message (y/n, default $defaultAnswer)"

    if ($answer.Trim() -eq '') {
        return $Default
    }

    return ($answer.Trim().ToUpperInvariant() -eq 'Y')
}

function Prompt-SetupParams($Spec) {
    $saved = Read-Json $SetupPath

    $defaultDataDir = $null

    if ($DataDirSpecified -and $DataDir) {
        $defaultDataDir = $DataDir
    } elseif ($saved -and $saved.dataDir) {
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
    $defaultKv = 'int8'
    $defaultPort = 8080
    $defaultOffline = $false

    if ($saved) {
        $defaultContext = Get-IntSetting $saved.context $defaultContext

        if ($saved.kv) {
            $defaultKv = [string]$saved.kv
        }

        $savedPort = Get-IntSetting $saved.port 0

        if ($savedPort -gt 0) {
            $defaultPort = $savedPort
        }

        if ($saved.offline -eq $true) {
            $defaultOffline = $true
        }
    }

    if ($ContextSpecified) {
        $defaultContext = $Context
    }

    if ($KvSpecified) {
        $defaultKv = $Kv
    }

    if ($PortSpecified) {
        $defaultPort = $Port
    }

    $selected = $null

    if ($Family -or $Model) {
        $requested = @(Get-Candidates $Family $Model)
        $selected = Select-FittingVariant $requested $Spec.RamGB

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
        $selected = Select-FittingVariant $Variants $Spec.RamGB
    }

    if (-not $selected) {
        Fail 'this PC does not have enough RAM for any configured Strata model variant.'
    }

    $gum = Get-Gum

    if ($gum) {
        Write-Step 'Asking for setup parameters (Enter picks the highlighted default)'
    } else {
        Write-Step 'Asking for setup parameters (Enter keeps the displayed default)'
    }

    if (-not ($Family -or $Model)) {
        $variantOptions = @(
            $Variants | ForEach-Object {
                @{
                    Value = $_
                    Name  = $_.Model
                    Label = "$($_.Title) (needs $($_.MinRamGB) GB RAM)"
                }
            }
        )

        $selected = Prompt-Choice 'Variant' $variantOptions $selected $gum
    }

    $selectedDataDir = $defaultDataDir

    if (-not $DataDirSpecified) {
        $answer = Prompt-Text 'Model folder' $defaultDataDir $gum

        if ($answer.Trim() -ne '') {
            $selectedDataDir = [IO.Path]::GetFullPath($answer.Trim())
        }
    }

    $contextChoices = @(4096, 8192, 16384, 32768, 65536, 131072, 204800, 262144, 393216, 524288)
    $selectedContext = $defaultContext

    if (-not $ContextSpecified) {
        if ($gum) {
            $contextOptions = @(
                @{ Value = 0; Name = '0'; Label = '0 (Strata recommendation)' }
            )

            foreach ($tokens in $contextChoices) {
                $label = "$([int]($tokens / 1024))k ($tokens tokens)"

                if ($tokens -gt 262144) {
                    $label += ' (past trained, YaRN)'
                }

                $contextOptions += @{
                    Value = $tokens
                    Name  = "$([int]($tokens / 1024))k"
                    Label = $label
                }
            }

            $selectedContext = Prompt-Choice 'Context' $contextOptions $defaultContext $true
        } else {
            $contextNumber = 0

            foreach ($tokens in $contextChoices) {
                $contextNumber++
                $label = "$([int]($tokens / 1024))k ($tokens tokens)"

                if ($tokens -gt 262144) {
                    $label += ' (past trained, YaRN)'
                }

                Write-Info "$contextNumber. $label"
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
                $answer = Read-Host "Context [0 = Strata recommendation, 1-$($contextChoices.Count) = 4k-512k]"

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
    }

    $kvChoices = @('int8', 'k8v4', 'q4_0')
    $kvLabels = [ordered]@{
        int8  = 'int8 (8-bit, default)'
        k8v4  = 'k8v4 (8-bit K, 4-bit V)'
        q4_0  = 'q4_0 (4-bit, smallest)'
    }
    $selectedKv = $defaultKv

    if (-not $KvSpecified) {
        $kvOptions = @(
            $kvChoices | ForEach-Object {
                @{ Value = $_; Name = $_; Label = $kvLabels[$_] }
            }
        )

        $selectedKv = Prompt-Choice 'KV cache' $kvOptions $defaultKv $gum
    }

    $selectedPort = $defaultPort

    if (-not $PortSpecified) {
        $selectedPort = Prompt-Port 'Server port' $defaultPort $gum
    }

    $selectedOffline = $defaultOffline

    if ($Offline) {
        $selectedOffline = $true
    } elseif ($OfflineSpecified) {
        $selectedOffline = $false
    } else {
        $selectedOffline = Prompt-YesNo `
            'Stay fully local, block OpenCode web access?' `
            $selectedOffline $gum
    }

    $callingShell = Get-CallingShell
    $addAlias = Prompt-YesNo "Add the scode shortcut to $callingShell?" $true $gum

    $config = [ordered]@{
        family   = $selected.Family
        model    = $selected.Model
        context  = $selectedContext
        kv       = $selectedKv
        dataDir  = $selectedDataDir
        port     = $selectedPort
        offline  = $selectedOffline
    }

    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Write-Utf8File $SetupPath ($config | ConvertTo-Json -Depth 5)
    Write-Ok "setup saved to $SetupPath"

    if ($addAlias) {
        Install-ScodeAlias $callingShell
    }

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
            $vramMiB = 0
            $computeCap = 0.0

            if (
                $fields.Count -ge 5 -and
                [int]::TryParse($fields[2], [ref] $vramMiB) -and
                [double]::TryParse($fields[4], [ref] $computeCap)
            ) {
                [pscustomobject]@{
                    Index      = $fields[0].Trim()
                    Name       = $fields[1].Trim()
                    VramMiB    = $vramMiB
                    Driver     = $fields[3].Trim()
                    ComputeCap = $computeCap
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

function Test-StrataConfig([string]$Root, [string]$Tag) {
    $config = Read-Json (Join-Path $Root "strata-$Tag.json")

    if (-not $config -or -not $config.exe) {
        return $false
    }

    return (
        (Test-Path -LiteralPath $config.exe) -and
        (Test-Path -LiteralPath (Join-Path $Root '.venv\Scripts\python.exe'))
    )
}

function Read-GgufString([IO.BinaryReader]$Reader) {
    $length = $Reader.ReadUInt64()

    if ($length -gt 1048576) {
        throw "GGUF string length $length is out of range"
    }

    return [Text.Encoding]::UTF8.GetString($Reader.ReadBytes([int]$length))
}

function Get-GgufValueSize([uint32]$Type) {
    switch ($Type) {
        0 { return 1 }
        1 { return 1 }
        2 { return 2 }
        3 { return 2 }
        4 { return 4 }
        5 { return 4 }
        6 { return 4 }
        7 { return 1 }
        10 { return 8 }
        11 { return 8 }
        12 { return 8 }
    }

    return 0
}

function Read-GgufValue([IO.BinaryReader]$Reader, [uint32]$Type, [int]$Depth = 0) {
    switch ($Type) {
        0 { return $Reader.ReadByte() }
        1 { return $Reader.ReadSByte() }
        2 { return $Reader.ReadUInt16() }
        3 { return $Reader.ReadInt16() }
        4 { return $Reader.ReadUInt32() }
        5 { return $Reader.ReadInt32() }
        6 { return $Reader.ReadSingle() }
        7 { return $Reader.ReadByte() -ne 0 }
        8 { return Read-GgufString $Reader }
        9 {
            $elementType = $Reader.ReadUInt32()
            $count = [long]$Reader.ReadUInt64()

            if ($Depth -ge 2) {
                throw 'GGUF array nesting is too deep'
            }

            $elementSize = Get-GgufValueSize $elementType

            if ($elementSize -gt 0) {
                [void]$Reader.BaseStream.Seek($count * $elementSize, [IO.SeekOrigin]::Current)
            } else {
                for ($i = 0; $i -lt $count; $i++) {
                    [void](Read-GgufValue $Reader $elementType ($Depth + 1))
                }
            }

            return $null
        }
        10 { return $Reader.ReadUInt64() }
        11 { return $Reader.ReadInt64() }
        12 { return $Reader.ReadDouble() }
    }

    throw "unknown GGUF value type $Type"
}

function Read-GgufMeta([string]$Path) {
    $meta = @{}

    try {
        $stream = [IO.File]::Open(
            $Path,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite
        )
        $reader = New-Object IO.BinaryReader($stream, [Text.Encoding]::UTF8)

        try {
            if ([Text.Encoding]::ASCII.GetString($reader.ReadBytes(4)) -ne 'GGUF') {
                return $null
            }

            $version = $reader.ReadUInt32()

            if ($version -lt 2 -or $version -gt 3) {
                return $null
            }

            [void]$reader.ReadUInt64()
            $nKv = [long]$reader.ReadUInt64()

            if ($nKv -gt 1048576) {
                return $null
            }

            for ($i = 0; $i -lt $nKv; $i++) {
                $key = Read-GgufString $reader
                $type = $reader.ReadUInt32()
                $meta[$key] = Read-GgufValue $reader $type
            }
        } finally {
            $reader.Dispose()
            $stream.Dispose()
        }
    } catch {
        return $null
    }

    return $meta
}

function Get-GgufArch($Meta) {
    if (-not $Meta) {
        return $null
    }

    $arch = "$($Meta['general.architecture'])".Trim()

    if (-not $arch) {
        return $null
    }

    return $arch
}

function Get-KvWeightsPerToken($Meta) {
    $arch = Get-GgufArch $Meta

    if (-not $arch) {
        return $null
    }

    $layers  = Get-IntSetting $Meta["$arch.block_count"] 0
    $heads   = Get-IntSetting $Meta["$arch.attention.head_count"] 0
    $headsKv = Get-IntSetting $Meta["$arch.attention.head_count_kv"] 0
    $embd    = Get-IntSetting $Meta["$arch.embedding_length"] 0

    if ($layers -le 0 -or $heads -le 0 -or $headsKv -le 0 -or $embd -le 0) {
        return $null
    }

    $headDim = [int][math]::Floor($embd / $heads)

    if ($headDim -le 0) {
        return $null
    }

    $interval = Get-IntSetting $Meta["$arch.full_attention_interval"] 1

    if ($interval -lt 1) {
        $interval = 1
    }

    $fullLayers = [math]::Max(1, [int][math]::Floor($layers / $interval))

    return [long]2 * $fullLayers * $headsKv * $headDim
}

function Get-GgufTrainedContext($Meta) {
    $arch = Get-GgufArch $Meta

    if (-not $arch) {
        return 0
    }

    return Get-IntSetting $Meta["$arch.context_length"] 0
}

function Get-KvCacheGB($WeightsPerToken, [long]$Tokens, [string]$KvQuant) {
    if (-not $WeightsPerToken -or $Tokens -le 0) {
        return $null
    }

    $bytesPerWeight = $KvBytesPerWeight[[string]$KvQuant]

    if (-not $bytesPerWeight) {
        $bytesPerWeight = $KvBytesPerWeight['int8']
    }

    return [math]::Round(($WeightsPerToken * $Tokens * $bytesPerWeight) / 1GB, 1)
}

function Get-ConfiguredContext([string]$Root, [string]$Tag) {
    $config = Read-Json (Join-Path $Root "strata-$Tag.json")

    if (-not $config -or -not $config.args) {
        return 0
    }

    $cfgArgs = @($config.args)

    for ($i = 0; $i -lt $cfgArgs.Count; $i++) {
        if ("$($cfgArgs[$i])" -eq '--max-context' -and $i + 1 -lt $cfgArgs.Count) {
            return Get-IntSetting $cfgArgs[$i + 1] 0
        }
    }

    return 0
}

function Show-RamStability(
    $Spec,
    $Variant,
    [int]$ContextTokens,
    [string]$KvQuant,
    [string]$Root,
    [string]$Tag,
    [string]$Dir,
    $GgufDir
) {
    Write-Step 'Memory stability'

    $shard = $null
    $shardFilter = ('*{0}*00001-of-*.gguf' -f $Variant.Model)

    foreach ($searchDir in @($GgufDir, $Dir)) {
        if (-not $searchDir -or -not (Test-Path -LiteralPath $searchDir)) {
            continue
        }

        $shard = Get-ChildItem `
            -LiteralPath $searchDir `
            -Recurse `
            -File `
            -Filter $shardFilter `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1

        if ($shard) {
            break
        }
    }

    $meta = if ($shard) { Read-GgufMeta $shard.FullName } else { $null }

    $weightsPerToken = Get-KvWeightsPerToken $meta
    $trained = Get-GgufTrainedContext $meta

    $configured = Get-ConfiguredContext $Root $Tag

    if ($configured -le 0) {
        $configured = $ContextTokens
    }

    if ($configured -le 0) {
        $configured = $trained
    }

    # The published minimum is treated as covering the model at its trained
    # context with int8 KV; a smaller context or a lower KV quant frees RAM.
    $estimated = [double]$Variant.MinRamGB
    $kvGB = $null
    $kvBaselineGB = $null

    if ($weightsPerToken -and $configured -gt 0) {
        $baselineContext = if ($trained -gt 0) { $trained } else { $configured }
        $kvGB = Get-KvCacheGB $weightsPerToken $configured $KvQuant
        $kvBaselineGB = Get-KvCacheGB $weightsPerToken $baselineContext 'int8'
        $estimated = [math]::Round($Variant.MinRamGB + $kvGB - $kvBaselineGB, 1)
    }

    Write-Info (
        'this PC has {0} GB RAM; {1} needs at least {2} GB' -f
        $Spec.RamGB,
        $Variant.Model,
        $Variant.MinRamGB
    )

    if ($null -ne $kvGB) {
        Write-Info (
            'the {0}-token context at {1} KV uses about {2} GB of it' -f
            $configured,
            $KvQuant,
            $kvGB
        )
    } else {
        Write-Info 'the KV cache size could not be measured; the estimate uses the published minimum'
    }

    $headroom = [math]::Round($Spec.RamGB - $estimated, 1)

    Write-Info "estimated peak use at full context: about $estimated GB"

    if ($headroom -ge $ComfortableHeadroomGB) {
        Write-Ok "headroom of about $headroom GB: this setup should run comfortably"
    } elseif ($headroom -ge $TightHeadroomGB) {
        Write-Warn "headroom of about $headroom GB: close other large apps while the server runs"
    } else {
        Write-Host "    warn  headroom of about $headroom GB: the server may run out of RAM and crash" -ForegroundColor Red
        Write-Host '          lower the context, pick q4_0 KV, or choose a smaller variant' -ForegroundColor Red
    }

    if ($headroom -lt $ComfortableHeadroomGB -and $weightsPerToken -and $kvBaselineGB) {
        $bytesPerWeight = $KvBytesPerWeight[[string]$KvQuant]

        if (-not $bytesPerWeight) {
            $bytesPerWeight = $KvBytesPerWeight['int8']
        }

        $fitTokens = [long](
            (
                ($Spec.RamGB - $ComfortableHeadroomGB - $Variant.MinRamGB + $kvBaselineGB) * 1GB
            ) / ($weightsPerToken * $bytesPerWeight)
        )

        if ($fitTokens -gt 0 -and $fitTokens -lt $configured) {
            $fitTokens = [long]([math]::Floor($fitTokens / 1024) * 1024)

            Write-Info "a context of $fitTokens tokens or less keeps about $ComfortableHeadroomGB GB of headroom"
        }
    }
}

function Test-SetupDrift([string]$Root, [string]$Tag, [int]$ContextTokens, [string]$KvQuant) {
    $config = Read-Json (Join-Path $Root "strata-$Tag.json")

    if (-not $config -or -not $config.args) {
        return $false
    }

    $cfgArgs = @($config.args)
    $currentContext = $null
    $currentKv = $null

    for ($i = 0; $i -lt $cfgArgs.Count; $i++) {
        if ("$($cfgArgs[$i])" -eq '--max-context' -and $i + 1 -lt $cfgArgs.Count) {
            $currentContext = "$($cfgArgs[$i + 1])"
        } elseif ("$($cfgArgs[$i])" -eq '--kv' -and $i + 1 -lt $cfgArgs.Count) {
            $currentKv = "$($cfgArgs[$i + 1])"
        }
    }

    if ($ContextTokens -and $currentContext -ne "$ContextTokens") {
        return $true
    }

    if ($KvQuant -and "$currentKv".ToLowerInvariant() -ne "$KvQuant".ToLowerInvariant()) {
        return $true
    }

    return $false
}

function Get-StrataVersion([string]$Root, $State) {
    if ((Split-Path $Root -Leaf) -match '^strata-(v[\d.]+)$') {
        return $Matches[1]
    }

    if ($State -and $State.StrataRoot -eq $Root -and $State.StrataVersion) {
        return $State.StrataVersion
    }

    return 'unknown'
}

function Install-StrataCode([string]$Version) {
    $destination = Join-Path $AppDir "strata-$Version"

    if (Test-Path -LiteralPath (Join-Path $destination 'setup.py')) {
        return $destination
    }

    $zip = Join-Path $DownloadDir "strata-$Version.zip"

    try {
        Invoke-Download `
            "https://github.com/Niko1221/Strata/archive/refs/tags/$Version.zip" `
            $zip
    } catch {
        Fail "could not download Strata $Version ($($_.Exception.Message))" `
            'Check network access and run the script again.'
    }

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

function New-CmdLauncher([string]$Path, [string]$Body) {
    Set-Content -LiteralPath $Path -Value (
        '@echo off' + [Environment]::NewLine + $Body
    ) -Encoding ASCII

    return $Path
}

function Set-PythonEnvironment {
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
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

    # The command travels through an environment variable so non-ASCII paths
    # survive: the launcher file itself stays pure ASCII for cmd.exe.
    $env:STRATA_CODER_CMD = (
        '"' + (Join-Path $Root 'START-HERE.bat') + '" ' + $quotedArguments + ' < NUL 2>&1'
    )

    $launcher = New-CmdLauncher (Join-Path $LogDir 'start-setup.cmd') '%STRATA_CODER_CMD%'

    $processInfo.FileName = $env:ComSpec
    $processInfo.Arguments = '/d /c "' + $launcher + '"'

    $processInfo.WorkingDirectory = $Root
    $processInfo.UseShellExecute = $false
    $processInfo.RedirectStandardOutput = $true
    $processInfo.StandardOutputEncoding = [Text.Encoding]::UTF8

    Set-PythonEnvironment

    try {
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
    } finally {
        Remove-Item Env:STRATA_CODER_CMD -ErrorAction SilentlyContinue
    }
}

function Get-SetupArgs {
    param(
        $Variant,
        [string]$Dir,
        [int]$ServerPort,
        [int]$ContextTokens,
        [string]$KvQuant,
        $GgufDir
    )

    $arguments = @(
        '--family', $Variant.Family,
        '--model', $Variant.Model,
        '--vision', 'no',
        '--data-dir', $Dir,
        '--port', $ServerPort,
        '--no-browser',
        '--no-start',
        '--yes'
    )

    if ($ContextTokens) {
        $arguments += @('--context', $ContextTokens)
    }

    if ($KvQuant) {
        $arguments += @('--kv', $KvQuant)
    }

    if ($GgufDir) {
        $arguments += @('--gguf-dir', $GgufDir)
    }

    return $arguments
}

function Invoke-ModelSetup {
    param(
        [string]$Root,
        $Variant,
        [string]$Dir,
        [int]$ServerPort,
        [int]$ContextTokens,
        [string]$KvQuant,
        [string]$GgufDir,
        [string]$Tag
    )

    New-Item -ItemType Directory -Force -Path $Dir | Out-Null

    $exitCode = Invoke-StrataSetup $Root (
        Get-SetupArgs $Variant $Dir $ServerPort $ContextTokens $KvQuant $GgufDir
    )

    return [pscustomobject]@{
        ExitCode   = $exitCode
        Configured = (Test-StrataConfig $Root $Tag)
    }
}

# ----------------------------------------------------------------------------------------------------
# Help, alias, stop, and statistics modes
# ----------------------------------------------------------------------------------------------------

if ($Help) {
    Show-Help
    exit 0
}

if ($AliasSpecified) {
    $target = if ([string]::IsNullOrWhiteSpace($AliasShell)) { 'auto' } else { $AliasShell.Trim().ToLowerInvariant() }

    $shell = switch ($target) {
        'auto' { Get-CallingShell }
        'cmd' { 'cmd' }
        'powershell' { 'powershell' }
        'ps' { 'powershell' }
        'pwsh' { 'powershell' }
        'bash' { 'bash' }
        'git-bash' { 'bash' }
        'gitbash' { 'bash' }
        default { $null }
    }

    if (-not $shell) {
        Fail "unknown alias target '$AliasShell'" 'Valid targets: cmd, powershell, bash.'
    }

    Install-ScodeAlias $shell
    exit 0
}

if ($Stop) {
    Write-Step 'Stopping the Strata server'

    if ((Stop-StrataServers) -eq 0) {
        Write-Skip 'no Strata server process was found'
    }

    exit 0
}

function Show-StatusPanel([int]$ServerPort) {
    $process = @(Get-StrataServerProcesses) | Select-Object -First 1

    $health = Get-StrataHealth $ServerPort

    if (-not $health -and $process) {
        $ServerPort = Get-ServerPort $process
        $health = Get-StrataHealth $ServerPort
    }

    if (-not $health) {
        Fail "no Strata server is answering on port $ServerPort."
    }

    $base = "http://127.0.0.1:$ServerPort"

    $gum = Get-Gum

    $interactive = $true

    try {
        $width = [Math]::Min(78, [Console]::WindowWidth - 1)
    } catch {
        $interactive = $false
        $width = 78
    }

    if ($gum -and $interactive) {
        try {
            [Console]::OutputEncoding = [Text.Encoding]::UTF8
        } catch {
        }

        # gum strips ANSI colors when its stdout is a pipe, which it is for the
        # captured style calls below; force colors on so the joined panel keeps them.
        $env:CLICOLOR_FORCE = '1'
    }

    $inner = $width - 2

    function Format-PanelRow([string]$Text) {
        if ($Text.Length -gt $inner) {
            $Text = $Text.Substring(0, $inner)
        }
        return ('|' + $Text.PadRight($inner) + '|')
    }

    if ($interactive) {
        Clear-Host
    }

    $panelTop = -1
    $failures = 0

    while ($true) {
        try {
            $statusInfo = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "$base/status" `
                -TimeoutSec 5

            $v1Status = Invoke-RestMethod `
                -UseBasicParsing `
                -Uri "$base/v1/status" `
                -TimeoutSec 5

            $failures = 0
        } catch {
            $failures++

            if ($failures -ge $StatusRetries) {
                Fail "lost the connection to the server ($($_.Exception.Message))"
            }

            Start-Sleep -Seconds 2
            continue
        }

        $timing = $v1Status.last_timings
        $machine = $v1Status.machine
        $activity = $v1Status.activity
        $modelText = if ($v1Status.model) { $v1Status.model } else { 'model' }

        if ($statusInfo.busy -and $statusInfo.tokens_per_s) {
            $liveText = ('generating at {0:N1} tok/s' -f $statusInfo.tokens_per_s)
            $border256 = 46
            $liveColor = 'Green'
        } elseif ($statusInfo.busy) {
            $liveText = 'busy'
            $border256 = 220
            $liveColor = 'Yellow'
        } else {
            $liveText = 'idle'
            $border256 = 241
            $liveColor = 'DarkGray'
        }

        $phaseText = if ($statusInfo.phase) { "  phase: $($statusInfo.phase)" } else { '' }

        $bodyLines = @(
            ('status:   {0}{1}' -f $liveText, $phaseText)
        )

        if ($timing -and $timing.predicted_per_second) {
            $bodyLines += (
                'answer:   {0} tok at {1:N1} tok/s, cache {2}' -f
                $timing.predicted_n,
                $timing.predicted_per_second,
                $timing.cache_n
            )
            $bodyLines += (
                'prompt:   {0} tok at {1:N1} tok/s' -f
                $timing.prompt_n,
                $timing.prompt_per_second
            )
        } else {
            $bodyLines += 'answer:   none yet'
            $bodyLines += 'prompt:   none yet'
        }

        if ($v1Status.cache_max_tokens -and $null -ne $statusInfo.prompt_tokens) {
            $bodyLines += (
                'context:  {0} / {1} ({2:N0}%)' -f
                $statusInfo.prompt_tokens,
                $v1Status.cache_max_tokens,
                (100 * $statusInfo.prompt_tokens / $v1Status.cache_max_tokens)
            )
        } else {
            $bodyLines += 'context:  unknown'
        }

        $gpu = $machine.gpu

        if ($gpu) {
            $bodyLines += (
                'gpu:      {0}% util, {1} C, {2:N0} W, VRAM {3:N1} / {4:N1} GiB' -f
                [Math]::Round($gpu.util_pct),
                [Math]::Round($gpu.temp_c),
                $gpu.power_w,
                ($gpu.used_mib / 1024),
                ($gpu.total_mib / 1024)
            )
        } else {
            $bodyLines += 'gpu:      n/a'
        }

        $ram = $machine.ram

        if ($ram) {
            $bodyLines += (
                'ram:      {0:N1} / {1:N1} GiB' -f
                $ram.used_gib,
                $ram.total_gib
            )
        } else {
            $bodyLines += 'ram:      n/a'
        }

        $bodyLines += (
            'requests: {0} total, {1} in flight, {2} queued' -f
            $activity.requests,
            $activity.in_flight,
            $statusInfo.queued
        )

        $upS = [double]$v1Status.uptime_s
        $upText = if ($upS -ge 3600) {
            '{0}h {1}m' -f [Math]::Floor($upS / 3600), [Math]::Floor(($upS % 3600) / 60)
        } else {
            '{0}m' -f [Math]::Floor($upS / 60)
        }

        $mtpText = if ($timing -and $timing.draft_n) {
            '  MTP accept {0:N0}%' -f (100 * $timing.draft_n_accepted / $timing.draft_n)
        } else {
            ''
        }

        $bodyLines += (
            'uptime:   {0}  engine {1}{2}' -f
            $upText,
            $v1Status.engine,
            $mtpText
        )

        if ($gum) {
            $panelLines = $bodyLines.Count + 4
            $maxLen = [Math]::Max(20, $width - 5)

            # gum sizes a block to its longest line, so without a fixed width the
            # panel grows and shrinks as values change and leaves stale cells from
            # wider draws behind; pin every block to the same outer width.
            $boxWidth = $maxLen + 4

            $bodyLines = @(
                $bodyLines | ForEach-Object {
                    if ($_.Length -gt $maxLen) { $_.Substring(0, $maxLen) } else { $_ }
                }
            )

            $titleText = " Strata  $modelText  $base "

            if ($titleText.Length -gt ($maxLen + 2)) {
                $titleText = $titleText.Substring(0, $maxLen + 2)
            }

            $title = & $gum style --bold --foreground 212 --padding '0,1' --width $boxWidth $titleText
            $body = & $gum style --border rounded --border-foreground $border256 --padding '0,1' --foreground 255 --width $boxWidth ($bodyLines -join "`n")
            $foot = & $gum style --italic --foreground 241 --padding '0,1' --width $boxWidth (' Ctrl+C to quit   {0} ' -f (Get-Date -Format 'HH:mm:ss'))
        } else {
            $panelLines = $bodyLines.Count + 2
        }

        if ($interactive) {
            if ($panelTop -ge 0) {
                if ($panelTop + $panelLines -ge [Console]::BufferHeight) {
                    Clear-Host
                    $panelTop = 0
                } else {
                    try {
                        [Console]::SetCursorPosition(0, $panelTop)
                    } catch {
                        Clear-Host
                        $panelTop = 0
                    }
                }
            } elseif ([Console]::CursorTop + $panelLines -ge [Console]::BufferHeight) {
                Clear-Host
            }
        }

        if ($gum) {
            & $gum join --vertical ($title -join "`n") ($body -join "`n") ($foot -join "`n")
        } else {
            $head = " Strata speed  $base "
            Write-Host ('+' + $head.PadRight($inner, '-') + '+')

            for ($i = 0; $i -lt $bodyLines.Count; $i++) {
                if ($i -eq 0) {
                    Write-Host (Format-PanelRow $bodyLines[$i]) -ForegroundColor $liveColor
                } else {
                    Write-Host (Format-PanelRow $bodyLines[$i])
                }
            }

            Write-Host ('+' + ('-' * $inner) + '+')
        }

        if ($interactive -and $panelTop -lt 0) {
            $panelTop = [Console]::CursorTop - $panelLines
        }

        Start-Sleep -Seconds 2
    }
}

if ($Status) {
    Show-StatusPanel $Port
    exit 0
}

# ----------------------------------------------------------------------------------------------------
# Transcript
# ----------------------------------------------------------------------------------------------------

try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    Get-ChildItem -LiteralPath $LogDir -Filter 'run-*.log' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip ($LogKeep - 1) |
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

$driverMajor = 0
[void][int]::TryParse(($spec.Gpu.Driver -split '\.')[0], [ref] $driverMajor)

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

$FamilyFilter = $Family
$ModelFilter = $Model

if ($savedSetup) {
    if (-not $FamilyFilter -and $savedSetup.family) {
        $FamilyFilter = $savedSetup.family
    }

    if (-not $ModelFilter -and $savedSetup.model) {
        $ModelFilter = $savedSetup.model
    }

    if (-not $ContextSpecified) {
        $Context = Get-IntSetting $savedSetup.context $Context
    }

    if (-not $KvSpecified -and $savedSetup.kv) {
        $Kv = [string]$savedSetup.kv
    }

    if (-not $DataDirSpecified -and $savedSetup.dataDir) {
        $DataDir = $savedSetup.dataDir
    }

    if (-not $PortSpecified) {
        $savedPort = Get-IntSetting $savedSetup.port 0

        if ($savedPort -gt 0) {
            $Port = $savedPort
        }
    }

    if (-not $OfflineSpecified -and ($savedSetup.offline -eq $true)) {
        $Offline = $true
    }
}

$candidates = @(Get-Candidates $FamilyFilter $ModelFilter)

$variant = Select-FittingVariant $candidates $spec.RamGB

if (-not $variant -and -not ($FamilySpecified -or $ModelSpecified)) {
    $variant = Select-FittingVariant $Variants $spec.RamGB

    if ($variant) {
        Write-Warn "the saved variant no longer fits this PC's RAM; switching to $($variant.Title)"
    }
}

if (-not $variant) {
    $smallest = if ($FamilySpecified -or $ModelSpecified) {
        @($candidates) | Sort-Object MinRamGB | Select-Object -First 1
    } else {
        @($Variants) | Sort-Object MinRamGB | Select-Object -First 1
    }

    $message = (
        '{0}/{1} needs about {2} GB RAM; this PC has {3} GB.' -f
        $smallest.Family,
        $smallest.Model,
        ($smallest.MinRamGB + 2),
        $spec.RamGB
    )

    if ($FamilySpecified -or $ModelSpecified) {
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

$script:lock = $null

if (-not $CheckOnly) {
    $script:lock = New-Object Threading.Mutex($false, 'Local\strata-coder-setup')

    try {
        if (-not $script:lock.WaitOne(0)) {
            Write-Step 'Another strata-coder setup is running; waiting for it to finish'
            [void]$script:lock.WaitOne()
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
        Test-StrataConfig $_ $tag
    }
) | Select-Object -First 1

$modelReady = [bool]$StrataRoot

if (-not $StrataRoot -and $roots) {
    $StrataRoot = $roots[0]
}

$strataPresent = [bool]$StrataRoot
$strataVersion = if ($strataPresent) {
    Get-StrataVersion $StrataRoot $state
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
    $latestRelease = Get-LatestRelease 'Niko1221/Strata'

    if (-not $latestRelease) {
        Write-Warn 'skipping the Strata update check; continuing with the installed version'
    } elseif ($latestRelease.tag_name -ne $strataVersion) {
        $strataTarget = $latestRelease.tag_name
        Write-Info "Strata update available: $strataVersion -> $strataTarget"
    } else {
        Write-Ok "Strata is up to date ($($latestRelease.tag_name))"
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

    if (-not $openCodeRelease) {
        Write-Warn 'skipping the OpenCode update check; continuing with the installed version'
    } elseif (
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
            if ($PortSpecified) {
                Fail "a Strata server is already running on port $otherPort, but port $Port was requested." `
                    'Stop it with -Stop first, or run without -Port to use the running server.'
            }

            Write-Warn "a Strata server is already running on port $otherPort; using it"
            $Port = $otherPort
        } else {
            Write-Info "a Strata server is starting on port $Port"
        }
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
    $shardFilter = ('*{0}*00001-of-*.gguf' -f $variant.Model)
    $anyShardFilter = ('*{0}*-of-*.gguf' -f $variant.Model)

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
        $ggufDir = @(
            Get-ChildItem `
                -LiteralPath (Join-Path $repoDir 'snapshots') `
                -Directory `
                -ErrorAction SilentlyContinue |
                ForEach-Object {
                    @($_.FullName, (Join-Path $_.FullName $variant.Model))
                }
        ) | Where-Object {
            $candidate = $_

            (
                @(
                    Get-ChildItem `
                        -LiteralPath $candidate `
                        -File `
                        -Filter $shardFilter `
                        -ErrorAction SilentlyContinue
                ).Count -ge 1
            ) -and (
                @(
                    Get-ChildItem `
                        -LiteralPath $candidate `
                        -File `
                        -Filter $anyShardFilter `
                        -ErrorAction SilentlyContinue
                ).Count -ge 2
            )
        } | Select-Object -First 1
    }

    if (-not $ggufDir -and (Test-Path -LiteralPath $DataDir)) {
        $firstShard = Get-ChildItem `
            -LiteralPath $DataDir `
            -Recurse `
            -File `
            -Filter $shardFilter `
            -ErrorAction SilentlyContinue |
            Select-Object -First 1

        if (
            $firstShard -and
            @(
                Get-ChildItem `
                    -LiteralPath $firstShard.DirectoryName `
                    -File `
                    -Filter $anyShardFilter `
                    -ErrorAction SilentlyContinue
            ).Count -ge 2
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
        $SlackGB
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
        }

        $setupResult = Invoke-ModelSetup $newRoot $variant $DataDir $Port $Context $Kv $ggufDir $tag

        if (-not $setupResult.Configured) {
            Remove-Item -LiteralPath $newRoot -Recurse -Force -ErrorAction SilentlyContinue

            Fail "Strata $strataTarget setup did not finish successfully (exit code $($setupResult.ExitCode))." `
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

$configDrift = $false

if ($modelReady -and -not $Setup) {
    $configDrift = Test-SetupDrift $StrataRoot $tag $Context $Kv

    if ($configDrift) {
        Write-Info 'saved setup settings differ from the configured model; reconfiguring'
    }
}

if (-not $modelReady -or $Setup -or $configDrift) {
    $reconfigure = (($Setup -or $configDrift) -and $modelReady)

    if ($reconfigure) {
        $reason = if ($Setup) { '-Setup' } else { 'saved setup settings' }
        Write-Step "Reconfiguring $($variant.Title) from $reason"

        if ($serverRunning) {
            Write-Info 'stopping the running server so the new settings take effect'
            [void](Stop-StrataServers)
            $serverRunning = $false
        }
    } else {
        Write-Step "Setting up $($variant.Title)"
    }

    if (-not $reconfigure) {
        $downloadEstimate = if ($ggufDir) {
            $SetupMinutes
        } else {
            [math]::Round($variant.DownloadGB + $SetupMinutes)
        }

        Write-Info "this may download about $downloadEstimate GB and can take a long time"
        Write-Info 'If interrupted, run the script again; supported downloads resume.'
    }

    $setupResult = Invoke-ModelSetup $StrataRoot $variant $DataDir $Port $Context $Kv $ggufDir $tag

    if (-not $setupResult.Configured) {
        Fail "Strata model setup did not finish successfully (exit code $($setupResult.ExitCode))." `
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

    if (-not $release) {
        Fail 'could not contact GitHub for the OpenCode release.' `
            'Check network access and run the script again.'
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

    try {
        $stagedExe = Get-DownloadedFile $asset.browser_download_url $zip $stage 'opencode.exe'
    } catch {
        Fail "the OpenCode archive could not be prepared ($($_.Exception.Message)); it was removed." `
            'Run the script again to download a fresh copy.'
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
# -Setup stops here: install and configure only, no server, no OpenCode
# ----------------------------------------------------------------------------------------------------

if ($Setup) {
    Write-Step 'Setup complete'
    Write-Info 'the server was not started and OpenCode was not opened'
    Write-Info 'run scode (or strata-coder) in a project folder when you are ready to use it'

    Show-RamStability `
        $spec `
        $variant `
        $Context `
        $Kv `
        $StrataRoot `
        $tag `
        $DataDir `
        $ggufDir

    Close-SessionResources
    exit 0
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

    Set-PythonEnvironment

    # The command travels through an environment variable so non-ASCII paths
    # survive: the launcher file itself stays pure ASCII for cmd.exe.
    $env:STRATA_CODER_CMD = (
        '"{0}" "{1}" --engine strata --config "{2}" --port {3} > "{4}" 2>&1' -f
        $python,
        (Join-Path $StrataRoot 'serve\server.py'),
        $strataConfigPath,
        $Port,
        $serverLog
    )

    $launcher = New-CmdLauncher (Join-Path $LogDir 'start-server.cmd') '%STRATA_CODER_CMD%'

    $serverProcess = Start-Process `
        -FilePath $launcher `
        -WorkingDirectory $StrataRoot `
        -WindowStyle Hidden `
        -PassThru

    Remove-Item Env:STRATA_CODER_CMD -ErrorAction SilentlyContinue

    Save-State @{
        Port = $Port
    }

    Write-Info 'loading the model; the first start may take several minutes'
    Write-Info "server output: $serverLog"

    if (-not (Wait-StrataReady $serverProcess $Port)) {
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
            [void](Stop-StrataServers $Port)
        }

        Fail "the Strata server did not come up: $reason." `
            "Review the logs above and $StrataRoot\START-HERE.bat."
    }
} elseif (-not (Test-StrataReady $Port)) {
    Write-Step 'Waiting for the running Strata server to finish loading'

    if (-not (Wait-StrataReady $null $Port)) {
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

$contextLimit = $DefaultContextLimit

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
    $MaxOutputLimit,
    [math]::Max($MinOutputLimit, [int]($contextLimit / 4))
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

if ($Offline) {
    $openCodeConfig['permission'] = [ordered]@{
        webfetch  = 'deny'
        websearch = 'deny'
    }
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

Close-SessionResources

if ($NoLaunch) {
    Write-Step 'Ready'
    Write-Info "server: http://127.0.0.1:$Port/v1"
    Write-Info "OpenCode config: $OpenCodeCfg"
    Write-Info "OpenCode model: strata/$modelId"

    if ($Offline) {
        Write-Info 'OpenCode web access: blocked (webfetch and websearch denied)'
    }

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

if ($Offline) {
    Write-Info 'OpenCode web access: blocked (webfetch and websearch denied)'
}

if (-not (Test-Path -LiteralPath $OpenCodeCfg)) {
    Fail "the generated OpenCode configuration file does not exist: $OpenCodeCfg"
}

$debugConfigPath = Join-Path $LogDir 'opencode-resolved-config.log'

try {
    # Native stderr must not become a terminating error here: opencode may
    # write warnings to stderr while still producing a usable config dump.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        $debugOutput = @(
            & $OpenCodeExe debug config 2>&1
        )
    } finally {
        $ErrorActionPreference = $oldPreference
    }

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

if ((Stop-StrataServers $Port) -eq 0) {
    Write-Skip 'no Strata server process was found'
}