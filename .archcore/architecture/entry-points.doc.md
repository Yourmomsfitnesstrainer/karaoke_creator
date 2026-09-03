---
title: "Точки входа"
status: accepted
tags:
  - "architecture"
  - "entry-points"
---

## Overview
Проект имеет два локальных входа: CLI и HTTP-интерфейс.

## Content
### CLI
- `karaoke-gen` — console script из `@pyproject.toml`; диспетчер в `@src/karaoke_generator/cli.py`.

### HTTP
- `GET /` — форма загрузки и индикатор выполнения.
- `POST /api/jobs` — создаёт фоновую генерацию и возвращает URL статуса.
- `GET /api/jobs/{job_id}` — возвращает состояние, процент, стадию, ошибку или ссылки.
- `POST /generate` — синхронный fallback для клиента без JavaScript.
- `GET /jobs/{job_id}/{filename}` — отдаёт разрешённый итоговый артефакт.
- Реализация HTTP-поверхности находится в `@src/karaoke_generator/web.py`.

## Examples
Локальный Web-вход запускается командой `karaoke-gen web --port 8080`.