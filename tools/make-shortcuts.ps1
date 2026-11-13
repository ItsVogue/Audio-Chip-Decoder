param([Parameter(Mandatory = $true)][string]$AppDir)
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($folder in $folders) {
    if (-not $folder) { continue }
    $link = $shell.CreateShortcut((Join-Path $folder 'Audio Chip Decoder.lnk'))
    $link.TargetPath = Join-Path $AppDir 'venv\Scripts\pythonw.exe'
    $link.Arguments = '-m audiochip'
    $link.WorkingDirectory = Join-Path $AppDir 'app'
    $link.Description = 'Find and decode audio stored on memory chips'
    $icon = Join-Path $AppDir 'app\audiochip\gui\icon.ico'
    if (Test-Path $icon) { $link.IconLocation = $icon }
    $link.Save()
}
