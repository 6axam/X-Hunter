# Эксплуатация

## Диагностика

```bash
docker compose ps
docker compose logs --tail=200 api worker scheduler bot gateway
curl -fsS http://localhost/api/v1/health
curl -fsS http://localhost/api/v1/ready
docker compose run --rm api alembic upgrade head
```

`source_http_403`, `source_http_429`, `source_network_error` и `source_server_error` требуют backoff и не подтверждают снятие. `listing_removed` считается сигналом только для прямой ссылки; удаление выполняется после второго подтверждения.

Для аудита отдельного ИИ-исследования скачайте ZIP кнопкой «Экспорт диагностики» в Mini App. Endpoint `GET /api/v1/research-sessions/{id}/diagnostics.zip` доступен только владельцу сессии и отдаётся с `Cache-Control: private, no-store`. События старше `RESEARCH_TRACE_RETENTION_DAYS` удаляются ежедневной задачей scheduler.

## Резервные копии

```bash
./scripts/backup
./scripts/test-restore backups/database-YYYYMMDDTHHMMSSZ.dump
./scripts/restore backups/database-YYYYMMDDTHHMMSSZ.dump
```

`backup` сохраняет custom dump PostgreSQL и архив медиа в `backups/`, удаляя локальные копии старше 14 дней. `test-restore` разворачивает dump в отдельную чистую базу и проверяет Alembic revision. `restore` очищает и заменяет рабочую базу, поэтому запускается оператором с выбранным dump.

Копии на том же сервере не защищают от потери диска. Настройте отдельное шифрованное внешнее копирование `backups/`; проект не объявляет его выполненным автоматически.

Временные CSV/JSON-экспорты и их записи автоматически удаляются Celery-задачей после 24 часов.

## Обновление

```bash
./scripts/backup
git pull --ff-only
docker compose build
docker compose up -d
docker compose ps
```

После запуска проверьте `/ready`, логи миграции и один ручной scan. Рабочие `.env`, базы, экспорты и медиа не коммитятся.
