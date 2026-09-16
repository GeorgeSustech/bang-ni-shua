# 帮你刷 · Windows 启动脚本：准备运行环境后启动命令行版本。
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $host.UI.RawUI.WindowTitle = "帮你刷" } catch {}

$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDir
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 读取项目里的 .env，只影响本次运行，不写入系统环境变量。
$envFile = Join-Path $projectDir ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#")) { continue }
        $parts = $text -split "=", 2
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name) { [Environment]::SetEnvironmentVariable($name, $value, "Process") }
    }
}

$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "未检测到运行环境，先执行安装脚本。" -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "setup-windows.ps1")
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "运行环境仍未就绪，请先双击“安装帮你刷环境.cmd”。" -ForegroundColor Red
        exit 1
    }
}

& $venvPython (Join-Path $projectDir "cli.py") @args
exit $LASTEXITCODE
