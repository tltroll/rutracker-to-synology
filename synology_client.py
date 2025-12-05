"""Клиент для работы с Synology Download Station."""
import logging
import tempfile
import os
from typing import Optional
from synology_api import downloadstation
from synology_api.exceptions import DownloadStationError
from config import (
    SYNOLOGY_HOST,
    SYNOLOGY_PORT,
    SYNOLOGY_USERNAME,
    SYNOLOGY_PASSWORD,
    SYNOLOGY_USE_HTTPS,
    DOWNLOAD_STATION_FOLDER_1080,
    DOWNLOAD_STATION_FOLDER_2160,
    DOWNLOAD_STATION_FOLDER_SERIAL,
)

logger = logging.getLogger(__name__)


class SynologyDownloadClient:
    """Клиент для работы с Synology Download Station."""
    
    def __init__(self):
        """Инициализация клиента."""
        self.ds = downloadstation.DownloadStation(
            ip_address=SYNOLOGY_HOST,
            port=str(SYNOLOGY_PORT),  # Преобразуем в строку как в тестовом скрипте
            username=SYNOLOGY_USERNAME,
            password=SYNOLOGY_PASSWORD,
            secure=SYNOLOGY_USE_HTTPS,
            download_st_version=2  # Используем версию 2 API для поддержки create_task_torrent
        )
        # В библиотеке synology-api аутентификация происходит автоматически при первом вызове метода
    
    def _reauthenticate(self):
        """Выполняет повторную аутентификацию при ошибке сессии."""
        try:
            logger.info("Выполняется повторная аутентификация...")
            
            # Пытаемся закрыть старую сессию, если есть метод logout
            old_ds = self.ds
            try:
                if hasattr(old_ds, 'session') and hasattr(old_ds.session, 'logout'):
                    old_ds.session.logout()
                    logger.debug("Старая сессия закрыта через logout")
            except Exception as logout_error:
                logger.debug(f"Не удалось закрыть старую сессию через logout: {logout_error}")
            
            # Явно удаляем старый объект для освобождения ресурсов
            del old_ds
            
            # Пересоздаем клиент для получения новой сессии
            self.ds = downloadstation.DownloadStation(
                ip_address=SYNOLOGY_HOST,
                port=str(SYNOLOGY_PORT),
                username=SYNOLOGY_USERNAME,
                password=SYNOLOGY_PASSWORD,
                secure=SYNOLOGY_USE_HTTPS,
                download_st_version=2
            )
            
            # Выполняем тестовый запрос для аутентификации
            # При первом вызове библиотека автоматически аутентифицируется
            result = self.ds.get_info()
            
            # Проверяем, что запрос успешен (не ошибка 105 или 119)
            if isinstance(result, dict):
                error = result.get('error')
                if error and isinstance(error, dict):
                    error_code = error.get('code')
                    if error_code in [105, 119]:
                        logger.error(f"Ошибка при повторной аутентификации: код {error_code}")
                        return False
            
            logger.info("Повторная аутентификация выполнена успешно")
            return True
        except DownloadStationError as e:
            # Если это ошибка 105 или 119, значит переаутентификация не удалась
            if hasattr(e, 'error_code') and e.error_code in [105, 119]:
                logger.error(f"Ошибка при повторной аутентификации: код {e.error_code} - {e}")
                return False
            # Другие ошибки тоже считаем неудачей
            logger.error(f"Ошибка при повторной аутентификации: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"Ошибка при повторной аутентификации: {e}", exc_info=True)
            return False
    
    def _check_and_handle_error(self, result):
        """
        Проверяет результат на наличие ошибок API и обрабатывает их.
        
        Returns:
            tuple: (is_error, error_code, error_message) или (False, None, None) если ошибок нет
        """
        if not isinstance(result, dict):
            return (False, None, None)
        
        # Проверяем наличие ошибки в ответе
        if 'error' in result:
            error = result['error']
            error_code = error.get('code') if isinstance(error, dict) else None
            error_message = error.get('message', 'Неизвестная ошибка') if isinstance(error, dict) else str(error)
            
            # Если это ошибка сессии (119) или ошибка прав доступа (105), пытаемся переаутентифицироваться
            # Ошибка 105 может возникать при использовании истекшей сессии
            if error_code in [105, 119]:
                logger.warning(f"Обнаружена ошибка сессии ({error_code}): {error_message}")
                if self._reauthenticate():
                    return ('retry', error_code, error_message)
                else:
                    return (True, error_code, f"{error_message} (не удалось переаутентифицироваться)")
            
            return (True, error_code, error_message)
        
        # Проверяем success флаг
        if 'success' in result and not result.get('success'):
            error_code = result.get('error', {}).get('code') if isinstance(result.get('error'), dict) else None
            error_message = result.get('error', {}).get('message', 'Операция не выполнена') if isinstance(result.get('error'), dict) else 'Операция не выполнена'
            
            # Если это ошибка сессии (119) или ошибка прав доступа (105), пытаемся переаутентифицироваться
            if error_code in [105, 119]:
                logger.warning(f"Обнаружена ошибка сессии ({error_code}): {error_message}")
                if self._reauthenticate():
                    return ('retry', error_code, error_message)
                else:
                    return (True, error_code, f"{error_message} (не удалось переаутентифицироваться)")
            
            return (True, error_code, error_message)
        
        return (False, None, None)
    
    def add_torrent_file(
        self,
        torrent_data: bytes | str,
        destination_folder: str,
        priority: str = "normal"
    ) -> Optional[str]:
        """
        Добавляет торрент-файл в Download Station.
        
        Args:
            torrent_data: Содержимое торрент-файла в виде bytes или путь к файлу (str)
            destination_folder: Папка назначения для загрузки
            priority: Приоритет загрузки (low, normal, high)
            
        Returns:
            ID задачи загрузки или None при ошибке
        """
        torrent_file_path = None
        
        try:
            # Если передан путь к файлу, используем его напрямую
            if isinstance(torrent_data, str):
                if os.path.exists(torrent_data) and os.path.isfile(torrent_data):
                    torrent_file_path = torrent_data
                    logger.info(f"Используем существующий файл: {torrent_file_path}")
                else:
                    logger.error(f"Файл не найден: {torrent_data}")
                    return None
            elif isinstance(torrent_data, bytes):
                # Если передан bytes, создаем временный файл в текущем каталоге
                current_dir = os.getcwd()
                with tempfile.NamedTemporaryFile(
                    delete=False, 
                    suffix='.torrent', 
                    mode='wb',
                    dir=current_dir
                ) as tmp_file:
                    tmp_file.write(torrent_data)
                    torrent_file_path = tmp_file.name
                    logger.debug(f"Создан временный торрент-файл: {torrent_file_path}, размер: {len(torrent_data)} байт")
            else:
                logger.error(f"Неподдерживаемый тип данных: {type(torrent_data)}")
                return None
            
            # Проверяем, что файл существует и не пустой
            if not os.path.exists(torrent_file_path):
                logger.error(f"Файл не существует: {torrent_file_path}")
                return None
            
            file_size = os.path.getsize(torrent_file_path)
            if file_size == 0:
                logger.error(f"Файл пустой: {torrent_file_path}")
                return None
            
            logger.info(f"Загрузка торрента: файл={torrent_file_path}, размер={file_size} байт, destination={destination_folder}")
            
            # Используем новый метод для загрузки локального файла
            result = self.ds.create_task_torrent(
                file_path=str(torrent_file_path),
                destination=destination_folder,
                create_list=False
            )
            
            logger.debug(f"Результат create_task_torrent: {result}")
            
            # Проверяем на наличие ошибок
            is_error, error_code, error_message = self._check_and_handle_error(result)
            
            # Если ошибка сессии и удалось переаутентифицироваться, повторяем попытку
            if is_error == 'retry':
                logger.info("Повторная попытка после переаутентификации...")
                result = self.ds.create_task_torrent(
                    file_path=str(torrent_file_path),
                    destination=destination_folder,
                    create_list=False
                )
                is_error, error_code, error_message = self._check_and_handle_error(result)
            
            # Если результат - строка, это может быть ошибка
            if isinstance(result, str):
                logger.error(f"Ошибка при создании задачи: {result}")
                # Проверяем, не является ли это ошибкой сессии (105 или 119)
                if "105" in result or "119" in result or "SID" in result.upper() or "session" in result.lower():
                    logger.warning("Возможная ошибка сессии, пытаемся переаутентифицироваться...")
                    if self._reauthenticate():
                        # Повторяем попытку
                        result = self.ds.create_task_torrent(
                            file_path=str(torrent_file_path),
                            destination=destination_folder,
                            create_list=False
                        )
                        is_error, error_code, error_message = self._check_and_handle_error(result)
                        if is_error:
                            logger.error(
                                f"Ошибка при создании задачи после повторной попытки: "
                                f"Ошибка API: {error_code} (детали: {{'code': {error_code}}}) "
                                f"при скачивании торрента. Сообщение: {error_message}"
                            )
                            return None
                    else:
                        return None
                else:
                    return None
            
            # Если есть ошибка, логируем и возвращаем None
            if is_error:
                # Ошибка 106 обычно означает "Invalid parameter"
                # Попробуем загрузить без destination, если указанный путь неверен
                if error_code == 106:
                    logger.warning(f"Ошибка 106 (Invalid parameter) при загрузке с destination={destination_folder}")
                    logger.info("Пробуем загрузить без указания destination...")
                    try:
                        result = self.ds.create_task_torrent(
                            file_path=str(torrent_file_path),
                            destination="",  # Пустой destination
                            create_list=False
                        )
                        logger.debug(f"Результат create_task_torrent без destination: {result}")
                        is_error, error_code, error_message = self._check_and_handle_error(result)
                        if not is_error:
                            logger.info("Успешно загружено без указания destination")
                            # Продолжаем обработку успешного результата ниже
                        else:
                            logger.error(
                                f"Ошибка при создании задачи без destination: Ошибка API: {error_code} "
                                f"Сообщение: {error_message}"
                            )
                            return None
                    except Exception as e:
                        logger.error(f"Ошибка при попытке загрузки без destination: {e}")
                        return None
                else:
                    logger.error(
                        f"Ошибка при создании задачи: Ошибка API: {error_code} "
                        f"(детали: {{'code': {error_code}}}) при скачивании торрента. "
                        f"Сообщение: {error_message}"
                    )
                    return None
            
            # Обрабатываем успешный результат
            if result and isinstance(result, dict):
                # Извлекаем task_id из ответа
                task_id = None
                if 'data' in result:
                    data = result['data']
                    if isinstance(data, dict):
                        # Проверяем taskid или task_id
                        if 'taskid' in data:
                            task_id = data['taskid']
                        elif 'task_id' in data:
                            # task_id может быть списком
                            task_id_list = data['task_id']
                            if isinstance(task_id_list, list) and len(task_id_list) > 0:
                                task_id = task_id_list[0]
                            else:
                                task_id = task_id_list
                    elif isinstance(data, list) and len(data) > 0:
                        task_id = data[0].get('taskid') if isinstance(data[0], dict) else data[0]
                elif 'taskid' in result:
                    task_id = result['taskid']
                
                if task_id:
                    # Устанавливаем приоритет если поддерживается
                    if priority != "normal":
                        try:
                            # Попытка установить приоритет через edit_task или другой метод
                            pass  # Приоритет может не поддерживаться API
                        except Exception:
                            pass
                    
                    # Удаляем файл только при успешной отправке
                    if torrent_file_path and os.path.exists(torrent_file_path):
                        try:
                            os.unlink(torrent_file_path)
                            logger.debug(f"Удален торрент-файл после успешной отправки: {torrent_file_path}")
                        except Exception as e:
                            logger.warning(f"Не удалось удалить файл {torrent_file_path}: {e}")
                    
                    return str(task_id)
                else:
                    logger.warning(f"Не удалось извлечь task_id из ответа: {result}")
                    return None
            else:
                logger.warning(f"Неожиданный тип результата: {type(result)}, значение: {result}")
                return None
                    
        except Exception as e:
            logger.error(f"Ошибка при добавлении торрента в Download Station: {e}", exc_info=True)
            return None
    
    def add_torrent_by_id(
        self,
        torrent_id: str,
        resolution: int,
        priority: str = "normal"
    ) -> Optional[str]:
        """
        Добавляет торрент в Download Station по ID из RuTracker.
        
        Args:
            torrent_id: ID торрента на RuTracker
            resolution: Разрешение (1080 или 2160)
            priority: Приоритет загрузки
            
        Returns:
            ID задачи загрузки или None при ошибке
        """
        try:
            # Определяем папку назначения в зависимости от разрешения
            if resolution == 2160:
                destination = DOWNLOAD_STATION_FOLDER_2160
            else:
                destination = DOWNLOAD_STATION_FOLDER_1080
            
            # Используем URL торрента напрямую
            torrent_url = f"https://rutracker.org/forum/dl.php?t={torrent_id}"
            
            result = self.ds.create_task(
                url=torrent_url,
                destination=destination
            )
            
            # Проверяем на наличие ошибок
            is_error, error_code, error_message = self._check_and_handle_error(result)
            
            # Если ошибка сессии и удалось переаутентифицироваться, повторяем попытку
            if is_error == 'retry':
                logger.info("Повторная попытка после переаутентификации...")
                result = self.ds.create_task(
                    url=torrent_url,
                    destination=destination
                )
                is_error, error_code, error_message = self._check_and_handle_error(result)
            
            # Если есть ошибка, логируем и возвращаем None
            if is_error:
                logger.error(
                    f"Ошибка при создании задачи: Ошибка API: {error_code} "
                    f"(детали: {{'code': {error_code}}}) при скачивании торрента. "
                    f"Сообщение: {error_message}"
                )
                return None
            
            if result:
                # В зависимости от API может быть разная структура ответа
                if isinstance(result, dict):
                    task_id = result.get('taskid') or result.get('id')
                elif isinstance(result, list) and len(result) > 0:
                    task_id = result[0].get('taskid') or result[0].get('id')
                else:
                    task_id = result
                
                if task_id:
                    if priority != "normal":
                        try:
                            self.ds.set_config(
                                taskid=task_id,
                                priority=priority
                            )
                        except Exception:
                            pass
                    return str(task_id)
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при добавлении торрента в Download Station: {e}", exc_info=True)
            return None
    
    def get_task_status(self, task_id: str) -> Optional[dict]:
        """
        Получает статус задачи загрузки.
        
        Args:
            task_id: ID задачи загрузки
            
        Returns:
            Словарь с информацией о задаче или None при ошибке
            Структура ответа:
            {
                'status': 'downloading' | 'finished' | 'error' | 'waiting' | 'paused' | 'finishing' | 'hash_checking',
                'title': str,  # название задачи
                'error': str | None  # сообщение об ошибке если есть
            }
        """
        max_retries = 2
        retry_count = 0
        result = None
        
        while retry_count <= max_retries:
            try:
                # Используем tasks_list для получения всех задач и ищем нужную по ID
                logger.debug(f"Получение статуса задачи {task_id} (попытка {retry_count + 1})")
                result = self.ds.tasks_list(
                    additional_param=['detail']
                )
                
                # Проверяем на наличие ошибок
                is_error, error_code, error_message = self._check_and_handle_error(result)
                
                if is_error == 'retry':
                    logger.info("Повторная попытка получения статуса после переаутентификации...")
                    result = self.ds.tasks_list(
                        additional_param=['detail']
                    )
                    is_error, error_code, error_message = self._check_and_handle_error(result)
                
                if is_error:
                    logger.error(f"Ошибка при получении статуса задачи {task_id}: {error_code} - {error_message}")
                    return None
                
                # Если успешно получили результат, выходим из цикла
                break
                
            except DownloadStationError as e:
                # Если это ошибка сессии (105 или 119), пытаемся переаутентифицироваться
                if hasattr(e, 'error_code') and e.error_code in [105, 119]:
                    logger.warning(f"Ошибка сессии ({e.error_code}) при получении статуса задачи {task_id}, пытаемся переаутентифицироваться...")
                    if self._reauthenticate() and retry_count < max_retries:
                        retry_count += 1
                        continue  # Повторяем попытку
                    else:
                        logger.error(f"Не удалось переаутентифицироваться или превышено количество попыток при получении статуса задачи {task_id}")
                        return None
                else:
                    logger.error(f"DownloadStationError при получении статуса задачи {task_id}: {e.error_code} - {e}")
                    return None
            except Exception as e:
                logger.error(f"Ошибка при получении статуса задачи {task_id}: {e}", exc_info=True)
                return None
        
        # Если мы дошли сюда, значит не удалось получить результат после всех попыток
        if result is None:
            logger.error(f"Не удалось получить результат для задачи {task_id} после всех попыток")
            return None
        
        # Продолжаем обработку успешного результата
        try:
            # Проверяем структуру ответа
            if not isinstance(result, dict):
                logger.warning(f"Неожиданный тип ответа для задачи {task_id}: {type(result)}, значение: {result}")
                return None
            
            if 'data' not in result:
                logger.warning(f"Отсутствует ключ 'data' в ответе для задачи {task_id}: {result}")
                return None
            
            # Извлекаем список задач из ответа tasks_list
            data = result.get('data', {})
            tasks = data.get('task', []) or data.get('tasks', [])
            
            # Проверяем, что tasks - это список
            if not isinstance(tasks, list):
                logger.warning(f"Неожиданный тип списка задач для задачи {task_id}: {type(tasks)}, значение: {tasks}")
                return None
            
            if len(tasks) == 0:
                logger.debug(f"Список задач пуст для задачи {task_id}")
                return None
            
            logger.debug(f"Найдено задач в списке: {len(tasks)}")
            
            # Ищем нужную задачу по ID
            task = None
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                task_id_from_list = t.get('id')
                # Сравниваем как строки для надежности
                if str(task_id_from_list) == str(task_id):
                    task = t
                    logger.debug(f"Задача {task_id} найдена в списке")
                    break
            
            if not task:
                logger.warning(f"Задача {task_id} не найдена в списке задач (всего задач: {len(tasks)})")
                # Логируем доступные ID для отладки
                available_ids = [str(t.get('id', 'N/A')) for t in tasks[:5] if isinstance(t, dict)]
                logger.debug(f"Доступные ID задач (первые 5): {available_ids}")
                return None
            
            # Извлекаем информацию о статусе
            # Статус может быть числом или строкой
            task_status = task.get('status')
            
            # Маппинг числовых статусов DownloadStation
            # Согласно документации и реальным данным:
            # 0 = waiting, 1 = downloading, 2 = paused, 3 = finishing, 4 = finished, 
            # 5 = error, 6 = seeding, 7 = hash_checking, 8 = downloading (альтернативный код)
            numeric_status_map = {
                1: 'waiting',
                2: 'downloading',
                3: 'paused',
                4: 'finishing',
                5: 'finished',
                6: 'error',
                7: 'finished',  # seeding считается завершенным
                8: 'finished',
                9: 'downloading',  # Альтернативный код для downloading
            }
            
            # Маппинг строковых статусов (согласно документации)
            string_status_map = {
                'waiting': 'waiting',
                'downloading': 'downloading',
                'paused': 'paused',
                'finishing': 'finishing',
                'finished': 'finished',
                'hash_checking': 'hash_checking',
                'seeding': 'finished',  # seeding считается завершенным
                'filehosting_waiting': 'waiting',
                'extracting': 'extracting',
                'error': 'error',
            }
            
            # Определяем статус
            if isinstance(task_status, (int, float)):
                status = numeric_status_map.get(int(task_status), 'unknown')
            elif isinstance(task_status, str):
                status = string_status_map.get(task_status.lower(), 'unknown')
            else:
                status = 'unknown'
                logger.warning(f"Неизвестный тип статуса для задачи {task_id}: {type(task_status)}, значение: {task_status}")
            
            # Получаем информацию об ошибке
            additional = task.get('additional', {})
            if not isinstance(additional, dict):
                additional = {}
            
            error_detail = None
            if status == 'error':
                error_detail = (
                    task.get('error_detail') or
                    task.get('error_message') or
                    additional.get('detail', {}).get('error_detail') if isinstance(additional.get('detail'), dict) else None
                )
            
            result_dict = {
                'status': status,
                'title': task.get('title', ''),
                'error': error_detail
            }
            
            logger.debug(f"Статус задачи {task_id}: {status}, название: {task.get('title', 'N/A')}")
            return result_dict
        except Exception as e:
            logger.error(f"Ошибка при обработке результата для задачи {task_id}: {e}", exc_info=True)
            return None

