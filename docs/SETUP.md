# Настройка OLX Hunter

## 1. Подготовка

Нужны Git, Docker с Compose plugin и OpenSSL. `./scripts/setup` создаёт `.env` с правами `0600`, генерирует `SESSION_SECRET` и пароль PostgreSQL, а также рабочие каталоги.

### Docker Engine на Linux

Проверьте установку:

```bash
docker --version
docker compose version
```

Если команды уже работают, устанавливать Docker повторно не нужно. Запустите демон и добавьте своего пользователя в группу Docker:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

После этого выйдите из системы и войдите снова (или выполните `newgrp docker`), затем проверьте доступ:

```bash
docker info
```

Если Docker ещё не установлен, на Ubuntu/Debian удобнее использовать официальный установочный скрипт Docker, после чего выполнить команды выше:

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Группа `docker` даёт пользователю права управления Docker Engine, поэтому добавляйте только доверенные учётные записи.

В `.env` владелец заполняет внешние значения:

- `TELEGRAM_BOT_TOKEN` — токен от BotFather;
- `OWNER_TELEGRAM_ID` — числовой ID единственного владельца;
- `TELEGRAM_INIT_DATA_MAX_AGE_SECONDS` — допустимый возраст Telegram `initData` в секундах, обычно `3600`;
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

Если прямой HTTP-доступ получает `403`, можно включить браузерный источник: `SOURCE_BROWSER_ENABLED=true`. Он использует Chromium в отдельном browser worker-процессе и всё равно возвращает `blocked`, если OLX показывает CAPTCHA или страницу проверки. Автоматический обход CAPTCHA не выполняется.

OpenRouter без ключа выключен. При включении UI получает актуальный список моделей и цены через API; модель должна поддерживать structured outputs. Модель и тариф не зафиксированы навсегда в исходном коде.

## 5. Локальные панели управления

Compose включает Portainer для управления Docker. После запуска откройте на этом компьютере:

```text
http://127.0.0.1:9000
```

При первом входе создайте локального администратора и выберите окружение `local`. Панель привязана к localhost и не публикуется через Telegram-туннель.

У запущенного контейнера ngrok есть инспектор запросов:

```text
http://127.0.0.1:4040
```

Там видны текущий публичный URL, запросы и ответы туннеля. Журналы ngrok можно смотреть командой `docker logs -f olx-hunter-ngrok`.
