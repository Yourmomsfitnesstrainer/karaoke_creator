# Karaoke Creator

Локальный pipeline `оригинальный MP3 + точный lyrics.txt → распознавание вокала → сравнение ASR с lyrics → ASS → karaoke.mp4`. Слова и тайминги сначала извлекаются из песни, затем глобально сопоставляются с TXT: отображаемый текст всегда остаётся пользовательским.

## Что уже работает

- MP3/WAV/FLAC/M4A/AAC/OGG через FFmpeg;
- базовая сетка распознанных слов через `faster-whisper` и уточнение доступных границ через WhisperX без потери ASR-слов;
- автоматическая очистка секционных заголовков и типового рекламного блока из скопированных lyrics без переписывания исполняемых строк;
- очищенный текст используется как `initial_prompt`, но display-слова по-прежнему берутся только из TXT;
- мягкий настраиваемый VAD и ручной сдвиг подсветки от −1000 до +1000 мс;
- глобальное монотонное сопоставление в стиле usersync, включая `никогда ↔ ни когда`, поэтому повторяющиеся строки получают разные времена;
- интерполяция нераспознанных слов с явной отметкой в JSON;
- диагностические `asr_text`, `match_similarity` и сводка качества в `alignment.json`;
- Demucs как опциональный separator с безопасным fallback на оригинал;
- ASS `\kf`, текущая + следующая строка, перенос длинных строк;
- procedural/solid/image/video background;
- H.264/AAC MP4, CLI, локальный Web UI, кеш и повторный render без ML;
- Apple Silicon без обязательной CUDA.

Полная подтверждённая спецификация: [docs/TZ.md](docs/TZ.md).

## Установка на macOS

```bash
brew install ffmpeg-full
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[ml,web,dev]'
```

`ffmpeg-full` keg-only и не ломает обычный `ffmpeg`; приложение само ищет `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`. Первая генерация скачает выбранную Whisper-модель и фонемную модель WhisperX для языка песни. Для русского по умолчанию выбрана компактная `bond005/wav2vec2-base-ru`; модель можно заменить через `alignment.align_models`. Стандартная ASR-модель `small` — компромисс скорости и качества; для сложного вокала можно выбрать `medium` или `large-v3`.

Для отделения вокала:

```bash
pip install -e '.[separation]'
```

Demucs на Apple Silicon запускается на CPU. Если он не установлен или падает в режиме `enabled: auto`, pipeline продолжит работу по оригиналу и честно укажет фактический `audio_mode` в `work/metadata.json`.

## CLI

```bash
karaoke-gen \
  --audio ./song.mp3 \
  --lyrics ./lyrics.txt \
  --output ./output \
  --language en \
  --backend whisperx \
  --timing-offset-ms -250 \
  --audio-mode instrumental
```

Расширенная форма эквивалентна: `karaoke-gen generate ...`.

Другой фон:

```bash
karaoke-gen generate ... --background ./background.jpg
karaoke-gen generate ... --background ./loop.mp4
karaoke-gen generate ... --background solid
```

После ручной правки таймингов:

```bash
karaoke-gen render \
  --alignment output/alignment.json \
  --audio output/instrumental.wav \
  --output output/karaoke-edited.mp4
```

Диагностика и Web UI:

```bash
karaoke-gen doctor
karaoke-gen web --port 8080
```

Откройте <http://127.0.0.1:8080>.

После запуска генерации страница показывает реальный прогресс пяти стадий в процентах,
не перезагружается и по завершении выводит ссылки на артефакты и встроенное видео.
При ошибке её текст появляется в том же блоке прогресса.
В форме можно выбрать WhisperX/faster-whisper, размер модели, включить или отключить
мягкий VAD и сдвинуть подсветку в диапазоне от −1000 до +1000 мс.

## Быстрый smoke demo без ML-модели

Этот тест проверяет весь visual/render pipeline на синтетическом тоне и явно выбранных равномерных таймингах:

```bash
./scripts/make_demo.sh
ffprobe demo/result/karaoke.mp4
```

Backend `uniform` предназначен только для demo/test и никогда не включается автоматически.

## Docker

```bash
docker compose up --build
```

Docker удобен для воспроизводимости, но на M3 нативный запуск обычно предпочтительнее. Базовый контейнер включает Debian FFmpeg с libass, `faster-whisper` и WhisperX; Demucs намеренно остаётся отдельной тяжёлой опцией.

## Выходные файлы

```text
output/
├── karaoke.mp4
├── karaoke.ass
├── alignment.json
├── processed_lyrics.txt
├── lyrics_cleanup.json
├── vocals.wav
├── instrumental.wav       # если separation успешен
└── work/
    ├── source.wav
    └── metadata.json
```

## Ограничения и права

Адлибы, перекрывающиеся голоса и отличающийся от записи текст могут потребовать ручной правки `alignment.json`. Инструмент не скачивает музыку или lyrics и не предоставляет каталог: используйте только файлы, которые вправе обрабатывать.

## Источники технических решений

- [WhisperX](https://github.com/m-bain/whisperX) — ASR + wav2vec2 word alignment.
- [usersync](https://github.com/iamjrmh/usersync) — проверенный паттерн mapping пользовательских lyrics на timestamp grid.
- [Demucs](https://github.com/facebookresearch/demucs) — two-stem vocal separation.
- [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html) — libass `ass` filter.
