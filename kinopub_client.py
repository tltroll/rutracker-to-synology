"""Клиент для работы с Kinopub API."""
import logging
from typing import List, Dict, Any, Optional
import aiohttp
from urllib.parse import quote

logger = logging.getLogger(__name__)


class KinopubSearchClient:
    """Клиент для поиска фильмов и сериалов на Kinopub."""
    
    BASE_URL = "https://api.kinopub.link/v1.1"
    POSTER_BASE_URL_SMALL = "https://m.pushbr.com/poster/item/small"
    POSTER_BASE_URL_BIG = "https://m.pushbr.com/poster/item/big"
    
    def __init__(self):
        """Инициализация клиента."""
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Получает или создает сессию aiohttp."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def close(self):
        """Закрывает сессию."""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Выполняет поиск фильмов и сериалов по запросу.
        
        Args:
            query: Поисковый запрос (название фильма/сериала)
            limit: Максимальное количество результатов
            
        Returns:
            Список словарей с информацией о фильмах/сериалах
        """
        try:
            session = await self._get_session()
            url = f"{self.BASE_URL}/autocomplete?query={quote(query)}"
            
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Ошибка при поиске на Kinopub: статус {response.status}")
                    return []
                
                data = await response.json()
                
                # Преобразуем результаты в нужный формат
                results = []
                for item in data[:limit]:
                    result = {
                        'id': item.get('id'),
                        'title': item.get('value', ''),
                        'type': item.get('type', 'movie'),  # movie, serial, documovie
                        'poster_url': self.get_poster_url(item.get('id'), big=False)
                    }
                    results.append(result)
                
                logger.debug(f"Найдено результатов на Kinopub: {len(results)}")
                return results
                
        except Exception as e:
            logger.error(f"Ошибка при поиске на Kinopub: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_poster_url(item_id: Optional[int], big: bool = False) -> Optional[str]:
        """
        Формирует URL постера для фильма/сериала.
        
        Args:
            item_id: ID фильма/сериала
            big: Если True, возвращает URL большого постера, иначе маленького
            
        Returns:
            URL постера или None
        """
        if item_id:
            base_url = KinopubSearchClient.POSTER_BASE_URL_BIG if big else KinopubSearchClient.POSTER_BASE_URL_SMALL
            return f"{base_url}/{item_id}.jpg"
        return None
    
    async def __aenter__(self):
        """Поддержка async context manager."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Закрытие сессии при выходе из контекста."""
        await self.close()

