"""Клиент для работы с Synology Download Station.

Особенности:
- Автоматическое восстановление истекшей сессии (коды 105/106/107/117/118/119).
- Проактивное обновление сессии через заданный интервал, чтобы предотвратить
  неожиданные истечения при длительной работе.
- Thread-safe: защищает вызовы и переавторизацию RLock'ом, что важно при
  параллельном мониторинге задач загрузки.
- Корректный сброс общей сессии библиотеки (`BaseApi.shared_session`) вместо
  ненадёжного `importlib.reload`.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from synology_api import downloadstation
from synology_api.base_api import BaseApi
from synology_api.exceptions import (
    DownloadStationError,
    HTTPError,
    LoginError,
    SynoBaseException,
    SynoConnectionError,
)

from config import (
    DOWNLOAD_STATION_FOLDER_1080,
    DOWNLOAD_STATION_FOLDER_2160,
    SYNOLOGY_HOST,
    SYNOLOGY_PASSWORD,
    SYNOLOGY_PORT,
    SYNOLOGY_USE_HTTPS,
    SYNOLOGY_USERNAME,
)

logger = logging.getLogger(__name__)

# Коды ошибок Synology, которые означают проблему с текущей сессией и
# требуют повторной авторизации.
#   105 - The logged in session does not have permission
#   106 - Session timeout
#   107 - Session interrupted by duplicated login
#   117, 118 - Network unstable / system busy (временные)
#   119 - Invalid session / SID not found
SESSION_ERROR_CODES: frozenset[int] = frozenset({105, 106, 107, 117, 118, 119})

# Коды ошибок Download Station, связанные с параметром destination.
# При их появлении имеет смысл повторить запрос без destination.
DESTINATION_ERROR_CODES: frozenset[int] = frozenset({101, 402, 403, 406})

# Максимальное количество повторных попыток при ошибках сессии/сети.
MAX_RETRIES: int = 2

# Принудительное обновление сессии каждые N секунд.
# Работает как защита от "тихого" истечения сессии на стороне NAS.
SESSION_REFRESH_INTERVAL: float = 30 * 60  # 30 минут

# Маппинг статусов задач DownloadStation к унифицированным значениям.
_NUMERIC_STATUS_MAP: dict[int, str] = {
    1: "waiting",
    2: "downloading",
    3: "paused",
    4: "finishing",
    5: "finished",
    6: "error",
    7: "finished",  # seeding
    8: "finished",
    9: "downloading",
}

_STRING_STATUS_MAP: dict[str, str] = {
    "waiting": "waiting",
    "downloading": "downloading",
    "paused": "paused",
    "finishing": "finishing",
    "finished": "finished",
    "hash_checking": "hash_checking",
    "seeding": "finished",
    "filehosting_waiting": "waiting",
    "extracting": "extracting",
    "error": "error",
}


class SynologyDownloadClient:
    """Клиент Synology Download Station с автоматическим переподключением."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_auth_ts: float = 0.0
        self.ds: Optional[downloadstation.DownloadStation] = None
        self._authenticate()

    def _create_client(self) -> downloadstation.DownloadStation:
        """Создаёт новый клиент библиотеки с полностью свежей сессией.

        Ключевой момент: сбрасываем class-level атрибут `BaseApi.shared_session`
        в `None`, чтобы библиотека не переиспользовала мёртвую сессию, а
        выполнила полноценный login.
        """
        BaseApi.shared_session = None
        return downloadstation.DownloadStation(
            ip_address=SYNOLOGY_HOST,
            port=str(SYNOLOGY_PORT),
            username=SYNOLOGY_USERNAME,
            password=SYNOLOGY_PASSWORD,
            secure=SYNOLOGY_USE_HTTPS,
            download_st_version=2,
            debug=False,
        )

    def _authenticate(self) -> None:
        """Открывает новую сессию, аккуратно закрывая предыдущую."""
        with self._lock:
            if self.ds is not None:
                try:
                    self.ds.logout()
                except SynoBaseException as exc:
                    logger.debug("logout завершился с ошибкой (игнорируем): %s", exc)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.debug("Неожиданная ошибка logout: %s", exc)
            try:
                self.ds = self._create_client()
                self._last_auth_ts = time.monotonic()
                logger.info("Установлена новая сессия Synology Download Station")
            except LoginError as exc:
                logger.error("Ошибка авторизации Synology: %s", exc.error_message)
                raise
            except (SynoConnectionError, HTTPError) as exc:
                logger.error(
                    "Сетевая ошибка при авторизации Synology: %s",
                    exc.error_message,
                )
                raise

    def _ensure_session_fresh(self) -> None:
        """Проактивно обновляет сессию, если она старше заданного интервала."""
        if time.monotonic() - self._last_auth_ts <= SESSION_REFRESH_INTERVAL:
            return
        logger.info("Сессия Synology устарела, выполняется обновление")
        try:
            self._authenticate()
        except SynoBaseException as exc:
            # Не фатально: если сессия ещё жива, следующий вызов сработает.
            logger.warning("Не удалось проактивно обновить сессию: %s", exc)

    @staticmethod
    def _extract_error(result: Any) -> tuple[bool, Optional[int], str]:
        """Извлекает код и сообщение об ошибке из ответа API.

        Возвращает: (is_error, error_code, error_message).
        """
        if isinstance(result, str):
            # create_task_torrent возвращает строку при ошибке, содержащую код.
            code: Optional[int] = None
            for candidate in SESSION_ERROR_CODES | DESTINATION_ERROR_CODES:
                if f"Ошибка API: {candidate}" in result:
                    code = candidate
                    break
            return True, code, result
        if not isinstance(result, dict):
            return False, None, ""
        if result.get("success") is False or "error" in result:
            err = result.get("error") or {}
            if isinstance(err, dict):
                return True, err.get("code"), err.get("message") or "Неизвестная ошибка"
            return True, None, str(err)
        return False, None, ""

    @staticmethod
    def _is_session_error(code: Optional[int], message: str) -> bool:
        """Определяет, связана ли ошибка с невалидной сессией."""
        if code in SESSION_ERROR_CODES:
            return True
        lowered = (message or "").lower()
        return any(token in lowered for token in ("session", "sid", "not logged"))

    def _call_api(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Вызывает метод клиента Download Station с ретраями по сессии/сети.

        Args:
            method_name: имя вызываемого метода библиотеки.
            *args, **kwargs: аргументы для этого метода.

        Returns:
            Результат вызова (как у библиотеки).

        Raises:
            DownloadStationError / SynoConnectionError / HTTPError — если
            все попытки исчерпаны.
        """
        self._ensure_session_fresh()
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                with self._lock:
                    method = getattr(self.ds, method_name)
                    result = method(*args, **kwargs)
                is_err, code, msg = self._extract_error(result)
                if is_err and self._is_session_error(code, msg) and attempt <= MAX_RETRIES:
                    logger.warning(
                        "Ошибка сессии (code=%s, msg=%s); переавторизация, попытка %d",
                        code, msg, attempt,
                    )
                    self._authenticate()
                    continue
                return result
            except DownloadStationError as exc:
                last_exc = exc
                code = getattr(exc, "error_code", None)
                if code in SESSION_ERROR_CODES and attempt <= MAX_RETRIES:
                    logger.warning(
                        "DownloadStationError code=%s; переавторизация, попытка %d",
                        code, attempt,
                    )
                    self._authenticate()
                    continue
                raise
            except (SynoConnectionError, HTTPError) as exc:
                last_exc = exc
                if attempt <= MAX_RETRIES:
                    wait = 2 ** (attempt - 1)
                    logger.warning(
                        "Сетевая ошибка: %s; повтор через %d сек, попытка %d",
                        getattr(exc, "error_message", exc), wait, attempt,
                    )
                    time.sleep(wait)
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        return None

    @staticmethod
    def _extract_task_id(result: Any) -> Optional[str]:
        """Извлекает task_id из ответа create_task / create_task_torrent."""
        if not isinstance(result, dict):
            return None
        data = result.get("data", result)
        if isinstance(data, dict):
            task_id = data.get("taskid") or data.get("id")
            if task_id:
                return str(task_id)
            task_ids = data.get("task_id")
            if isinstance(task_ids, list) and task_ids:
                return str(task_ids[0])
            if task_ids:
                return str(task_ids)
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                tid = first.get("taskid") or first.get("id")
                if tid:
                    return str(tid)
            return str(first)
        return None

    @contextmanager
    def _torrent_file(self, torrent_data: bytes | str) -> Iterator[str]:
        """Возвращает путь к торрент-файлу, создавая временный при необходимости."""
        created_temp = False
        path: Optional[str] = None
        try:
            if isinstance(torrent_data, (bytes, bytearray)):
                if not torrent_data:
                    raise ValueError("Пустые данные торрент-файла")
                fd, path = tempfile.mkstemp(suffix=".torrent")
                created_temp = True
                with os.fdopen(fd, "wb") as fh:
                    fh.write(torrent_data)
                logger.debug(
                    "Создан временный торрент-файл %s (%d байт)",
                    path, len(torrent_data),
                )
            elif isinstance(torrent_data, str):
                if not (os.path.exists(torrent_data) and os.path.isfile(torrent_data)):
                    raise FileNotFoundError(f"Файл не найден: {torrent_data}")
                path = torrent_data
            else:
                raise TypeError(f"Неподдерживаемый тип данных: {type(torrent_data)}")
            if os.path.getsize(path) == 0:
                raise ValueError(f"Файл пустой: {path}")
            yield path
        finally:
            if created_temp and path and os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.debug("Удалён временный файл: %s", path)
                except OSError as exc:
                    logger.warning("Не удалось удалить %s: %s", path, exc)

    def add_torrent_file(
        self,
        torrent_data: bytes | str,
        destination_folder: str,
        priority: str = "normal",
    ) -> Optional[str]:
        """Добавляет торрент-файл в Download Station.

        Args:
            torrent_data: содержимое .torrent (bytes) или путь к файлу.
            destination_folder: целевая папка на NAS.
            priority: зарезервирован для совместимости интерфейса; библиотека
                Synology не позволяет задать приоритет при создании задачи.

        Returns:
            task_id в виде строки или None при ошибке.
        """
        _ = priority  # сохраняем параметр для обратной совместимости
        try:
            with self._torrent_file(torrent_data) as path:
                logger.info(
                    "Загрузка торрента %s (%d байт) в %s",
                    os.path.basename(path),
                    os.path.getsize(path),
                    destination_folder,
                )
                result = self._call_api(
                    "create_task_torrent",
                    file_path=path,
                    destination=destination_folder,
                    create_list=False,
                )
                is_err, code, msg = self._extract_error(result)
                if is_err and code in DESTINATION_ERROR_CODES:
                    logger.warning(
                        "Ошибка destination (code=%s msg=%s); "
                        "повтор без указания destination",
                        code, msg,
                    )
                    result = self._call_api(
                        "create_task_torrent",
                        file_path=path,
                        destination="",
                        create_list=False,
                    )
                    is_err, code, msg = self._extract_error(result)
                if is_err:
                    logger.error(
                        "Не удалось создать задачу: code=%s msg=%s",
                        code, msg,
                    )
                    return None
                task_id = self._extract_task_id(result)
                if task_id is None:
                    logger.warning("Не удалось извлечь task_id из ответа: %s", result)
                return task_id
        except (FileNotFoundError, ValueError, TypeError) as exc:
            logger.error("Ошибка подготовки торрент-файла: %s", exc)
            return None
        except DownloadStationError as exc:
            logger.error("DownloadStationError: %s", exc.error_message)
            return None
        except (SynoConnectionError, HTTPError) as exc:
            logger.error(
                "Сетевая ошибка при загрузке торрента: %s",
                getattr(exc, "error_message", exc),
            )
            return None
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Ошибка при добавлении торрента: %s", exc, exc_info=True)
            return None

    def add_torrent_by_id(
        self,
        torrent_id: str,
        resolution: int,
        priority: str = "normal",
    ) -> Optional[str]:
        """Добавляет торрент в Download Station по ID c RuTracker."""
        _ = priority  # интерфейс сохранён; приоритет DS API не поддерживает
        destination = (
            DOWNLOAD_STATION_FOLDER_2160
            if resolution == 2160
            else DOWNLOAD_STATION_FOLDER_1080
        )
        torrent_url = f"https://rutracker.org/forum/dl.php?t={torrent_id}"
        try:
            result = self._call_api(
                "create_task", url=torrent_url, destination=destination,
            )
            is_err, code, msg = self._extract_error(result)
            if is_err:
                logger.error("Ошибка create_task: code=%s msg=%s", code, msg)
                return None
            return self._extract_task_id(result)
        except DownloadStationError as exc:
            logger.error("DownloadStationError: %s", exc.error_message)
            return None
        except (SynoConnectionError, HTTPError) as exc:
            logger.error(
                "Сетевая ошибка: %s", getattr(exc, "error_message", exc),
            )
            return None
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Ошибка при добавлении торрента: %s", exc, exc_info=True)
            return None

    def get_task_status(self, task_id: str) -> Optional[dict]:
        """Возвращает статус задачи по ID.

        Returns:
            Словарь {'status', 'title', 'error'} либо None при ошибке.
        """
        try:
            result = self._call_api("tasks_list", additional_param=["detail"])
        except DownloadStationError as exc:
            logger.error(
                "Не удалось получить список задач (task %s): %s",
                task_id, exc.error_message,
            )
            return None
        except (SynoConnectionError, HTTPError) as exc:
            logger.error(
                "Сетевая ошибка при получении списка задач (task %s): %s",
                task_id, getattr(exc, "error_message", exc),
            )
            return None
        is_err, code, msg = self._extract_error(result)
        if is_err:
            logger.error(
                "Ошибка tasks_list: code=%s msg=%s", code, msg,
            )
            return None
        if not isinstance(result, dict):
            logger.warning("Неожиданный тип ответа tasks_list: %s", type(result))
            return None
        data = result.get("data") or {}
        tasks = data.get("task") or data.get("tasks") or []
        if not isinstance(tasks, list):
            logger.warning("Некорректный формат списка задач: %s", type(tasks))
            return None
        task = next(
            (t for t in tasks if isinstance(t, dict) and str(t.get("id")) == str(task_id)),
            None,
        )
        if task is None:
            logger.debug(
                "Задача %s не найдена (всего задач: %d)", task_id, len(tasks),
            )
            return None
        return self._parse_task_status(task, task_id)

    @staticmethod
    def _parse_task_status(task: dict, task_id: str) -> dict:
        """Преобразует запись задачи в унифицированный словарь статуса."""
        raw_status = task.get("status")
        if isinstance(raw_status, (int, float)):
            status = _NUMERIC_STATUS_MAP.get(int(raw_status), "unknown")
        elif isinstance(raw_status, str):
            status = _STRING_STATUS_MAP.get(raw_status.lower(), "unknown")
        else:
            status = "unknown"
            logger.debug(
                "Неизвестный тип статуса у задачи %s: %r", task_id, raw_status,
            )
        error_detail: Optional[str] = None
        if status == "error":
            additional = task.get("additional") or {}
            detail = additional.get("detail") if isinstance(additional, dict) else None
            error_detail = (
                task.get("error_detail")
                or task.get("error_message")
                or (detail.get("error_detail") if isinstance(detail, dict) else None)
            )
        return {
            "status": status,
            "title": task.get("title", ""),
            "error": error_detail,
        }

    def close(self) -> None:
        """Завершает сессию и освобождает ресурсы."""
        with self._lock:
            if self.ds is None:
                return
            try:
                self.ds.logout()
                logger.info("Сессия Synology закрыта")
            except SynoBaseException as exc:
                logger.debug("Ошибка logout игнорирована: %s", exc)
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("Неожиданная ошибка logout: %s", exc)
            finally:
                self.ds = None
                BaseApi.shared_session = None
