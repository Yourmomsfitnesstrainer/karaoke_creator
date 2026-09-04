---
title: "Контракт выравнивания текста"
status: draft
tags:
  - "alignment"
  - "spec"
---

## Purpose & Scope
Спецификация определяет очистку пользовательского lyrics и recognition-first привязку точного текста к временной сетке слов, распознанных в оригинальной песне. Результат потребляют pipeline, генератор ASS и повторный рендер из JSON.

Продукт получает от пользователя только оригинальный MP3 и `lyrics.txt`. Получение музыки или текста из внешних источников находится за пределами продукта.

## Surface
- Очистка, нормализация и сравнительные токены: `@src/karaoke_generator/lyrics.py`.
- Backend-контракт, ASR-реализации и usersync-style sequence alignment: `@src/karaoke_generator/alignment.py`.
- Модели `LyricsDocument`, `RemovedLyricsLine`, `TimedWord`, `SequenceMatch`, `AlignedWord`, `AlignmentQuality` и `AlignmentResult`: `@src/karaoke_generator/models.py`.
- Оркестрация ASR и публикация diagnostics: `@src/karaoke_generator/pipeline.py`.

## Normative Behavior
1. WHEN lyrics читается, parser MUST исключить одиночные секционные заголовки в квадратных скобках.
2. WHEN parser встречает известный рекламный marker, parser MUST исключить marker и последующие рекомендации до следующего секционного заголовка.
3. WHEN строка не является известной метаданной, parser MUST сохранить её независимо от языка.
4. WHEN очистка завершена, pipeline MUST сохранить `processed_lyrics.txt` и `lyrics_cleanup.json` с исходными номерами, текстом и причиной удаления.
5. WHEN generate обрабатывает песню, выбранный backend MUST сначала получить из аудио последовательность `TimedWord`.
6. WHEN включён lyrics prompt, backend MUST передать очищенные слова распознавателю как подсказку.
7. Lyrics prompt MUST NOT быть источником display-текста или времени без аудиодоказательства.
8. WHEN выбран WhisperX, backend MUST получить базовую сетку `TimedWord` через faster-whisper.
9. WHEN фонемное выравнивание сопоставило ASR-токен, backend MUST заменить его начало и конец значениями WhisperX.
10. WHEN фонемное выравнивание не сопоставило ASR-токен, backend MUST сохранить его исходные времена faster-whisper.
11. WhisperX MUST быть backend по умолчанию.
12. WHEN включён VAD, backend MUST использовать настраиваемый мягкий порог.
13. WHEN VAD выключен, backend MUST анализировать полную временную шкалу.
14. WHEN ASR возвращает слова, aligner MUST глобально и монотонно сопоставить их с токенами `LyricsDocument`.
15. WHEN совпадение принято, aligner MUST сохранить исходный пользовательский текст в `AlignedWord.text`, а распознанный текст — только в `AlignedWord.asr_text`.
16. WHEN соседние токены различаются способом разбиения, aligner MUST сравнивать span одного-двух пользовательских токенов со span одного-двух ASR-токенов.
17. WHEN один ASR-токен соответствует двум пользовательским токенам, aligner MUST разделить его аудиоинтервал между ними с положительной длительностью каждого слова.
18. IF пользовательский токен не достигает порога сходства, THEN aligner MUST интерполировать его интервал между известными соседями.
19. WHEN строки повторяются, aligner MUST назначить каждому вхождению отдельную возрастающую позицию.
20. WHEN результат сериализуется, serializer MUST округлить временные метки до миллисекунд и добавить `AlignmentQuality`.
21. The backend factory MUST поддерживать `whisperx`, `faster-whisper` и явно выбранный тестовый `uniform`.

## Constraints & Invariants
- `lyrics.txt` MUST быть единственным источником исполняемого display-текста в JSON, ASS и MP4.
- Очиститель MUST NOT переписывать слова или удалять обычную строку только из-за языка.
- ASR MUST быть источником временного доказательства; обычный generate MUST NOT равномерно распределять текст без распознавания песни.
- Уточнение WhisperX MUST NOT удалять слова из базовой ASR-сетки или снижать полноту распознанной последовательности.
- The mapping MUST NOT уменьшать индекс ASR между соседними пользовательскими токенами.
- Every emitted interval MUST находиться внутри продолжительности аудио и иметь длительность не менее 0,01 секунды.
- `min_similarity` MAY изменяться конфигурацией; значение по умолчанию равно 0,62.
- Прямое совпадение MUST содержать `asr_text`, `match_similarity`, confidence и source выбранного backend.
- Интерполированное слово MUST иметь `aligned=false`, confidence `0.0`, `asr_text=null`, `match_similarity=null` и source `interpolated`.
- Backend `uniform` MUST использоваться только при явном выборе в demo/test.

## Failure Behavior
1. IF после очистки не осталось слов, THEN parser MUST завершиться понятной ошибкой.
2. IF ни один пользовательский токен не совпал с ASR, THEN aligner MUST выбросить `RuntimeError`.
3. IF длительности аудио недостаточно для положительного интервала каждого слова, THEN aligner MUST выбросить понятную ошибку.
4. IF выбранный backend не установлен, THEN backend MUST сообщить команду установки соответствующего extra.
5. IF имя backend неизвестно, THEN factory MUST выбросить `ValueError`.

## Conformance
Проверки находятся в `@tests/test_lyrics.py` и `@tests/test_alignment.py` и доказывают очистку, сохранение двуязычных строк, передачу lyrics prompt/VAD, неизменность display-слов, span matching, отсутствие равномерного fallback и монотонность времён.