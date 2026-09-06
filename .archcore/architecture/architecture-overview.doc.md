---
title: "Обзор архитектуры"
status: accepted
tags:
  - "architecture"
  - "architecture-overview"
---

## Overview

Python; FFmpeg/libass + faster-whisper + WhisperX; pytest

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

Перенос JSON → ASS → MP4 использует абсолютные границы в @src/karaoke_generator/subtitles.py. Происхождение таймингов хранится в schema 3. Раздельные ASR/refinement-кеши находятся в @src/karaoke_generator/timing_cache.py; метрики акустической приёмки — в @src/karaoke_generator/evaluation.py. Проверка синтетического рендера не доказывает акустическую точность на песнях.