---
title: "Локальный запуск проекта"
status: accepted
tags:
  - "onboarding"
---

## Установка
```sh
brew install ffmpeg-full
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[ml,web,dev]'
```
## Локальный запуск
```sh
karaoke-gen web --port 8080
```
## Smoke test
```sh
./scripts/make_demo.sh
```