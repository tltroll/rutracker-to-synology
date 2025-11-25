"""Утилиты для приоритизации и фильтрации результатов поиска."""
import re
from typing import List, Dict, Any


def extract_resolution(title: str) -> int:
    """Извлекает разрешение из названия торрента."""
    # Ищем 2160p, 4K, UHD
    if re.search(r'2160p|4K|UHD', title, re.IGNORECASE):
        return 2160
    # Ищем 1080p, FullHD
    if re.search(r'1080p|FullHD', title, re.IGNORECASE):
        return 1080
    return 0


def extract_movie_name(title: str) -> str:
    """
    Извлекает только название фильма.
    Пропускает префиксы в квадратных скобках (например, [iPad], [iPhone]).
    Берет название до скобки с режиссером или до квадратных скобок с метаданными.
    """
    # Убираем префиксы в квадратных скобках в начале строки (например, [iPad], [iPhone])
    # Паттерн: [любой_текст] в начале строки, опционально с пробелами после
    title = re.sub(r'^\[[^\]]+\]\s*', '', title).strip()
    
    # Теперь ищем первую скобку (круглую или квадратную), которая не является префиксом
    # Сначала проверяем круглые скобки (обычно режиссер)
    match_round = re.search(r'\(', title)
    # Затем квадратные скобки (обычно метаданные)
    match_square = re.search(r'\[', title)
    
    # Выбираем ближайшую скобку
    if match_round and match_square:
        # Берем ту, которая ближе к началу
        if match_round.start() < match_square.start():
            return title[:match_round.start()].strip()
        else:
            return title[:match_square.start()].strip()
    elif match_round:
        return title[:match_round.start()].strip()
    elif match_square:
        return title[:match_square.start()].strip()
    
    return title.strip()


def extract_year(title: str) -> int:
    """Извлекает год выпуска фильма из названия."""
    # Приоритет 1: Ищем год в квадратных скобках [2005, ...] или [2005]
    # Это самый надежный способ, так как год обычно указывается в метаданных
    match = re.search(r'\[(\d{4})', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass
    
    # Приоритет 2: Ищем год в круглых скобках после названия фильма (2005)
    # Обычно формат: "Название фильма (2005)" или "Название / Name (2005)"
    # Исключаем случаи, когда это часть других конструкций типа "(custom)", "(Eng)"
    match = re.search(r'\)\s*\((\d{4})\)', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass
    
    # Приоритет 3: Ищем год в круглых скобках, но не в конце строки после других скобок
    # Формат: "Название (2005) ..."
    # Исключаем короткие слова в скобках типа "(Eng)", "(Rus)", "(US)", "(custom)"
    match = re.search(r'\((\d{4})\)', title)
    if match:
        try:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass
    
    # Приоритет 4: Ищем просто 4 цифры подряд (год обычно между 1900-2100)
    # Но только если они не являются частью других чисел (например, 1080p, 720p, DVD5)
    # Исключаем числа, за которыми следует 'p' (разрешение)
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
        return "4K"  # Компактное обозначение для 2160p
    elif resolution == 1080:
        return "HD"  # Компактное обозначение для 1080p
    return ""


def has_dv(title: str) -> bool:
    """Проверяет наличие Dolby Vision в названии."""
    # Ищем "DV" как отдельное слово (не внутри других слов) или "Dolby Vision"
    # Используем границы слов, чтобы не находить "DV" в "Dub", "DVD" и т.д.
    return bool(re.search(r'\bDV\b|Dolby[\s-]?Vision', title, re.IGNORECASE))


def has_hdr(title: str) -> bool:
    """Проверяет наличие HDR в названии."""
    return bool(re.search(r'HDR|HDR10|HDR10\+', title, re.IGNORECASE))


def get_hdr_dv_icons(title: str) -> str:
    """Возвращает значки HDR/DV для экономии места."""
    icons = []
    if has_dv(title):
        icons.append("DV")  # Dolby Vision
    elif has_hdr(title):
        icons.append("HDR")  # HDR
    return " ".join(icons) if icons else ""


def is_bdremux(title: str) -> bool:
    """Проверяет является ли торрент BDRemux."""
    return bool(re.search(r'BDRemux|BD[\s-]?Remux', title, re.IGNORECASE))


def is_rip(title: str) -> bool:
    """Проверяет является ли торрент rip."""
    rip_patterns = [
        r'BDRip', r'WEBRip', r'DVDRip', r'HDTVRip',
        r'Rip', r'\.rip\b'
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


def calculate_priority(torrent: Dict[str, Any]) -> int:
    """
    Вычисляет приоритет торрента для сортировки.
    Чем выше значение, тем выше приоритет.
    """
    title = torrent.get('title', '').upper()
    priority = 0
    
    # Приоритет по разрешению: 2160 > 1080
    resolution = extract_resolution(title)
    if resolution == 2160:
        priority += 1000
    elif resolution == 1080:
        priority += 500
    
    # Приоритет по типу источника: BDRemux > WEB-DL > rip
    # Тип источника важнее качества (DV/HDR), поэтому проверяем его первым
    if is_bdremux(title):
        priority += 500  # BDRemux имеет приоритет над DV/HDR
    elif is_webdl(title):
        priority += 40  # WEB-DL менее приоритетный чем BDRemux, но выше обычного rip
    elif is_rip(title):
        priority += 30
    
    # Приоритет по качеству: Hybrid > DV > HDR > обычный
    # Учитывается только внутри одного типа источника
    if has_hybrid(title):
        priority += 50  # Hybrid имеет самый высокий приоритет (обычно DV + HDR)
    elif has_dv(title):
        priority += 30  # DV внутри типа источника
    elif has_hdr(title):
        priority += 20  # HDR внутри типа источника
    
    return priority


def prioritize_torrents(torrents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Сортирует торренты по приоритету.
    Сначала 2160 DV/HDR, затем 1080, предпочтение BDRemux.
    """
    # Добавляем приоритет к каждому торренту
    for torrent in torrents:
        torrent['_priority'] = calculate_priority(torrent)
    
    # Сортируем по приоритету (убывание)
    sorted_torrents = sorted(torrents, key=lambda x: x.get('_priority', 0), reverse=True)
    
    # Удаляем временное поле приоритета
    for torrent in sorted_torrents:
        torrent.pop('_priority', None)
    
    return sorted_torrents


def filter_torrents(torrents: List[Dict[str, Any]], max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Фильтрует и ограничивает количество результатов.
    Исключает торренты с Blu-ray disc.
    
    Для фильмов: если есть 4K, показывает только 4K. Если нет 4K, показывает 1080p.
    Для сериалов (если в title есть "Сезон"): показывает и 4K, и 1080p, чтобы видеть все сезоны.
    """
    if not torrents:
        return []
    
    # Исключаем торренты с Blu-ray disc
    filtered = [
        t for t in torrents
        if not is_bluray_disc(t.get('title', ''))
    ]
    
    if not filtered:
        return []
    
    # Проверяем, является ли это сериалом (есть слово "Сезон" в title)
    # Считаем сериалом, если большинство результатов содержат "Сезон"
    serial_count = sum(
        1 for t in filtered
        if re.search(r'\bСезон\b', t.get('title', ''), re.IGNORECASE)
    )
    is_serial = serial_count > len(filtered) / 2  # Больше половины - сериал
    
    # Сортируем по приоритету
    prioritized = prioritize_torrents(filtered)
    
    # Для сериалов показываем и 4K, и 1080p, чтобы видеть все сезоны
    if is_serial:
        result = []
        
        # Собираем все 4K торренты
        high_quality_4k = [
            t for t in prioritized
            if extract_resolution(t.get('title', '')) == 2160
            and (has_dv(t.get('title', '')) or has_hdr(t.get('title', '')))
        ]
        
        uhd_torrents = [
            t for t in prioritized
            if extract_resolution(t.get('title', '')) == 2160
            and t not in high_quality_4k
        ]
        
        all_4k = high_quality_4k + uhd_torrents
        
        # Собираем все 1080p торренты
        fhd_bdremux = [
            t for t in prioritized
            if extract_resolution(t.get('title', '')) == 1080
            and is_bdremux(t.get('title', ''))
        ]
        
        fhd_torrents = [
            t for t in prioritized
            if extract_resolution(t.get('title', '')) == 1080
            and t not in fhd_bdremux
        ]
        
        all_1080 = fhd_bdremux + fhd_torrents
        
        # Если есть оба типа, ограничиваем количество каждого, чтобы показать оба
        if all_4k and all_1080:
            # Распределяем места: 60% для 4K, 40% для 1080p (но минимум по 3 каждого)
            max_4k = max(3, int(max_results * 0.6))
            max_1080 = max(3, max_results - max_4k)
            
            result.extend(all_4k[:max_4k])
            result.extend(all_1080[:max_1080])
        elif all_4k:
            # Если есть только 4K, показываем их
            result.extend(all_4k[:max_results])
        elif all_1080:
            # Если есть только 1080p, показываем их
            result.extend(all_1080[:max_results])
        
        # Возвращаем до max_results (на случай если сумма превысила лимит)
        return result[:max_results]
    
    # Для фильмов используем стандартную логику с приоритетом 4K
    # Сначала проверяем, есть ли вообще 4K торренты
    all_4k_torrents = [
        t for t in prioritized
        if extract_resolution(t.get('title', '')) == 2160
    ]
    
    # Если есть 4K, возвращаем ТОЛЬКО их (приоритет DV/HDR), исключая все остальное
    if all_4k_torrents:
        # Сначала ищем 4K с DV или HDR
        high_quality_4k = [
            t for t in all_4k_torrents
            if has_dv(t.get('title', '')) or has_hdr(t.get('title', ''))
        ]
        
        if high_quality_4k:
            # Если есть 4K DV/HDR, возвращаем ТОЛЬКО их (до max_results)
            return high_quality_4k[:max_results]
        else:
            # Если нет 4K DV/HDR, возвращаем ТОЛЬКО любые 4K
            return all_4k_torrents[:max_results]
    
    # Если нет 4K, ищем ТОЛЬКО 1080p
    # Сначала ищем 1080 BDRemux
    fhd_bdremux = [
        t for t in prioritized
        if extract_resolution(t.get('title', '')) == 1080
        and is_bdremux(t.get('title', ''))
    ]
    
    if fhd_bdremux:
        return fhd_bdremux[:max_results]
    
    # Если нет 1080 BDRemux, возвращаем ТОЛЬКО любые 1080
    fhd_torrents = [
        t for t in prioritized
        if extract_resolution(t.get('title', '')) == 1080
    ]
    
    if fhd_torrents:
        return fhd_torrents[:max_results]
    
    # Если ничего не найдено (ни 4K, ни 1080p), возвращаем первые max_results
    # Но это не должно происходить для нормальных фильмов
    return prioritized[:max_results]

