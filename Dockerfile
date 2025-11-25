# Используем Python Alpine для минимального размера образа
FROM python:3.13-alpine

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости, необходимые для сборки Python пакетов
# и для работы с SSL/TLS (требуется для aiohttp и других библиотек)
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем все Python зависимости в одном слое
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir py-rutracker-client==0.2.0 && \
    apk del gcc musl-dev libffi-dev && \
    rm -rf /var/cache/apk/*

# Копируем патч-файлы для библиотек
COPY patches/ /tmp/patches/

# Применяем патч к synology_api (заменяем измененный файл)
RUN if [ -f /tmp/patches/synology_api/downloadstation.py ]; then \
        SITE_PACKAGES=$(python -c "import site; packages = site.getsitepackages(); print(packages[0] if packages else '/usr/local/lib/python3.13/site-packages')") && \
        cp /tmp/patches/synology_api/downloadstation.py "$SITE_PACKAGES/synology_api/downloadstation.py" && \
        rm -rf /tmp/patches; \
    fi

# Копируем код приложения
COPY *.py ./

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Запускаем бота
CMD ["python", "-u","bot.py"]

