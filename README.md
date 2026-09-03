# Karaoke Creator

Локальный pipeline `audio + точный lyrics.txt → word timings → ASS → karaoke.mp4`. Распознавание используется только как источник таймингов: отображаемые слова всегда берутся из пользовательского TXT.

## Что уже работает

- MP3/WAV/FLAC/M4A/AAC/OGG через FFmpeg;
- word timestamps через `faster-whisper` или опциональный WhisperX;
- глобальное монотонное сопоставление, поэтому повторяющиеся строки получают разные времена;
- интерполяция нераспознанных слов с явной отметкой в JSON;
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

`ffmpeg-full` keg-only и не ломает обычный `ffmpeg`; приложение само ищет `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`. Первая генерация скачает выбранную Whisper-модель. Стандартная `small` — компромисс скорости и качества; для сложного вокала можно выбрать `large-v3`.

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

Docker удобен для воспроизводимости, но на M3 нативный запуск обычно предпочтительнее. Базовый контейнер включает Debian FFmpeg с libass и `faster-whisper`; Demucs намеренно остаётся отдельной тяжёлой опцией.

## Выходные файлы

```text
output/
├── karaoke.mp4
├── karaoke.ass
├── alignment.json
├── processed_lyrics.txt
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

