---
title: "Обзор архитектуры"
status: accepted
tags:
  - "architecture"
  - "architecture-overview"
---

5 modules; Python; FFmpeg/libass + faster-whisper + WhisperX; pytest

| Area | Type | Covers |
|---|---|---|
| Stack | rule | язык, media/alignment stack, тестовый runner |
| Running locally | guide | установка, локальный запуск, smoke test |
| Entry points | doc | CLI и HTTP |
| Public surface | doc | каталог команд |
| Hotspot: alignment | spec | mapping точного текста на временную сетку |
| Hotspot: audio | spec | FFmpeg discovery и подготовка WAV |
| Hotspot: pipeline | spec | стадии, cache и artifacts |
| Hotspot: CLI | spec | аргументы, overrides и dispatch |

Ranked hotspots not yet specced (run /archcore:document to document):
- subtitles: `@src/karaoke_generator/subtitles.py` — 114 LOC, 29 LOC tests → /archcore:document src/karaoke_generator/subtitles.py