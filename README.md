# BirGi — Claude-powered Binance testnet trading bot

Прототип торгового бота: получает рыночные данные с Binance, спрашивает
Claude что делать, исполняет сделку на **testnet** (демо-счёт, без реальных денег).

## Что делает бот

1. Раз в N минут запрашивает свечи 1h/15m/5m по выбранной паре (по умолчанию `BTCUSDT`).
2. Локально считает индикаторы (RSI, EMA20/50/200, MACD, Bollinger, ATR,
   Stochastic, volume vs SMA20) на каждом таймфрейме.
3. Опционально: тянет последние заголовки CryptoPanic по монете.
4. Читает последние N собственных решений с логом (кто что предсказал и как
   потом двинулась цена) — это "память" бота.
5. Всё это (компактный JSON) отправляется в Claude API. Claude возвращает
   структурированное решение `BUY`/`SELL`/`HOLD`, размер позиции, обоснование
   и уверенность.
6. Если уверенность выше порога, лимиты не превышены и есть средства —
   исполняется market-ордер на testnet.
7. Всё пишется в `logs/decisions.jsonl` и `logs/trades.jsonl`.

## Что нужно, чтобы запустить

### 1. Ключи Binance testnet (бесплатно, без верификации)

- Зайдите на https://testnet.binance.vision/
- Войдите через GitHub-аккаунт
- Нажмите **Generate HMAC_SHA256 Key**
- Сохраните `API Key` и `Secret Key`

Testnet даёт виртуальные 10 000 USDT и другие тестовые монеты. Никаких реальных
денег. Ключи ничего не стоят и не связаны с настоящим Binance-аккаунтом.

### 2. Ключ Anthropic API

- https://console.anthropic.com/ → Settings → API Keys → Create Key
- Пополните минимальный баланс ($5 хватит на тысячи запросов Sonnet)

### 3. Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env — впишите три ключа
```

### 4. Запуск

Одна итерация (посмотреть, что решит Claude, и — если разрешено — сделать одну сделку):

```bash
python bot.py once
```

Цикл (крутится непрерывно с интервалом из `.env`):

```bash
python bot.py loop
```

Только анализ, без исполнения (dry-run):

```bash
python bot.py once --dry-run
```

## Предохранители

Всё в `.env`:

- `SYMBOL` — торговая пара (по умолчанию `BTCUSDT`)
- `INTERVAL_MINUTES` — как часто крутится цикл
- `MAX_POSITION_USDT` — максимальный размер одной позиции в USDT
- `MIN_CONFIDENCE` — минимальная уверенность Claude (0.0-1.0), ниже — ничего не делать
- `MAX_TRADES_PER_HOUR` — жёсткий лимит на количество сделок в час
- `DRY_RUN` — если `true`, только логирует решение, не торгует

## Структура

```
bot.py              — main loop + CLI
backtest.py         — прогон стратегии на исторических свечах
config.py           — загрузка .env
binance_client.py   — обёртка над Binance testnet (мульти-таймфрейм)
indicators.py       — технические индикаторы (RSI, EMA, MACD, BB, ATR, Stoch)
news_client.py     — CryptoPanic API (опционально)
memory.py           — прошлые решения с реализованным PnL
claude_advisor.py   — обёртка над Claude API + system prompt
logs/               — trades.jsonl, decisions.jsonl
```

## Бэктест

Прогон стратегии на реальной истории (публичные Binance-эндпоинты, ключи для чтения не нужны):

```bash
# Дешёвая проверка на RSI-стратегии, без Claude
python backtest.py --symbol BTCUSDT --days 30 --steps 200 --dry-strategy

# Реальный прогон с Claude (стоит денег — ~$0.5-2 за 200 шагов на sonnet-5)
python backtest.py --symbol BTCUSDT --days 30 --steps 100
```

Выведет PnL, число сделок, максимальную просадку, Sharpe (наивный).

## Важное предупреждение

Это **прототип**. Не переключайте его на mainnet без:
- Продуманного риск-менеджмента (stop-loss, position sizing по волатильности)
- Полноценного бэктеста стратегии на исторических данных
- Мониторинга и алертов
- Понимания, что LLM может ошибаться в моменте катастрофически

Testnet — это песочница. Используйте её, чтобы поиграть с промптами и понять,
как Claude ведёт себя на рынке, прежде чем даже думать о реальных деньгах.
