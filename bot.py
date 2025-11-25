"""Основной файл Telegram бота для поиска и загрузки фильмов."""
import asyncio
import logging
import re
from typing import Any, Awaitable, Callable
from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, 
    TelegramObject, Update, InlineQuery, InlineQueryResultPhoto, 
    InlineQueryResultArticle, InputTextMessageContent, InputMediaPhoto
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import TELEGRAM_BOT_TOKEN, validate_config, ALLOWED_USER_IDS, WEBHOOK_URL
from rutracker_client import RutrackerSearchClient
from kinopub_client import KinopubSearchClient
from synology_client import SynologyDownloadClient
from utils import filter_torrents, extract_resolution, extract_movie_name, extract_year, resolution_to_icon, get_hdr_dv_icons

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AccessControlMiddleware(BaseMiddleware):
    """Middleware для проверки доступа пользователей."""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any]
    ) -> Any:
        # Если список разрешенных пользователей пуст, разрешаем всем
        if not ALLOWED_USER_IDS:
            return await handler(event, data)
        
        # Получаем user_id из события
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
        
        # Если user_id не найден или пользователь не в списке разрешенных
        if user_id is None or user_id not in ALLOWED_USER_IDS:
            if isinstance(event, Message):
                await event.answer("❌ У вас нет доступа к этому боту.")
            elif isinstance(event, CallbackQuery):
                await event.answer("❌ У вас нет доступа к этому боту.", show_alert=True)
            return
        
        # Разрешаем обработку
        return await handler(event, data)


# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Регистрируем middleware для проверки доступа
dp.message.middleware(AccessControlMiddleware())
dp.callback_query.middleware(AccessControlMiddleware())

# Инициализация клиентов
rutracker_client = RutrackerSearchClient()
kinopub_client = KinopubSearchClient()
synology_client = SynologyDownloadClient()

# Инициализация монитора задач (будет создан после инициализации bot)
task_monitor = None

# Временное хранилище данных о торрентах (user_id -> {torrent_id -> torrent_info})
torrents_cache = {}

# Хранилище состояния списка фильмов для возврата назад
# (user_id -> {text: str, keyboard: InlineKeyboardMarkup, filtered_torrents: list})
list_state_cache = {}

# Кэш для хранения типа контента (movie/serial) по тексту запроса
# (normalized_query -> type)
content_type_cache = {}

# Кэш для хранения ID кинопаба по тексту запроса
# (normalized_query -> kinopub_id)
kinopub_id_cache = {}

# Хранилище для мониторинга задач загрузки
# (task_id -> {'user_id': int, 'title': str, 'size': str, 'message_id': int})
task_monitor_storage = {}


class TaskMonitor:
    """Класс для мониторинга статуса задач загрузки."""
    
    def __init__(self, bot: Bot, synology_client: SynologyDownloadClient):
        self.bot = bot
        self.synology_client = synology_client
        self.monitoring_tasks = {}  # task_id -> asyncio.Task
        
    async def start_monitoring(
        self,
        task_id: str,
        user_id: int,
        title: str,
        size: str,
        message_id: int
    ):
        """
        Начинает мониторинг задачи загрузки.
        
        Args:
            task_id: ID задачи в Download Station
            user_id: ID пользователя Telegram
            title: Название фильма/торрента
            size: Размер файла
            message_id: ID сообщения для редактирования
        """
        # Сохраняем информацию о задаче
        task_monitor_storage[task_id] = {
            'user_id': user_id,
            'title': title,
            'size': size,
            'message_id': message_id
        }
        
        # Запускаем задачу мониторинга
        if task_id not in self.monitoring_tasks:
            self.monitoring_tasks[task_id] = asyncio.create_task(
                self._monitor_task(task_id)
            )
            logger.info(f"Начат мониторинг задачи {task_id} для пользователя {user_id}")
    
    async def _monitor_task(self, task_id: str):
        """Внутренний метод для мониторинга задачи."""
        task_info = task_monitor_storage.get(task_id)
        if not task_info:
            logger.warning(f"Информация о задаче {task_id} не найдена")
            return
        
        user_id = task_info['user_id']
        title = task_info['title']
        size = task_info['size']
        message_id = task_info['message_id']
        
        check_interval = 1 * 60  # По умолчанию 1 минута
        
        logger.info(f"Начало мониторинга задачи {task_id}, первая проверка через 10 секунд")
        
        # Небольшая задержка перед первой проверкой, чтобы задача успела появиться в системе
        await asyncio.sleep(10)  # 10 секунд задержка перед первой проверкой
        
        iteration = 0
        while task_id in task_monitor_storage:
            iteration += 1
            try:
                logger.info(f"Проверка #{iteration} статуса задачи {task_id}")
                # Получаем статус задачи
                status_info = self.synology_client.get_task_status(task_id)
                
                if not status_info:
                    # Если не удалось получить статус, возможно задача удалена из Download Station
                    # Удаляем из мониторинга и прекращаем следить
                    logger.warning(f"Задача {task_id} не найдена в Download Station (проверка #{iteration}), удаляем из мониторинга")
                    
                    # Удаляем задачу из мониторинга
                    if task_id in task_monitor_storage:
                        del task_monitor_storage[task_id]
                    if task_id in self.monitoring_tasks:
                        del self.monitoring_tasks[task_id]
                    
                    logger.info(f"Мониторинг задачи {task_id} прекращен, задача не найдена в Download Station")
                    break
                
                status = status_info.get('status')
                error = status_info.get('error')
                
                if status == 'finished':
                    # Задача завершена - отправляем уведомление
                    try:
                        # Создаем клавиатуру с кнопкой нового поиска
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(
                                text="🔍 Начать новый поиск",
                                switch_inline_query_current_chat=""
                            )
                        ]])
                        
                        await self.bot.edit_message_text(
                            chat_id=user_id,
                            message_id=message_id,
                            text=(
                                f"✅ Загрузка завершена!\n\n"
                                f"📽️ {title}\n"
                                f"💾 Размер: {size}\n\n"
                                f"🎉 Фильм готов к просмотру!"
                            ),
                            reply_markup=keyboard
                        )
                        logger.info(f"Задача {task_id} завершена, уведомление отправлено пользователю {user_id}")
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления о завершении: {e}")
                    
                    # Удаляем задачу из мониторинга
                    if task_id in task_monitor_storage:
                        del task_monitor_storage[task_id]
                    if task_id in self.monitoring_tasks:
                        del self.monitoring_tasks[task_id]
                    break
                
                elif status == 'error':
                    # Ошибка загрузки
                    error_msg = error or "Неизвестная ошибка"
                    try:
                        await self.bot.edit_message_text(
                            chat_id=user_id,
                            message_id=message_id,
                            text=(
                                f"❌ Ошибка при загрузке\n\n"
                                f"📽️ {title}\n"
                                f"💾 Размер: {size}\n\n"
                                f"⚠️ {error_msg}"
                            )
                        )
                        logger.warning(f"Задача {task_id} завершилась с ошибкой: {error_msg}")
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления об ошибке: {e}")
                    
                    # Удаляем задачу из мониторинга
                    if task_id in task_monitor_storage:
                        del task_monitor_storage[task_id]
                    if task_id in self.monitoring_tasks:
                        del self.monitoring_tasks[task_id]
                    break
                
                else:
                    # Все остальные статусы (downloading, waiting, paused и т.д.)
                    # Проверяем через 1 минуту
                    logger.info(f"Задача {task_id} в статусе {status}, проверка через 1 минуту")
                    await asyncio.sleep(check_interval)
                    
            except asyncio.CancelledError:
                logger.info(f"Мониторинг задачи {task_id} отменен")
                break
            except Exception as e:
                logger.error(f"Ошибка при мониторинге задачи {task_id}: {e}", exc_info=True)
                # При ошибке проверяем через 1 минуту
                await asyncio.sleep(check_interval)
        
        logger.info(f"Мониторинг задачи {task_id} завершен")
    
    def stop_monitoring(self, task_id: str):
        """Останавливает мониторинг задачи."""
        if task_id in self.monitoring_tasks:
            self.monitoring_tasks[task_id].cancel()
            del self.monitoring_tasks[task_id]
        if task_id in task_monitor_storage:
            del task_monitor_storage[task_id]


class SearchStates(StatesGroup):
    """Состояния FSM для поиска."""
    waiting_for_query = State()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    # Создаем клавиатуру с кнопкой для inline поиска
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔍 Начать поиск на Kinopub",
            switch_inline_query_current_chat=""
        )
    ]])
    
    await message.answer(
        "Привет! Я бот для поиска и загрузки фильмов.\n\n"
        "Для поиска на Kinopub используйте inline режим:\n"
        "Нажмите кнопку ниже или начните вводить @ваш_бот название фильма\n\n"
        "Или просто отправь название фильма для поиска на RuTracker.",
        reply_markup=keyboard
    )


@dp.message(SearchStates.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    """Обработка поискового запроса."""
    query = message.text.strip()
    
    if not query:
        await message.answer("Пожалуйста, введите название фильма.")
        return
    
    # Проверяем тип контента и ID кинопаба в кэше (для результатов из inline режима)
    # Нормализуем запрос для поиска в кэше
    normalized_query = ' '.join(query.lower().split())
    content_type = content_type_cache.get(normalized_query)
    kinopub_id = kinopub_id_cache.get(normalized_query)
    
    # Формируем поисковый запрос: для сериалов убираем год, для фильмов оставляем
    search_query = query
    if content_type == "serial":
        # Для сериалов убираем год из запроса (если есть)
        # Ищем год в конце строки (формат: "Название 1998" или "Название (1998)")
        search_query = re.sub(r'\s*\(?\d{4}\)?\s*$', '', query).strip()
        # Также убираем год, если он идет после названия через пробел
        search_query = re.sub(r'\s+\d{4}\s*$', '', search_query).strip()
    
    # Отправляем сообщение о начале поиска
    search_msg = await message.answer(f"🔍 Ищу фильм: {search_query}...")
    
    try:
        # Выполняем поиск на RuTracker
        torrents = await rutracker_client.search(search_query, limit=1000)
        
        if not torrents:
            await search_msg.edit_text(
                f"❌ По запросу '{search_query}' ничего не найдено на RuTracker."
            )
            await state.clear()
            return
        
        # Фильтруем и приоритизируем результаты
        filtered_torrents = filter_torrents(torrents, max_results=15)
        
        if not filtered_torrents:
            await search_msg.edit_text(
                f"❌ Не найдено подходящих раздач для запроса '{search_query}'."
            )
            await state.clear()
            return
        
        # Сохраняем данные о торрентах во временное хранилище для использования при выборе
        user_id = message.from_user.id
        torrents_dict = {}
        for torrent in filtered_torrents:
            torrent_id = torrent.get('id')
            torrent_data = torrent.copy()
            # Определяем тип контента: сначала из кэша, затем по наличию "Сезон" в title
            torrent_title = torrent.get('title', '')
            if content_type:
                torrent_data['content_type'] = content_type
            elif re.search(r'\bСезон\b', torrent_title, re.IGNORECASE):
                torrent_data['content_type'] = 'serial'
            else:
                torrent_data['content_type'] = 'movie'
            # Сохраняем ID кинопаба, если он есть
            if kinopub_id:
                torrent_data['kinopub_id'] = kinopub_id
            torrents_dict[torrent_id] = torrent_data
        torrents_cache[user_id] = torrents_dict
        
        # Создаем клавиатуру с кнопками
        keyboard_buttons = []
        for torrent in filtered_torrents:
            full_title = torrent.get('title', 'Без названия')
            
            # Извлекаем только название фильма (до первой скобки)
            movie_name = extract_movie_name(full_title)
            
            # Извлекаем год, разрешение и HDR/DV
            year = extract_year(full_title)
            resolution = extract_resolution(full_title)
            hdr_dv_icons = get_hdr_dv_icons(full_title)
            
            # Формируем текст кнопки: название, год, разрешение, HDR/DV (в виде значков)
            parts = [movie_name]
            if year:
                parts.append(str(year))
            if resolution:
                resolution_icon = resolution_to_icon(resolution)
                if resolution_icon:
                    parts.append(resolution_icon)
            if hdr_dv_icons:
                parts.append(hdr_dv_icons)
            
            button_text = ' '.join(parts)
            
            # Передаем ID и разрешение через callback_data
            callback_data = f"torrent_{torrent.get('id')}_{resolution}"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=callback_data
                )
            ])
        
        # Добавляем кнопку "Начать новый поиск" в конец списка
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔍 Начать новый поиск",
                switch_inline_query_current_chat=""
            )
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        list_text = (
            f"✅ Найдено {len(filtered_torrents)} раздач на rutracker для '{search_query}':\n\n"
            "Выберите раздачу для загрузки:"
        )
        
        # Сохраняем состояние списка для возможности вернуться назад
        list_state_cache[user_id] = {
            'text': list_text,
            'keyboard': keyboard,
            'filtered_torrents': filtered_torrents,
            'kinopub_id': kinopub_id  # Сохраняем ID кинопаба для отображения постера
        }
        
        # Если есть ID кинопаба, отправляем постер вместе со списком
        if kinopub_id:
            poster_url = kinopub_client.get_poster_url(kinopub_id, big=True)
            if poster_url:
                try:
                    # Удаляем сообщение о поиске и отправляем новое с постером
                    await search_msg.delete()
                    await bot.send_photo(
                        chat_id=message.chat.id,
                        photo=poster_url,
                        caption=list_text,
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить постер: {e}")
                    # Если не удалось отправить фото, отправляем текстовое сообщение
                    await search_msg.edit_text(
                        list_text,
                        reply_markup=keyboard
                    )
            else:
                await search_msg.edit_text(
                    list_text,
                    reply_markup=keyboard
                )
        else:
            await search_msg.edit_text(
                list_text,
                reply_markup=keyboard
            )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка при поиске: {e}", exc_info=True)
        await search_msg.edit_text(
            f"❌ Произошла ошибка при поиске: {str(e)}"
        )
        await state.clear()


@dp.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """Обработчик inline запросов для поиска на Kinopub."""
    query = inline_query.query.strip()
    
    # Если запрос пустой или слишком короткий, не ищем
    if not query or len(query) < 2:
        await inline_query.answer(
            results=[],
            switch_pm_text="Введите название фильма для поиска",
            switch_pm_parameter="help"
        )
        return
    
    try:
        # Выполняем поиск на Kinopub
        results = await kinopub_client.search(query, limit=20)
        
        if not results:
            await inline_query.answer(
                results=[],
                cache_time=1
            )
            return
        
        # Формируем результаты для inline режима
        inline_results = []
        seen_ids = set()  # Для предотвращения дублирования
        
        for item in results[:20]:  # Telegram позволяет до 50 результатов
            item_id = item.get('id')
            
            # Пропускаем дубликаты
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            
            title = item.get('title', 'Без названия')
            item_type = item.get('type', 'movie')
            poster_url = item.get('poster_url')
            
            # Формируем текст с типом контента
            type_emoji = "🎬" if item_type == "movie" else "📺" if item_type == "serial" else "📽️"
            type_text = "Фильм" if item_type == "movie" else "Сериал" if item_type == "serial" else "Документальный"
            
            # Извлекаем название и год из формата Kinopub (например: "Такси / Taxi (1998)")
            # Берем часть до "/" (русское название) и год из скобок
            movie_name = title.split(' / ')[0].strip() if ' / ' in title else title.split('/')[0].strip()
            # Ищем год в скобках
            year_match = re.search(r'\((\d{4})\)', title)
            year = year_match.group(1) if year_match else None
            
            # Формируем текст сообщения: только название и год (без ID)
            # При выборе этого результата сообщение отправится в чат и автоматически выполнится поиск на RuTracker
            # Для фильмов добавляем год, для сериалов - только название
            if item_type == "serial":
                # Для сериалов не добавляем год
                message_text = movie_name
            elif year:
                # Для фильмов добавляем год
                message_text = f"{movie_name} {year}"
            else:
                message_text = movie_name
            
            # Сохраняем тип контента и ID кинопаба в кэш для использования при поиске на rutracker
            # Нормализуем текст для поиска (убираем лишние пробелы, приводим к нижнему регистру)
            normalized_query = ' '.join(message_text.lower().split())
            content_type_cache[normalized_query] = item_type
            kinopub_id_cache[normalized_query] = item_id
            
            # Используем InlineQueryResultArticle с миниатюрой для отображения постера слева и текста справа
            # Кнопка не нужна - при выборе результата сообщение автоматически отправится и выполнится поиск
            inline_results.append(
                InlineQueryResultArticle(
                    id=str(item_id),  # Уникальный ID
                    title=title,  # Отображается справа от миниатюры
                    description=f"{type_emoji} {type_text}",  # Описание под названием
                    thumbnail_url=poster_url if poster_url else None,  # Постер слева
                    input_message_content=InputTextMessageContent(
                        message_text=message_text
                    )
                )
            )
        
        await inline_query.answer(
            results=inline_results,
            cache_time=60  # Кэшируем результаты на 60 секунд
        )
        
    except Exception as e:
        logger.error(f"Ошибка при inline поиске на Kinopub: {e}", exc_info=True)
        await inline_query.answer(
            results=[],
            cache_time=1
        )


@dp.message(F.text)
async def handle_text_message(message: Message, state: FSMContext):
    """Обработчик текстовых сообщений (поиск по умолчанию)."""
    # Обрабатываем как поиск, если это не команда
    current_state = await state.get_state()
    if current_state != SearchStates.waiting_for_query:
        # Если не в состоянии ожидания, устанавливаем состояние и обрабатываем
        await state.set_state(SearchStates.waiting_for_query)
    await process_search_query(message, state)


@dp.callback_query(F.data.startswith("rutracker_search_"))
async def handle_rutracker_search_from_kinopub(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки поиска на RuTracker из результатов Kinopub."""
    await callback.answer()
    
    # Извлекаем ID из callback_data
    kinopub_id = callback.data.replace("rutracker_search_", "")
    
    # Получаем текст сообщения (название фильма и год)
    message_text = callback.message.caption or callback.message.text or ""
    
    # Текст уже содержит только название и год (без эмодзи и ID)
    # Просто убираем возможные лишние пробелы
    query = message_text.strip()
    
    if not query:
        await callback.message.edit_text("❌ Не удалось извлечь название фильма.")
        return
    
    # Проверяем тип контента и ID кинопаба в кэше (для результатов из inline режима)
    # Нормализуем запрос для поиска в кэше
    normalized_query = ' '.join(query.lower().split())
    content_type = content_type_cache.get(normalized_query)
    kinopub_id = kinopub_id_cache.get(normalized_query)
    
    # Формируем поисковый запрос: для сериалов убираем год, для фильмов оставляем
    search_query = query
    if content_type == "serial":
        # Для сериалов убираем год из запроса (если есть)
        # Ищем год в конце строки (формат: "Название 1998" или "Название (1998)")
        search_query = re.sub(r'\s*\(?\d{4}\)?\s*$', '', query).strip()
        # Также убираем год, если он идет после названия через пробел
        search_query = re.sub(r'\s+\d{4}\s*$', '', search_query).strip()
    
    # Редактируем сообщение, показывая процесс поиска
    await callback.message.edit_caption(
        f"⏳ Ищу '{search_query}' на RuTracker...",
        reply_markup=None
    )
    
    try:
        # Выполняем поиск на RuTracker
        torrents = await rutracker_client.search(search_query, limit=1000)
        
        if not torrents:
            await callback.message.edit_caption(
                f"❌ По запросу '{search_query}' ничего не найдено на RuTracker."
            )
            return
        
        # Фильтруем и приоритизируем результаты
        filtered_torrents = filter_torrents(torrents, max_results=15)
        
        if not filtered_torrents:
            await callback.message.edit_caption(
                f"❌ Не найдено подходящих раздач для запроса '{search_query}'."
            )
            return
        
        # Сохраняем данные о торрентах во временное хранилище
        user_id = callback.from_user.id
        torrents_dict = {}
        for torrent in filtered_torrents:
            torrent_id = torrent.get('id')
            torrent_data = torrent.copy()
            # Определяем тип контента: сначала из кэша, затем по наличию "Сезон" в title
            torrent_title = torrent.get('title', '')
            if content_type:
                torrent_data['content_type'] = content_type
            elif re.search(r'\bСезон\b', torrent_title, re.IGNORECASE):
                torrent_data['content_type'] = 'serial'
            else:
                torrent_data['content_type'] = 'movie'
            # Сохраняем ID кинопаба, если он есть
            if kinopub_id:
                torrent_data['kinopub_id'] = kinopub_id
            torrents_dict[torrent_id] = torrent_data
        torrents_cache[user_id] = torrents_dict
        
        # Создаем клавиатуру с кнопками
        keyboard_buttons = []
        for torrent in filtered_torrents:
            full_title = torrent.get('title', 'Без названия')
            movie_name = extract_movie_name(full_title)
            year = extract_year(full_title)
            resolution = extract_resolution(full_title)
            hdr_dv_icons = get_hdr_dv_icons(full_title)
            
            parts = [movie_name]
            if year:
                parts.append(str(year))
            if resolution:
                resolution_icon = resolution_to_icon(resolution)
                if resolution_icon:
                    parts.append(resolution_icon)
            if hdr_dv_icons:
                parts.append(hdr_dv_icons)
            
            button_text = ' '.join(parts)
            callback_data = f"torrent_{torrent.get('id')}_{resolution}"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=callback_data
                )
            ])
        
        # Добавляем кнопку "Начать новый поиск" в конец списка
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔍 Начать новый поиск",
                switch_inline_query_current_chat=""
            )
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        list_text = (
            f"✅ Найдено {len(filtered_torrents)} раздач на RuTracker для '{search_query}':\n\n"
            "Выберите раздачу для загрузки:"
        )
        
        # Сохраняем состояние списка
        list_state_cache[user_id] = {
            'text': list_text,
            'keyboard': keyboard,
            'filtered_torrents': filtered_torrents,
            'kinopub_id': kinopub_id  # Сохраняем ID кинопаба для отображения постера
        }
        
        # Обновляем сообщение с результатами
        # Если есть ID кинопаба, показываем постер
        if kinopub_id:
            poster_url = kinopub_client.get_poster_url(kinopub_id, big=True)
            if poster_url:
                try:
                    # Если сообщение уже содержит фото, редактируем его
                    if callback.message.photo:
                        await callback.message.edit_media(
                            media=InputMediaPhoto(
                                media=poster_url,
                                caption=list_text
                            ),
                            reply_markup=keyboard
                        )
                    else:
                        # Если сообщение текстовое, отправляем новое с фото
                        chat_id = callback.message.chat.id
                        sent_message = await bot.send_photo(
                            chat_id=chat_id,
                            photo=poster_url,
                            caption=list_text,
                            reply_markup=keyboard
                        )
                        # Удаляем старое текстовое сообщение
                        try:
                            await callback.message.delete()
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Не удалось отправить постер: {e}")
                    # Если не удалось отправить фото, отправляем текстовое сообщение
                    if callback.message.photo:
                        await callback.message.edit_caption(
                            caption=list_text,
                            reply_markup=keyboard
                        )
                    else:
                        await callback.message.edit_text(
                            list_text,
                            reply_markup=keyboard
                        )
            else:
                # Если URL постера не получен, отправляем текстовое сообщение
                if callback.message.photo:
                    await callback.message.edit_caption(
                        caption=list_text,
                        reply_markup=keyboard
                    )
                else:
                    await callback.message.edit_text(
                        list_text,
                        reply_markup=keyboard
                    )
        else:
            # Если нет ID кинопаба, отправляем текстовое сообщение
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=list_text,
                    reply_markup=keyboard
                )
            else:
                await callback.message.edit_text(
                    list_text,
                    reply_markup=keyboard
                )
            
    except Exception as e:
        logger.error(f"Ошибка при поиске на RuTracker: {e}", exc_info=True)
        await callback.message.edit_caption(
            f"❌ Произошла ошибка при поиске: {str(e)}"
        )


@dp.callback_query(F.data.startswith("torrent_"))
async def handle_torrent_selection(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора торрента - показывает детали фильма."""
    await callback.answer()
    
    # Парсим callback_data: torrent_ID_RESOLUTION
    parts = callback.data.replace("torrent_", "").split("_")
    torrent_id = parts[0]
    try:
        resolution = int(parts[1]) if len(parts) > 1 and parts[1] else 1080
    except (ValueError, IndexError):
        resolution = 1080  # По умолчанию
    
    # Получаем данные о торренте из временного хранилища
    user_id = callback.from_user.id
    torrents_data = torrents_cache.get(user_id, {})
    torrent_info = torrents_data.get(torrent_id, {})
    
    if not torrent_info:
        await callback.message.edit_text("❌ Информация о торренте не найдена.")
        return
    
    # Получаем title и size
    title = torrent_info.get('title', 'Неизвестно')
    size_value = torrent_info.get('size_value', 0)
    unit = torrent_info.get('unit', '')
    
    # Получаем ID кинопаба для постера
    kinopub_id = torrent_info.get('kinopub_id')
    
    # Формируем текст с деталями фильма
    details_text = (
        f"📽️ {title}\n\n"
        f"💾 Размер: {size_value} {unit}"
    )
    
    # Создаем клавиатуру с кнопками "Скачать" и "Назад"
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="⬇️ Скачать",
                callback_data=f"download_{torrent_id}_{resolution}"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_to_list"
            )
        ]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    # Если есть ID кинопаба, отправляем постер
    if kinopub_id:
        poster_url = kinopub_client.get_poster_url(kinopub_id, big=True)
        if poster_url:
            try:
                # Если сообщение уже содержит фото, редактируем его
                if callback.message.photo:
                    await callback.message.edit_media(
                        media=InputMediaPhoto(
                            media=poster_url,
                            caption=details_text
                        ),
                        reply_markup=keyboard
                    )
                else:
                    # Если сообщение текстовое, отправляем новое сообщение с фото
                    # В aiogram нельзя заменить текстовое сообщение на медиа через edit_media
                    chat_id = callback.message.chat.id
                    sent_message = await bot.send_photo(
                        chat_id=chat_id,
                        photo=poster_url,
                        caption=details_text,
                        reply_markup=keyboard
                    )
                    # Удаляем старое текстовое сообщение
                    try:
                        await callback.message.delete()
                    except Exception:
                        pass  # Игнорируем ошибку, если сообщение уже удалено
                    # Обновляем callback.message для корректной работы кнопки "Назад"
                    # Это не сработает напрямую, но при нажатии "Назад" callback будет ссылаться на новое сообщение
            except Exception as e:
                # Если не удалось отправить фото, отправляем текстовое сообщение
                logger.warning(f"Не удалось отправить постер: {e}")
                try:
                    await callback.message.edit_text(
                        details_text,
                        reply_markup=keyboard
                    )
                except Exception:
                    # Если и редактирование не удалось, отправляем новое сообщение
                    await callback.message.answer(
                        details_text,
                        reply_markup=keyboard
                    )
        else:
            # Если URL постера не получен, отправляем текстовое сообщение
            await callback.message.edit_text(
                details_text,
                reply_markup=keyboard
            )
    else:
        # Если нет ID кинопаба, отправляем текстовое сообщение
        await callback.message.edit_text(
            details_text,
            reply_markup=keyboard
        )


@dp.callback_query(F.data.startswith("download_"))
async def handle_download(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Скачать' - отправляет торрент в Download Station."""
    await callback.answer()
    
    # Парсим callback_data: download_ID_RESOLUTION
    parts = callback.data.replace("download_", "").split("_")
    torrent_id = parts[0]
    try:
        resolution = int(parts[1]) if len(parts) > 1 and parts[1] else 1080
    except (ValueError, IndexError):
        resolution = 1080  # По умолчанию
    
    # Получаем данные о торренте из временного хранилища
    user_id = callback.from_user.id
    torrents_data = torrents_cache.get(user_id, {})
    torrent_info = torrents_data.get(torrent_id, {})
    
    if not torrent_info:
        await callback.message.edit_text("❌ Информация о торренте не найдена.")
        return
    
    # Получаем title и size
    title = torrent_info.get('title', 'Неизвестно')
    size_value = torrent_info.get('size_value', 0)
    unit = torrent_info.get('unit', '')
    
    # Редактируем сообщение, показывая процесс загрузки
    await callback.message.edit_text(
        f"⏳ Скачиваю торрент и добавляю в Download Station...\n\n"
        f"📽️ {title}\n"
        f"💾 Размер: {size_value} {unit}"
    )
    
    try:
        # Скачиваем торрент-файл
        torrent_data = await rutracker_client.download_torrent(torrent_id)
        
        if not torrent_data:
            await callback.message.edit_text(
                f"❌ Не удалось скачать торрент-файл.\n\n"
                f"📽️ {title}\n"
                f"💾 Размер: {size_value} {unit}"
            )
            return
        
        # Определяем папку назначения в зависимости от типа контента и разрешения
        from config import (
            DOWNLOAD_STATION_FOLDER_1080, 
            DOWNLOAD_STATION_FOLDER_2160,
            DOWNLOAD_STATION_FOLDER_SERIAL
        )
        
        # Получаем тип контента из информации о торренте
        content_type = torrent_info.get('content_type')
        
        # Если тип контента не определен, пытаемся определить по названию
        if not content_type:
            # Проверяем наличие слова "Сезон" в названии
            if re.search(r'\bСезон\b', title, re.IGNORECASE):
                content_type = 'serial'
            else:
                content_type = 'movie'
        
        # Для сериалов используем одну папку независимо от разрешения
        if content_type == 'serial':
            destination_folder = DOWNLOAD_STATION_FOLDER_SERIAL
        else:
            # Для фильмов разделяем по разрешению
            destination_folder = DOWNLOAD_STATION_FOLDER_2160 if resolution == 2160 else DOWNLOAD_STATION_FOLDER_1080
        
        # Добавляем торрент в Download Station
        task_id = synology_client.add_torrent_file(
            torrent_data=torrent_data,
            destination_folder=destination_folder,
            priority="high"
        )
        
        if task_id:
            # Создаем клавиатуру с кнопкой "Начать новый поиск"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔍 Начать новый поиск",
                    switch_inline_query_current_chat=""
                )
            ]])
            
            await callback.message.edit_text(
                f"✅ Торрент успешно добавлен в Download Station!\n\n"
                f"📽️ {title}\n"
                f"💾 Размер: {size_value} {unit}\n"
                f"📁 Папка: {destination_folder}\n\n"
                f"⏳ Начинаю мониторинг загрузки...",
                reply_markup=keyboard
            )
            
            # Запускаем мониторинг задачи
            if task_monitor:
                await task_monitor.start_monitoring(
                    task_id=task_id,
                    user_id=user_id,
                    title=title,
                    size=f"{size_value} {unit}",
                    message_id=callback.message.message_id
                )
            else:
                logger.warning("TaskMonitor не инициализирован, мониторинг не запущен")
        else:
            await callback.message.edit_text(
                f"❌ Не удалось добавить торрент в Download Station.\n\n"
                f"📽️ {title}\n"
                f"💾 Размер: {size_value} {unit}"
            )
            
    except Exception as e:
        logger.error(f"Ошибка при обработке торрента: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Произошла ошибка: {str(e)}\n\n"
            f"📽️ {title}\n"
            f"💾 Размер: {size_value} {unit}"
        )


@dp.callback_query(F.data == "back_to_list")
async def handle_back_to_list(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад' - возвращает к списку фильмов."""
    await callback.answer()
    
    user_id = callback.from_user.id
    list_state = list_state_cache.get(user_id)
    
    if not list_state:
        await callback.message.edit_text("❌ Состояние списка не найдено.")
        return
    
    # Получаем ID кинопаба из сохраненного состояния
    kinopub_id = list_state.get('kinopub_id')
    
    # Восстанавливаем список фильмов
    # Если есть ID кинопаба, показываем постер
    if kinopub_id:
        poster_url = kinopub_client.get_poster_url(kinopub_id, big=True)
        if poster_url:
            try:
                # Если сообщение уже содержит фото, редактируем его
                if callback.message.photo:
                    await callback.message.edit_media(
                        media=InputMediaPhoto(
                            media=poster_url,
                            caption=list_state['text']
                        ),
                        reply_markup=list_state['keyboard']
                    )
                else:
                    # Если сообщение текстовое, отправляем новое с фото
                    chat_id = callback.message.chat.id
                    sent_message = await bot.send_photo(
                        chat_id=chat_id,
                        photo=poster_url,
                        caption=list_state['text'],
                        reply_markup=list_state['keyboard']
                    )
                    # Удаляем старое текстовое сообщение
                    try:
                        await callback.message.delete()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Не удалось отправить постер при возврате назад: {e}")
                # Если не удалось отправить фото, отправляем текстовое сообщение
                if callback.message.photo:
                    await callback.message.edit_caption(
                        caption=list_state['text'],
                        reply_markup=list_state['keyboard']
                    )
                else:
                    await callback.message.edit_text(
                        list_state['text'],
                        reply_markup=list_state['keyboard']
                    )
        else:
            # Если URL постера не получен, отправляем текстовое сообщение
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=list_state['text'],
                    reply_markup=list_state['keyboard']
                )
            else:
                await callback.message.edit_text(
                    list_state['text'],
                    reply_markup=list_state['keyboard']
                )
    else:
        # Если нет ID кинопаба, отправляем текстовое сообщение
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=list_state['text'],
                reply_markup=list_state['keyboard']
            )
        else:
            await callback.message.edit_text(
                list_state['text'],
                reply_markup=list_state['keyboard']
            )


async def on_startup():
    """Выполняется при запуске бота."""
    logger.info("Бот запущен")
    validate_config()


async def on_shutdown():
    """Выполняется при остановке бота."""
    logger.info("Бот остановлен")
    await rutracker_client.close()
    await kinopub_client.close()


async def main():
    global task_monitor
    # Инициализируем монитор задач
    task_monitor = TaskMonitor(bot, synology_client)
    """Главная функция."""
    # Регистрируем обработчики событий
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Если задан WEBHOOK_URL, используем webhook, иначе polling
    if WEBHOOK_URL:
        from urllib.parse import urlparse
        
        # Парсим URL для извлечения пути
        parsed_url = urlparse(WEBHOOK_URL)
        webhook_path = parsed_url.path or '/webhook'
        
        # Устанавливаем webhook
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Бот запущен в режиме webhook: {WEBHOOK_URL}")
        
        # Вызываем startup обработчики
        await on_startup()
        
        # Запускаем webhook сервер
        # Для webhook нужен запуск через aiohttp или другой ASGI сервер
        # Используем упрощенный вариант с aiogram
        from aiohttp import web
        
        async def webhook_handler(request):
            """Обработчик webhook запросов."""
            if request.path == webhook_path:
                try:
                    data = await request.json()
                    update = Update(**data)
                    await dp.feed_update(bot, update)
                    return web.Response()
                except Exception as e:
                    logger.error(f"Ошибка при обработке webhook: {e}", exc_info=True)
                    return web.Response(status=500)
            return web.Response(status=404)
        
        app = web.Application()
        app.router.add_post(webhook_path, webhook_handler)
        
        # Определяем host и port для локального сервера
        # Если URL внешний (https://), используем дефолтные значения для локального сервера
        # Если нужен другой порт, можно добавить переменную WEBHOOK_PORT
        host = '0.0.0.0'  # Слушаем на всех интерфейсах
        port = 8000  # Дефолтный порт
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"Webhook сервер запущен на {host}:{port}{webhook_path}")
        
        # Ждем бесконечно
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Получен сигнал остановки")
        finally:
            await runner.cleanup()
            await bot.delete_webhook()
            await on_shutdown()
    else:
        # Запускаем бота в режиме polling
        # Сначала удаляем webhook, если он был установлен ранее
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook удален, переходим в режим polling")
        except Exception as e:
            logger.warning(f"Ошибка при удалении webhook (возможно, он не был установлен): {e}")
        
        logger.info("Бот запущен в режиме polling")
        await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")

