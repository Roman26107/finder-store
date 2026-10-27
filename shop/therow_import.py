"""Безопасная подготовка полного экспорта The Row перед заменой каталога."""

from copy import deepcopy
from pathlib import PurePosixPath

from PIL import Image, UnidentifiedImageError

from .catalog_normalization import catalog_quality_report, normalize_therow_catalog
from .services import _validate_zip_entries, parse_json_bytes, storefront_image_order


MIN_STOREFRONT_IMAGE_EDGE = 300
CRITICAL_QUALITY_ISSUES = {
    'image_not_found',
    'missing_canonical_taxonomy',
    'missing_image_source',
    'partially_untranslated_description',
    'suspicious_title_translation',
    'suspicious_translation',
    'suspicious_translation_length',
    'untranslated_description',
    'untranslated_title',
}


def prepare_therow_archive(archive):
    """Проверяет ZIP и возвращает нормализованные карточки и отчёт качества."""
    entries = _validate_zip_entries(archive)
    json_entries = [(name, item) for name, item in entries if name.lower().endswith('.json')]
    if len(json_entries) != 1:
        raise ValueError(
            f'в архиве должен быть ровно один JSON-файл, найдено: {len(json_entries)}'
        )
    json_name, json_entry = json_entries[0]
    raw_cards = parse_json_bytes(archive.read(json_entry), json_name)
    normalized, normalization_errors = normalize_therow_catalog(raw_cards)
    if normalization_errors:
        first = normalization_errors[0]
        raise ValueError(
            f'не нормализовано карточек: {len(normalization_errors)}; '
            f'{first.get("id")}: {first.get("error")}'
        )
    if not normalized:
        raise ValueError('каталог The Row не содержит карточек')
    if len({card.get('id') for card in normalized}) != len(normalized):
        raise ValueError('каталог The Row содержит повторяющиеся id')

    image_entries = {
        name: item for name, item in entries
        if PurePosixPath(name.lower()).suffix in {'.jpg', '.jpeg', '.png', '.webp'}
    }
    valid_images = set()
    low_resolution_images = set()
    invalid_images = []
    image_dimensions = {}
    for name, entry in image_entries.items():
        try:
            with archive.open(entry) as source:
                with Image.open(source) as image:
                    width, height = image.size
                    image.verify()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            invalid_images.append({'file': name, 'error': str(exc)[:500]})
            continue
        image_dimensions[name] = [width, height]
        if min(width, height) < MIN_STOREFRONT_IMAGE_EDGE:
            low_resolution_images.add(name)
        else:
            valid_images.add(name)

    prepared = []
    excluded_without_file = 0
    for source_card in normalized:
        card = deepcopy(source_card)
        media = card.setdefault('media', {})
        suitable = []
        for image in media.get('images') or []:
            path = str(image.get('file') or '').replace('\\', '/')
            if not path:
                excluded_without_file += 1
                continue
            if path in valid_images:
                suitable.append(image)
        suitable = storefront_image_order(suitable)
        for position, image in enumerate(suitable):
            image['position'] = position
        media['images'] = suitable
        if not suitable:
            raise ValueError(f'{card.get("id")}: нет полноразмерных локальных изображений')
        prepared.append(card)

    quality = catalog_quality_report(prepared, valid_images)
    quality.update({
        'source_products': len(raw_cards),
        'source_images': len(image_entries),
        'eligible_images': len(valid_images),
        'excluded_low_resolution_images': len(low_resolution_images),
        'excluded_images_without_file': excluded_without_file,
        'invalid_images': invalid_images,
        'image_dimensions': image_dimensions,
    })
    if invalid_images:
        quality['issue_counts']['invalid_image_file'] = len(invalid_images)
    critical = {
        key: count for key, count in quality['issue_counts'].items()
        if key in CRITICAL_QUALITY_ISSUES and count
    }
    if invalid_images:
        critical['invalid_image_file'] = len(invalid_images)
    quality['critical_issue_counts'] = critical
    return prepared, quality


def compact_quality_report(report):
    """Убирает тяжёлую карту размеров из итогового JSON-отчёта."""
    result = dict(report)
    dimensions = result.pop('image_dimensions', {})
    result['image_dimension_variants'] = sorted({
        f'{width}x{height}' for width, height in dimensions.values()
    })
    return result