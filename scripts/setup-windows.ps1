#Requires -Version 5.1
# 帮你刷 · Windows 环境安装脚本：准备 Python 3.11+、虚拟环境和依赖。
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $host.UI.RawUI.WindowTitle = "帮你刷 · 安装环境" } catch {}

if ($env:OS -ne "Windows_NT") {
    Write-Host "此安装脚本只支持 Windows。" -ForegroundColor Red
    exit 1
}

$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Get-PythonCandidates {
    # py.exe 启动器、PATH 上的 python、常见安装位置，最后才是 conda。
    # WindowsApps 里的 python.exe 只是应用商店的占位程序，必须跳过。
    $found = New-Object System.Collections.Generic.List[object]
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) { $found.Add(@{ Exe = $launcher.Source; Prefix = @("-3") }) }
    foreach ($name in @("python.exe", "python3.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and $command.Source -notlike "*\WindowsApps\*") {
            $found.Add(@{ Exe = $command.Source; Prefix = @() })
        }
    }
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA "Programs\Python") }
    $roots += @("C:\Program Files", "C:\Program Files (x86)")
    foreach ($root in $roots) {
        foreach ($version in @("314", "313", "312", "311")) {
            $candidate = Join-Path $root "Python$version\python.exe"
            if (Test-Path -LiteralPath $candidate) { $found.Add(@{ Exe = $candidate; Prefix = @() }) }
        }
    }
    foreach ($conda in @("C:\ProgramData\anaconda3\python.exe",
                         (Join-Path $env:USERPROFILE "anaconda3\python.exe"),
                         (Join-Path $env:USERPROFILE "miniconda3\python.exe"))) {
        if ($conda -and (Test-Path -LiteralPath $conda)) { $found.Add(@{ Exe = $conda; Prefix = @() }) }
    }
    return $found
}

function Test-PythonExe([string]$exe, [string[]]$prefix) {
    if (-not (Test-Path -LiteralPath $exe)) { return $false }
    $arguments = @($prefix) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
    try { & $exe @arguments *> $null } catch { return $false }
    return $LASTEXITCODE -eq 0
}

function Find-Python {
    foreach ($candidate in Get-PythonCandidates) {
        if (Test-PythonExe $candidate.Exe $candidate.Prefix) { return $candidate }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "未找到 Python 3.11 或更高版本。" -ForegroundColor Yellow
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        $answer = Read-Host "是否现在用 winget 安装 Python 3.12？（Y/N）"
        if ($answer -match '^(y|yes|Y|YES|是)$') {
            Write-Host "正在安装 Python 3.12 ……"
            winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $python = Find-Python
        }
    }
    if (-not $python) {
        Write-Host "请到 https://www.python.org/downloads/windows/ 安装 Python 3.11+，安装时勾选 Add python.exe to PATH，然后重新运行本脚本。" -ForegroundColor Yellow
        try { Start-Process "https://www.python.org/downloads/windows/" } catch {}
        exit 1
    }
}

$version = (& $python.Exe @($python.Prefix) -c "import sys; print(sys.version.split()[0])") -join ""
Write-Host "使用 Python $version ：$($python.Exe)"

$venvDir = Join-Path $projectDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "创建虚拟环境 .venv ……"
    & $python.Exe @($python.Prefix) -m venv $venvDir
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "虚拟环境创建失败。" -ForegroundColor Red
        exit 1
    }
}

Write-Host "安装依赖（playwright）……"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip 升级失败，请检查网络后重试。" -ForegroundColor Red
    exit 1
}
# 只安装 Python 依赖：程序通过调试端口驱动已安装的 Google Chrome，
# 不需要另外执行 playwright install 下载浏览器。
& $venvPython -m pip install -r (Join-Path $projectDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "依赖安装失败，请检查网络后重试。" -ForegroundColor Red
    exit 1
}

function Get-ChromePath {
    foreach ($hive in @("HKCU:", "HKLM:")) {
        $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        if (Test-Path $key) {
            $value = (Get-Item -LiteralPath $key).GetValue("")
            if ($value) {
                $clean = $value.Trim('"')
                if (Test-Path -LiteralPath $clean) { return $clean }
            }
        }
    }
    foreach ($guess in @("$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
                         "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
                         "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")) {
        if ($guess -and (Test-Path -LiteralPath $guess)) { return $guess }
    }
    return $null
}

if (-not (Get-ChromePath)) {
    Write-Host "未检测到 Google Chrome，将打开官方安装页面。" -ForegroundColor Yellow
    try { Start-Process "https://www.google.com/chrome/" } catch {}
    Read-Host "安装完成后按回车继续"
}

Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host "以后双击“启动帮你刷.cmd”即可运行，也可以执行："
Write-Host "  .\.venv\Scripts\python.exe cli.py run 课程关键字"
Write-Host "如需飞书通知，把 .env.example 复制为 .env，填入自己的 webhook（不要提交）。"
exit 0
