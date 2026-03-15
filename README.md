# Inline Deleter Bot

У вас чат в Telegram. Участники постят GIF через @gif, стикеры через @sticker, результаты поиска через @wiki — и чат превращается в мусорку. Этот бот решает проблему: удаляет `via_bot` сообщения по гибким правилам, которые вы сами настраиваете.

## Why

Telegram не даёт никакого встроенного контроля над inline-ботами. Нельзя запретить @gif, ограничить частоту, разрешить только в определённое время. Этот бот закрывает этот пробел через систему именованных политик — каждому боту своё правило, для всех остальных — default.

## Example use case

Допустим, у вас очень строгий чат. Вы хотите:

- **@gif запрещён ночью** — днём можно, с 00:00 до 08:00 гифки летят в мусор
- **@wiki разрешён всегда** — полезный бот, трогать не надо
- **@gif ограничен до 3 в минуту** — чтобы не флудили

```bash
# Создаём политики
policy new ночной-бан  schedule 00:00-08:00 UTC+3
policy new без-лимита  whitelist
policy new антифлуд    throttle 3/60

# Назначаем ботам
bot assign @gif   ночной-бан
bot assign @wiki  без-лимита
bot assign @gif   антифлуд     # переназначаем на более строгий
```

Всё остальное (неназначенные боты) удаляется через 60 секунд по default-политике.

---

## Типы политик

| Тип | Синтаксис | Поведение |
|---|---|---|
| `whitelist` | `policy new safe whitelist` | Никогда не удалять |
| `blacklist` | `policy new ban blacklist` | Мгновенное удаление |
| `delay` | `policy new slow delay 120` | Удалить через N секунд (3–3600) |
| `throttle` | `policy new лимит throttle 3/60` | Не более N сообщений за W секунд, остальные — сразу |
| `schedule` | `policy new ночь schedule 20:00-23:00 UTC+3` | Разрешить только в указанное окно, вне — удалить. Поддерживает ночные окна (22:00–06:00) |
| `shadow` | `policy new призрак shadow 30-300` | Удалить через случайную задержку MIN–MAX секунд |

**Приоритет:** назначенная политика → default политика чата

При создании чата автоматически создаётся политика `default delay 60`. Удалить её нельзя — можно только переопределить или переназначить.

---

## Установка

### Требования

- Python 3.10+
- nginx + certbot (рекомендуется для production)

### Шаги

```bash
git clone https://github.com/Lena727/inline-deleter-bot.git
cd inline-deleter-bot

python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env   # заполнить BOT_TOKEN и WEBHOOK_HOST
```

### `.env`

| Переменная | Обязательная | Описание |
|---|---|---|
| `BOT_TOKEN` | ✅ | Токен от @BotFather |
| `WEBHOOK_HOST` | ✅ | Публичный HTTPS-адрес (напр. `https://bot.example.com`) |
| `PORT` | — | Порт aiohttp-сервера (по умолчанию `8443`) |
| `SSL_CERT` | — | Путь к cert.pem (оставить пустым при nginx) |
| `SSL_KEY` | — | Путь к key.pem (оставить пустым при nginx) |
| `DB_PATH` | — | Путь к SQLite-файлу (по умолчанию `bot.db`) |

### systemd

```bash
sudo cp inline_deleter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inline_deleter
```

### Деплой обновлений

```bash
cd /srv/inline_deleter && git pull && systemctl restart inline_deleter
```

---

## Команды в группе

Администратор с правом **удаления сообщений** может управлять ботом прямо в чате — без `/`, в bash-стиле.

### Политики

```bash
policy list                           # список всех политик чата
policy new <name> <type> [args]       # создать политику
policy set default <name>             # сменить default
policy rename <old> <new>             # переименовать
policy del <name>                     # удалить (нельзя удалить default)
policy show <name>                    # детали и конфиг политики
```

**Примеры:**
```bash
policy new тихий    delay 300
policy new ночь     schedule 22:00-08:00 UTC+3
policy new антифлуд throttle 5/60
policy new рандом   shadow 30-600
policy new вип      whitelist
policy set default  тихий
```

### Боты

```bash
bot list                              # назначения + default для остальных
bot assign @username <policy>         # назначить политику боту
bot unassign @username                # сбросить на default
```

### Прочие команды

```bash
/togglecmds    # вкл/выкл удаление /команд пользователей
/toggleown     # вкл/выкл автоудаление ответов самого бота
/chatstatus    # полный статус: политики + назначения
/help          # справка
/reload        # горячая перезагрузка (только владелец бота)
```

---

## Личный кабинет (ЛС)

Напишите `/start` — бот покажет список чатов, где вы администратор с правом удаления. Всё то же самое, что в командах, но через инлайн-меню.

```
/start
 └─ 💬 Мой Linux чат
      ├─ 🗑 Удалять команды: ✅
      ├─ 🤖 Удалять ответы бота: ❌
      ├─ 📋 Политики
      │    ├─ ⭐ default — delay 60 с.
      │    ├─ ночь — schedule 22:00–08:00 UTC+3
      │    ├─ антифлуд — throttle 5/60
      │    └─ ➕ Новая политика
      └─ 🤖 Боты
           ├─ @gif  → антифлуд
           ├─ @wiki → (default)
           └─ ➕ Назначить бота
```

---

## Архитектура

### Файлы

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

### Схема БД

```
chat_settings
  chat_id          PK
  delete_commands  bool
  delete_own       bool

policies
  id               PK
  chat_id          FK
  name             unique per chat
  type             whitelist|blacklist|delay|throttle|schedule|shadow
  config           JSON  {"delay":60} / {"limit":3,"window":60} / ...
  is_default       bool  (ровно одна per chat, нельзя удалить)

bot_assignments
  chat_id          PK
  bot_username     PK
  policy_id        FK → policies.id  (CASCADE DELETE)

known_chats
  chat_id          PK
  chat_title
  username
```

### Инфраструктура

```
Telegram
   │ HTTPS :443
   ▼
nginx  (TLS termination, Let's Encrypt)
   │ HTTP  127.0.0.1:8443
   ▼
aiohttp  (main.py)
   ├── Policy Engine  (engine.py)
   └── SQLite         (bot.db)
```

---

## Development

```bash
git clone https://github.com/Lena727/inline-deleter-bot.git
cd inline-deleter-bot

pip install -r requirements.txt
cp .env.example .env   # заполнить токен, можно без WEBHOOK_HOST для polling

python main.py
```

> Для локальной разработки без публичного IP удобно использовать [ngrok](https://ngrok.com/) или [localtunnel](https://theboroer.github.io/localtunnel-www/) вместо настоящего webhook-сервера.

---

## Стек

- [aiogram 3.x](https://github.com/aiogram/aiogram)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- [aiosqlite](https://github.com/omnilib/aiosqlite)
- [python-dotenv](https://github.com/theskumar/python-dotenv)

## Лицензия

MIT
