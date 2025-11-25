"""Конфигурация проекта с поддержкой переменных окружения."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env файл если он существует
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')  # Опционально: URL для webhook (если не задан, используется polling)

# Rutracker
RUTRACKER_LOGIN = os.getenv('RUTRACKER_LOGIN', '')
RUTRACKER_PASSWORD = os.getenv('RUTRACKER_PASSWORD', '')
RUTRACKER_PROXY = os.getenv('RUTRACKER_PROXY', '')  # Опционально: http://proxy_ip:port
RUTRACKER_USER_AGENT = os.getenv('RUTRACKER_USER_AGENT', '')  # Опционально: кастомный User-Agent

# Kinopub (не требует авторизации для поиска)

# Synology NAS
SYNOLOGY_HOST = os.getenv('SYNOLOGY_HOST', '')
SYNOLOGY_PORT = int(os.getenv('SYNOLOGY_PORT', '5000'))
SYNOLOGY_USERNAME = os.getenv('SYNOLOGY_USERNAME', '')
SYNOLOGY_PASSWORD = os.getenv('SYNOLOGY_PASSWORD', '')
SYNOLOGY_USE_HTTPS = os.getenv('SYNOLOGY_USE_HTTPS', 'False').lower() == 'true'

# Download Station папки
DOWNLOAD_STATION_FOLDER_1080 = os.getenv('DOWNLOAD_STATION_FOLDER_1080', '/downloads/1080p')
DOWNLOAD_STATION_FOLDER_2160 = os.getenv('DOWNLOAD_STATION_FOLDER_2160', '/downloads/2160p')
DOWNLOAD_STATION_FOLDER_SERIAL = os.getenv('DOWNLOAD_STATION_FOLDER_SERIAL', '/downloads/serials')

# Разрешенные пользователи (ID через запятую)
ALLOWED_USER_IDS = [
    int(user_id.strip())
    for user_id in os.getenv('ALLOWED_USER_IDS', '').split(',')
    if user_id.strip().isdigit()
]

# Проверка обязательных переменных
REQUIRED_VARS = [
    'TELEGRAM_BOT_TOKEN',
    'RUTRACKER_LOGIN',
    'RUTRACKER_PASSWORD',
    'SYNOLOGY_HOST',
    'SYNOLOGY_USERNAME',
    'SYNOLOGY_PASSWORD',
]


def validate_config():
    """Проверяет наличие всех обязательных переменных окружения."""
    missing_vars = []
    for var in REQUIRED_VARS:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        raise ValueError(
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}"
        )
    
    return True

