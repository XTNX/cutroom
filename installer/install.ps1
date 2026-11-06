# Cutroom: установка из распакованного архива. Права администратора не нужны.
# Python, модули, ffmpeg и модель речи Cutroom скачает сам при первом запуске.
param(
  [string]$Target = (Join-Path $env:LOCALAPPDATA "Programs\Cutroom"),
  [switch]$NoShortcuts,
  [switch]$NoLaunch
)
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
Write-Host ""
Write-Host "Cutroom — установка" -ForegroundColor Yellow
Write-Host "Папка: $Target"

$exe = Join-Path $Target "app\Cutroom.exe"
$running = Get-Process Cutroom -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }
if ($running) { throw "Cutroom сейчас открыт из этой папки. Закройте его и запустите установку снова." }

New-Item -ItemType Directory -Force -Path $Target | Out-Null
# /E: всё дерево; runtime\ и models\ прошлой установки не трогаются (их нет в архиве)
$skip = @("install.ps1", "Установить Cutroom.cmd")
robocopy $src $Target /E /NFL /NDL /NJH /NJS /NP /XF $skip | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Не удалось скопировать файлы (robocopy $LASTEXITCODE)." }
# файлы из скачанного архива помечены Windows как «из интернета»: снимаем пометку
Get-ChildItem -Path $Target -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

if (-not $NoShortcuts) {
  $shell = New-Object -ComObject WScript.Shell
  foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $s = $shell.CreateShortcut((Join-Path $dir "Cutroom.lnk"))
    $s.TargetPath = $exe
    $s.WorkingDirectory = Split-Path -Parent $exe
    $s.IconLocation = (Join-Path $Target "icon.ico") + ",0"
    $s.Description = "Cutroom — монтаж поверх video-use"
    $s.Save()
  }
  Write-Host "Ярлыки: рабочий стол и меню «Пуск»"
}

# Claude Code нужен агентам монтажа; без него Cutroom запустится, но планы и правки агентом будут недоступны
$claude = @(
  (Get-ChildItem (Join-Path $env:LOCALAPPDATA "Claude-Data") -Recurse -Filter claude.exe -ErrorAction SilentlyContinue | Select-Object -First 1),
  (Get-Item (Join-Path $env:USERPROFILE ".local\bin\claude.exe") -ErrorAction SilentlyContinue),
  (Get-Command claude -ErrorAction SilentlyContinue)
) | Where-Object { $_ } | Select-Object -First 1
if ($claude) { Write-Host "Claude Code: найден" -ForegroundColor Green }
else { Write-Host "Claude Code не найден. Установите приложение Claude для рабочего стола (claude.ai/download) и войдите в аккаунт — Cutroom подключится к нему сам." -ForegroundColor Yellow }

Write-Host ""
Write-Host "Готово. При первом запуске Cutroom скачает модули (несколько гигабайт с видеокартой NVIDIA, около гигабайта без неё)." -ForegroundColor Green
if (-not $NoLaunch) { Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) }
