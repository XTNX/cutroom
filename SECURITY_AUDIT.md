# Аудит video-use перед подключением к Cutroom

Дата: 2026-11-05. Правила: `D:\sync\02 Система и архитектура\Безопасность агентов.md` §4.

## Что и откуда

| | |
|---|---|
| Репозиторий | https://github.com/browser-use/video-use |
| Зафиксированный коммит | `43cfc566833548093455c9d4a46bd69913b7e552` (2026-10-08, «README: point cloud link to video-use.com waitlist (#222)») |
| Копия в нашем репозитории | `vendor/video-use` (git-клон, коммит выше; обновление — только осознанным `git checkout <sha>` после чтения diff) |
| Установленный скилл | `%USERPROFILE%\.claude\skills\video-use` — **не git-клон** (распакован архивом 2026-10-23). Сверен с `43cfc56` файл в файл (без учёта CRLF): **идентичен**, кроме одного лишнего файла `helpers/transcribe_whisper.py` (whisper.cpp, добавлен в прошлой сессии, нарушает Hard Rule 12 «ничего не писать в video-use/»). Файл не удалён — решение за вами. Cutroom его не использует. |

## Прочитано

Все файлы репозитория: `SKILL.md`, `install.md`, `README.md`, `poster.html`, `pyproject.toml`, `helpers/*.py` (7 шт.), `tests/*.py`, `skills/manim-video/**` (SKILL.md, references, `scripts/setup.sh`).

## Сетевые вызовы

| Где | Что | Статус в Cutroom |
|---|---|---|
| `helpers/transcribe.py:33,102` | `requests.post("https://api.elevenlabs.io/v1/speech-to-text")` — загрузка аудио в ElevenLabs Scribe, ключ из `.env`/`ELEVENLABS_API_KEY` | **Отключено.** Cutroom никогда не запускает `transcribe.py` и `transcribe_batch.py` (последний импортирует первый). `.env` не читается. Замена — `backend/helpers/transcribe_whisper.py` (локально). |
| `poster.html:7-8` | `cdn.tailwindcss.com`, Google Fonts | Статичная промо-страница, Cutroom её не открывает. |
| `SKILL.md:67,214,216` | `npx --yes hyperframes …`, `npx create-video@latest` | Не пиновано. В Cutroom: HyperFrames/Remotion ставятся только с точной версией в папку слота (этап 6), без `--yes` на непиннованные пакеты. Remotion — только после вашего подтверждения лицензии (бесплатно до 3 человек). |
| `skills/manim-video/references/rendering.md:141-144` | примеры `pip install manim-voiceover[elevenlabs|gtts|azure]` | Документация, не исполняется. Облачные TTS не используем. |

Иных сетевых вызовов (`urllib`, `socket`, `curl`, `wget`, `fetch`) в коде нет.

## Прочие проверки

- `curl | bash`, `base64`-блобы, чтение `~/.ssh`, `~/.aws`, скрытые инструкции «игнорируй правила» — **не найдено**.
- `skills/manim-video/scripts/setup.sh` — только проверки `command -v` и `python3 -c "import manim"`; ничего не ставит.
- Подпроцессы: `ffmpeg`/`ffprobe` в `render.py` (14), `grade.py` (4), `timeline_view.py` (3), `transcribe.py` (3). Аргументы — пути и фильтры из EDL; `shell=True` не используется.
- Хардкод ключей — нет (`.env.example` пустой).

## Несовместимости с Windows / нашими требованиями (будут закрыты обёрткой, не правкой vendor)

1. `render.py` всегда масштабирует финал в 1080p (`scale=1920:-2`) — противоречит «финал в исходном разрешении» (этап 8). Решение: обёртка `backend/helpers/render_full.py`, переиспользующая функции `render.py`, с параметром разрешения.
2. Шрифты macOS: `SUB_FORCE_STYLE` — `Helvetica`, `timeline_view.py` — `/System/Library/Fonts/Menlo.ttc`, на Windows молча падает в fallback. Решение: явный путь из `C:\Windows\Fonts` (Arial/Segoe UI/Consolas с кириллицей) и проверка загрузки; шрифт бренда — [NEED].
3. `pack_transcripts.py` читает JSON через `read_text()` без кодировки (cp1251 на русской Windows). Решение: наш хелпер пишет JSON с `ensure_ascii=True`, подпроцессы запускаются с `PYTHONUTF8=1`.

## Наши зависимости

- Python (backend/.venv, uv): fastapi, uvicorn, pydantic, numpy, scipy, faster-whisper 1.2.1, ctranslate2 4.8.5, nvidia-cublas-cu12 12.8.4.1, nvidia-cudnn-cu12 9.x, librosa, matplotlib, pillow, psutil; опционально torch/torchaudio cu128 и gigaam @ `7447938d791c4f3e643386ee22c33777004293a5`.
- npm: точные версии в `package.json` (`save-exact`), без постустановочных скриптов кроме electron.
- Модели: `Systran/faster-whisper-large-v3`, `mobiuslabsgmbh/faster-whisper-large-v3-turbo` с Hugging Face (единственный сетевой доступ Cutroom — разовая загрузка весов по кнопке на экране «Система»).
- Чужих MCP-серверов не добавлено.

## GigaAM (добавлено после установки)

- Пакет `gigaam` @ `7447938d791c4f3e643386ee22c33777004293a5` (GitHub salute-developers/GigaAM), torch/torchaudio 2.11 cu128.
- Сеть: `gigaam/__init__.py` качает веса с `https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM/` и сверяет MD5 по зашитому списку. **На 2026-11-05 у CDN просрочен TLS-сертификат.** Проверку TLS не отключали. Вместо этого Cutroom берёт те же веса с официального зеркала Hugging Face `ai-sage/GigaAM-v3` (ветки `e2e_rnnt`, `rnnt`) и собирает модель классами пакета `gigaam`; `modeling_gigaam.py` с зеркала **не исполняется** (`torch.load(..., weights_only=True)`).
- Длинный режим GigaAM (`transcribe_longform`) требует pyannote и HF-токен — не используем; аудио режется на окна ≤22 с по тихим местам.

## Музыка и внешние ссылки (добавлено 2026-11-05)

- Cutroom не скачивает музыку и не обращается к музыкальным сервисам. Треки человек кладёт в папку `%USERPROFILE%\Music\Cutroom`; анализ (темп, энергия) — локально, librosa.
- Ссылки на библиотеки открываются в системном браузере только для разрешённых хостов (mixkit.co, pixabay.com, studio.youtube.com, freemusicarchive.org, uppbeat.io) — проверка в Electron main (`open-external`).
- «Показать в папке» разрешено только внутри проекта и папки «Музыка» пользователя.
- Агент правки фрагмента вызывается так же, как остальные: без инструментов, данные в `<data>`, ответ по JSON-схеме.
