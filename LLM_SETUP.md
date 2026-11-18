# LLM-оценка вакансий: ключи и запуск

Каждую новую вакансию оценивает LLM (`jobwatch/llm.py`, сервис `scorer`) по твоему профилю `profile.md`.
Провайдеров много, у каждого свои бесплатные лимиты; кончился у одного — работают остальные.
Достаточно одного ключа, чтобы всё заработало; чем больше ключей, тем быстрее разбирается бэклог.

Без ключей всё работает по-старому (ключевые слова и порог).

## 1. Получить ключи

Один аккаунт на провайдера. Ключи никуда не пересылай, только в `.env` на сервере.

| Переменная в `.env` | Где взять | Что сделать |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Войти Google-аккаунтом → **Create API key**. Без карты. Даёт 3 слота: Flash-Lite, Flash, Gemma. |
| `MISTRAL_API_KEY` | https://console.mistral.ai | Регистрация → Billing/Plans → бесплатный план **Experiment** (подтверждение телефоном) → **API Keys → Create**. Самый большой объём, ставь обязательно. |
| `CEREBRAS_API_KEY` | https://cloud.cerebras.ai | Регистрация → **API Keys**. Около 1M токенов в день. |
| `GROQ_API_KEY` | https://console.groq.com/keys | Регистрация → **Create API Key**. Без карты. |
| `NVIDIA_API_KEY` | https://build.nvidia.com | Войти → открыть любую модель → **Get API Key** (ключ начинается с `nvapi-`). |
| `OPENROUTER_API_KEY` | https://openrouter.ai/settings/keys | Регистрация → **Create Key**. Без пополнения 50 запросов в день. |
| `SAMBANOVA_API_KEY` | https://cloud.sambanova.ai | Регистрация → **API Keys**. |
| `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | https://dash.cloudflare.com | AI → **Workers AI** → **Use REST API**: там Account ID и кнопка **Create a Workers AI API Token**. |

Порядок по пользе: Mistral, Gemini, Cerebras, Groq, NVIDIA — первых пяти хватает. Остальные — добавка.

Бесплатные планы (Gemini, Mistral Experiment, часть моделей OpenRouter) могут использовать запросы для обучения.
В запрос уходят публичные тексты вакансий и `profile.md` (имя, город, опыт — без телефона и почты).

## 2. Вписать ключи на сервере

```bash
ssh yeakbay@57.129.40.172
cd ~/jobwatch && git pull
nano .env              # вписать ключи, пустые оставить пустыми
docker compose up -d --build --force-recreate collector scorer bot
```

## 3. Проверить, что каждый слот работает

```bash
docker compose exec scorer python -m jobwatch.llm models
```

Каждому слоту уходит одна настоящая вакансия: `OK` — ключ, адрес и модель рабочие; `FAIL` — ошибка и список моделей
провайдера. Провайдер переименовал модель — выбери id из списка, поправь `model:` в `config.yaml`, `docker compose restart scorer`.
У OpenRouter бесплатные модели (`:free`) меняются часто.

## 4. Калибровка: согласна ли модель с тобой

```bash
docker compose exec scorer python -m jobwatch.llm calibrate              # первый слот с ключом
docker compose exec scorer python -m jobwatch.llm calibrate mistral-small  # конкретный слот
```

Прогоняет вакансии, где ты уже нажал ✅/❌, и печатает рядом твой ответ и вердикт модели (`!!` — расхождение).
Ничего не записывает. Расхождения правятся в `profile.md` (что ищешь, условия, что «не моё») — он перечитывается
при каждом запросе, перезапуск не нужен.

## 5. Отправить на оценку всё накопленное

```bash
docker compose exec scorer python -m jobwatch.llm backfill
```

Все вакансии без вердикта (и старые «в очереди», и отсеянные) уходят в очередь `llm`, новые первыми.
Новые вакансии из сборщика идут вне очереди. Пока бэклог разбирается, бот показывает только то, что модель
уже оценила: сначала 🟢 «подходит», потом 🟡 «возможно». «Нет» уходит в отсеянные (`/skipped`, вернуть — `/promote`).

## 6. Следить

- `/stats` в боте: сколько ждут LLM, сколько запросов сделал каждый слот сегодня (💤 — слот спит до сброса лимита).
- Логи: `docker compose logs -f scorer`.

## Настройка слотов

`config.yaml → llm.slots`. Поля: `rpm` (запросов в минуту), `rpd` (в день, 0 — не считаем), `batch` (вакансий
в одном запросе), `max_chars` (обрезка текста), `json: false` / `system: false` (для моделей, которые это не
поддерживают), `extra` (добавить поля в запрос). Новый слот у того же провайдера = другая модель с отдельными лимитами.
Свой сервер с моделью (например GB10 с sglang/ollama) — ещё один слот со своим `url`, лимиты не нужны.
