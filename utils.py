"""Утилиты для приоритизации и фильтрации результатов поиска."""
import re
from typing import Any, Dict, List, Optional


def extract_resolution(title: str) -> int:
    """Извлекает разрешение из названия торрента."""
    if re.search(r'2160p|4K|UHD', title, re.IGNORECASE):
        return 2160
    if re.search(r'1080p|FullHD', title, re.IGNORECASE):
        return 1080
    return 0


def extract_movie_name(title: str) -> str:
    """Извлекает только название фильма, пропуская префиксы и метаданные."""
    title = re.sub(r'^\[[^\]]+\]\s*', '', title).strip()

    match_round = re.search(r'\(', title)
    match_square = re.search(r'\[', title)

    if match_round and match_square:
        if match_round.start() < match_square.start():
            return title[:match_round.start()].strip()
        return title[:match_square.start()].strip()
    if match_round:
        return title[:match_round.start()].strip()
    if match_square:
        return title[:match_square.start()].strip()

    return title.strip()


def extract_year(title: str) -> int:
    """Извлекает год выпуска фильма из названия."""
    match = re.search(r'\[(\d{4})', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass

    match = re.search(r'\)\s*\((\d{4})\)', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass

    match = re.search(r'\((\d{4})\)', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass

    match = re.search(r'\b(19\d{2}|20\d{2})(?!\s*p\b)', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass

    return 0


def resolution_to_icon(resolution: int) -> str:
    """Преобразует разрешение в значок для экономии места."""
    if resolution == 2160:
        return "4K"
    if resolution == 1080:
        return "HD"
    return ""


def has_dv(title: str) -> bool:
    """Проверяет наличие Dolby Vision в названии."""
    return bool(re.search(r'\bDV\b|Dolby[\s-]?Vision', title, re.IGNORECASE))


def has_hdr(title: str) -> bool:
    """Проверяет наличие HDR в названии."""
    return bool(re.search(r'HDR|HDR10|HDR10\+', title, re.IGNORECASE))


def get_hdr_dv_icons(title: str) -> str:
    """Возвращает значки HDR/DV для экономии места."""
    icons = []
    if has_dv(title):
        icons.append("DV")
    elif has_hdr(title):
        icons.append("HDR")
    return " ".join(icons) if icons else ""


def is_bdremux(title: str) -> bool:
    """Проверяет является ли торрент BDRemux."""
    return bool(re.search(r'BDRemux|BD[\s-]?Remux', title, re.IGNORECASE))


def is_rip(title: str) -> bool:
    """Проверяет является ли торрент rip."""
    rip_patterns = [
        r'BDRip', r'WEBRip', r'DVDRip', r'HDTVRip',
        r'Rip', r'\.rip\b',
    ]
    return any(re.search(pattern, title, re.IGNORECASE) for pattern in rip_patterns)


def is_webdl(title: str) -> bool:
    """Проверяет является ли торрент WEB-DL."""
    return bool(re.search(r'WEB[\s-]?DL', title, re.IGNORECASE))


def is_bluray_disc(title: str) -> bool:
    """Проверяет является ли торрент Blu-ray disc (исключаем такие торренты)."""
    return bool(re.search(r'Blu[\s-]?ray[\s-]?disc', title, re.IGNORECASE))


def has_hybrid(title: str) -> bool:
    """Проверяет наличие Hybrid в названии."""
    return bool(re.search(r'\bHybrid\b', title, re.IGNORECASE))


def _seeders_bonus(seeders: int) -> int:
    """Возвращает бонус приоритета за количество сидеров."""
    if seeders >= 20:
        return 60
    if seeders >= 5:
        return 30
    if seeders >= 1:
        return 10
    return 0


def calculate_priority(torrent: Dict[str, Any]) -> int:
    """Вычисляет приоритет торрента для сортировки (больше = важнее).

    Разрешение доминирует над типом источника, чтобы 1080p BDRemux
    не обходил 2160p WEB-DL.
    """
    title = torrent.get('title', '').upper()
    priority = 0

    resolution = extract_resolution(title)
    if resolution == 2160:
        priority += 10000
    elif resolution == 1080:
        priority += 5000

    if is_bdremux(title):
        priority += 500
    elif is_webdl(title):
        priority += 400
    elif is_rip(title):
        priority += 300

    if has_hybrid(title):
        priority += 50
    elif has_dv(title):
        priority += 30
    elif has_hdr(title):
        priority += 20

    try:
        seeders = int(torrent.get('seeders', 0) or 0)
    except (TypeError, ValueError):
        seeders = 0
    priority += _seeders_bonus(seeders)

    return priority


def prioritize_torrents(torrents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Сортирует торренты по приоритету (убывание)."""
    for torrent in torrents:
        torrent['_priority'] = calculate_priority(torrent)

    sorted_torrents = sorted(torrents, key=lambda x: x.get('_priority', 0), reverse=True)

    for torrent in sorted_torrents:
        torrent.pop('_priority', None)

    return sorted_torrents


def _is_serial_title(title: str) -> bool:
    """Проверяет, похож ли заголовок на сериал."""
    return bool(re.search(r'\bСезон\b', title, re.IGNORECASE))


def _detect_is_serial(
    torrents: List[Dict[str, Any]],
    content_type: Optional[str],
) -> bool:
    """Определяет, является ли выборка сериалом."""
    if content_type == 'serial':
        return True
    if content_type in ('movie', 'documovie'):
        return False
    serial_count = sum(1 for t in torrents if _is_serial_title(t.get('title', '')))
    return serial_count > len(torrents) / 2


def _filter_by_year(
    torrents: List[Dict[str, Any]],
    expected_year: Optional[int],
) -> List[Dict[str, Any]]:
    """Отсекает раздачи с явно другим годом выпуска (±1 год допускается).

    Раздачи без распознанного года пропускаем - не хватает данных для решения.
    """
    if not expected_year:
        return torrents
    result = []
    for torrent in torrents:
        year = extract_year(torrent.get('title', ''))
        if year == 0 or abs(year - expected_year) <= 1:
            result.append(torrent)
    return result


def _filter_by_seeders(
    torrents: List[Dict[str, Any]],
    min_seeders: int,
) -> List[Dict[str, Any]]:
    """Отсекает мёртвые раздачи. Если после фильтра пусто - возвращаем исходный список."""
    if min_seeders <= 0:
        return torrents
    alive = []
    for torrent in torrents:
        try:
            seeders = int(torrent.get('seeders', 0) or 0)
        except (TypeError, ValueError):
            seeders = 0
        if seeders >= min_seeders:
            alive.append(torrent)
    return alive if alive else torrents


def _split_by_resolution(
    torrents: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Разделяет торренты на корзины по разрешению/качеству."""
    buckets: Dict[str, List[Dict[str, Any]]] = {
        '4k_high': [],
        '4k_rest': [],
        '1080_bdremux': [],
        '1080_rest': [],
        'other': [],
    }
    for torrent in torrents:
        title = torrent.get('title', '')
        resolution = extract_resolution(title)
        if resolution == 2160:
            if has_dv(title) or has_hdr(title):
                buckets['4k_high'].append(torrent)
            else:
                buckets['4k_rest'].append(torrent)
        elif resolution == 1080:
            if is_bdremux(title):
                buckets['1080_bdremux'].append(torrent)
            else:
                buckets['1080_rest'].append(torrent)
        else:
            buckets['other'].append(torrent)
    return buckets


def _pick_for_movie(
    buckets: Dict[str, List[Dict[str, Any]]],
    max_results: int,
) -> List[Dict[str, Any]]:
    """Собирает выдачу для фильма с мягким миксом 4K/1080p.

    Если есть 4K DV/HDR - отдаём им большую часть мест, но оставляем
    несколько слотов под 1080p BDRemux как альтернативу.
    Если 4K DV/HDR нет, но есть просто 4K и 1080p BDRemux - показываем оба.
    """
    result: List[Dict[str, Any]] = []
    high_4k = buckets['4k_high']
    rest_4k = buckets['4k_rest']
    bdremux_1080 = buckets['1080_bdremux']
    rest_1080 = buckets['1080_rest']

    if high_4k:
        primary_slots = max(max_results - 3, int(max_results * 0.6))
        result.extend(high_4k[:primary_slots])
        remaining = max_results - len(result)
        if remaining > 0:
            alternatives = bdremux_1080 + rest_4k + rest_1080
            result.extend(alternatives[:remaining])
        return result[:max_results]

    if rest_4k or bdremux_1080:
        half = max(1, max_results // 2)
        result.extend(bdremux_1080[:half])
        remaining = max_results - len(result)
        if remaining > 0:
            result.extend(rest_4k[:remaining])
        remaining = max_results - len(result)
        if remaining > 0:
            result.extend(rest_1080[:remaining])
        return result[:max_results]

    if rest_1080:
        return rest_1080[:max_results]

    return buckets['other'][:max_results]


def _pick_for_serial(
    buckets: Dict[str, List[Dict[str, Any]]],
    max_results: int,
) -> List[Dict[str, Any]]:
    """Для сериалов показываем и 4K, и 1080p, чтобы пользователь видел все сезоны."""
    all_4k = buckets['4k_high'] + buckets['4k_rest']
    all_1080 = buckets['1080_bdremux'] + buckets['1080_rest']

    if all_4k and all_1080:
        max_4k = max(3, int(max_results * 0.6))
        max_1080 = max(3, max_results - max_4k)
        result = all_4k[:max_4k] + all_1080[:max_1080]
        return result[:max_results]

    if all_4k:
        return all_4k[:max_results]
    if all_1080:
        return all_1080[:max_results]
    return buckets['other'][:max_results]


def filter_torrents(
    torrents: List[Dict[str, Any]],
    max_results: int = 10,
    *,
    content_type: Optional[str] = None,
    expected_year: Optional[int] = None,
    min_seeders: int = 1,
) -> List[Dict[str, Any]]:
    """Фильтрует и приоритизирует результаты поиска.

    Args:
        torrents: Исходный список раздач.
        max_results: Максимум раздач в итоговой выдаче.
        content_type: 'movie' | 'serial' | 'documovie' | None. Если не задан -
            тип определяется эвристически по наличию "Сезон" в заголовках.
        expected_year: Год выпуска для фильма (±1 год). Применяется только
            к фильмам. Раздачи без распознанного года не отсекаются.
        min_seeders: Минимум сидеров для показа. При 0 фильтр отключён.
            Если после фильтра ничего не осталось - показываем то, что есть.
    """
    if not torrents:
        return []

    filtered = [t for t in torrents if not is_bluray_disc(t.get('title', ''))]
    if not filtered:
        return []

    is_serial = _detect_is_serial(filtered, content_type)

    if not is_serial:
        filtered = _filter_by_year(filtered, expected_year)
        if not filtered:
            return []

    filtered = _filter_by_seeders(filtered, min_seeders)
    if not filtered:
        return []

    prioritized = prioritize_torrents(filtered)
    buckets = _split_by_resolution(prioritized)

    if is_serial:
        return _pick_for_serial(buckets, max_results)
    return _pick_for_movie(buckets, max_results)
