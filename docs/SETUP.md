# Настройка OLX Hunter

## 1. Подготовка

Нужны Git, Docker с Compose plugin и OpenSSL. `./scripts/setup` создаёт `.env` с правами `0600`, генерирует `SESSION_SECRET` и пароль PostgreSQL, а также рабочие каталоги.

В `.env` владелец заполняет внешние значения:

- `TELEGRAM_BOT_TOKEN` — токен от BotFather;
- `OWNER_TELEGRAM_ID` — числовой ID единственного владельца;
- `PUBLIC_BASE_URL` — публичный HTTPS URL приложения;
- `APP_DOMAIN` — домен для автоматического TLS Caddy, например `hunter.example.com`;
- `OPENROUTER_API_KEY` — только если нужен ИИ.

В production установите `APP_ENV=production`, `DEV_AUTH_ENABLED=false`, корректный `CORS_ORIGINS` и оставьте секреты только в `.env`. Для локальной разработки допустимы `APP_DOMAIN=http://localhost` и `DEV_AUTH_ENABLED=true`.

## 2. Запуск

Без Telegram-реквизитов:

```bash
docker compose up --build gateway api worker scheduler postgres redis
```

После заполнения Telegram-реквизитов:

```bash
docker compose up --build -d
docker compose ps
curl -fsS https://YOUR_DOMAIN/api/v1/health
curl -fsS https://YOUR_DOMAIN/api/v1/ready
```

API применяет Alembic-миграции перед стартом. `/health` проверяет процесс API; `/ready` отдельно проверяет PostgreSQL и Redis. Недоступный OLX не делает весь API неготовым.

## 3. BotFather

Создайте бота, задайте команды `/start`, `/app`, `/help`, включите Main Mini App и укажите тот же `PUBLIC_BASE_URL`. Сервер проверяет raw `Telegram.WebApp.initData`; `initDataUnsafe` не используется как источник доверия.

## 4. Источник и ИИ

`SOURCE_HTTP_ENABLED=false` оставляет мониторинг в честном состоянии `not_configured`. После проверки условий доступа с сервера включите флаг и наблюдайте scan status. `403`, `429`, CAPTCHA, timeout и `5xx` не считаются удалением объявления.

OpenRouter без ключа выключен. При включении UI получает актуальный список моделей и цены через API; модель должна поддерживать structured outputs. Модель и тариф не зафиксированы навсегда в исходном коде.
