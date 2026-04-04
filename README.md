# Нейрогазета

Ежедневное профессиональное издание об AI. Собирается автоматически каждый день в 07:00 МСК.

## Структура

```
docs/                   → GitHub Pages
  index.html            → главная (последний выпуск)
  style.css             → единые стили
  app.js                → логика отображения
  archive/index.html    → архив всех выпусков
  editor/index.html     → редактор с паролем
  data/
    latest.json         → актуальный выпуск
    YYYY-MM-DD.json     → выпуск по дате
    index.json          → индекс всех выпусков

scripts/
  collect.py            → сборка через Anthropic API + web_search

.github/workflows/
  daily.yml             → ежедневный запуск в 07:00 МСК
```

## Настройка

### 1. GitHub Secrets

В Settings → Secrets → Actions добавить:

| Секрет | Описание |
|---|---|
| `ANTHROPIC_API_KEY` | Ключ Anthropic API (с доступом к web_search) |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота (опционально) |
| `TELEGRAM_CHAT_ID` | ID чата для уведомлений (опционально) |

### 2. GitHub Pages

В Settings → Pages → Source выбрать `main` / `docs`.

### 3. Пароль редактора

В `docs/editor/index.html` переменная `PASSWORD_HASH` — SHA-256 от пароля.
По умолчанию пароль: `neuro2026`.

Сменить: `echo -n "новый_пароль" | sha256sum`

## Рубрики

| ID | Название | Что входит |
|---|---|---|
| `models` | Модели | Выпуски и обновления языковых моделей |
| `platforms` | Платформы | Инструменты, IDE, API, продукты на AI |
| `industry` | Индустрия | Инвестиции, регуляция, кадры |
| `hype` | Желтуха | Слухи, утечки, неподтверждённое |

## Запуск локально

```bash
pip install anthropic
ANTHROPIC_API_KEY=sk-... python scripts/collect.py
# затем открыть docs/index.html в браузере
```

## Ручной запуск

GitHub Actions → Daily Issue → Run workflow.
