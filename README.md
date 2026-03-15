# 🤖 Inline Deleter Bot

Telegram-бот на [aiogram 3.x](https://docs.aiogram.dev/) + aiohttp webhook, который автоматически удаляет сообщения, отправленные через inline-ботов (`via_bot`).

## Возможности

- ⏱ Удаление via-сообщений с настраиваемой задержкой (3–3600 с.) — **per chat**
- 🚫 Бан-лист ботов — мгновенное удаление
- ✅ Белый список ботов — сообщения никогда не удаляются
- 🗑 Автоудаление команд пользователей (`/command`)
- 🤖 Автоудаление собственных ответов бота
- 💬 Управление через личку — меню со списком чатов, где пользователь является администратором
- 🔄 Hot reload через `/reload` (только для владельца)
- 🪝 Webhook (aiohttp) + поддержка Let's Encrypt / self-signed SSL

## Требования

- Python 3.10+
- nginx + certbot (рекомендуется)

## Установка

```bash
git clone https://github.com/Lena727/inline-deleter-bot.git
cd inline-deleter-bot

python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env  # заполнить BOT_TOKEN и WEBHOOK_HOST
```

## Конфигурация `.env`

| Переменная | Обязательная | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен от @BotFather |
| `WEBHOOK_HOST` | ✅ | Публичный HTTPS-адрес (напр. `https://bot.example.com`) |
| `PORT` | — | Порт aiohttp-сервера (по умолчанию `8443`) |
| `SSL_CERT` | — | Путь к сертификату (оставить пустым при nginx) |
| `SSL_KEY` | — | Путь к ключу (оставить пустым при nginx) |
| `DB_PATH` | — | Путь к SQLite-файлу (по умолчанию `bot.db`) |

## Запуск

```bash
venv/bin/python main.py
```

### systemd

```bash
sudo cp inline_deleter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inline_deleter
```

## Команды бота

### В группе (только администраторы с правом удаления сообщений)

| Команда | Описание |
|---|---|
| `/setdelay <сек>` | Задержка удаления via-сообщений (3–3600) |
| `/togglecmds` | Вкл/выкл удаление команд пользователей |
| `/toggleown` | Вкл/выкл автоудаление ответов бота |
| `/banbot @username` | Мгновенно удалять сообщения этого бота |
| `/unbanbot @username` | Убрать из бан-листа |
| `/whitebot @username` | Никогда не удалять сообщения этого бота |
| `/unwhitebot @username` | Убрать из белого списка |
| `/chatstatus` | Текущие настройки чата |

### В личке

Напишите `/start` — бот покажет список групп, где вы администратор с правом удаления сообщений, и позволит управлять настройками через инлайн-меню.

### Только для владельца

| Команда | Описание |
|---|---|
| `/reload` | Горячая перезагрузка процесса (`os.execv`) |

## Приоритет обработки via-сообщений

```
Белый список → пропустить
Бан-лист     → удалить мгновенно
Остальные    → удалить через delete_delay секунд
```

## Стек

- [aiogram 3.x](https://github.com/aiogram/aiogram)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- [aiosqlite](https://github.com/omnilib/aiosqlite)
- [python-dotenv](https://github.com/theskumar/python-dotenv)

## Лицензия

MIT
