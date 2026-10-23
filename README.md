# jobwatch

Собирает вакансии из нескольких источников, оценивает по ключевым словам, кладёт в RabbitMQ.
Telegram-бот выдаёт **по одной** (лучшие первыми), пока не нажмёшь ✅ / ❌ — следующая не придёт.

```
habr / remoteok / hh / telegram-каналы   (collector, каждые 15 мин)
        │  score по config.yaml; дубли режет SQLite (source, ext_id)
        ▼
  score ≥ threshold ──► RabbitMQ "vacancies" (priority = score, prefetch=1) ──► bot ──► ты ✅/❌
  score <  threshold ─► SQLite status=skipped (не теряется: /skipped, /promote)
```

Всё состояние — `data/jobwatch.db` (SQLite): статус, оценка, когда решил. История не живёт в чате.

## Запуск (сервер)

```bash
cp .env.example .env        # TELEGRAM_BOT_TOKEN, OWNER_CHAT_ID, RABBITMQ_PASS
docker compose up -d --build
docker compose logs -f collector bot
```

Habr и RemoteOK работают сразу. Остальное включается по мере готовности:

- **hh.ru** — зарегистрируй app на dev.hh.ru, впиши `HH_CLIENT_ID/SECRET` в `.env` (анонимный API отдаёт 403).
  Код написан по документации, вживую не проверялся.
- **Telegram-каналы** — `TG_API_ID/HASH` с my.telegram.org, каналы в `telegram_channels` в `config.yaml`, один раз:
  `docker compose run --rm -it collector python -m jobwatch.sources.telegram` (код из SMS). Лучше отдельный аккаунт.
  Вживую не проверялся.

RabbitMQ UI: `ssh -L 16000:localhost:16000 server` → http://localhost:16000 (user `jobwatch`).

## Команды бота

`/stats` · `/liked` — последние ✅ · `/skipped` — отсеянные, лучшие сверху · `/promote <id>` — вернуть отсеянную в очередь

## Как не потерять нужное

Ключевые слова **не вырезают**, а начисляют баллы (в заголовке ×2, минусы понижают). Ниже порога вакансия лежит
в базе. Если в `/skipped` видишь нужную — подними вес слова или опусти `threshold` в `config.yaml` (подхватится
в следующем цикле), а саму вакансию верни `/promote`.

## Разработка

```bash
uv sync && uv run pytest
```

## Не сделано (осознанно)

- Описание Habr тянется только для новых вакансий с баллом ≥ threshold/2 по заголовку и навыкам; остальные оцениваются без описания.
- Оценка через LLM вместо слов — точка расширения в `collect.run_once` (там, где вызывается `score`).
- MCP/доступ с локального Claude к `data/jobwatch.db` — отдельным шагом.
- Telegram-курсор двигается до записи в базу: падение ровно между ними теряет пачку постов.
