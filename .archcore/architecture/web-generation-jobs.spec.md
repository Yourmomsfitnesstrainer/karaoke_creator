---
title: "Контракт фоновой Web-генерации"
status: draft
tags:
  - "jobs"
  - "progress"
  - "spec"
  - "web"
---

## Purpose & Scope
Спецификация задаёт контракт фоновой Web-генерации karaoke. Браузерный UI и локальные HTTP-клиенты зависят от состояний job, процентов и ссылок.
За пределами: сохранение очереди между перезапусками, несколько процессов Uvicorn и удалённый хостинг.

## Surface
- HTTP-приложение, хранилище job и HTML: `@src/karaoke_generator/web.py`.
- Отчёт стадий pipeline: `@src/karaoke_generator/pipeline.py`.
- Проверки Web-контракта: `@tests/test_web.py`.
- Точки входа: `GET /`, `POST /api/jobs`, `GET /api/jobs/{job_id}`, `POST /generate`, `GET /jobs/{job_id}/{filename}`.
- Состояния job: `queued`, `running`, `complete`, `failed`.
- Поля статуса: `id`, `status`, `progress`, `stage`, `detail`, `artifacts`, опциональный `error`.

## Normative Behavior
1. WHEN `POST /api/jobs` получает допустимые файлы, Web API MUST вернуть `202` с `job_id` и `status_url`.
2. WHEN Web API принимает job, Web API MUST запустить генерацию после формирования HTTP-ответа.
3. WHILE job выполняется, Web API MUST публиковать монотонный целый `progress` от 0 до 100.
4. WHEN стадия pipeline начинается или завершается, pipeline MUST передать процент границы и название через callback.
5. WHEN клиент запрашивает job, Web API MUST вернуть текущий снимок его состояния.
6. WHEN job завершается, Web API MUST установить `complete`, `progress=100` и ссылки на три итоговых артефакта.
7. WHEN браузер отправляет форму, Web UI MUST создать job без полной перезагрузки страницы.
8. WHILE job ожидает или выполняется, Web UI MUST показывать процент, название стадии и индикатор выполнения.
9. WHILE job ожидает или выполняется, Web UI MUST блокировать повторное нажатие кнопки генерации.
10. WHEN job завершается, Web UI MUST показать ссылки на артефакты и встроенное видео.
11. WHEN пользователь вводит `English` или `Russian`, Web API MUST преобразовать значение в `en` или `ru`.
12. WHEN пользователь окружает язык пробелами, Web API MUST удалить пробелы до валидации.
13. WHILE индикатор видим, Web UI MUST обновлять `aria-valuenow` текущим процентом.

## Constraints & Invariants
- Invariant: `progress` MUST оставаться в диапазоне 0–100 и не уменьшаться.
- Invariant: Web API MUST защищать чтение и изменение `JOBS` одним lock.
- Constraint: Web API MUST хранить job в памяти процесса, потому что MVP работает локально с одним Uvicorn-процессом.
- Constraint: Web API MUST сохранять входы и результаты под системным каталогом временных файлов.
- Constraint: endpoint артефактов MUST разрешать только `karaoke.mp4`, `karaoke.ass` и `alignment.json`.
- Constraint: синхронный `POST /generate` MUST оставаться fallback для клиента без JavaScript.

## Failure Behavior
1. IF формат аудио, режим или язык недопустим, THEN Web API MUST вернуть `400` до постановки job в очередь.
2. IF `job_id` отсутствует или имеет неверный формат, THEN Web API MUST вернуть `404`.
3. IF конфигурация или pipeline завершается исключением, THEN Web API MUST установить `failed` и сохранить текст ошибки.
4. WHEN Web UI получает `failed`, Web UI MUST показать ошибку и снова включить кнопку.
5. IF запрошенный артефакт отсутствует, THEN Web API MUST вернуть `404`.
6. WHEN процесс перезапускается, Web API MAY потерять состояния job, потому что постоянное хранилище находится за пределами MVP.

## Conformance
Реализация соответствует контракту, когда выполняет требования 1–13, сохраняет инварианты и обрабатывает перечисленные ошибки.
Проверка включает `@tests/test_web.py` и live HTTP-сценарий с наблюдаемыми границами 0%, 20%, 40%, 80% и 100%.