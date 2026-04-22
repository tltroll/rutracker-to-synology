"""Клиент для работы с RuTracker.

Поддерживает переиспользование одной авторизованной сессии между запросами,
TTL-кеш результатов поиска и однократный повтор при падении авторизации.
"""
import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

from py_rutracker import AsyncRuTrackerClient
from py_rutracker.exceptions import (
    RuTrackerAuthError,
    RuTrackerDownloadError,
    RuTrackerParsingError,
    RuTrackerRequestError,
)

from config import (
    RUTRACKER_LOGIN,
    RUTRACKER_MAX_PAGES,
    RUTRACKER_MIN_SEEDERS,  # noqa: F401  (экспорт для удобства внешнего импорта)
    RUTRACKER_PASSWORD,
    RUTRACKER_PROXY,
    RUTRACKER_SEARCH_CACHE_TTL,
    RUTRACKER_USER_AGENT,
)

logger = logging.getLogger(__name__)


def _normalize_query(query: str) -> str:
    """Нормализует запрос: схлопывает пробелы."""
    return ' '.join(query.split())


class RutrackerSearchClient:
    """Клиент для поиска торрентов на RuTracker с переиспользованием сессии."""

    def __init__(self) -> None:
        self.login = RUTRACKER_LOGIN
        self.password = RUTRACKER_PASSWORD
        self.proxy = RUTRACKER_PROXY if RUTRACKER_PROXY else None
        self.user_agent = RUTRACKER_USER_AGENT if RUTRACKER_USER_AGENT else None
        self.max_pages = RUTRACKER_MAX_PAGES
        self.cache_ttl = RUTRACKER_SEARCH_CACHE_TTL

        self._client: Optional[AsyncRuTrackerClient] = None
        self._client_lock = asyncio.Lock()
        self._search_cache: Dict[str, tuple] = {}
        self._cache_lock = asyncio.Lock()

    async def _get_client(self) -> AsyncRuTrackerClient:
        """Возвращает инициализированный клиент, создавая его при необходимости."""
        if self._client is not None and self._client.session and not self._client.session.closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and self._client.session and not self._client.session.closed:
                return self._client

            logger.debug("Создаём новую сессию RuTracker")
            client = AsyncRuTrackerClient(
                self.login,
                self.password,
                self.proxy,
                self.user_agent,
            )
            await client.init()
            self._client = client
            return self._client

    async def _reset_client(self) -> None:
        """Аккуратно закрывает текущий клиент и сбрасывает ссылку."""
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                await client.close()
            except Exception as ex:  # pylint: disable=broad-except
                logger.debug("Ошибка при закрытии старой сессии RuTracker: %s", ex)

    def _cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        """Возвращает закешированный ответ, если он не протух."""
        entry = self._search_cache.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if self.cache_ttl <= 0 or (time.monotonic() - stored_at) > self.cache_ttl:
            self._search_cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: List[Dict[str, Any]]) -> None:
        """Сохраняет ответ в кеше."""
        if self.cache_ttl <= 0:
            return
        self._search_cache[key] = (time.monotonic(), value)

    async def search(self, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Выполняет поиск торрентов по запросу.

        Args:
            query: Поисковый запрос.
            limit: Максимальное количество результатов.

        Returns:
            Список словарей с информацией о торрентах.

        Raises:
            RuTrackerAuthError: Если авторизация не удалась даже после повтора.
            RuTrackerRequestError: Сетевые/HTTP ошибки.
            RuTrackerParsingError: Ошибки парсинга результатов.
        """
        normalized = _normalize_query(query)
        if not normalized:
            return []

        cache_key = normalized.lower()
        async with self._cache_lock:
            cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("RuTracker search cache hit: '%s' (%d раздач)", normalized, len(cached))
            return cached[:limit]

        results = await self._search_with_retry(normalized)

        torrents: List[Dict[str, Any]] = []
        for result in results:
            size_value = result.get('size', 0)
            unit_value = result.get('unit', '')
            torrents.append({
                'id': str(result.get('topic_id', '')),
                'title': result.get('title', ''),
                'size': f"{size_value} {unit_value}".strip(),
                'size_value': size_value,
                'unit': unit_value,
                'seeders': result.get('seedmed', 0) or 0,
                'leechers': result.get('leechmed', 0) or 0,
                'url': result.get('title_url', ''),
                'download_url': result.get('download_url', ''),
            })

        async with self._cache_lock:
            self._cache_set(cache_key, torrents)

        return torrents[:limit]

    async def _search_with_retry(self, query: str) -> List[Dict[str, Any]]:
        """Поиск с однократным повтором при падении авторизации."""
        attempts = 0
        last_error: Optional[Exception] = None
        while attempts < 2:
            attempts += 1
            try:
                client = await self._get_client()
                return await client.search_all_pages(
                    query,
                    return_search_dict=True,
                    max_pages=self.max_pages,
                )
            except RuTrackerAuthError as ex:
                last_error = ex
                logger.warning("RuTracker: ошибка авторизации (попытка %d): %s", attempts, ex)
                await self._reset_client()
            except RuTrackerRequestError as ex:
                last_error = ex
                needs_reauth = 'аутентификация' in str(ex).lower() or getattr(ex, 'status_code', None) == 401
                if needs_reauth and attempts < 2:
                    logger.warning("RuTracker: требуется переавторизация: %s", ex)
                    await self._reset_client()
                    continue
                raise
        if last_error is not None:
            raise last_error
        return []

    async def download_torrent(self, torrent_id: str) -> Optional[Union[bytes, str]]:
        """Скачивает торрент-файл по ID или URL.

        Args:
            torrent_id: ID торрента (строка/число) или download_url.

        Returns:
            Содержимое торрент-файла, путь к файлу или None при ошибке.
        """
        try:
            if isinstance(torrent_id, str) and torrent_id.startswith('https://'):
                download_param: Union[int, str] = torrent_id
            else:
                try:
                    download_param = int(torrent_id)
                except (ValueError, TypeError):
                    logger.error("Некорректный формат torrent_id: %s", torrent_id)
                    return None

            torrent_data = await self._download_with_retry(download_param)
            if torrent_data is None:
                return None

            return self._validate_torrent_payload(torrent_data)
        except RuTrackerDownloadError as ex:
            logger.error("Ошибка скачивания торрента %s: %s", torrent_id, ex)
            return None
        except (RuTrackerAuthError, RuTrackerRequestError, RuTrackerParsingError) as ex:
            logger.error("Ошибка при скачивании торрента %s: %s", torrent_id, ex)
            return None
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Непредвиденная ошибка при скачивании торрента %s: %s", torrent_id, ex, exc_info=True)
            return None

    async def _download_with_retry(self, download_param: Union[int, str]) -> Optional[Union[bytes, str]]:
        """Выполняет скачивание торрента с повтором при падении авторизации."""
        attempts = 0
        last_error: Optional[Exception] = None
        while attempts < 2:
            attempts += 1
            try:
                client = await self._get_client()
                return await client.download(download_param)
            except RuTrackerAuthError as ex:
                last_error = ex
                logger.warning("RuTracker: ошибка авторизации при скачивании (попытка %d): %s", attempts, ex)
                await self._reset_client()
            except RuTrackerRequestError as ex:
                last_error = ex
                needs_reauth = 'аутентификация' in str(ex).lower() or getattr(ex, 'status_code', None) == 401
                if needs_reauth and attempts < 2:
                    await self._reset_client()
                    continue
                raise
        if last_error is not None:
            raise last_error
        return None

    @staticmethod
    def _validate_torrent_payload(torrent_data: Union[bytes, str]) -> Optional[Union[bytes, str]]:
        """Валидирует полученный торрент (bytes или путь к файлу) и чинит при необходимости."""
        if isinstance(torrent_data, bytes):
            return RutrackerSearchClient._validate_bytes_payload(torrent_data)
        if isinstance(torrent_data, str):
            return RutrackerSearchClient._validate_path_payload(torrent_data)
        logger.warning("Неожиданный тип данных торрента: %s", type(torrent_data).__name__)
        return None

    @staticmethod
    def _validate_bytes_payload(torrent_data: bytes) -> Optional[bytes]:
        """Проверяет и при необходимости обрезает мусор в начале bencoded-файла."""
        logger.debug("Получены данные торрента: размер=%d байт", len(torrent_data))
        if torrent_data.startswith(b'd'):
            return torrent_data

        logger.warning("Торрент-файл не начинается с 'd', начало: %s", torrent_data[:100])
        if torrent_data.startswith(b'<') or b'<html' in torrent_data[:200].lower():
            logger.error("Получена HTML-страница вместо торрент-файла (вероятно, требуется авторизация)")
            return None

        start_idx = RutrackerSearchClient._find_bencoded_start(torrent_data[:200])
        if start_idx > 0:
            logger.warning("Найдены лишние данные в начале файла (%d байт), обрезаем", start_idx)
            return torrent_data[start_idx:]
        logger.error("Не удалось найти валидное начало торрент-файла")
        return None

    @staticmethod
    def _validate_path_payload(file_path: str) -> Optional[str]:
        """Проверяет файл, полученный от библиотеки, на валидность bencoded."""
        if not (os.path.exists(file_path) and os.path.isfile(file_path)):
            logger.error("Получена строка, которая не является путём к файлу: %s", file_path[:80])
            return None

        logger.info("Получен путь к файлу торрента: %s", file_path)
        try:
            with open(file_path, 'rb') as handle:
                first_bytes = handle.read(200)
        except OSError as ex:
            logger.error("Ошибка при чтении файла %s: %s", file_path, ex)
            return None

        if first_bytes.startswith(b'<') or b'<html' in first_bytes.lower():
            logger.error("Файл является HTML-страницей вместо торрент-файла")
            return None
        if first_bytes.startswith(b'd'):
            return file_path

        start_idx = RutrackerSearchClient._find_bencoded_start(first_bytes)
        if start_idx < 0:
            logger.error("Не удалось найти валидное начало торрент-файла в %s", file_path)
            return None
        return file_path

    @staticmethod
    def _find_bencoded_start(data: bytes) -> int:
        """Ищет индекс начала bencoded-словаря в первых байтах файла."""
        for pattern in (b'd8:announce', b'd10:created', b'd4:info'):
            idx = data.find(pattern)
            if 0 <= idx < 200:
                return idx
        first_d = data.find(b'd')
        if first_d >= 0 and first_d + 1 < len(data):
            next_char = data[first_d + 1:first_d + 2]
            if next_char.isdigit() or next_char in b':ie':
                return first_d
        return -1

    async def close(self) -> None:
        """Закрывает переиспользуемую сессию."""
        await self._reset_client()
        self._search_cache.clear()
