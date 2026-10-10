# OLX Hunter

Личный Telegram-бот и Mini App для наблюдения за выдачами OLX.ua. Сервис импортирует URL, применяет цену и текстовые правила к полному описанию, хранит находки и фото, сообщает о новых объявлениях и снижении цены. OpenRouter включается отдельно на уровне поиска.

## Быстрый локальный запуск

```bash
./scripts/setup
docker compose up --build gateway api worker scheduler postgres redis
```

Откройте `http://localhost`. В development-режиме интерфейс использует явно включённую локальную авторизацию. Сервис `bot` запускайте после заполнения `TELEGRAM_BOT_TOKEN` и `OWNER_TELEGRAM_ID`:

```bash
docker compose up -d bot
```

Проверки без внешних ключей:

```bash
cd backend && uv sync --frozen && uv run pytest -q && uv run ruff check app tests migrations
cd ../frontend && npm ci && npm run typecheck && npm test && npm run build
```

Живой HTTP-сбор включается только через `SOURCE_HTTP_ENABLED=true`. Код сетевого адаптера проверен контролируемыми ответами, но доступность OLX с конкретного сервера и длительный пилот этим не подтверждены.

- [Настройка](docs/SETUP.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Эксплуатация и бэкапы](docs/OPERATIONS.md)
- [Текущий прогресс и ограничения](docs/PROGRESS.md)
