---
title: "Контракт выравнивания текста"
status: draft
tags:
  - "alignment"
  - "spec"
---

## Purpose & Scope
Спецификация определяет привязку пользовательского текста к временной сетке ASR. Результат потребляют pipeline, генератор ASS и ручной редактор JSON.
За пределами: распознавание аудио внутри выбранного backend и визуальное оформление субтитров.

## Surface
- Backend-контракт `TimingBackend` и реализации: `@src/karaoke_generator/alignment.py`.
- Модели входа и результата: `@src/karaoke_generator/models.py`.
- Нормализация сравнительных токенов: `@src/karaoke_generator/lyrics.py`.

## Normative Behavior
1. WHEN ASR возвращает слова, aligner MUST сопоставить их с пользовательскими токенами глобально и монотонно.
2. WHEN совпадение принято, aligner MUST сохранить исходный пользовательский текст в `AlignedWord.text`.
3. IF токен не достигает порога сходства, THEN aligner MUST интерполировать его интервал между известными соседями.
4. WHEN строки повторяются, aligner MUST назначить каждому вхождению отдельную возрастающую позицию.
5. WHEN результат сериализуется, aligner MUST округлить временные метки до миллисекунд.
6. The backend factory MUST поддерживать `faster-whisper`, `whisperx` и тестовый `uniform`.

## Constraints & Invariants
- The mapping MUST NOT уменьшать индекс ASR между соседними пользовательскими токенами.
- The result MUST сохранять порядок строк и слов из `LyricsDocument`.
- Every emitted interval MUST иметь положительную длительность не менее 0,01 секунды.
- `min_similarity` MAY изменяться конфигурацией; значение по умолчанию равно 0,62.
- Интерполированное слово MUST иметь `aligned=false`, confidence `0.0` и source `interpolated`.

## Failure Behavior
1. IF ни один пользовательский токен не совпал, THEN aligner MUST завершиться с `RuntimeError`.
2. IF выбранный backend не установлен, THEN backend MUST сообщить команду установки соответствующего extra.
3. IF имя backend неизвестно, THEN factory MUST завершиться с `ValueError`.

## Conformance
Реализация соответствует контракту, когда выполняет все требования и инварианты.
Проверки повторов и интерполяции находятся в `@tests/test_alignment.py`.