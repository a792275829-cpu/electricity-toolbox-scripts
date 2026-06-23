Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

$script:runningProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]
$script:runningTimers = New-Object System.Collections.Generic.List[System.Windows.Forms.Timer]

$toolRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$nodeScript = Join-Path $toolRoot 'scripts\upload-private-data.mjs'
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $toolRoot)
$nodeExe = Join-Path $workspaceRoot 'runtime\node\node.exe'
if (-not (Test-Path -LiteralPath $nodeExe -PathType Leaf)) {
    $nodeExe = 'node'
}
$bundledBrowsers = Join-Path $workspaceRoot 'runtime\ms-playwright'
if (Test-Path -LiteralPath $bundledBrowsers -PathType Container) {
    $env:PLAYWRIGHT_BROWSERS_PATH = $bundledBrowsers
}
$reviewRoot = 'C:\Users\lllg\Desktop\复盘'
$defaultFolder = $reviewRoot
try {
    $latestReviewDir = Get-ChildItem -LiteralPath $reviewRoot -Directory -ErrorAction Stop |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($latestReviewDir) {
        $defaultFolder = $latestReviewDir.FullName
    }
} catch {
    $defaultFolder = $reviewRoot
}

function Append-Log {
    param(
        [System.Windows.Forms.TextBox]$TextBox,
        [string]$Text
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return
    }

    $box = $TextBox
    $line = $Text
    $action = [System.Windows.Forms.MethodInvoker]{
        $box.AppendText($line + [Environment]::NewLine)
        $box.SelectionStart = $box.TextLength
        $box.ScrollToCaret()
    }

    if ($TextBox.InvokeRequired) {
        [void]$TextBox.BeginInvoke($action)
    } else {
        $action.Invoke()
    }
}

function Append-RawLog {
    param(
        [System.Windows.Forms.TextBox]$TextBox,
        [string]$Text
    )

    if ([string]::IsNullOrEmpty($Text)) {
        return
    }

    $box = $TextBox
    $chunk = $Text
    $action = [System.Windows.Forms.MethodInvoker]{
        $box.AppendText($chunk)
        $box.SelectionStart = $box.TextLength
        $box.ScrollToCaret()
    }

    if ($TextBox.InvokeRequired) {
        [void]$TextBox.BeginInvoke($action)
    } else {
        $action.Invoke()
    }
}

function Clear-Log {
    param([System.Windows.Forms.TextBox]$TextBox)

    $box = $TextBox
    $action = [System.Windows.Forms.MethodInvoker]{
        $box.Clear()
    }

    if ($TextBox.InvokeRequired) {
        [void]$TextBox.Invoke($action)
    } else {
        $action.Invoke()
    }
}

function Quote-CmdArg {
    param([string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Read-SharedText {
    param([string]$Path)

    $stream = $null
    $reader = $null
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, $true)
        return $reader.ReadToEnd()
    } finally {
        if ($reader) {
            $reader.Dispose()
        } elseif ($stream) {
            $stream.Dispose()
        }
    }
}

function Set-ButtonsEnabled {
    param(
        [System.Windows.Forms.Button[]]$Buttons,
        [bool]$Enabled
    )

    $items = $Buttons
    $state = $Enabled
    $action = [System.Windows.Forms.MethodInvoker]{
        foreach ($button in $items) {
            $button.Enabled = $state
        }
    }

    if ($Buttons[0].InvokeRequired) {
        [void]$Buttons[0].BeginInvoke($action)
    } else {
        $action.Invoke()
    }
}

function Invoke-UploadCommand {
    param(
        [string]$Mode,
        [string]$SourceFolder,
        [System.Windows.Forms.TextBox]$LogBox,
        [System.Windows.Forms.Button[]]$Buttons
    )

    if (-not (Test-Path -LiteralPath $nodeScript -PathType Leaf)) {
        [System.Windows.Forms.MessageBox]::Show("找不到上传脚本：$nodeScript", '私有数据上传工具', 'OK', 'Error') | Out-Null
        return
    }

    if (-not (Test-Path -LiteralPath $SourceFolder -PathType Container)) {
        [System.Windows.Forms.MessageBox]::Show("请选择有效文件夹：$SourceFolder", '私有数据上传工具', 'OK', 'Warning') | Out-Null
        return
    }

    Clear-Log -TextBox $LogBox
    Set-ButtonsEnabled -Buttons $Buttons -Enabled $false
    Append-Log -TextBox $LogBox -Text ("[{0}] {1}：{2}" -f (Get-Date -Format 'HH:mm:ss'), $(if ($Mode -eq '--execute') { '开始上传' } else { '预览文件' }), $SourceFolder)
    Append-Log -TextBox $LogBox -Text '正在启动上传进程...'

    $logDir = Join-Path $toolRoot 'logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $logFile = Join-Path $logDir ('upload-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    [System.IO.File]::WriteAllText($logFile, '', [System.Text.Encoding]::UTF8)
    Append-Log -TextBox $LogBox -Text ("日志文件：{0}" -f $logFile)

    $command = ('{0} {1} {2} --source {3} > {4} 2>&1' -f (Quote-CmdArg $nodeExe), (Quote-CmdArg $nodeScript), $Mode, (Quote-CmdArg $SourceFolder), (Quote-CmdArg $logFile))

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'cmd.exe'
    $startInfo.WorkingDirectory = $toolRoot
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $false
    $startInfo.RedirectStandardError = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.Arguments = ('/d /c "{0}"' -f $command)

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $process.EnableRaisingEvents = $false
    [void]$script:runningProcesses.Add($process)

    try {
        [void]$process.Start()
        Append-Log -TextBox $LogBox -Text ("进程已启动，PID={0}" -f $process.Id)

        $timer = New-Object System.Windows.Forms.Timer
        $timer.Interval = 800
        $timer.Tag = @{
            Process = $process
            LogFile = $logFile
            LastLength = 0
            Buttons = $Buttons
            LogBox = $LogBox
        }
        $timer.Add_Tick({
            $state = $this.Tag
            $p = [System.Diagnostics.Process]$state.Process
            $file = [string]$state.LogFile

            try {
                if (Test-Path -LiteralPath $file -PathType Leaf) {
                    $content = Read-SharedText -Path $file
                    $lastLength = [int]$state.LastLength
                    if ($content.Length -gt $lastLength) {
                        Append-RawLog -TextBox $state.LogBox -Text $content.Substring($lastLength)
                        $state.LastLength = $content.Length
                    }
                }

                if ($p.HasExited) {
                    $this.Stop()
                    [void]$script:runningTimers.Remove($this)
                    [void]$script:runningProcesses.Remove($p)
                    $message = if ($p.ExitCode -eq 0) { '完成' } else { "失败，退出码 $($p.ExitCode)，请查看上方日志" }
                    Append-Log -TextBox $state.LogBox -Text ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message)
                    Set-ButtonsEnabled -Buttons $state.Buttons -Enabled $true
                    $p.Dispose()
                    $this.Dispose()
                }
            } catch {
                Append-Log -TextBox $state.LogBox -Text ("读取日志失败：{0}" -f $_.Exception.Message)
            }
        })
        [void]$script:runningTimers.Add($timer)
        $timer.Start()
    } catch {
        Append-Log -TextBox $LogBox -Text ("启动失败：{0}" -f $_.Exception.Message)
        Set-ButtonsEnabled -Buttons $Buttons -Enabled $true
        [void]$script:runningProcesses.Remove($process)
        $process.Dispose()
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = '私有数据上传工具'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(820, 560)
$form.MinimumSize = New-Object System.Drawing.Size(720, 460)
$form.Add_FormClosing({
    foreach ($timer in @($script:runningTimers)) {
        $timer.Stop()
        $timer.Dispose()
    }
    foreach ($process in @($script:runningProcesses)) {
        try {
            if (-not $process.HasExited) {
                $process.Kill()
            }
            $process.Dispose()
        } catch {
        }
    }
})

$label = New-Object System.Windows.Forms.Label
$label.Text = '选择包含海风、鮀莲、归湖、东莞、汕头、海门等子文件夹的复盘目录：'
$label.AutoSize = $true
$label.Location = New-Object System.Drawing.Point(18, 18)
$form.Controls.Add($label)

$folderText = New-Object System.Windows.Forms.TextBox
$folderText.Location = New-Object System.Drawing.Point(20, 46)
$folderText.Size = New-Object System.Drawing.Size(615, 26)
$folderText.Anchor = 'Top,Left,Right'
$folderText.Text = $defaultFolder
$form.Controls.Add($folderText)

$browseButton = New-Object System.Windows.Forms.Button
$browseButton.Text = '选择文件夹'
$browseButton.Location = New-Object System.Drawing.Point(650, 44)
$browseButton.Size = New-Object System.Drawing.Size(130, 30)
$browseButton.Anchor = 'Top,Right'
$form.Controls.Add($browseButton)

$planButton = New-Object System.Windows.Forms.Button
$planButton.Text = '预览'
$planButton.Location = New-Object System.Drawing.Point(20, 88)
$planButton.Size = New-Object System.Drawing.Size(120, 34)
$form.Controls.Add($planButton)

$uploadButton = New-Object System.Windows.Forms.Button
$uploadButton.Text = '开始上传'
$uploadButton.Location = New-Object System.Drawing.Point(154, 88)
$uploadButton.Size = New-Object System.Drawing.Size(120, 34)
$form.Controls.Add($uploadButton)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = '建议先点“预览”确认文件匹配；“开始上传”会登录并切换到对应公司后逐个上传。'
$hint.AutoSize = $true
$hint.Location = New-Object System.Drawing.Point(292, 97)
$form.Controls.Add($hint)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Location = New-Object System.Drawing.Point(20, 138)
$logBox.Size = New-Object System.Drawing.Size(760, 360)
$logBox.Anchor = 'Top,Bottom,Left,Right'
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Both'
$logBox.WordWrap = $false
$logBox.Font = New-Object System.Drawing.Font('Consolas', 10)
$form.Controls.Add($logBox)

$buttons = @($browseButton, $planButton, $uploadButton)

$browseButton.Add_Click({
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择复盘文件夹'
    $dialog.ShowNewFolderButton = $false
    if (Test-Path -LiteralPath $folderText.Text -PathType Container) {
        $dialog.SelectedPath = $folderText.Text
    } elseif (Test-Path -LiteralPath $defaultFolder -PathType Container) {
        $dialog.SelectedPath = $defaultFolder
    }

    if ($dialog.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
        $folderText.Text = $dialog.SelectedPath
    }
})

$planButton.Add_Click({
    Invoke-UploadCommand -Mode '--plan' -SourceFolder $folderText.Text.Trim() -LogBox $logBox -Buttons $buttons
})

$uploadButton.Add_Click({
    $result = [System.Windows.Forms.MessageBox]::Show("确认上传此文件夹下匹配到的文件？`r`n$($folderText.Text.Trim())", '确认上传', 'OKCancel', 'Question')
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
        Invoke-UploadCommand -Mode '--execute' -SourceFolder $folderText.Text.Trim() -LogBox $logBox -Buttons $buttons
    }
})

Append-Log -TextBox $logBox -Text '操作流程：选择文件夹 -> 预览 -> 确认无误后开始上传。'
Append-Log -TextBox $logBox -Text '公司映射：金平=鮀莲，潮州/潮安=归湖，东莞=谢岗。'

[void]$form.ShowDialog()
