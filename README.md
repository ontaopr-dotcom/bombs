# Tg bot

Простой Telegram-бот с игровой экономикой:

- `работа` с профессиями, уровнями и лутом
- структурированные логи смен
- интерактивные `мины` через inline-кнопки
- главное меню и выбор профессий через inline-кнопки
- инвентарь
- поддержка premium/custom emoji через `custom_emoji_id`

## Запуск

```bash
mkdir main
cd ~/main
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
touch .env
python3 bot.py
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
