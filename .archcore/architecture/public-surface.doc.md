---
title: "Публичная поверхность"
status: accepted
tags:
  - "architecture"
  - "surface"
---

Surface: каталог CLI-команд · 4 команды.

## CLI
- `generate` — запускает полный pipeline из аудио и точного TXT.
- `render` — повторно рендерит MP4 из отредактированного `alignment.json`.
- `doctor` — проверяет Python, архитектуру, FFmpeg/libass, faster-whisper и Demucs.
- `web` — запускает локальный FastAPI UI через Uvicorn.

Короткая форма с `--audio` без подкоманды маршрутизируется в `generate`; определения находятся в `@src/karaoke_generator/cli.py`.