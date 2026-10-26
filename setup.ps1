#Requires -Version 5.1
<#
    ricefish 一键启动 / 安装

    双击 start.bat 会跑这个脚本。它会：
      1. 检查 Python（没有就问你要不要自动装）
      2. 下载 NapCat 到 vendor\napcat（已存在就跳过）
      3. 问你几个问题（DeepSeek Key、主人的 QQ、允许说话的群），写进 .env
      4. 自动写好 NapCat 的反向 WebSocket 配置
      5. 启动 NapCat（扫码登录）和机器人，打开管理面板

    参数：
      -CheckOnly     只做检查，不下载、不弹窗、不启动
      -Reconfigure   重新问一遍配置
      -NoLaunch      只安装配置，不启动
#>
param(
    [switch]$CheckOnly,
    [switch]$Reconfigure,
    [switch]$NoLaunch,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$ProgressPreference = "SilentlyContinue"

# ---------------------------------------------------------------------------
# 基础路径
# ---------------------------------------------------------------------------
$Root = $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $Root

$Vendor     = Join-Path $Root "vendor"
$NapcatDir  = Join-Path $Vendor "napcat"
$EnvFile    = Join-Path $Root ".env"
$EnvExample = Join-Path $Root ".env.example"
$PathFile   = Join-Path $Vendor "napcat_path.txt"
$InstalledMarker = Join-Path $Vendor ".napcat-installed"
$SetupLog   = Join-Path $Root "setup.log"

$script:Interactive = -not $CheckOnly
$script:PythonExe = ""
$script:PanelPort = 8721
$script:PanelToken = ""
$script:WsToken = ""

$ReleaseApi = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------------------------------------------------------------------------
# 输出与弹窗
# ---------------------------------------------------------------------------
function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host ("==> " + $Text) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host ("    " + $Text) -ForegroundColor Green
}

function Write-Warn {
    param([string]$Text)
    Write-Host ("    " + $Text) -ForegroundColor Yellow
}

function Join-Lines {
    param([string[]]$Lines)
    return ($Lines -join "`n")
}

function Ensure-WinForms {
    if (-not ("System.Windows.Forms.MessageBox" -as [type])) {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    }
}

function Show-Info {
    param([string]$Message, [string]$Title = "ricefish")
    if (-not $script:Interactive) {
        Write-Host ("[info] " + $Message)
        return
    }
    try {
        Ensure-WinForms
        [System.Windows.Forms.MessageBox]::Show($Message, $Title, "OK", "Information") | Out-Null
    } catch {
        Write-Host ("[info] " + $Message)
    }
}

function Show-Error {
    param([string]$Message, [string]$Title = "ricefish 出错了")
    if (-not $script:Interactive) {
        Write-Host ("[error] " + $Message) -ForegroundColor Red
        return
    }
    try {
        Ensure-WinForms
        [System.Windows.Forms.MessageBox]::Show($Message, $Title, "OK", "Error") | Out-Null
    } catch {
        Write-Host ("[error] " + $Message) -ForegroundColor Red
    }
}

function Show-Confirm {
    param([string]$Message, [string]$Title = "ricefish")
    if (-not $script:Interactive) {
        Write-Host ("[confirm] " + $Message)
        return $false
    }
    try {
        Ensure-WinForms
        $result = [System.Windows.Forms.MessageBox]::Show($Message, $Title, "YesNo", "Question")
        return ($result -eq "Yes")
    } catch {
        return $false
    }
}

function Show-Input {
    param(
        [string]$Prompt,
        [string]$Title = "ricefish",
        [string]$Default = ""
    )
    if (-not $script:Interactive) {
        Write-Host ("[input] " + $Prompt + "  默认: " + $Default)
        return $Default
    }
    try {
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
        return [Microsoft.VisualBasic.Interaction]::InputBox($Prompt, $Title, $Default)
    } catch {
        try {
            Ensure-WinForms
            Add-Type -AssemblyName System.Drawing -ErrorAction Stop
            $form = New-Object System.Windows.Forms.Form
            $form.Text = $Title
            $form.Width = 480
            $form.Height = 200
            $form.StartPosition = "CenterScreen"
            $label = New-Object System.Windows.Forms.Label
            $label.Text = $Prompt
            $label.Left = 16
            $label.Top = 14
            $label.Width = 430
            $label.Height = 46
            $box = New-Object System.Windows.Forms.TextBox
            $box.Left = 16
            $box.Top = 66
            $box.Width = 430
            $box.Text = $Default
            $ok = New-Object System.Windows.Forms.Button
            $ok.Text = "OK"
            $ok.Left = 258
            $ok.Top = 108
            $ok.DialogResult = "OK"
            $cancel = New-Object System.Windows.Forms.Button
            $cancel.Text = "Cancel"
            $cancel.Left = 356
            $cancel.Top = 108
            $cancel.DialogResult = "Cancel"
            $form.Controls.AddRange(@($label, $box, $ok, $cancel))
            $form.AcceptButton = $ok
            $form.CancelButton = $cancel
            $result = $form.ShowDialog()
            if ($result -eq "OK") { return $box.Text }
            return ""
        } catch {
            Write-Host ("[input] " + $Prompt)
            return (Read-Host)
        }
    }
}

function Show-OpenFile {
    param([string]$Title = "选择 napcat.bat", [string]$Filter = "启动脚本 (*.bat)|*.bat|所有文件 (*.*)|*.*")
    if (-not $script:Interactive) { return "" }
    try {
        Ensure-WinForms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = $Title
        $dialog.Filter = $Filter
        $dialog.CheckFileExists = $true
        if ($dialog.ShowDialog() -eq "OK") { return $dialog.FileName }
        return ""
    } catch {
        return ""
    }
}

function Write-Log {
    param([string]$Text)
    try {
        $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $Text
        Add-Content -Path $SetupLog -Value $line -Encoding UTF8
    } catch { }
}

# ---------------------------------------------------------------------------
# .env 读写
# ---------------------------------------------------------------------------
function Get-EnvValue {
    param([string]$Key)
    if (-not (Test-Path $EnvFile)) { return "" }
    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*=(.*)$"
    foreach ($line in [IO.File]::ReadAllLines($EnvFile, [Text.Encoding]::UTF8)) {
        if ($line -match $pattern) {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path $EnvFile) {
        foreach ($line in [IO.File]::ReadAllLines($EnvFile, [Text.Encoding]::UTF8)) {
            $lines.Add($line)
        }
    }
    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*="
    $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = $Key + "=" + $Value
            $done = $true
        }
    }
    if (-not $done) { $lines.Add($Key + "=" + $Value) }
    [IO.File]::WriteAllLines($EnvFile, $lines, $Utf8NoBom)
}

function New-Token {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
function Test-PythonCandidate {
    param([string]$Exe, [string[]]$Prefix)
    try {
        $argsList = @($Prefix) + @("--version")
        $out = & $Exe $argsList 2>&1
        $text = ($out | Out-String).Trim()
        if ($text -match "Python (\d+)\.(\d+)") {
            $version = [version]($matches[1] + "." + $matches[2])
            if ($version -ge [version]"3.10") {
                $argv = @($Exe) + @($Prefix)
                $command = '"' + $argv[0] + '"'
                if ($argv.Count -gt 1) {
                    $command = $command + " " + ($argv[1..($argv.Count - 1)] -join " ")
                }
                return @{ Command = $command; Argv = $argv; Version = $version.ToString() }
            }
        }
    } catch { }
    return $null
}

function Find-Python {
    $found = Test-PythonCandidate "python" @()
    if ($found) { return $found }
    $found = Test-PythonCandidate "py" @("-3")
    if ($found) { return $found }

    $candidates = @()
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Directory -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "C:\Python*" -Directory -ErrorAction SilentlyContinue
    foreach ($dir in $candidates) {
        $exe = Join-Path $dir.FullName "python.exe"
        if (Test-Path $exe) {
            $found = Test-PythonCandidate $exe @()
            if ($found) { return $found }
        }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { return $false }
    $msg = Join-Lines @(
        "没有找到 Python 3.10+。",
        "",
        "要用 winget 自动安装 Python 3.12 吗？",
        "（会下载约 30MB，装到系统默认位置）"
    )
    if (-not (Show-Confirm -Message $msg -Title "安装 Python")) { return $false }
    Write-Step "正在用 winget 安装 Python 3.12（窗口可能没有进度，请等一会儿）"
    try {
        $wingetArgs = @(
            "install", "-e", "--id", "Python.Python.3.12",
            "--silent", "--accept-package-agreements", "--accept-source-agreements"
        )
        Start-Process -FilePath "winget" -ArgumentList $wingetArgs -Wait -NoNewWindow
    } catch {
        Write-Warn ("winget 调用失败：" + $_.Exception.Message)
    }
    return ($null -ne (Find-Python))
}

# ---------------------------------------------------------------------------
# NapCat
# ---------------------------------------------------------------------------
function Get-Release {
    $headers = @{
        "User-Agent" = "ricefish-setup"
        "Accept"     = "application/vnd.github+json"
    }
    return Invoke-RestMethod -Uri $ReleaseApi -Headers $headers -TimeoutSec 40
}

function Select-Asset {
    param($Release)
    $preferred = @(
        "NapCat.Shell.Windows.OneKey.zip",
        "NapCat.Shell.Windows.Node.zip",
        "NapCat.Shell.zip"
    )
    foreach ($name in $preferred) {
        foreach ($asset in $Release.assets) {
            if ($asset.name -eq $name) { return $asset }
        }
    }
    return $null
}

function Save-NapcatZip {
    param($Asset, [string]$Tag)
    New-Item -ItemType Directory -Force -Path $Vendor | Out-Null
    $zip = Join-Path $Vendor ("napcat-" + $Tag + ".zip")
    if ((Test-Path $zip) -and ((Get-Item $zip).Length -eq $Asset.size)) {
        Write-Ok "压缩包已存在，跳过下载"
        return $zip
    }
    $sizeMb = [math]::Round($Asset.size / 1MB, 1)
    Write-Step ("下载 " + $Asset.name + "（" + $sizeMb + " MB）")
    Write-Host "    如果卡住不动，多半是连不上 GitHub，可以稍后重试或手动下载。"
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $zip -UseBasicParsing -TimeoutSec 1200
    Write-Ok ("下载完成：" + $zip)
    return $zip
}

function Expand-Napcat {
    param([string]$Zip)
    New-Item -ItemType Directory -Force -Path $NapcatDir | Out-Null
    Write-Step "解压 NapCat"
    Expand-Archive -Path $Zip -DestinationPath $NapcatDir -Force
    Write-Ok ("已解压到 " + $NapcatDir)
}

function Get-QqInstallPath {
    $keys = @(
        "HKLM:\SOFTWARE\WOW6432Node\Tencent\QQNT",
        "HKLM:\SOFTWARE\Tencent\QQNT",
        "HKCU:\SOFTWARE\Tencent\QQNT"
    )
    foreach ($key in $keys) {
        $value = (Get-ItemProperty $key -ErrorAction SilentlyContinue).Install
        if ($value -and (Test-Path $value)) { return $value }
    }
    return ""
}

function Test-PathNeedsAscii {
    param([string]$Path)
    if (-not $Path) { return $false }
    foreach ($ch in $Path.ToCharArray()) {
        if ([int]$ch -gt 127) { return $true }
    }
    return $false
}

function Ensure-NapcatShellZip {
    param([string]$Dir)
    $target = Join-Path $Dir "NapCat.Shell.zip"
    if ((Test-Path $target) -and ((Get-Item $target).Length -gt 30000000)) {
        Write-Ok "NapCat.Shell.zip 已就绪，跳过安装器的镜像下载"
        return
    }
    try {
        $release = Get-Release
        $asset = $release.assets | Where-Object { $_.name -eq "NapCat.Shell.zip" } | Select-Object -First 1
        if (-not $asset) { return }
        Write-Step "预先下载 NapCat.Shell.zip（绕开安装器那个会截断的镜像）"
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $target -UseBasicParsing -TimeoutSec 900
        $size = (Get-Item $target).Length
        if ($size -ne $asset.size) {
            Write-Warn ("下载大小不对（" + $size + " / " + $asset.size + "），删掉让安装器自己重试")
            Remove-Item $target -Force -ErrorAction SilentlyContinue
        } else {
            Write-Ok "NapCat.Shell.zip 已下好并校验大小"
        }
    } catch {
        Write-Warn ("预下载失败，安装器会自己试：" + $_.Exception.Message)
    }
}

function Invoke-NapcatInstaller {
    if (-not $script:Interactive) { return }
    if (Test-Path $InstalledMarker) { return }
    $installer = Get-ChildItem -Path $NapcatDir -Recurse -Filter "NapCatInstaller.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $installer) { return }

    $qqPath = Get-QqInstallPath
    $reason = "它会帮你下载/安装 QQ 并铺好 NapCat，按它的提示点完即可（大约几分钟）。"
    if ($qqPath -and (Test-PathNeedsAscii $qqPath)) {
        $reason = Join-Lines @(
            ("发现你的 QQ 装在中文路径下：" + $qqPath),
            "NapCat 对中文路径兼容性不好，建议让安装器重新装一份干净的 QQ。",
            "",
            "安装器会引导你下载/安装 QQ，按提示点完即可（大约几分钟）。"
        )
    }

    $msg = Join-Lines @(
        "要现在运行 NapCat 的官方安装器吗？",
        "",
        $reason,
        "",
        "装完回到这里点「是」，我继续。",
        "（以后不想再问，点「否」即可；想重来就删掉 vendor\.napcat-installed）"
    )
    if (-not (Show-Confirm -Message $msg -Title "安装 NapCat")) {
        Write-Warn "跳过了 NapCat 安装器（之后想装可以删掉 vendor\.napcat-installed 再运行 start.bat）"
        [IO.File]::WriteAllText($InstalledMarker, "skipped " + (Get-Date -Format "s"), $Utf8NoBom)
        return
    }
    Write-Step "正在运行 NapCatInstaller.exe"
    Ensure-NapcatShellZip -Dir $installer.DirectoryName
    try {
        Start-Process -FilePath $installer.FullName -WorkingDirectory $installer.DirectoryName -Wait
        Write-Ok "安装器已退出"
        $shellBat = Get-ChildItem -Path $NapcatDir -Recurse -Depth 3 -Filter "napcat.bat" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\NapCat\.[^\\]+\.Shell\\" } |
            Select-Object -First 1
        if (-not $shellBat) {
            Write-Warn "安装器退出了，但没找到 NapCat.*.Shell\napcat.bat，可能没装完"
            Show-Error -Message (Join-Lines @(
                "NapCat 安装器结束了，但没找到装好的 Shell 目录。",
                "",
                "常见原因是 NapCat.Shell.zip 下载不完整（镜像抽风）。",
                "解决办法：删掉 vendor\.napcat-installed，再双击 start.bat 重试；",
                "脚本现在会自己先把完整的 NapCat.Shell.zip 下好，安装器就不会再走镜像。"
            ))
            return
        }
        [IO.File]::WriteAllText($InstalledMarker, "installed " + (Get-Date -Format "s"), $Utf8NoBom)
    } catch {
        Write-Warn ("安装器运行失败：" + $_.Exception.Message)
    }
}

function Find-NapcatLauncher {
    # 优先级：自带的 NapCat.*.Shell\napcat.bat > 上次记录的路径 > 其它 napcat.bat
    #          > launcher.bat > bootmain\napcat.bat（裸 hook，最不想要）
    $searchRoots = @($Vendor, $Root)
    $candidates = @()
    foreach ($searchRoot in $searchRoots) {
        if (-not (Test-Path $searchRoot)) { continue }
        foreach ($name in @("napcat.bat", "launcher.bat", "launcher-win10.bat")) {
            $candidates += Get-ChildItem -Path $searchRoot -Recurse -Depth 4 -Filter $name -ErrorAction SilentlyContinue
        }
    }

    $chosen = $null
    $shellHit = $candidates | Where-Object { $_.FullName -match "\\NapCat\.[^\\]+\.Shell\\napcat\.bat$" } | Select-Object -First 1
    if ($shellHit) { $chosen = $shellHit.FullName }

    if (-not $chosen -and (Test-Path $PathFile)) {
        $saved = (Get-Content $PathFile -Raw -Encoding UTF8).Trim()
        if ($saved -and (Test-Path $saved)) { $chosen = $saved }
    }

    if (-not $chosen) {
        $plain = $candidates | Where-Object {
            $_.FullName -notmatch "\\bootmain\\" -and $_.Name -eq "napcat.bat"
        } | Select-Object -First 1
        if ($plain) { $chosen = $plain.FullName }
    }
    if (-not $chosen) {
        $launcher = $candidates | Where-Object { $_.Name -like "launcher*.bat" } | Select-Object -First 1
        if ($launcher) { $chosen = $launcher.FullName }
    }
    if (-not $chosen) {
        $boot = $candidates | Where-Object { $_.FullName -match "\\bootmain\\" } | Select-Object -First 1
        if ($boot) { $chosen = $boot.FullName }
    }

    if ($chosen) {
        New-Item -ItemType Directory -Force -Path $Vendor | Out-Null
        [IO.File]::WriteAllText($PathFile, $chosen, $Utf8NoBom)
    }
    return $chosen
}

function Resolve-NapcatConfigDir {
    param([string]$Launcher)
    $shellDir = Split-Path -Parent $Launcher
    # 自带的 Shell：配置在 versions\<ver>\resources\app\napcat\config
    $versionsDir = Join-Path $shellDir "versions"
    if (Test-Path $versionsDir) {
        foreach ($ver in Get-ChildItem $versionsDir -Directory -ErrorAction SilentlyContinue) {
            $candidate = Join-Path $ver.FullName "resources\app\napcat\config"
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return (Join-Path $shellDir "config")
}

function Repair-NapcatGpuFlag {
    <#
        有些机器上 QQ 的 GPU 子进程起不来（error_code=18），Chromium 直接
        "GPU process isn't usable. Goodbye."，表现就是没有登录窗口。
        NapCatWinBootMain.exe 里硬编码了传给 QQ 的 "--enable-logging"，
        这里把它等长替换成 "--no-sandbox"，避免子进程（GPU/渲染）启动被安全软件拦掉。
    #>
    param([string]$Launcher)
    $shellDir = Split-Path -Parent $Launcher
    $dirs = @($shellDir)
    $versionsDir = Join-Path $shellDir "versions"
    if (Test-Path $versionsDir) {
        foreach ($ver in Get-ChildItem $versionsDir -Directory -ErrorAction SilentlyContinue) {
            $dirs += (Join-Path $ver.FullName "resources\app\napcat")
        }
    }
    $oldText = "--enable-logging"
    # 必须与旧串**等长**（16 字符），否则会把后面的字符串表写坏
    $newText = "--no-sandbox    "
    if ($newText.Length -ne $oldText.Length) {
        Write-Warn "内部错误：替换串长度不一致，跳过 GPU 补丁"
        return
    }
    $old = [Text.Encoding]::Unicode.GetBytes($oldText)
    $new = [Text.Encoding]::Unicode.GetBytes($newText)

    foreach ($dir in $dirs) {
        $exe = Join-Path $dir "NapCatWinBootMain.exe"
        if (-not (Test-Path $exe)) { continue }
        $bytes = [IO.File]::ReadAllBytes($exe)
        $wide = [Text.Encoding]::Unicode.GetString($bytes)
        if ($wide.Contains("--no-sandbox")) { continue }
        $index = -1
        for ($i = 0; $i -le $bytes.Length - $old.Length; $i++) {
            $match = $true
            for ($j = 0; $j -lt $old.Length; $j++) {
                if ($bytes[$i + $j] -ne $old[$j]) { $match = $false; break }
            }
            if ($match) { $index = $i; break }
        }
        if ($index -lt 0) { continue }
        $backup = $exe + ".ricefish.bak"
        if (-not (Test-Path $backup)) { Copy-Item $exe $backup }
        [Array]::Copy($new, 0, $bytes, $index, $new.Length)
        [IO.File]::WriteAllBytes($exe, $bytes)
        Write-Ok "已给 NapCat 启动器加上 --no-sandbox（绕开子进程启动失败）"
    }
}

function Repair-NapcatLauncherScript {
    <#
        OneKey 装出来的 napcat.bat 是个残桩：只写了 .\NapCatWinBootMain.exe，
        既没传 QQ 路径和 hook 路径，也没设 NAPCAT_* 环境变量。
        结果就是 QQ 带着被改过的 package.json 裸跑，完整性校验失败直接崩。
        这里按官方 launcher.bat 的姿势重写一份（自动找 versions 下的 app 目录）。
    #>
    param([string]$Launcher)
    $shellDir = Split-Path -Parent $Launcher
    if (-not (Test-Path (Join-Path $shellDir "versions"))) { return }

    $appDir = ""
    foreach ($ver in Get-ChildItem (Join-Path $shellDir "versions") -Directory -ErrorAction SilentlyContinue) {
        $candidate = Join-Path $ver.FullName "resources\app\napcat"
        if (Test-Path (Join-Path $candidate "napcat.mjs")) {
            $appDir = $candidate
            break
        }
    }
    if (-not $appDir) { return }

    $lines = @(
        '@echo off',
        'setlocal DisableDelayedExpansion',
        'cd /d "%~dp0" || exit /b 1',
        'chcp 65001 >nul',
        'set "APP="',
        'for /d %%d in ("%cd%\versions\*") do (',
        '  if exist "%%~fd\resources\app\napcat\napcat.mjs" set "APP=%%~fd\resources\app\napcat"',
        ')',
        'if not defined APP (',
        '  echo [ricefish] NapCat app folder not found under versions\',
        '  pause',
        '  exit /b 1',
        ')',
        'set "NAPCAT_PATCH_PACKAGE=%APP%\qqnt.json"',
        'set "NAPCAT_LOAD_PATH=%APP%\loadNapCat.js"',
        'set "NAPCAT_INJECT_PATH=%APP%\NapCatWinBootHook.dll"',
        'set "NAPCAT_MAIN_PATH=%APP%\napcat.mjs"',
        'set "NAPCAT_LAUNCHER_PATH=%APP%\NapCatWinBootMain.exe"',
        'if not exist "%NAPCAT_LAUNCHER_PATH%" (',
        '  echo [ricefish] NapCatWinBootMain.exe not found',
        '  pause',
        '  exit /b 1',
        ')',
        'net session >nul 2>&1',
        'if not errorlevel 1 goto :run',
        'echo [ricefish] requesting administrator rights...',
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath ''%~f0'' -Verb RunAs"',
        'exit /b 0',
        '',
        ':run',
        'title ricefish NapCat',
        'echo [ricefish] starting NapCat with bundled QQ...',
        'echo [ricefish] 等出现二维码后，用手机小号扫码；扫完不要关这个窗口',
        'echo.',
        '"%NAPCAT_LAUNCHER_PATH%" "%cd%\QQ.exe" "%NAPCAT_INJECT_PATH%"',
        'echo.',
        'echo [ricefish] NapCat exited with code %ERRORLEVEL%',
        'pause'
    )
    [IO.File]::WriteAllLines($Launcher, [string[]]$lines, $Utf8NoBom)
    Write-Ok "已修正 NapCat 启动脚本（带参数和环境变量）"
}

function Open-NapcatWebUi {
    <#
        QQ 自己的登录窗口在部分机器上渲染不出来，但 NapCat 的 WebUI 会显示同一个
        二维码。这里从 webui.json 读出 token，等 6099 起来后自动打开浏览器。
    #>
    param([string]$Launcher)
    try {
        $configDir = Resolve-NapcatConfigDir -Launcher $Launcher
        $token = ""
        $webuiFile = Join-Path $configDir "webui.json"
        if (Test-Path $webuiFile) {
            $webui = Get-Content $webuiFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $token = [string]$webui.token
        }
        for ($i = 0; $i -lt 40; $i++) {
            if (Test-NetConnection -ComputerName 127.0.0.1 -Port 6099 -InformationLevel Quiet -WarningAction SilentlyContinue) {
                $url = "http://127.0.0.1:6099/webui"
                if ($token) { $url = $url + "?token=" + $token }
                Start-Process $url
                Write-Ok ("已打开 NapCat WebUI：" + $url)
                return $true
            }
            Start-Sleep -Milliseconds 500
        }
        Write-Warn "NapCat WebUI 没在 20 秒内起来，二维码看 NapCat 窗口里的字符画"
    } catch {
        Write-Warn ("打开 NapCat WebUI 失败：" + $_.Exception.Message)
    }
    return $false
}

function Set-NapcatOneBotConfig {
    param([string]$Launcher, [string]$WsUrl, [string]$Token)
    $configDir = Resolve-NapcatConfigDir -Launcher $Launcher
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    Write-Ok ("NapCat 配置目录：" + $configDir)

    $existingAccount = Get-ChildItem -Path $configDir -Filter "onebot11_*.json" -ErrorAction SilentlyContinue
    if ($existingAccount) {
        Write-Warn "检测到已存在的账号配置，跳过自动写入（要改请在 NapCat WebUI 里改）"
        return
    }
    $target = Join-Path $configDir "onebot11.json"
    if (Test-Path $target) {
        Write-Ok "NapCat 的 OneBot 配置已存在，保留不动"
        return
    }

    $client = [ordered]@{
        name              = "ricefish"
        enable            = $true
        url               = $WsUrl
        messagePostFormat = "array"
        reportSelfMessage = $false
        reconnectInterval = 5000
        token             = $Token
        debug             = $false
        heartInterval     = 30000
    }
    $network = [ordered]@{
        httpServers      = @()
        httpClients      = @()
        websocketServers = @()
        websocketClients = @($client)
    }
    $config = [ordered]@{
        network             = $network
        musicSignUrl        = ""
        enableLocalFile2Url = $false
        parseMultMsg        = $false
    }
    $json = $config | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($target, $json, $Utf8NoBom)
    Write-Ok ("已写好 NapCat 反向 WS 配置：" + $target)
}

# ---------------------------------------------------------------------------
# 配置向导
# ---------------------------------------------------------------------------
function Initialize-EnvFile {
    if (Test-Path $EnvFile) { return }
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile -Force
        Write-Ok "已从 .env.example 创建 .env"
    } else {
        [IO.File]::WriteAllText($EnvFile, "DEEPSEEK_API_KEY=", $Utf8NoBom)
    }
}

function Invoke-ConfigWizard {
    $key = Get-EnvValue "DEEPSEEK_API_KEY"
    if ($Reconfigure -or -not $key -or $key -like "*xxxx*") {
        $tip = Join-Lines @(
            "请输入 DeepSeek API Key（sk- 开头）",
            "",
            "还没申请？先去 https://platform.deepseek.com/api_keys 拿一个。",
            "留空也能继续，但机器人不会说话。"
        )
        $value = Show-Input -Prompt $tip -Title "ricefish 第 1 步 / 3" -Default $key
        if ($value) {
            Set-EnvValue "DEEPSEEK_API_KEY" $value.Trim()
            Write-Ok "API Key 已保存"
        }
    }

    $owner = Get-EnvValue "RICEFISH_OWNER_QQ"
    if ($Reconfigure -or -not $owner) {
        $tip = Join-Lines @(
            "你自己的 QQ 号（管理命令只认这个号）",
            "",
            "留空也行，之后可以在 .env 里补。"
        )
        $value = Show-Input -Prompt $tip -Title "ricefish 第 2 步 / 3" -Default $owner
        if ($value) {
            $clean = ($value -replace "[^0-9]", "")
            if ($clean) {
                Set-EnvValue "RICEFISH_OWNER_QQ" $clean
                Write-Ok ("主人 QQ 已保存：" + $clean)
            }
        }
    }

    $groups = Get-EnvValue "RICEFISH_ALLOWED_GROUPS"
    if ($Reconfigure -or -not $groups) {
        $tip = Join-Lines @(
            "允许机器人在哪些群里说话？（群号，多个用逗号隔开）",
            "",
            "还不知道群号？可以留空先启动，之后在管理面板的「会话」页看，",
            "或者直接改 .env 里的 RICEFISH_ALLOWED_GROUPS。"
        )
        $value = Show-Input -Prompt $tip -Title "ricefish 第 3 步 / 3" -Default $groups
        if ($value) {
            $clean = ($value -replace "[^0-9,，;； ]", "").Trim()
            if ($clean) {
                Set-EnvValue "RICEFISH_ALLOWED_GROUPS" $clean
                Write-Ok ("白名单群已保存：" + $clean)
            }
        }
    }

    $friends = Get-EnvValue "RICEFISH_ALLOWED_FRIENDS"
    if ($Reconfigure -or -not $friends) {
        $tip = Join-Lines @(
            "允许私聊机器人的人（QQ 号，多个用逗号隔开）。",
            "",
            "一般填你自己的号就行。留空 = 不响应私聊。"
        )
        $value = Show-Input -Prompt $tip -Title "ricefish 私聊白名单" -Default $friends
        if ($value) {
            $clean = ($value -replace "[^0-9,，;； ]", "").Trim()
            if ($clean) {
                Set-EnvValue "RICEFISH_ALLOWED_FRIENDS" $clean
                Write-Ok ("私聊白名单已保存：" + $clean)
            }
        }
    }

    $wsToken = Get-EnvValue "RICEFISH_WS_TOKEN"
    if (-not $wsToken) {
        $wsToken = New-Token
        Set-EnvValue "RICEFISH_WS_TOKEN" $wsToken
        Write-Ok "已生成 NapCat 连接 token"
    }
    $panelToken = Get-EnvValue "RICEFISH_PANEL_TOKEN"
    if (-not $panelToken) {
        $panelToken = (New-Token).Substring(0, 16)
        Set-EnvValue "RICEFISH_PANEL_TOKEN" $panelToken
        Write-Ok "已生成管理面板 token"
    }
    $panelPort = Get-EnvValue "RICEFISH_PANEL_PORT"
    if (-not $panelPort) {
        Set-EnvValue "RICEFISH_PANEL_PORT" "8721"
        $panelPort = "8721"
    }
    $script:WsToken = $wsToken
    $script:PanelToken = $panelToken
    $script:PanelPort = [int]$panelPort
}

# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
function Wait-Panel {
    param([int]$Port, [int]$Seconds = 40)
    $url = "http://127.0.0.1:" + $Port + "/login"
    for ($i = 0; $i -lt ($Seconds * 2); $i++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function New-BotLauncherScript {
    $dataDir = Join-Path $Root "data"
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $batPath = Join-Path $dataDir "run_bot.bat"
    $lines = @(
        "@echo off",
        "chcp 65001 >nul",
        "title ricefish",
        ($script:PythonExe + " run.py"),
        "echo.",
        "echo ricefish stopped. Press any key to close.",
        "pause >nul"
    )
    [IO.File]::WriteAllLines($batPath, [string[]]$lines, $Utf8NoBom)
    return $batPath
}

function Start-BotWindow {
    $batPath = New-BotLauncherScript
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", ('"' + $batPath + '"')) -WorkingDirectory $Root
}

function Start-Ricefish {
    param([string]$Launcher)

    if ($Launcher) {
        Write-Step "启动 NapCat"
        $napcatWorkDir = Split-Path -Parent $Launcher
        Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", ('"' + $Launcher + '"')) -WorkingDirectory $napcatWorkDir
        Write-Ok "NapCat 窗口已打开"

        Write-Step "打开 NapCat 登录页"
        $webuiOpened = Open-NapcatWebUi -Launcher $Launcher

        if ($webuiOpened) {
            $msg = Join-Lines @(
                "NapCat 已启动，浏览器里已经打开登录二维码页面。",
                "",
                "1. 用【手机 QQ】扫浏览器里的二维码（【小号】，千万别用主号）",
                "2. 手机上点确认登录",
                "3. 登录成功后点这里的「确定」，我继续启动机器人",
                "",
                "如果二维码过期了，浏览器页面里可以刷新。"
            )
        } else {
            $msg = Join-Lines @(
                "NapCat 已启动，但 WebUI 没自动打开。",
                "",
                "请直接看 NapCat 窗口里的二维码字符画，用【手机 QQ 小号】扫。",
                "或者在浏览器打开：http://127.0.0.1:6099/webui（token 见 NapCat 窗口日志）",
                "",
                "登录成功后点「确定」继续。"
            )
        }
        Show-Info -Message $msg -Title "ricefish 第 4 步"
    } else {
        Write-Warn "没找到 NapCat 启动器，只启动机器人"
    }

    Write-Step "启动机器人"
    Start-BotWindow
    Write-Ok "机器人窗口已打开"

    Write-Step "等待管理面板起来"
    if (Wait-Panel -Port $script:PanelPort) {
        Write-Ok ("面板已就绪：http://127.0.0.1:" + $script:PanelPort)
        $panelUrl = "http://127.0.0.1:" + $script:PanelPort + "/login?token=" + $script:PanelToken
        Start-Process $panelUrl
        $msg = Join-Lines @(
            "都启动好了。",
            "",
            ("管理面板：http://127.0.0.1:" + $script:PanelPort),
            ("面板 Token：" + $script:PanelToken),
            "",
            "浏览器应该已经自动登录并打开了面板。",
            "如果 NapCat 那边还没扫码，先扫码登录。",
            "",
            "提示：默认是试运行（dry_run），只会写日志不会真的发消息。",
            "确认没问题后，在面板「设置」里关掉试运行即可。"
        )
        Show-Info -Message $msg -Title "ricefish 已启动"
    } else {
        $msg = Join-Lines @(
            "机器人窗口起来了，但管理面板 40 秒内没响应。",
            "",
            "去那个写着 ricefish 的命令行窗口看看报错，常见原因：",
            "- Python 没装好",
            "- 端口被占用（改 .env 里的 RICEFISH_PANEL_PORT）",
            "",
            "日志在 data\logs\bot.log"
        )
        Show-Error -Message $msg
    }
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
function Invoke-Main {
    Write-Host ""
    Write-Host "  ricefish · 一条住在 QQ 里的白饭鱼" -ForegroundColor Cyan
    Write-Host "  ----------------------------------" -ForegroundColor DarkGray
    if ($CheckOnly) {
        Write-Host "  （检查模式：不下载、不弹窗、不启动）" -ForegroundColor DarkGray
    }
    Write-Log "==== 启动脚本开始 ===="

    # 1. Python
    Write-Step "检查 Python"
    $python = Find-Python
    if (-not $python) {
        Write-Warn "没找到 Python 3.10+"
        if ($script:Interactive -and (Install-Python)) {
            $python = Find-Python
        }
    }
    if (-not $python) {
        $msg = Join-Lines @(
            "没有可用的 Python 3.10+。",
            "",
            "请到 https://www.python.org/downloads/ 下载安装，",
            "安装时务必勾选「Add Python to PATH」，装完再双击 start.bat。"
        )
        Show-Error -Message $msg
        Start-Process "https://www.python.org/downloads/"
        return 1
    }
    $script:PythonExe = $python.Command
    Write-Ok ("Python " + $python.Version + "（" + $python.Command + "）")
    Write-Log ("python: " + $python.Command + " " + $python.Version)

    # 2.5 可选依赖：edge-tts（实时语音）。装不上不影响启动，只是没语音。
    if (-not $CheckOnly) {
        try {
            $probe = & $python.Command -c "import edge_tts" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Log "安装可选依赖 edge-tts ..."
                & $python.Command -m pip install --quiet --disable-pip-version-check edge-tts *> $null
            }
            if ($LASTEXITCODE -eq 0) { Write-Ok "实时语音（edge-tts）可用" }
            else { Write-Warn "edge-tts 没装上，语音功能会降级（不影响聊天）" }
        } catch {
            Write-Warn "edge-tts 安装跳过：$($_.Exception.Message)"
        }
    }

    # 2. 目录
    foreach ($dir in @($Vendor, (Join-Path $Root "data"), (Join-Path $Root "plugins"))) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    }

    # 3. 配置
    if ($CheckOnly) {
        Write-Ok "检查模式：跳过 .env 写入"
    } else {
        Initialize-EnvFile
        Invoke-ConfigWizard
    }

    # 4. NapCat
    Write-Step "检查 NapCat"
    if ($Reconfigure) {
        Remove-Item $InstalledMarker -Force -ErrorAction SilentlyContinue
    }
    $launcher = Find-NapcatLauncher
    $selfContained = $false
    if ($launcher) {
        $selfContained = Test-Path (Join-Path (Split-Path -Parent $launcher) "QQ.exe")
    }
    $qqPath = Get-QqInstallPath
    if ($selfContained) {
        Write-Ok "NapCat 自带一份独立的 QQ，不用管系统里装的那份"
    } elseif ($qqPath) {
        Write-Ok ("QQ：" + $qqPath)
        if (Test-PathNeedsAscii $qqPath) {
            Write-Warn "QQ 装在中文路径下，NapCat 可能挂不上，建议用安装器重装到默认目录"
        }
    } else {
        Write-Warn "没检测到 QQ（NapCat 需要 QQNT 客户端）"
    }

    if (-not $launcher -and -not $CheckOnly) {
        try {
            $release = Get-Release
            $asset = Select-Asset $release
            if (-not $asset) { throw "这个 release 里没有可用的 Windows 包" }
            Write-Ok ("最新版本：" + $release.tag_name)
            $zip = Save-NapcatZip -Asset $asset -Tag $release.tag_name
            Expand-Napcat -Zip $zip
            $launcher = Find-NapcatLauncher
        } catch {
            Write-Warn ("自动下载失败：" + $_.Exception.Message)
            $msg = Join-Lines @(
                "NapCat 自动下载失败了。",
                "",
                "可以手动操作：",
                "1. 打开 https://github.com/NapNeko/NapCatQQ/releases/latest",
                "2. 下载 NapCat.Shell.Windows.OneKey.zip",
                ("3. 解压到：" + $NapcatDir),
                "4. 运行里面的 NapCatInstaller.exe，装完再双击 start.bat",
                "",
                "机器人本身可以先跑起来（没有 NapCat 就连不上 QQ）。"
            )
            Show-Error -Message $msg
            Start-Process "https://github.com/NapNeko/NapCatQQ/releases/latest"
        }
    }

    if (-not $CheckOnly) {
        Invoke-NapcatInstaller
        $launcher = Find-NapcatLauncher
    }

    if (-not $launcher -and -not $CheckOnly -and $script:Interactive) {
        $msg = Join-Lines @(
            "没自动找到 NapCat 的启动脚本（napcat.bat）。",
            "",
            "如果刚才的安装器把 NapCat 装到了别的目录，点「是」手动选一下；",
            "点「否」就先只启动机器人（连不上 QQ，但面板可以看）。"
        )
        if (Show-Confirm -Message $msg -Title "选择 NapCat") {
            $picked = Show-OpenFile -Title "请选择 napcat.bat"
            if ($picked) {
                New-Item -ItemType Directory -Force -Path $Vendor | Out-Null
                [IO.File]::WriteAllText($PathFile, $picked, $Utf8NoBom)
                $launcher = $picked
            }
        }
    }

    if ($launcher) {
        Write-Ok ("NapCat：" + $launcher)
        if (-not $CheckOnly) {
            Repair-NapcatLauncherScript -Launcher $launcher
            Repair-NapcatGpuFlag -Launcher $launcher
            Set-NapcatOneBotConfig -Launcher $launcher -WsUrl "ws://127.0.0.1:8080/onebot/v11/ws" -Token $script:WsToken
        }
    } elseif ($CheckOnly) {
        Write-Warn "NapCat 还没装（第一次运行 start.bat 会自动下载）"
    }

    if (Get-Process HipsDaemon -ErrorAction SilentlyContinue) {
        Write-Warn "检测到火绒安全：如果 QQ 一闪就退、提示「文件已损坏」，请把整个项目目录加入火绒信任区"
    }

    # 5. 自检
    Write-Step "配置自检"
    $checkArgs = @($python.Argv) + @("run.py", "--check")
    $checkExe = $checkArgs[0]
    $checkRest = @()
    if ($checkArgs.Count -gt 1) {
        $checkRest = $checkArgs[1..($checkArgs.Count - 1)]
    }
    & $checkExe $checkRest 2>&1 | ForEach-Object { Write-Host ("    " + $_) }
    Write-Log "--check 已执行"

    if ($CheckOnly) {
        $napcatText = "未安装"
        if ($launcher) { $napcatText = $launcher }
        $msg = Join-Lines @(
            "检查完成。",
            "",
            ("Python：" + $python.Version),
            ("NapCat：" + $napcatText),
            "",
            "双击 start.bat 就会开始下载和配置。"
        )
        Show-Info -Message $msg
        return 0
    }

    if ($NoLaunch) {
        Write-Ok "配置完成（-NoLaunch，没有启动）"
        return 0
    }

    Start-Ricefish -Launcher $launcher
    return 0
}

# ---------------------------------------------------------------------------
# 自测：不联网、不弹窗，验证纯逻辑（开发用）
# ---------------------------------------------------------------------------
function Invoke-SelfTest {
    $script:SelfTestFailures = 0
    function Assert-True {
        param([bool]$Condition, [string]$Name)
        if ($Condition) {
            Write-Host ("  ok   " + $Name) -ForegroundColor Green
        } else {
            Write-Host ("  FAIL " + $Name) -ForegroundColor Red
            $script:SelfTestFailures++
        }
    }

    $temp = Join-Path $env:TEMP ("ricefish_selftest_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    $savedEnvFile = $EnvFile
    try {
        # 1. .env 读写
        $testEnv = Join-Path $temp ".env"
        [IO.File]::WriteAllText($testEnv, "A=1`n# comment`nB=2", $Utf8NoBom)
        Set-Variable -Name EnvFile -Value $testEnv -Scope Script
        Set-EnvValue "B" "3"
        Set-EnvValue "C" "hello"
        Assert-True ((Get-EnvValue "A") -eq "1") ".env 保留原有键"
        Assert-True ((Get-EnvValue "B") -eq "3") ".env 覆盖已有键"
        Assert-True ((Get-EnvValue "C") -eq "hello") ".env 追加新键"
        $content = [IO.File]::ReadAllText($testEnv)
        Assert-True (([regex]::Matches($content, "B=")).Count -eq 1) ".env 不产生重复键"

        # 2. 选择下载包
        $fakeRelease = [pscustomobject]@{
            tag_name = "v9.9.9"
            assets = @(
                [pscustomobject]@{ name = "NapCat.Shell.zip"; size = 1; browser_download_url = "x" },
                [pscustomobject]@{ name = "NapCat.Shell.Windows.OneKey.zip"; size = 2; browser_download_url = "y" }
            )
        }
        $asset = Select-Asset $fakeRelease
        Assert-True ($asset.name -eq "NapCat.Shell.Windows.OneKey.zip") "优先选 OneKey 包"
        $fakeNone = [pscustomobject]@{
            tag_name = "v1"
            assets = @([pscustomobject]@{ name = "other.zip"; size = 1; browser_download_url = "z" })
        }
        Assert-True ($null -eq (Select-Asset $fakeNone)) "没有可用包时返回 null"

        # 3. NapCat OneBot 配置
        $napcatHome = Join-Path $temp "napcat"
        New-Item -ItemType Directory -Force -Path $napcatHome | Out-Null
        $bat = Join-Path $napcatHome "napcat.bat"
        [IO.File]::WriteAllText($bat, "@echo off", $Utf8NoBom)
        Set-NapcatOneBotConfig -Launcher $bat -WsUrl "ws://127.0.0.1:8080/onebot/v11/ws" -Token "tok123"
        $cfgPath = Join-Path $napcatHome "config\onebot11.json"
        Assert-True (Test-Path $cfgPath) "写出了 NapCat 配置"
        if (Test-Path $cfgPath) {
            $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
            $client = $cfg.network.websocketClients[0]
            Assert-True ($client.url -eq "ws://127.0.0.1:8080/onebot/v11/ws") "WS 地址正确"
            Assert-True ($client.token -eq "tok123") "token 正确"
            Assert-True ($client.messagePostFormat -eq "array") "消息格式为 array"
            Assert-True ($client.enable -eq $true) "默认启用"
        }
        Set-NapcatOneBotConfig -Launcher $bat -WsUrl "ws://other" -Token "other"
        $again = Get-Content $cfgPath -Raw | ConvertFrom-Json
        Assert-True ($again.network.websocketClients[0].url -eq "ws://127.0.0.1:8080/onebot/v11/ws") "重复执行不覆盖已有配置"

        # 4. token 生成
        $t1 = New-Token
        $t2 = New-Token
        Assert-True ($t1.Length -eq 48) "token 长度 48"
        Assert-True ($t1 -ne $t2) "两次 token 不同"

        # 5. 启动脚本生成（只写文件，不真的启动）
        $script:PythonExe = '"python"'
        $botBat = New-BotLauncherScript
        Assert-True (Test-Path $botBat) "生成了机器人启动脚本"
        if (Test-Path $botBat) {
            $batText = [IO.File]::ReadAllText($botBat)
            Assert-True ($batText.Contains("run.py")) "启动脚本里包含 run.py"
        }
    } finally {
        Set-Variable -Name EnvFile -Value $savedEnvFile -Scope Script
        Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
    }

    Write-Host ""
    if ($script:SelfTestFailures -gt 0) {
        Write-Host ("自测失败 " + $script:SelfTestFailures + " 项") -ForegroundColor Red
        return 1
    }
    Write-Host "自测全部通过" -ForegroundColor Green
    return 0
}

# ---------------------------------------------------------------------------
try {
    if ($SelfTest) {
        exit (Invoke-SelfTest)
    }
    $code = Invoke-Main
    exit $code
} catch {
    $message = $_.Exception.Message
    Write-Log ("错误: " + $message)
    Write-Log ($_.ScriptStackTrace)
    Write-Host ""
    Write-Host ("出错了：" + $message) -ForegroundColor Red
    Show-Error -Message ("启动过程中出错了：`n`n" + $message + "`n`n详细信息见 setup.log")
    exit 1
}
