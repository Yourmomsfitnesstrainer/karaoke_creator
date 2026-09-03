---
title: "Контракт генерационного pipeline"
status: draft
tags:
  - "pipeline"
  - "spec"
---

## Purpose & Scope
Спецификация определяет оркестрацию полного пути `audio + lyrics → artifacts`. Её потребляют CLI и локальный Web UI.
За пределами: внутренние алгоритмы alignment, ASS и FFmpeg-фильтры.

## Surface
- Полная генерация: `generate` в `@src/karaoke_generator/pipeline.py`.
- Повторный рендер: `rerender` в `@src/karaoke_generator/pipeline.py`.
- Конфигурационные секции: `alignment`, `separation`, `video`, `karaoke`, `output` в `@config.yaml`.

## Normative Behavior
1. WHEN вызывается `generate`, pipeline MUST выполнить подготовку, separation, alignment, ASS и MP4 в пяти наблюдаемых стадиях.
2. WHEN пользовательский TXT прочитан, pipeline MUST записать `processed_lyrics.txt` из его нормализованной структуры.
3. WHEN ключ входа совпадает, pipeline MAY использовать сохранённые audio, separation и alignment artifacts.
4. IF Demucs недоступен в режиме `auto`, THEN pipeline MUST продолжить с исходным аудио.
5. WHEN запрошен instrumental без готового stem, pipeline MUST рендерить исходное аудио и записать фактический режим.
6. WHEN `rerender` вызывается, pipeline MUST пропустить ML и создать ASS вместе с MP4.

## Constraints & Invariants
- The cache key MUST включать SHA-256 аудио, SHA-256 текста и соответствующую конфигурацию.
- The pipeline MUST публиковать JSON через временный файл и атомарную замену.
- The result MUST включать video, subtitles, alignment, processed lyrics и vocals.
- The metadata MUST различать requested и actual audio mode.
- The alignment source MUST быть вокальным stem после успешного separation.

## Failure Behavior
1. IF входной audio отсутствует, THEN pipeline MUST завершиться с `FileNotFoundError`.
2. IF входной lyrics отсутствует, THEN pipeline MUST завершиться с `FileNotFoundError`.
3. IF separation включён строго и завершается ошибкой, THEN pipeline MUST передать ошибку вызывающему коду.
4. IF любая стадия завершается ошибкой, THEN stage logger MUST записать номер, название и длительность.

## Conformance
Реализация соответствует контракту, когда сохраняет порядок стадий, артефакты, ключи кеша и правила деградации.
Проверка выполняется unit-тестами и smoke-сценариями из `@README.md`.