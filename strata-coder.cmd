@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem ============================================================================
rem strata-coder.cmd
rem ============================================================================
rem Expected layout:
rem   strata-coder.cmd
rem   strata-coder.ps1
rem ============================================================================

set "ARG1=%~1"
set "ARG2=%~2"

rem ============================================================================
rem Parameter Parsing
rem ============================================================================

rem Help flags
if /i "%ARG1%"=="help"     goto :show_help
if /i "%ARG1%"=="--help"   goto :show_help
if /i "%ARG1%"=="-help"    goto :show_help
if /i "%ARG1%"=="-h"       goto :show_help
if /i "%ARG1%"=="/?"       goto :show_help

rem Explicit alias targets
if /i "%ARG1%"=="--alias-cmd"        goto :setup_cmd
if /i "%ARG1%"=="-alias-cmd"         goto :setup_cmd
if /i "%ARG1%"=="--alias-powershell" goto :setup_powershell
if /i "%ARG1%"=="-alias-powershell"  goto :setup_powershell
if /i "%ARG1%"=="--alias-bash"       goto :setup_git_bash
if /i "%ARG1%"=="-alias-bash"        goto :setup_git_bash

rem Automatic alias detection, with optional target:
rem   strata-coder --alias cmd
rem   strata-coder --alias powershell
rem   strata-coder --alias bash
if /i "%ARG1%"=="alias"   goto :alias_option
if /i "%ARG1%"=="--alias" goto :alias_option
if /i "%ARG1%"=="-alias"  goto :alias_option
if /i "%ARG1%"=="-a"      goto :alias_option

rem ============================================================================
rem Normal Execution
rem ============================================================================

if not exist "%~dp0strata-coder.ps1" (
    echo [!] Error: strata-coder.ps1 was not found beside this CMD file.
    echo     Expected: "%~dp0strata-coder.ps1"
    endlocal & exit /b 2
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0strata-coder.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%


rem ============================================================================
rem Alias Option Routing
rem ============================================================================

:alias_option
if /i "%ARG2%"=="cmd"        goto :setup_cmd
if /i "%ARG2%"=="powershell" goto :setup_powershell
if /i "%ARG2%"=="ps"         goto :setup_powershell
if /i "%ARG2%"=="pwsh"       goto :setup_powershell
if /i "%ARG2%"=="bash"       goto :setup_git_bash
if /i "%ARG2%"=="git-bash"   goto :setup_git_bash
if /i "%ARG2%"=="gitbash"    goto :setup_git_bash

if not "%ARG2%"=="" (
    echo [!] Unknown alias target: "%ARG2%"
    echo     Valid targets: cmd, powershell, bash
    endlocal & exit /b 2
)

goto :detect_shell


rem ============================================================================
rem Help Screen
rem ============================================================================

:show_help
echo strata-coder - OpenCode Launch and Setup Utility
echo.
echo Usage:
echo   strata-coder [options] [arguments...]
echo   scode [arguments...] after alias setup
echo.
echo Options:
echo   --alias, -alias, -a, alias
echo       Detects the calling shell and configures the scode alias.
echo.
echo   --alias cmd
echo   --alias powershell
echo   --alias bash
echo       Configures the alias for a specific shell. This is recommended
echo       when automatic shell detection chooses the wrong target.
echo.
echo   --alias-cmd
echo   --alias-powershell
echo   --alias-bash
echo       Equivalent explicit alias setup options.
echo.
echo   --help, -help, -h, /?, help
echo       Displays this help menu.
echo.
echo Any other arguments are passed directly to strata-coder.ps1.
endlocal & exit /b 0


rem ============================================================================
rem Automatic Shell Detection
rem ============================================================================

:detect_shell
echo [*] Detecting shell for persistent scode alias...

rem Git Bash normally exposes MSYSTEM and/or SHELL.
if defined MSYSTEM goto :setup_git_bash
if defined BASH goto :setup_git_bash

echo(%SHELL% | findstr /i /c:"bash" >nul
if not errorlevel 1 goto :setup_git_bash

rem A CMD file always runs through cmd.exe. Inspect its caller to determine
rem whether PowerShell launched this script. This is heuristic; use an explicit
rem alias target when CMD was launched from within PowerShell.
set "PARENT_PROC="

for /f "usebackq delims=" %%I in (`
    powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID); $parent = if ($p) { Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId) }; while ($parent -and $parent.Name -eq 'cmd.exe') { $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $parent.ParentProcessId) }; if ($parent) { $parent.Name } else { 'cmd.exe' }" 2^>nul
`) do set "PARENT_PROC=%%I"

if /i "%PARENT_PROC%"=="powershell.exe" goto :setup_powershell
if /i "%PARENT_PROC%"=="pwsh.exe"       goto :setup_powershell

goto :setup_cmd


rem ============================================================================
rem CMD / Command Prompt Setup
rem ============================================================================

:setup_cmd
echo [+] Configuring alias for Command Prompt

set "MACRO_DIR=%USERPROFILE%\bin"
set "MACRO_FILE=%MACRO_DIR%\cmd_macros.doskey"
set "TEMP_FILE=%MACRO_DIR%\cmd_macros_%RANDOM%_%RANDOM%.tmp"

if not exist "%MACRO_DIR%\" (
    mkdir "%MACRO_DIR%" >nul 2>&1
    if errorlevel 1 (
        echo [!] Failed to create macro directory:
        echo     "%MACRO_DIR%"
        goto :setup_failed
    )
)

rem Rebuild the macro file while removing an existing scode macro.
> "%TEMP_FILE%" (
    if exist "%MACRO_FILE%" (
        findstr /v /i /b /c:"scode=" "%MACRO_FILE%"
    )
    echo scode=call "%~f0" $*
)

move /y "%TEMP_FILE%" "%MACRO_FILE%" >nul
if errorlevel 1 (
    echo [!] Failed to update CMD macro file:
    echo     "%MACRO_FILE%"
    del "%TEMP_FILE%" >nul 2>&1
    goto :setup_failed
)

echo [+] Updated DOSKEY macro:
echo     %MACRO_FILE%

rem Preserve any existing AutoRun value and append the DOSKEY loader only when
rem this macro file is not already referenced.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$key = 'HKCU:\Software\Microsoft\Command Processor'; $old = (Get-ItemProperty -Path $key -Name AutoRun -ErrorAction SilentlyContinue).AutoRun; $loader = 'doskey /macrofile=""' + $env:MACRO_FILE + '""'; if ([string]::IsNullOrWhiteSpace($old)) { $new = $loader } elseif ($old.Contains($env:MACRO_FILE)) { $new = $old } else { $new = $old + ' & ' + $loader }; New-ItemProperty -Path $key -Name AutoRun -Value $new -PropertyType ExpandString -Force | Out-Null"

if errorlevel 1 (
    echo [!] The DOSKEY macro was created, but the CMD AutoRun registry entry
    echo     could not be updated.
    goto :setup_failed
)

echo [+] Preserved or updated the CMD AutoRun entry.
goto :done


rem ============================================================================
rem Git Bash Setup
rem ============================================================================

:setup_git_bash
echo [+] Configuring alias for Git Bash

set "TMP_SH=%TEMP%\scode_setup_%RANDOM%_%RANDOM%.sh"

rem Escape parentheses because this is generated inside a CMD block.
> "%TMP_SH%" (
    echo P=$^(cygpath -u "%~f0"^)
    echo touch ~/.bash_profile
    echo sed -i '/^^alias scode=/d' ~/.bash_profile
    echo printf "alias scode='\"%%s\"'\n" "$P" ^>^> ~/.bash_profile
)

sh "%TMP_SH%"
set "SH_EXIT=%ERRORLEVEL%"

del "%TMP_SH%" >nul 2>&1

if not "%SH_EXIT%"=="0" (
    echo [!] Git Bash alias setup failed.
    goto :setup_failed
)

echo [+] Updated scode alias in ~/.bash_profile
goto :done


rem ============================================================================
rem PowerShell Setup
rem ============================================================================

:setup_powershell
echo [+] Configuring alias for PowerShell

set "TARGET_CMD=%~f0"

rem Use a marked block so rerunning setup replaces only content owned by this
rem script and does not remove unrelated PowerShell profile content.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$prof = $PROFILE.CurrentUserCurrentHost; if (-not $prof) { $prof = $PROFILE }; $dir = Split-Path -Parent $prof; [System.IO.Directory]::CreateDirectory($dir) | Out-Null; $begin = '# >>> strata-coder scode >>>'; $end = '# <<< strata-coder scode <<<'; $target = $env:TARGET_CMD.Replace('''', ''''''); $block = @($begin, ('function scode { & ''' + $target + ''' @args }'), $end) -join [Environment]::NewLine; $text = if (Test-Path -LiteralPath $prof) { [System.IO.File]::ReadAllText($prof) } else { '' }; $pattern = '(?ms)^\s*' + [regex]::Escape($begin) + '.*?^\s*' + [regex]::Escape($end) + '\s*\r?\n?'; $text = [regex]::Replace($text, $pattern, ''); if ($text.Length -gt 0 -and -not $text.EndsWith([Environment]::NewLine)) { $text += [Environment]::NewLine }; [System.IO.File]::WriteAllText($prof, $text + $block + [Environment]::NewLine); Write-Host '[+] Updated scode function in' $prof"

if errorlevel 1 (
    echo [!] PowerShell profile update failed.
    goto :setup_failed
)

goto :done


rem ============================================================================
rem Completion / Error Handling
rem ============================================================================

:done
echo.
echo [+] Setup complete.
echo     Open a new shell window, or reload the applicable shell profile.
endlocal & exit /b 0

:setup_failed
echo.
echo [!] Setup did not complete successfully.
endlocal & exit /b 1