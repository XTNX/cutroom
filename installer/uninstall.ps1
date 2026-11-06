# Cutroom: удаление. Ваши проекты (папки edit\ рядом с видео) и настройки в %APPDATA%\Cutroom не удаляются.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$exe = Join-Path $root "app\Cutroom.exe"
if (Get-Process Cutroom -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) { throw "Закройте Cutroom и запустите удаление снова." }
$answer = Read-Host "Удалить Cutroom из $root вместе со скачанными модулями? (да/нет)"
if ($answer -notmatch "^(да|д|y|yes)$") { Write-Host "Отменено."; exit 0 }
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
  Remove-Item (Join-Path $dir "Cutroom.lnk") -Force -ErrorAction SilentlyContinue
}
Set-Location $env:TEMP
Remove-Item $root -Recurse -Force
Write-Host "Cutroom удалён. Проекты и настройки сохранены."
