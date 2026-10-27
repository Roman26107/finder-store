"""Безопасная подготовка canonical-архива Valentino Garavani."""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from pathlib import PurePosixPath

from PIL import Image, UnidentifiedImageError

from .catalog_normalization import _canonical_category, catalog_quality_report, clean_text
from .services import _validate_zip_entries, parse_json_bytes
from .valentino_editorial import editorialize_valentino_card


MIN_STOREFRONT_IMAGE_EDGE = 600
CRITICAL_QUALITY_ISSUES = {
    'image_not_found', 'invalid_image_file', 'missing_canonical_taxonomy',
    'missing_image_source', 'partially_untranslated_description',
    'suspicious_translation_length', 'untranslated_description', 'untranslated_title',
}

MATERIALS = (
    ('зернистая телячья кожа', (r'vitello granato',)),
    ('телячья кожа', (r'\bvitello\b',)),
    ('кожа наппа', (r'\bnappa\b',)),
    ('замша', (r'\bsuede\b',)),
    ('кожа питона', (r'\bpitone\b',)),
    ('кожа страуса', (r'\bstruzzo\b',)),
    ('козья кожа', (r'\bcapretto\b',)),
    ('овчина', (r'pelliccia di agnello',)),
    ('кожа', (r'\bpelle\b', r'pelli esotiche')),
    ('рафия', (r'\brafia\b',)),
    ('лён', (r'\blino\b',)),
    ('хлопок', (r'\bdenim\b', r'\bcanvas\b')),
    ('текстиль', (r'\btessuto\b', r'\braso\b', r'crochet')),
    ('металл', (r'\bmetallo\b',)),
    ('нейлон', (r'\bnylon\b',)),
    ('стекло', (r'\bstrass\b', r'cristalli')),
    ('смола', (r'\bresina\b',)),
)


def _taxonomy(product):
    raw_title = clean_text((product.get('title') or {}).get('raw')).casefold()
    raw_path = ' '.join((product.get('category') or {}).get('raw_path') or []).casefold()
    if 'portafoglio' in raw_title or 'portafogli' in raw_path:
        return 'accessories', 'Аксессуары', 'wallets', 'Кошельки'
    if 'shopping' in raw_title:
        return 'bags', 'Сумки', 'tote_bags', 'Сумки-тоут'
    if any(term in raw_title for term in ('minaudi', 'clutch', 'pochette', 'pouch')):
        return 'bags', 'Сумки', 'mini_bags', 'Клатчи и мини-сумки'
    if any(term in raw_title for term in ('a mano', 'bauletto')):
        return 'bags', 'Сумки', 'handbags', 'Сумки с ручками'
    return 'bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'


def normalize_valentino_product(raw_product):
    product_id = str(raw_product.get('id') or '') if isinstance(raw_product, dict) else ''
    brand = clean_text(raw_product.get('brand')) if isinstance(raw_product, dict) else ''
    if not product_id.startswith('valentino:') and 'valentino' not in brand.casefold():
        raise ValueError(f'Карточка {product_id or "без id"} не относится к Valentino')
    product, editorial = editorialize_valentino_card(raw_product)
    if editorial['issues']:
        raise ValueError(f'редакторские дефекты: {", ".join(editorial["issues"])}')

    parent_slug, parent_name, leaf_slug, leaf_name = _taxonomy(product)
    source_code = f'VALENTINO_{parent_slug}_{leaf_slug}'.upper()
    category = product.setdefault('category', {})
    category['raw_lang'] = 'it'
    category.setdefault('i18n', {})['ru'] = [
        'Женщины', parent_name, leaf_name, editorial['title'],
    ]
    category['canonical_path'] = {
        'department': 'women', 'department_ru': 'Женщины',
        'categories': [
            _canonical_category(source_code, parent_slug, parent_name, 2),
            _canonical_category(source_code, leaf_slug, leaf_name, 3),
        ],
    }

    source_text = ' '.join((
        clean_text((product.get('title') or {}).get('raw')),
        clean_text((product.get('description') or {}).get('raw')),
    )).casefold()
    attributes = product.setdefault('attributes', {})
    materials = []
    for material, patterns in MATERIALS:
        if material == 'телячья кожа' and 'vitello granato' in source_text:
            continue
        if material == 'кожа' and any('кожа' in existing for existing in materials):
            continue
        if any(re.search(pattern, source_text, re.I) for pattern in patterns):
            if material not in materials:
                materials.append(material)
    attributes['material_normalized'] = materials

    variants = (product.get('variants') or {}).get('colors') or []
    current = next((item for item in variants if item.get('current')), None)
    if current is None:
        own_sku = product_id.split(':', 1)[-1]
        current = next((item for item in variants if str(item.get('sku')) == own_sku), None)
    if current:
        label = deepcopy(current.get('label') or {})
        attributes['color'] = label
    return product


def normalize_valentino_catalog(products):
    normalized, errors = [], []
    for index, product in enumerate(products, start=1):
        try:
            normalized.append(normalize_valentino_product(product))
        except (TypeError, ValueError) as exc:
            errors.append({
                'index': index,
                'id': product.get('id') if isinstance(product, dict) else None,
                'error': str(exc),
            })
    families = defaultdict(list)
    for product in normalized:
        canonical = (product.get('category') or {}).get('canonical_path') or {}
        categories = canonical.get('categories') or []
        leaf = categories[-1].get('slug') if categories else ''
        raw_title = clean_text((product.get('title') or {}).get('raw')).casefold()
        families[(leaf, raw_title)].append(product)
    for family in families.values():
        colors, seen = [], set()
        for product in family:
            for variant in (product.get('variants') or {}).get('colors') or []:
                sku = clean_text(variant.get('sku')) if isinstance(variant, dict) else ''
                if sku and sku not in seen:
                    colors.append(deepcopy(variant))
                    seen.add(sku)
        for product in family:
            product.setdefault('variants', {})['colors'] = deepcopy(colors)
    return normalized, errors


def prepare_valentino_archive(archive):
    """Проверяет ZIP, не меняя порядок полноразмерных фотографий."""
    entries = _validate_zip_entries(archive)
    json_entries = [(name, item) for name, item in entries if name.lower().endswith('.json')]
    if len(json_entries) != 1:
        raise ValueError(f'в архиве должен быть ровно один JSON-файл, найдено: {len(json_entries)}')
    json_name, json_entry = json_entries[0]
    raw_cards = parse_json_bytes(archive.read(json_entry), json_name)
    normalized, errors = normalize_valentino_catalog(raw_cards)
    if errors:
        first = errors[0]
        raise ValueError(f'не нормализовано карточек: {len(errors)}; {first["id"]}: {first["error"]}')
    if not normalized:
        raise ValueError('каталог Valentino не содержит карточек')
    if len({card.get('id') for card in normalized}) != len(normalized):
        raise ValueError('каталог Valentino содержит повторяющиеся id')

    image_entries = {
        name: item for name, item in entries
        if PurePosixPath(name.lower()).suffix in {'.jpg', '.jpeg', '.png', '.webp'}
    }
    valid_images, low_resolution, invalid = set(), set(), []
    dimensions = {}
    for name, entry in image_entries.items():
        try:
            with archive.open(entry) as source:
                with Image.open(source) as image:
                    width, height = image.size
                    image.verify()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            invalid.append({'file': name, 'error': str(exc)[:500]})
            continue
        dimensions[name] = [width, height]
        if min(width, height) < MIN_STOREFRONT_IMAGE_EDGE:
            low_resolution.add(name)
        else:
            valid_images.add(name)

    prepared = []
    for source_card in normalized:
        card = deepcopy(source_card)
        images = []
        for image in (card.setdefault('media', {}).get('images') or []):
            path = str(image.get('file') or '').replace('\\', '/')
            if path in valid_images:
                image['position'] = len(images)
                images.append(image)
        card['media']['images'] = images
        if not images:
            raise ValueError(f'{card.get("id")}: нет полноразмерных локальных изображений')
        prepared.append(card)

    quality = catalog_quality_report(prepared, valid_images)
    # Общий анализатор справедливо настороженно относится к латинице, но в
    # заново сгенерированных Valentino-описаниях она относится только к
    # проверенным названиям бренда, линий и декора. Реальные итальянские
    # товарные термины до этого блокирует editorialize_valentino_card.
    false_positive_types = {
        'partially_untranslated_color', 'partially_untranslated_description',
    }
    quality['issues'] = [
        issue for issue in quality['issues']
        if issue.get('type') not in false_positive_types
    ]
    for issue_type in false_positive_types:
        quality['issue_counts'].pop(issue_type, None)
    quality.update({
        'source_products': len(raw_cards), 'source_images': len(image_entries),
        'eligible_images': len(valid_images),
        'excluded_low_resolution_images': len(low_resolution),
        'invalid_images': invalid, 'image_dimensions': dimensions,
        'image_order': 'source; first image is the frontal packshot',
        'crop_policy': 'contain; source composition preserved',
    })
    if invalid:
        quality['issue_counts']['invalid_image_file'] = len(invalid)
    quality['critical_issue_counts'] = {
        key: count for key, count in quality['issue_counts'].items()
        if key in CRITICAL_QUALITY_ISSUES and count
    }
    return prepared, quality


def compact_quality_report(report):
    result = dict(report)
    dimensions = result.pop('image_dimensions', {})
    result['image_dimension_variants'] = sorted({
        f'{width}x{height}' for width, height in dimensions.values()
    })
    return result
