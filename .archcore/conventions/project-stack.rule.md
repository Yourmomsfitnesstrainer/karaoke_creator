---
title: "Стек проекта"
status: accepted
tags:
  - "conventions"
  - "stack"
---

Код пишется на Python 3.10–3.13.
Используйте FFmpeg с libass для подготовки аудио и рендеринга видео.
Используйте faster-whisper как основной источник временной сетки слов.
Сохраняйте WhisperX, Demucs и FastAPI опциональными зависимостями.
Тестируйте поведение через pytest.