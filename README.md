# Telegram Game Bot

Простой Telegram-бот с игровой экономикой:

- `работа` с профессиями, уровнями и лутом
- структурированные логи смен
- `трейд` вверх/вниз
- интерактивные `мины` через inline-кнопки
- главное меню и выбор профессий через inline-кнопки
- инвентарь
- inline-режим через `@бот query`
- поддержка premium/custom emoji через `custom_emoji_id`

## Запуск

```bash
cd /home/user/bots/main
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
touch .env
python3 bot.py
```

## Команды

- `/start`
- `/help`
- `/balance`
- `/work`
- `/worklog`
- `/job`
- `/job <name>`
- `/inventory`
- `/trade <bet>`
- `/mines <bet>`

Примеры:

```text
/trade 200
/job trader
/mines 300
@your_bot_username balance
```

## Inline mode

После включения inline mode в BotFather можно вызывать бота так:

```text
@your_bot_username
@your_bot_username balance
@your_bot_username work
@your_bot_username jobs
@your_bot_username inventory
@your_bot_username logs
```

## Premium emoji

В `.env` можно указать:

- `PREMIUM_EMOJI_UP_ID`
- `PREMIUM_EMOJI_DOWN_ID`
- `PREMIUM_EMOJI_MINE_ID`
- `PREMIUM_EMOJI_COIN_ID`
- `PREMIUM_EMOJI_WORK_ID`

Если `id` пустой, бот использует обычные emoji.

`custom_emoji_id` можно получить через Telegram Bot API из сообщения, где уже есть нужный premium emoji, или через клиентские инструменты/библиотеки, которые показывают entity metadata.
