"""Клиент для работы с RuTracker."""
import logging
from typing import List, Dict, Any, Optional
from py_rutracker import AsyncRuTrackerClient
from config import RUTRACKER_LOGIN, RUTRACKER_PASSWORD, RUTRACKER_PROXY, RUTRACKER_USER_AGENT

logger = logging.getLogger(__name__)


class RutrackerSearchClient:
    """Клиент для поиска торрентов на RuTracker."""
    
    def __init__(self):
        """Инициализация клиента."""
        self.login = RUTRACKER_LOGIN
        self.password = RUTRACKER_PASSWORD
        self.proxy = RUTRACKER_PROXY if RUTRACKER_PROXY else None
        self.user_agent = RUTRACKER_USER_AGENT if RUTRACKER_USER_AGENT else None
    
    async def search(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Выполняет поиск торрентов по запросу.
        
        Args:
            query: Поисковый запрос
            limit: Максимальное количество результатов
            
        Returns:
            Список словарей с информацией о торрентах
        """
        try:
            # Используем async with для правильного управления контекстом
            async with AsyncRuTrackerClient(
                self.login,
                self.password,
                self.proxy,
                self.user_agent
            ) as client:
                # Выполняем асинхронный поиск на всех страницах
                results = await client.search_all_pages(query, return_search_dict=True)
                
                # Преобразуем результаты в нужный формат
                torrents = []
                for result in results[:limit]:
                    # Если результат уже словарь (return_search_dict=True)
                    if isinstance(result, dict):
                        size_value = result.get('size', 0)
                        unit_value = result.get('unit', '')
                        torrent_info = {
                            'id': str(result.get('topic_id', '')),
                            'title': result.get('title', ''),
                            'size': f"{size_value} {unit_value}",
                            'size_value': size_value,
                            'unit': unit_value,
                            'seeders': result.get('seedmed', 0),
                            'leechers': result.get('leechmed', 0),
                            'url': result.get('title_url', ''),
                            'download_url': result.get('download_url', ''),
                        }
                    else:
                        # Если это объект SearchResult
                        size_value = getattr(result, 'size', 0)
                        unit_value = getattr(result, 'unit', '')
                        torrent_info = {
                            'id': str(getattr(result, 'topic_id', '')),
                            'title': getattr(result, 'title', ''),
                            'size': f"{size_value} {unit_value}",
                            'size_value': size_value,
                            'unit': unit_value,
                            'seeders': getattr(result, 'seedmed', 0),
                            'leechers': getattr(result, 'leechmed', 0),
                            'url': getattr(result, 'title_url', ''),
                            'download_url': getattr(result, 'download_url', ''),
                        }
                    torrents.append(torrent_info)
                
                return torrents
        except Exception as e:
            logger.error(f"Ошибка при поиске на RuTracker: {e}", exc_info=True)
            return []
    
    async def download_torrent(self, torrent_id: str) -> Optional[bytes | str]:
        """
        Скачивает торрент-файл по ID.
        
        Args:
            torrent_id: ID торрента (строка или число) или download_url
            
        Returns:
            Содержимое торрент-файла в виде bytes, путь к файлу (str) если библиотека сохранила файл,
            или None при ошибке
        """
        try:
            # Преобразуем torrent_id в int, если это не URL
            if isinstance(torrent_id, str) and torrent_id.startswith('https://'):
                # Если это URL, передаем как есть
                download_param = torrent_id
            else:
                # Преобразуем строку в int для topic_id
                try:
                    download_param = int(torrent_id)
                except (ValueError, TypeError):
                    logger.error(f"Некорректный формат torrent_id: {torrent_id}")
                    return None
            
            # Используем async with для правильного управления контекстом
            async with AsyncRuTrackerClient(
                self.login,
                self.password,
                self.proxy,
                self.user_agent
            ) as client:
                # Выполняем асинхронное скачивание
                # Метод download принимает topic_id (int) или download_url (str)
                torrent_data = await client.download(download_param)
                
                # Логируем информацию о полученных данных
                if torrent_data:
                    data_type = type(torrent_data).__name__
                    if isinstance(torrent_data, bytes):
                        logger.debug(f"Получены данные торрента: тип={data_type}, размер={len(torrent_data)} байт, начало={torrent_data[:20]}")
                        # Проверяем валидность торрент-файла
                        if not torrent_data.startswith(b'd'):
                            logger.warning(f"Торрент-файл не начинается с 'd', начало: {torrent_data[:100]}")
                            # Проверяем, не является ли это HTML-страницей
                            if torrent_data.startswith(b'<') or b'<html' in torrent_data[:200].lower():
                                logger.error("Получена HTML-страница вместо торрент-файла. Возможно, требуется авторизация или произошла ошибка.")
                                return None
                            
                            # Пробуем найти начало bencoded данных (торрент должен начинаться с 'd')
                            # Ищем паттерны начала торрент-файла
                            patterns = [b'd8:announce', b'd10:created', b'd4:info']
                            start_idx = -1
                            for pattern in patterns:
                                idx = torrent_data.find(pattern)
                                if idx >= 0 and idx < 200:  # Ищем в первых 200 байтах
                                    start_idx = idx
                                    break
                            
                            if start_idx > 0:
                                logger.warning(f"Найдены лишние данные в начале файла ({start_idx} байт), обрезаем")
                                torrent_data = torrent_data[start_idx:]
                            else:
                                # Если не нашли начало, пробуем найти первый 'd' который может быть началом
                                first_d = torrent_data.find(b'd', 0, min(200, len(torrent_data)))
                                if first_d >= 0:
                                    # Проверяем, что после 'd' идет число (для bencoded)
                                    if first_d + 1 < len(torrent_data):
                                        next_char = torrent_data[first_d + 1:first_d + 2]
                                        if next_char.isdigit() or next_char in b':ie':
                                            logger.warning(f"Найдено возможное начало bencoded данных на позиции {first_d}, обрезаем")
                                            torrent_data = torrent_data[first_d:]
                                        else:
                                            logger.error("Не удалось найти валидное начало торрент-файла")
                                            return None
                                else:
                                    logger.error("Не удалось найти начало торрент-файла (нет символа 'd')")
                                    return None
                    elif isinstance(torrent_data, str):
                        # Проверяем, является ли это путем к файлу
                        import os
                        if os.path.exists(torrent_data) and os.path.isfile(torrent_data):
                            file_path = torrent_data  # Сохраняем путь к файлу
                            logger.info(f"Получен путь к файлу: {file_path}")
                            
                            # Проверяем валидность файла, читая только начало
                            try:
                                with open(file_path, 'rb') as f:
                                    first_bytes = f.read(200)
                                
                                # Проверяем, не является ли это HTML-страницей
                                if first_bytes.startswith(b'<') or b'<html' in first_bytes.lower():
                                    logger.error("Файл является HTML-страницей вместо торрент-файла")
                                    return None
                                
                                # Проверяем, что файл начинается с 'd' (bencoded словарь)
                                if not first_bytes.startswith(b'd'):
                                    # Пробуем найти начало bencoded данных
                                    patterns = [b'd8:announce', b'd10:created', b'd4:info']
                                    start_idx = -1
                                    for pattern in patterns:
                                        idx = first_bytes.find(pattern)
                                        if idx >= 0:
                                            start_idx = idx
                                            break
                                    
                                    if start_idx < 0:
                                        first_d = first_bytes.find(b'd')
                                        if first_d >= 0 and first_d + 1 < len(first_bytes):
                                            next_char = first_bytes[first_d + 1:first_d + 2]
                                            if next_char.isdigit() or next_char in b':ie':
                                                start_idx = first_d
                                    
                                    if start_idx < 0:
                                        logger.error("Не удалось найти валидное начало торрент-файла")
                                        return None
                                
                                # Файл валиден, возвращаем путь к нему
                                logger.info(f"Торрент-файл найден и валиден: {file_path}")
                                return file_path
                            except Exception as e:
                                logger.error(f"Ошибка при проверке файла {file_path}: {e}")
                                return None
                        else:
                            logger.warning(f"Получены строковые данные вместо bytes: тип={data_type}, длина={len(torrent_data)}, начало={torrent_data[:50]}")
                            # Пробуем декодировать как base64, если это похоже на base64
                            if len(torrent_data) > 20 and torrent_data.replace('+', '').replace('/', '').replace('=', '').isalnum():
                                try:
                                    import base64
                                    torrent_data = base64.b64decode(torrent_data)
                                    logger.info("Декодированы base64 данные")
                                except Exception as e:
                                    logger.error(f"Ошибка при декодировании base64: {e}")
                                    return None
                            else:
                                logger.error("Получена строка, которая не является путем к файлу и не является base64")
                                return None
                    else:
                        logger.warning(f"Неожиданный тип данных: {data_type}")
                
                return torrent_data
        except Exception as e:
            logger.error(f"Ошибка при скачивании торрента {torrent_id}: {e}", exc_info=True)
            return None
    
    async def close(self):
        """Закрывает соединение."""
        # Контекстный менеджер автоматически закрывает соединение
        # Этот метод оставлен для совместимости
        pass

