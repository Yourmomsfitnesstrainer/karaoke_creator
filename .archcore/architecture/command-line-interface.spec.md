---
title: "Контракт командной строки"
status: draft
tags:
  - "cli"
  - "spec"
---

## Purpose & Scope
Спецификация определяет пользовательский контракт `karaoke-gen`. Его потребляют локальные пользователи, shell-скрипты и Docker.
За пределами: алгоритмы генерации и HTML локального Web UI.

## Surface
- Console script: `@pyproject.toml`.
- Парсер, overrides и dispatch: `@src/karaoke_generator/cli.py`.
- Команды: `generate`, `render`, `doctor`, `web`.

## Normative Behavior
1. WHEN argv содержит `--audio` без подкоманды, CLI MUST вставить `generate`.
2. WHEN вызывается `generate`, CLI MUST требовать пути audio, lyrics и output.
3. WHEN переданы overrides, CLI MUST применить их поверх YAML-конфигурации.
4. WHEN передан `--vad` или `--no-vad`, CLI MUST включить или отключить VAD.
5. WHEN generate или render получает `--timing-offset-ms`, CLI MUST применить override поверх YAML.
6. WHEN вызывается `render`, CLI MUST создать видео без запуска alignment backend.
7. WHEN вызывается `doctor`, CLI MUST вывести состояние FFmpeg/libass, faster-whisper, WhisperX и Demucs.
8. WHEN вызывается `web`, CLI MUST запускать Uvicorn на выбранных host и port.
9. WHEN генерация завершена, CLI MUST вывести имена и пути созданных artifacts.

## Constraints & Invariants
- The CLI MUST принимать audio mode только из `original` и `instrumental`.
- The CLI MUST принимать backend только из `faster-whisper`, `whisperx` и `uniform`.
- Timing offset MUST находиться в диапазоне −1000…+1000 мс.
- Default новых запусков равен 0 мс. Явные значения в существующем YAML сохраняются.
- `--config` перед подкомандой render не переключает CLI в generate.
- The resolution MUST иметь форму `<width>x<height>`.
- The default web binding MUST быть `127.0.0.1:8080`.

## Failure Behavior
1. IF resolution не разбирается, THEN CLI MUST выдать ошибку с сообщением о формате `1920x1080`.
2. IF timing offset выходит за диапазон, THEN CLI MUST выдать ошибку с понятным сообщением.
3. IF web extra отсутствует, THEN CLI MUST вывести команду установки `.[web]`.
4. IF обязательный аргумент отсутствует, THEN argparse MUST завершить команду с ненулевым кодом.

## Conformance
Проверка парсинга и overrides находится в `@tests/test_cli.py`.