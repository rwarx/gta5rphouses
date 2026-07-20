# 🏠 GTA5RP Apartment Checker

Система мониторинга квартир для сервера GTA5RP. Автоматически собирает данные о доступности квартир с вики-карты, отслеживает изменения и уведомляет через Telegram.

## 📋 Функциональность

- **Автоматический сбор данных** — Playwright скрапинг всех ~35 квартир с карты
- **RealEstate-источник** — быстрый HTTP-мониторинг каталога `/realestate` одного сервера: ловит освобождение домов/квартир по их исчезновению из каталога (см. `REALESTATE_*` в `.env.example`)
- **«Возможные слёты»** — смена ника владельца в окно Payday трактуется как вероятное освобождение (дом/квартира могли слететь и быть перекуплены до обновления каталога); отдельный тумблер уведомлений
- **История владельцев** — таймлайн ников по каждому объекту + каталог владельцев (дома/квартиры с номером, классом, госстоимостью) через Telegram-команды
- **Smart Mode** — адаптивный мониторинг с учетом Payday (HH:59)
- **История изменений** — хранение снэпшотов и отслеживание изменений
- **Telegram Bot** — уведомления об изменениях и управление
- **Web UI** — админ-панель на React с графиками
- **REST API** — FastAPI для интеграции и экспорта
- **Docker** — полностью контейнеризированное приложение

## 🏗 Архитектура

```
┌─────────────────────────────────────┐
│          Docker Compose             │
│  ┌───────┐ ┌───────┐ ┌──────────┐  │
│  │  DB   │ │ Redis │ │ Frontend │  │
│  │(PgSQL)│ │       │ │ (React)  │  │
│  └───┬───┘ └───┬───┘ └────┬─────┘  │
│      │         │          │        │
│  ┌───┴─────────┴──────────┴─────┐  │
│  │         App Container        │  │
│  │  ┌───────────────────────┐   │  │
│  │  │   FastAPI (API)       │   │  │
│  │  ├───────────────────────┤   │  │
│  │  │   SmartScheduler      │   │  │
│  │  ├───────────────────────┤   │  │
│  │  │   ApartmentScraper    │   │  │
│  │  │   (Playwright)        │   │  │
│  │  ├───────────────────────┤   │  │
│  │  │   Telegram Bot        │   │  │
│  │  │   (aiogram)           │   │  │
│  │  └───────────────────────┘   │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
```

## 🚀 Быстрый старт

### Предварительные требования

- Docker и Docker Compose v2+
- Git

### Установка

```bash
# Клонировать репозиторий
git clone <repo-url>
cd apartment-checker

# Скопировать и настроить .env
cp .env.example .env
# Отредактируйте .env, укажите BOT_TOKEN и ALLOWED_USER_IDS

# Запустить все сервисы
docker-compose up -d
```

### Доступ к сервисам

| Сервис | URL |
|--------|-----|
| Web UI | http://localhost:3000 |
| API    | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

## 📖 Использование

### Telegram Bot

**Команды:**
- `/start` — начальное приветствие
- `/list` — список всех квартир
- `/search <текст>` — поиск квартиры
- `/status <id>` — статус квартиры
- `/free` — свободные квартиры
- `/occupied` — занятые квартиры
- `/history [id]` — история изменений
- `/stats` — статистика
- `/last_update` — последнее обновление
- `/scrape` — ручной запуск парсера
- `/realestate` — состояние каталога `/realestate` и последние освобождения
- `/buildings` — список жилых зданий со счётчиком свободно/всего
- `/building <название>` — все квартиры здания: номер, класс, госстоимость, владелец
- `/houses [текст]` — занятые дома с владельцами (с необязательным поиском)
- `/owners <ник>` — все объекты (дома + квартиры), принадлежащие игроку
- `/owner_history <ключ>` — таймлайн смены владельцев объекта (ключ вида `20:house:242`)
- `/possibly_notify` — вкл/выкл уведомления о «возможных слётах» (смена ника в Payday)

### Web UI

- **Дашборд** — общая статистика и последние изменения
- **Квартиры** — список с поиском и фильтрацией
- **Детали квартиры** — полная информация + график истории
- **Парсер** — состояние и логи запусков

## ⚙️ Конфигурация

### Основные переменные .env

```env
# Telegram (обязательно)
BOT_TOKEN=your_bot_token
ALLOWED_USER_IDS=123456789,987654321

# База данных
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/apartment_checker

# Smart Mode
SMART_MODE=true
LOW_INTERVAL=600          # 10 минут между проверками
HIGH_INTERVAL=5           # 5 секунд в Payday окно
PAYDAY_START_MINUTE=56    # Начало Payday (минута часа)
PAYDAY_END_MINUTE=1       # Конец Payday (минута следующего часа)

# Гейт по обновлению карты: запускать браузерный скрап только когда каталог
# /realestate сообщает о новых данных (метка «Обновлено»/fetchedAtMs сдвинулась),
# а не по слепому таймеру. Экономит ресурсы в Payday. Fail-open: при ошибке
# чтения метки скрап всё равно выполняется, чтобы не пропустить обновление.
MAP_UPDATE_GATE=true

# Источник /realestate (каталог сервера)
REALESTATE_ENABLED=false        # Включить опрос каталога /realestate
REALESTATE_SERVER=Murrieta      # Имя сервера
REALESTATE_INTERVAL=300         # Интервал опроса, сек (>= 5)
REALESTATE_NOTIFY_FREED=true    # Уведомлять об освобождениях
```

## 🐳 Docker

### Сборка и запуск

```bash
# Запуск всех сервисов
docker-compose up -d

# Запуск только определенного сервиса
docker-compose up -d app   # Только бекенд
docker-compose up -d frontend  # Только фронтенд

# Просмотр логов
docker-compose logs -f app
```

### Масштабирование

```bash
# Запуск нескольких экземпляров парсера
docker-compose up -d --scale app=2
```

## 🚂 Деплой на Railway

Проект готов к деплою на [Railway](https://railway.app): образ собирается из `docker/Dockerfile` (указан в `railway.json`), миграции применяются автоматически при старте, порт берётся из `$PORT`.

1. Создайте проект и подключите GitHub-репозиторий (`New Project → Deploy from GitHub repo`).
2. Добавьте плагин **PostgreSQL** (`New → Database → PostgreSQL`). Railway создаст переменную `DATABASE_URL` вида `postgresql://…` — приложение само добавит драйвер `+asyncpg` и выведет sync-URL для Alembic.
3. Задайте переменные окружения сервиса (см. `.env.example`):
   - `BOT_TOKEN`, `ALLOWED_USER_IDS` — Telegram-бот
   - `REALESTATE_ENABLED=true`, `REALESTATE_SERVER=Murrieta` — источник каталога
   - `TIMEZONE`, `SMART_MODE`, `PAYDAY_START_MINUTE`, `PAYDAY_END_MINUTE` — при необходимости
   - `DATABASE_URL` подставляется автоматически, если БД в том же проекте (Reference variable).
4. Деплой запускает entrypoint в режиме `railway`: FastAPI на `$PORT` + все фоновые сервисы (`run_all`).
5. Healthcheck настроен на `/api/v1/health` (см. `railway.json`).

> Playwright-парсер карты в контейнере требует системных библиотек Chromium (уже ставятся в `Dockerfile`). Если нужен только HTTP-источник `/realestate`, можно оставить `HEADLESS=true` и не запускать браузерный парсер.

## 🧪 Тестирование

```bash
# Установка зависимостей (runtime + dev)
pip install -r requirements.txt -r requirements-dev.txt

# Запуск тестов (используют in-memory SQLite, внешние сервисы не нужны)
pytest tests/
```

## 📦 Структура проекта

```
project/
├── app/                    # Python backend
│   ├── config/            # Настройки (Pydantic)
│   ├── database/          # SQLAlchemy модели и репозитории
│   ├── scraper/           # Playwright парсер
│   │   ├── anti_detect.py # Обход Cloudflare
│   │   ├── playwright_scraper.py # Основной парсер
│   │   ├── change_detector.py    # Детектор изменений
│   │   └── scheduler.py  # Smart Scheduler
│   ├── telegram/          # Telegram бот
│   └── api/               # FastAPI REST API
├── frontend/              # React web UI
├── migrations/            # Alembic миграции
├── docker/                # Docker файлы
├── tests/                 # Тесты
└── docker-compose.yml     # Оркестрация
```

## 🔒 Безопасность

- Доступ к боту ограничен Telegram ID администраторов
- API не требует аутентификации (для простоты), но доступен только внутри Docker сети
- Все конфиденциальные данные в .env
- Playwright использует stealth-режим для обхода защиты

## 🛠 Технический стек

- **Python 3.12+** — основной язык
- **Playwright** — браузерная автоматизация
- **FastAPI** — REST API
- **SQLAlchemy 2.0** — ORM (async)
- **PostgreSQL 16** — база данных
- **Redis** — кэш и очереди
- **aiogram 3.x** — Telegram Bot API
- **React 18 + Ant Design** — frontend
- **Docker Compose** — оркестрация
- **APScheduler** — планировщик задач
- **Loguru** — логирование

## 📄 Лицензия

MIT

## 🤝 Вклад в проект

Pull Requests приветствуются! Пожалуйста, следуйте принципам SOLID и используйте type hints.
