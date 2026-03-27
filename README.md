# Генерация постов в VK и Telegram

Проект переведен из набора отдельных скриптов в единый пакет:

1. `app/` - backend API на FastAPI.
2. `frontend/` - простой web-интерфейс.
3. `docs/` - ТЗ для backend и frontend разработчиков, плюс план координации.
4. `test.py` и старые модули сохранены для совместимости с legacy-сценарием.

## Что умеет пакет
1. Генерировать текст поста через OpenAI.
2. Генерировать промпт и изображение.
3. Публиковать в VK.
4. Публиковать в Telegram.
5. Работать через единый web-интерфейс.

## Быстрый старт
1. Создать `.env` по образцу `.env.example`.
2. Заполнить ключи OpenAI, VK и Telegram.
3. Установить зависимости:

```bash
pip install -r requirements.txt
```

4. Запустить приложение:

```bash
python main.py
```

5. Открыть в браузере [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Диалоговый режим Telegram-бота
Для запуска бота `ГенПост` в диалоговом режиме:

```bash
python genpost_bot.py
```

Что умеет бот:
1. Спрашивает нишу или продукт клиента.
2. Спрашивает цель поста.
3. Предлагает несколько тем на выбор.
4. Генерирует черновик после выбора темы.
5. Публикует в Telegram, VK или сразу в обе площадки.
6. Поддерживает trial/подписку, `/plan`, и админ-команду `/invoice` для создания ссылки оплаты YooKassa.

## Переменные окружения
1. `OPENAI_API_KEY`
2. `VK_API_KEY`
3. `VK_GROUP_ID`
4. `TELEGRAM_BOT_TOKEN`
5. `TELEGRAM_CHAT_ID`

## API
1. `GET /api/health`
2. `GET /api/status`
3. `POST /api/generate`
4. `POST /api/publish`
5. `POST /api/payments/subscription-webhook` (автопродление подписки)
6. `POST /api/payments/yookassa-webhook` (адаптер YooKassa)

### Webhook оплаты подписки
- Endpoint: `POST /api/payments/subscription-webhook`
- Защита: заголовок `X-Webhook-Secret` должен совпадать с `PAYMENT_WEBHOOK_SECRET`
- Идемпотентность: повторный `event_id` не продлевает подписку второй раз

Пример payload:

```json
{
  "event_id": "pay_2026_03_27_0001",
  "provider": "cloudpayments",
  "status": "paid",
  "chat_id": "123456789",
  "amount_rub": 990,
  "period_days": 30
}
```

### YooKassa webhook
- Endpoint: `POST /api/payments/yookassa-webhook`
- Защита: заголовок `X-YooKassa-Token` должен совпадать с `YOOKASSA_WEBHOOK_TOKEN`
- Поддерживается событие только `payment.succeeded`
- В `object.metadata` должны быть:
  - `chat_id` — Telegram chat id клиента
  - `period_days` — необязательно (по умолчанию `SUBSCRIPTION_DEFAULT_DAYS`)

Пример `metadata` при создании платежа в YooKassa:

```json
{
  "chat_id": "123456789",
  "period_days": "30"
}
```

### Создание платежа YooKassa (готовый скрипт)
1. Заполните в `.env`:
   - `YOOKASSA_SHOP_ID`
   - `YOOKASSA_SECRET_KEY`
   - `TARGET_CHAT_ID` (chat id клиента в Telegram)
   - `SUBSCRIPTION_PRICE_RUB`
   - `SUBSCRIPTION_DEFAULT_DAYS`
2. Запустите:

```bash
python scripts/create_yookassa_payment.py
```

Скрипт вернет `confirmation_url` — отправьте эту ссылку клиенту для оплаты.

## Важно
Ранее в проекте секреты лежали прямо в `config.py`. Теперь `config.py` читает значения только из окружения. Старые ключи нужно считать скомпрометированными и заменить.
