# Cutroom

Десктоп-студия монтажа для Windows поверх скилла [video-use](https://github.com/browser-use/video-use) и ffmpeg. Расшифровка локальная (faster-whisper / GigaAM), без облака и ключей.

## Запуск

Ярлык **Cutroom** на рабочем столе или в меню «Пуск» → `release\win-unpacked\Cutroom.exe`. Окно открывается сразу, бэкенд и связь с Claude Code поднимаются сами (индикатор «Claude» в шапке; при отсутствии входа Cutroom один раз откроет `claude auth login`).

Пересобрать `.exe` и ярлыки после изменений:

```bash
npm run dist
```

```bash
npm run shortcut
```

`Cutroom.exe` — оболочка над этой папкой: бэкенд берётся из `backend/.venv` (путь записан в `resources/cutroom-home.txt`, переопределяется переменной `CUTROOM_HOME`). Переносимый установщик со встроенным бэкендом — этап 9.

## Запуск (разработка)

```bash
cd D:/Video/cutroom/backend && uv sync
```

```bash
cd D:/Video/cutroom && npm install && npm run dev
```

`npm run dev` поднимает Vite и окно Electron; Electron сам запускает бэкенд на свободном порту со случайным токеном и гасит его вместе со всеми ffmpeg при закрытии.

Для самопроверки в браузере: `python -m cutroom.server --port 8765 --token dev` в `backend/` и `npm run dev:web`, затем http://localhost:5173.

Статус этапов и архитектура — [PLAN.md](PLAN.md). Аудит чужого кода — [SECURITY_AUDIT.md](SECURITY_AUDIT.md).
