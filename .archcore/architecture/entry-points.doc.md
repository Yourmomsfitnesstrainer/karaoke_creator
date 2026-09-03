---
title: "Точки входа"
status: accepted
tags:
  - "architecture"
  - "entry-points"
---

2 точки входа: CLI и локальный HTTP-интерфейс.

## CLI
- `karaoke-gen` — console script из `@pyproject.toml`; диспетчер в `@src/karaoke_generator/cli.py`.

## HTTP
- `@src/karaoke_generator/web.py` — FastAPI-приложение: `GET /`, `POST /generate`, `GET /jobs/{job_id}/{filename}`.