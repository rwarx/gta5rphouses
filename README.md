# 🏠 GTA5RP Apartment Checker

Система мониторинга квартир для сервера GTA5RP Murrieta. Автоматически собирает данные о доступности квартир с вики-карты, отслеживает изменения и уведомляет через Telegram.

## 📋 Функциональность

- **Автоматический сбор данных** — Playwright скрапинг всех ~35 квартир с карты
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

## 🧪 Тестирование

```bash
# Установка зависимостей
pip install -r docker/requirements.txt

# Запуск тестов
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