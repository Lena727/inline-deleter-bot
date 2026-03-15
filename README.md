# 🤖 Inline Deleter Bot

Telegram-бот на [aiogram 3.x](https://docs.aiogram.dev/) + aiohttp webhook, который автоматически удаляет сообщения, отправленные через inline-ботов (`via_bot`), по гибкой системе политик.

## Возможности

- 📋 **Policy Engine** — именованные политики удаления per-chat
- 🔗 Назначение политики на конкретного бота (`bot assign @gif вечерний`)
- ⭐ Default-политика для всех неназначенных ботов
- 🗑 Автоудаление команд пользователей
- 🤖 Автоудаление собственных ответов бота
- 💬 Управление через личку — инлайн-меню
- 🖥 Bash-like команды в группе (без `/`)
- 🔄 Hot reload (`/reload`, только владелец)
- 🪝 Webhook (aiohttp) + Let's Encrypt / self-signed SSL

## Типы политик

| Тип | Синтаксис | Поведение |
|---|---|---|
| `whitelist` | `policy new safe whitelist` | Никогда не удалять |
| `blacklist` | `policy new ban blacklist` | Мгновенное удаление |
| `delay` | `policy new slow delay 120` | Удалить через N секунд (3–3600) |
| `throttle` | `policy new gif throttle 3/60` | Не более N сообщений за W секунд, остальные — сразу |
| `schedule` | `policy new night schedule 20:00-23:00 UTC+3` | Разрешить только в указанное окно, вне окна — удалить |
| `shadow` | `policy new shadow shadow 30-300` | Удалить через случайную задержку в диапазоне MIN–MAX с. |

**Приоритет:** назначенная политика → default политика чата

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
nano .env   # заполнить BOT_TOKEN и WEBHOOK_HOST
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

---

## Команды в группе

Администратор с правом **удаления сообщений** может использовать команды без `/` — в bash-стиле.

### Политики

```bash
policy list                              # список всех политик чата
policy new <name> <type> [args]          # создать политику
policy set default <name>               # сменить default
policy rename <old> <new>               # переименовать
policy del <name>                       # удалить (нельзя удалить default)
policy show <name>                      # детали политики
```

**Примеры:**
```bash
policy new default delay 60
policy new вечерний schedule 20:00-23:00 UTC+3
policy new гифки throttle 3/60
policy new тихий shadow 60-600
policy new вип whitelist
policy set default вечерний
```

### Боты

```bash
bot list                                 # назначения ботов
bot assign @username <policy>           # назначить политику боту
bot unassign @username                  # сбросить на default
```

**Примеры:**
```bash
bot assign @gif гифки
bot assign @wiki вип
bot unassign @gif
```

### Прочие команды

```bash
/togglecmds    # вкл/выкл удаление команд пользователей
/toggleown     # вкл/выкл автоудаление ответов самого бота
/chatstatus    # полный статус чата (политики + назначения)
/help          # справка
```

### Только для владельца

```bash
/reload        # горячая перезагрузка процесса (os.execv)
```

---

## Личный кабинет (ЛС)

Напишите боту `/start` — появится список чатов, где вы администратор с правом удаления сообщений.

```
/start
 └─ 💬 Название чата
      ├─ 🗑 Удалять команды: ✅/❌
      ├─ 🤖 Удалять ответы бота: ✅/❌
      ├─ 📋 Политики
      │    ├─ ⭐ default — delay (60 с.)
      │    ├─ вечерний — schedule (20:00–23:00 UTC+3)
      │    └─ ➕ Новая политика (FSM: имя → тип → конфиг)
      └─ 🤖 Боты
           ├─ @gif → гифки (throttle)
           └─ ➕ Назначить бота
```

---

## Архитектура

```
main.py          — aiohttp webhook, startup/shutdown
handlers.py      — bash-like команды в группах
handlers_pm.py   — инлайн-меню в личке (FSM)
engine.py        — исполнение политик
db.py            — async SQLite (aiosqlite)
middlewares.py   — TrackChats + DeleteCommands
utils.py         — schedule_delete, is_admin, smart_reply
config.py        — конфигурация из .env
```

```
Telegram
   │ HTTPS :443
   ▼
nginx (TLS termination, Let's Encrypt)
   │ HTTP 127.0.0.1:8443
   ▼
aiohttp (main.py)
   │
   ├── Policy Engine (engine.py)
   └── SQLite (bot.db)
```

## Деплой обновлений

```bash
cd /srv/inline_deleter && git pull && systemctl restart inline_deleter
```

## Стек

- [aiogram 3.x](https://github.com/aiogram/aiogram)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- [aiosqlite](https://github.com/omnilib/aiosqlite)
- [python-dotenv](https://github.com/theskumar/python-dotenv)

## Лицензия

MIT
