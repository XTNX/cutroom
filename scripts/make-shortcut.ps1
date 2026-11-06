# Desktop + Start-menu shortcuts to the packaged Cutroom.exe (with the Cutroom icon).
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "release\win-unpacked\Cutroom.exe"
if (-not (Test-Path $exe)) { throw "Сначала соберите приложение: npm run dist" }
$icon = Join-Path $root "build\icon.ico"
$shell = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path ([Environment]::GetFolderPath("Desktop")) "Cutroom.lnk"),
  (Join-Path ([Environment]::GetFolderPath("Programs")) "Cutroom.lnk")
)
foreach ($lnk in $targets) {
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = $exe
  $s.WorkingDirectory = Split-Path -Parent $exe
  $s.IconLocation = "$icon,0"
  $s.Description = "Cutroom — монтаж поверх video-use"
  $s.Save()
  Write-Output "ярлык: $lnk"
}
