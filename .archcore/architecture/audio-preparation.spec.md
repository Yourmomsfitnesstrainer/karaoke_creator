---
title: "Контракт подготовки аудио"
status: draft
tags:
  - "audio"
  - "spec"
---

## Purpose & Scope
Спецификация определяет поиск FFmpeg, проверку libass, чтение длительности и нормализацию входного аудио. Эти функции потребляют pipeline, renderer и separator.
За пределами: разделение источников, распознавание речи и кодирование финального видео.

## Surface
- Поиск инструментов и запуск процессов: `@src/karaoke_generator/audio.py`.
- Пользовательская переменная выбора бинарника: `KARAOKE_FFMPEG`.
- Ошибка внешнего процесса: `ExternalCommandError`.

## Normative Behavior
1. WHEN требуется FFmpeg, locator MUST проверить `KARAOKE_FFMPEG`, Homebrew ffmpeg-full и системный `PATH` по порядку.
2. WHEN `require_ass=true`, locator MUST вернуть только бинарник с фильтром `ass`.
3. WHEN pipeline готовит аудио, preparer MUST создать stereo PCM S16LE WAV с частотой 44,1 кГц.
4. WHEN преобразование завершено, preparer MUST атомарно заменить целевой WAV временным файлом.
5. WHEN читается длительность, probe MUST использовать соседний `ffprobe` либо системный `PATH`.
6. WHEN внешний процесс возвращает ошибку, runner MUST включить код завершения и последние строки диагностики.

## Constraints & Invariants
- The preparer MUST исключать видеопоток через `-vn`.
- The command runner MUST NOT запускать shell-интерпретацию аргументов.
- The target WAV MUST появляться только после успешного завершения FFmpeg.
- The libass probe MUST анализировать вывод `ffmpeg -filters`.

## Failure Behavior
1. IF FFmpeg отсутствует, THEN locator MUST завершиться с инструкцией установки или настройки `KARAOKE_FFMPEG`.
2. IF libass отсутствует при обязательной проверке, THEN locator MUST отклонить бинарник.
3. IF `ffprobe` отсутствует, THEN probe MUST завершиться с `RuntimeError`.
4. IF длительность не читается, THEN probe MUST завершиться с `ExternalCommandError`.

## Conformance
Реализация соответствует контракту, когда выполняет требования поиска, нормализации и атомарной публикации файла.
Интеграционная проверка выполняется командой `./scripts/make_demo.sh`.