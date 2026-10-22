# BirGi — Telegram-бот для выгрузки данных компаний по ИНН

Telegram-бот принимает Excel-файл со списком ИНН узбекских юрлиц, тянет через API MyOrg.uz полную карточку каждой компании (данные, учредители, связи, торговые марки, суды, госзакупки, лицензии и т.д.) и возвращает готовый Excel с ~70 базовыми колонками плюс динамически расширяемые блоки под учредителей, связи и торговые марки.

## Возможности

- Приём `.xlsx` файла в чате
- Автоопределение столбца с ИНН по заголовку (`INN`, `ИНН`, `INN_NUMBER`, `INNUMBER`)
- Ручной выбор столбца (буква `A`, номер `1`, название заголовка) если автодетект не сработал
- Двуязычный интерфейс: русский / узбекский (переключение `/language`)
- Прогресс-индикация во время обработки
- Rate limiting — 3 запроса/сек к API (с запасом от лимита MyOrg в 5 RPS)
- `/cancel` — отмена текущей обработки

## Технологии

- Python 3.11+
- `python-telegram-bot` 21.6
- `httpx` для асинхронных запросов к API
- `openpyxl` для работы с Excel
- `python-dotenv` для конфига

## Локальный запуск

```bash
git clone https://github.com/saeedmuhammadkarimov/BirGi.git
cd BirGi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Отредактировать .env — вставить BOT_TOKEN и API_TOKEN
nano .env

python bot.py
```

## Деплой на сервер

### Вариант 1 — Docker Compose (рекомендуется)

Простейший способ. Нужен VPS с Docker (большинство провайдеров ставят в один клик).

```bash
# На сервере:
git clone https://github.com/saeedmuhammadkarimov/BirGi.git
cd BirGi

# Создать .env с реальными токенами
cp .env.example .env
nano .env

# Запустить
docker compose up -d

# Посмотреть логи
docker compose logs -f

# Остановить
docker compose down

# Обновить после git pull
docker compose up -d --build
```

Контейнер автоматически перезапускается при падении и при ребуте сервера (`restart: unless-stopped`).

### Вариант 2 — systemd (без Docker)

Если не хочешь ставить Docker.

```bash
# На сервере:
sudo useradd -m -s /bin/bash birgi
sudo mkdir -p /opt/birgi-bot
sudo chown birgi:birgi /opt/birgi-bot

sudo -u birgi -i
cd /opt/birgi-bot
git clone https://github.com/saeedmuhammadkarimov/BirGi.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env  # вставить токены
exit

# Установить systemd unit
sudo cp /opt/birgi-bot/systemd/birgi-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birgi-bot

# Посмотреть статус и логи
sudo systemctl status birgi-bot
sudo journalctl -u birgi-bot -f
```

## Конфигурация (`.env`)

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `API_TOKEN` | JWT-токен для MyOrg API (запрашивается у менеджера MyOrg) |
| `API_BASE_URL` | База API, по умолчанию `https://api.myorg.uz` |

## Структура проекта

```
BirGi/
├── bot.py              — Telegram-хендлеры, точка входа
├── processor.py        — Клиент MyOrg API + обработка Excel
├── messages.py         — Локализация (RU/UZ)
├── requirements.txt    — Python зависимости
├── .env.example        — Шаблон конфига
├── .gitignore          — Исключения из git (в т.ч. .env с секретами)
├── Dockerfile          — Контейнер для деплоя
├── docker-compose.yml  — Compose-конфиг для одной командой
├── systemd/            — Юнит-файл для systemd-деплоя
└── README.md
```

## Как использовать

1. Открой бота в Telegram
2. Нажми `/start`, выбери язык
3. Отправь `.xlsx` файл со столбцом `ИНН` или `INN`
4. Если бот не нашёл столбец автоматически — укажи его (буква/номер/название)
5. Жди завершения — файл с результатами придёт тем же сообщением

## Безопасность

- **`.env` НИКОГДА не коммитится в git** (защищено `.gitignore`)
- API-токен MyOrg хранится только в `.env` на сервере
- Входные Excel-файлы хранятся во временной директории и удаляются сразу после обработки
