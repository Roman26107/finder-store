import json
from io import BytesIO
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils.text import get_valid_filename, slugify

from .catalog_data import product_catalog_metadata, rebuild_product_catalog_metadata
from .image_fields import optimized_webp
from .image_processing import (
    create_product_image_detail, create_product_image_gallery,
    create_product_image_preview,
)
from .models import (
    CatalogBrand, CatalogCategory, CatalogColor, CatalogMaterial,
    ColorVariant, Product, ProductImage, SizeVariant,
)
from .pricing import normalize_currency


MOJIBAKE_MARKERS = ('Ã', 'Â', 'Ð', 'Ñ', 'é¢', 'è²', 'å', 'ä', 'ç', 'æ')
KNOWN_BROKEN_VALUES = {
    # Встречается в исходных Hermès-карточках в поле color_label.
    'é¢è²': '颜色',
}
ZIP_MAX_FILES = 20_000
ZIP_MAX_UNCOMPRESSED_SIZE = 3 * 1024 * 1024 * 1024
ZIP_MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024
ZIP_MAX_COMPRESSION_RATIO = 250
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'}


@dataclass
class ZipImportReport:
    archive_name: str
    json_files: int = 0
    image_files: int = 0
    cards: int = 0
    created: int = 0
    updated: int = 0
    images_attached: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def fix_mojibake(value):
    """Осторожно исправляет строки, ошибочно декодированные как latin1."""
    if isinstance(value, list):
        return [fix_mojibake(item) for item in value]
    if isinstance(value, dict):
        return {key: fix_mojibake(item) for key, item in value.items()}
    if value in KNOWN_BROKEN_VALUES:
        return KNOWN_BROKEN_VALUES[value]
    if not isinstance(value, str) or not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value
    for source_encoding in ('latin1', 'cp1252'):
        try:
            candidate = value.encode(source_encoding).decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate.count('�') == 0:
            return candidate
    return value


def decimal_value(value):
    if value in (None, ''):
        return Decimal('0')
    try:
        return Decimal(str(value).replace(' ', '').replace(',', '.'))
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _localized(data, field, default=''):
    ru = (data.get('i18n') or {}).get('ru') or {}
    return ru.get(field) or data.get(field) or default


def _canonical_value(value, default=''):
    """Извлекает русский перевод или исходное значение canonical-поля."""
    if not isinstance(value, dict):
        return value if value not in (None, '') else default
    localized = (value.get('i18n') or {}).get('ru')
    if localized not in (None, ''):
        return localized
    return value.get('raw') if value.get('raw') not in (None, '') else default


def _in_stock(value):
    if isinstance(value, dict):
        return value.get('status') == 'in_stock'
    return bool(value)


def _normalize_gender(value):
    aliases = {
        '女士': 'women',
        '女装': 'women',
        'female': 'women',
        'женщины': 'women',
        '男士': 'men',
        '男装': 'men',
        'male': 'men',
        'мужчины': 'men',
    }
    values = value if isinstance(value, list) else [value] if value else []
    result = []
    for item in values:
        normalized = aliases.get(str(item).strip().lower(), str(item).strip().lower())
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _article_from_sku(value):
    """Убирает служебный префикс источника из канонического SKU."""
    return str(value or '').strip().split(':', 1)[-1]


def _canonical_to_source(data):
    """Поддержка унифицированной карточки normalize_products.py."""
    if 'sku' in data or 'id' not in data:
        return data
    attributes = data.get('attributes') or {}
    availability = data.get('availability') or {}
    media = data.get('media') or {}
    variants = data.get('variants') or {}
    price = data.get('price') or {}
    canonical_images = media.get('images', media if isinstance(media, list) else [])
    images = []
    for index, image in enumerate(canonical_images or []):
        if not isinstance(image, dict):
            continue
        urls = image.get('urls') or {}
        images.append({
            'tag': image.get('role') or image.get('tag') or '',
            'position': image.get('position', index),
            'thumbnail': image.get('thumbnail') or urls.get('thumbnail', ''),
            'medium': image.get('medium') or urls.get('medium', ''),
            'large': image.get('large') or urls.get('large', ''),
            'original': image.get('original') or urls.get('original', ''),
            'local_path': image.get('file') or image.get('local_path', ''),
        })

    colors = []
    for variant in variants.get('colors', []):
        variant_price = variant.get('price') or {}
        colors.append({
            'sku': str(variant.get('sku') or ''),
            'color': _canonical_value(variant.get('label')),
            'price': (
                variant_price.get('amount', 0)
                if isinstance(variant_price, dict) else variant_price
            ),
            'url': variant.get('url', ''),
            'in_stock_ecom': _in_stock(variant.get('availability')),
            'thumbnail': variant.get('thumbnail') or '',
        })

    sizes = []
    for variant in variants.get('sizes', []):
        variant_price = variant.get('price') or {}
        sizes.append({
            'sku': str(variant.get('sku') or ''),
            'size': str(_canonical_value(
                variant.get('label') or variant.get('size') or variant.get('value')
            )),
            'price': (
                variant_price.get('amount', 0)
                if isinstance(variant_price, dict) else variant_price
            ),
            'in_stock_ecom': _in_stock(variant.get('availability')),
        })

    category = data.get('category') or {}
    breadcrumbs = category.get('raw_path', []) if isinstance(category, dict) else []
    translated_path = (
        (category.get('i18n') or {}).get('ru') or []
        if isinstance(category, dict) else []
    )
    category_name = (
        translated_path[-2] if len(translated_path) > 1
        else (translated_path[-1] if translated_path else category)
    )
    current_variant = next(
        (variant for variant, original in zip(colors, variants.get('colors', []))
         if original.get('current')),
        colors[0] if colors else {},
    )
    current_size_variant = next(
        (variant for variant in sizes if variant.get('in_stock_ecom')),
        sizes[0] if sizes else {},
    )
    normalized_from = data.get('normalized_from') or {}
    source = data.get('source') or {}
    material = (
        attributes.get('material_normalized')
        if 'material_normalized' in attributes
        else _canonical_value(attributes.get('material'), [])
    )
    if isinstance(material, str):
        material = [material]
    gender = _normalize_gender(data.get('gender'))
    return {
        **data,
        'sku': str(data.get('id') or data.get('normalized_from') or ''),
        'sku_without_size': (
            _article_from_sku(data.get('id') or data.get('normalized_from'))
            or current_variant.get('sku') or None
        ),
        'url': (
            data.get('url')
            or (source.get('url') if isinstance(source, dict) else '')
            or current_variant.get('url', '')
        ),
        'title': _canonical_value(data.get('title'), f"Товар {data.get('id', '')}"),
        'description': _canonical_value(data.get('description')),
        'price': price.get('amount', price if isinstance(price, (int, float, str)) else 0),
        'currency': price.get('currency', 'CNY') if isinstance(price, dict) else 'CNY',
        'current_color': _canonical_value(attributes.get('color')) or current_variant.get('color', ''),
        'current_size': str(
            _canonical_value(attributes.get('size'))
            or current_size_variant.get('size')
            or 'SANS_TAILLE'
        ),
        'gender': gender,
        'material': material,
        'in_stock_ecom': _in_stock(availability),
        'dimensions': _canonical_value(attributes.get('dimensions')),
        'made_in': _canonical_value(attributes.get('made_in')),
        'categories_breadcrumbs': breadcrumbs,
        'category_name': category_name,
        'fetched_at': (
            normalized_from.get('fetched_at')
            if isinstance(normalized_from, dict) else None
        ),
        'images': images,
        'colors': colors,
        'sizes': sizes,
    }


def storefront_image_order(images):
    """Ставит третий кадр первым, сохраняя относительный порядок остальных."""
    images = list(images or [])
    return images[2:] + images[:2] if len(images) > 2 else images


def parse_json_bytes(content, filename='JSON-файл'):
    try:
        payload = json.loads(content.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{filename}: некорректный JSON ({exc})') from exc
    if isinstance(payload, dict):
        if isinstance(payload.get('products'), list):
            return payload['products']
        return [payload]
    if isinstance(payload, list):
        return payload
    raise ValueError(f'{filename}: ожидается объект или массив объектов')


def parse_json_file(uploaded_file):
    return parse_json_bytes(uploaded_file.read(), uploaded_file.name)


def _safe_archive_path(filename):
    """Нормализует имя ZIP entry и блокирует path traversal."""
    if not isinstance(filename, str) or '\x00' in filename:
        raise ValueError('архив содержит некорректное имя файла')
    normalized = filename.replace('\\', '/').strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith('/')
        or (len(normalized) > 1 and normalized[1] == ':')
        or '..' in path.parts
    ):
        raise ValueError(f'небезопасный путь в архиве: {filename}')
    return '/'.join(part for part in path.parts if part not in ('', '.'))


def _validate_zip_entries(archive, *, max_files=ZIP_MAX_FILES,
                          max_uncompressed_size=ZIP_MAX_UNCOMPRESSED_SIZE):
    entries = [item for item in archive.infolist() if not item.is_dir()]
    if len(entries) > max_files:
        raise ValueError(f'в архиве больше {max_files} файлов')
    total_size = 0
    validated = []
    for item in entries:
        safe_name = _safe_archive_path(item.filename)
        if item.file_size > ZIP_MAX_SINGLE_FILE_SIZE:
            raise ValueError(f'{safe_name}: файл больше допустимых 100 МБ')
        total_size += item.file_size
        if total_size > max_uncompressed_size:
            max_gib = max_uncompressed_size // (1024 * 1024 * 1024)
            raise ValueError(
                f'распакованный архив превышает допустимый размер {max_gib} ГБ'
            )
        compressed_size = max(item.compress_size, 1)
        if item.file_size > 1024 * 1024 and item.file_size / compressed_size > ZIP_MAX_COMPRESSION_RATIO:
            raise ValueError(f'{safe_name}: подозрительно высокий коэффициент сжатия')
        validated.append((safe_name, item))
    return validated


def _path_variants(value):
    if not value:
        return []
    value = unquote(str(value)).replace('\\', '/').split('?', 1)[0].split('#', 1)[0]
    if '://' in value:
        value = urlparse(value).path
    value = value.lstrip('/')
    # Убираем Windows drive из исходного local_path.
    if len(value) > 1 and value[1] == ':':
        value = value[2:].lstrip('/')
    parts = [part for part in value.split('/') if part not in ('', '.')]
    return ['/'.join(parts[index:]).lower() for index in range(len(parts))]


def _find_archive_image(image, image_entries, basename_index):
    sources = (
        image.source_local_path, image.original, image.large,
        image.medium, image.thumbnail,
    )
    for source in sources:
        for candidate in _path_variants(source):
            exact = image_entries.get(candidate)
            if exact:
                return exact
            suffix_matches = [
                entry for name, entry in image_entries.items()
                if name.endswith('/' + candidate)
            ]
            if len(suffix_matches) == 1:
                return suffix_matches[0]
        variants = _path_variants(source)
        if variants:
            basename_matches = basename_index.get(PurePosixPath(variants[0]).name, [])
            if len(basename_matches) == 1:
                return basename_matches[0]
    return None


def _attach_archive_images(archive, product, image_entries, basename_index):
    attached = 0
    sku_directory = slugify(product.sku)[:80] or f'product-{product.pk}'
    dolce_images = []
    for image in product.images.all():
        entry = _find_archive_image(image, image_entries, basename_index)
        if not entry:
            continue
        filename = get_valid_filename(PurePosixPath(entry.filename.replace('\\', '/')).name)
        if not filename:
            continue
        webp_name = f'{PurePosixPath(filename).stem}.webp'
        relative_name = f'zip/{sku_directory}/{webp_name}'
        # При повторном импорте перезаписываем стабильный путь вместо
        # создания копий вида image_ABC123.webp.
        storage_name = image.local_image.field.generate_filename(image, relative_name)
        image.local_image.storage.delete(storage_name)
        if product.brand == 'dolce-gabbana':
            # One optimized 1600px WebP serves all image URLs for this brand.
            # Avoid redundant previews/galleries and per-image SQL writes.
            image.local_image.name = image.local_image.storage.save(
                storage_name,
                ContentFile(optimized_webp(BytesIO(archive.read(entry)), method=0)),
            )
            image.detail_processed = True
            dolce_images.append(image)
            attached += 1
            continue
        image.local_image.save(
            relative_name,
            ContentFile(archive.read(entry)),
            save=True,
        )
        create_product_image_preview(image, force=True)
        if product.brand in {'the-row', 'valentino-garavani', 'off-white'}:
            # Эти бренды поставляют готовые белофоновые кадры. Не вырезаем товар и
            # не меняем фон: gallery сохраняет исходную композицию целиком.
            create_product_image_gallery(image, force=True)
            image.detail_processed = True
            image.save(update_fields=('detail_processed',))
        else:
            create_product_image_detail(image, force=True)
        attached += 1
    if dolce_images:
        ProductImage.objects.bulk_update(dolce_images, ('local_image', 'detail_processed'))
    return attached


def _delete_unreferenced_image_files(files):
    """Удаляет заменённые файлы только после успешного commit и без живых ссылок."""
    for storage, name in files:
        referenced = ProductImage.objects.filter(
            Q(local_image=name) | Q(preview_image=name) |
            Q(gallery_image=name) | Q(detail_image=name)
        ).exists()
        if name and not referenced:
            storage.delete(name)


def import_zip_archive(
    uploaded_file, replace_relations=True, extract_images=True, progress_callback=None
):
    """Импортирует JSON и связанные изображения из загруженного ZIP."""
    report = ZipImportReport(archive_name=uploaded_file.name)
    try:
        uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file) as archive:
            entries = _validate_zip_entries(archive)
            json_entries = [(name, item) for name, item in entries if name.lower().endswith('.json')]
            image_pairs = [
                (name, item) for name, item in entries
                if PurePosixPath(name.lower()).suffix in IMAGE_EXTENSIONS
            ]
            report.json_files = len(json_entries)
            report.image_files = len(image_pairs)
            if not json_entries:
                raise ValueError('в архиве не найдено ни одного JSON-файла')

            image_entries = {name.lower(): item for name, item in image_pairs}
            basename_index = defaultdict(list)
            for name, item in image_pairs:
                basename_index[PurePosixPath(name.lower()).name].append(item)

            imported_brands = set()
            for json_name, json_entry in json_entries:
                try:
                    cards = parse_json_bytes(archive.read(json_entry), json_name)
                except ValueError as exc:
                    report.errors.append(str(exc))
                    continue
                for index, card in enumerate(cards, start=1):
                    report.cards += 1
                    if not isinstance(card, dict):
                        report.errors.append(f'{json_name}, карточка {index}: ожидается объект')
                        continue
                    imported_brands.add(
                        slugify(str(card.get('brand') or 'hermes').strip()) or 'hermes'
                    )
                    try:
                        with transaction.atomic():
                            product, was_created = import_product(card, replace_relations)
                            if extract_images:
                                report.images_attached += _attach_archive_images(
                                    archive, product, image_entries, basename_index
                                )
                        report.created += int(was_created)
                        report.updated += int(not was_created)
                    except (ValueError, TypeError, OSError) as exc:
                        report.errors.append(f'{json_name}, карточка {index}: {exc}')
                    if progress_callback:
                        progress_callback(report)
            if extract_images and image_pairs and not report.images_attached:
                report.warnings.append(
                    'Изображения найдены, но ни одно не сопоставлено с local_path/URL карточек.'
                )
            if imported_brands:
                rebuild_product_catalog_metadata(Product.objects.filter(brand__in=imported_brands))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError(f'{uploaded_file.name}: повреждённый или неподдерживаемый ZIP') from exc
    except ValueError as exc:
        raise ValueError(f'{uploaded_file.name}: {exc}') from exc
    return report


@transaction.atomic
def import_product(raw_data, replace_relations=True):
    data = fix_mojibake(_canonical_to_source(raw_data))
    sku = str(data.get('sku') or '').strip()
    if not sku:
        raise ValueError('У карточки отсутствует обязательное поле sku')

    breadcrumbs = data.get('categories_breadcrumbs') or []
    category_name = data.get('category_name') or data.get('category') or ''
    if isinstance(category_name, dict):
        category_name = category_name.get('name') or category_name.get('value') or ''
    if not category_name and breadcrumbs:
        category_name = breadcrumbs[-1].get('value', '')

    catalog_metadata = product_catalog_metadata(
        raw_data,
        fallback_category=category_name,
        fallback_breadcrumbs=breadcrumbs,
        fallback_color=data.get('current_color', ''),
    )

    defaults = {
        'brand': slugify(str(data.get('brand') or 'hermes').strip()) or 'hermes',
        'source_url': data.get('url') or data.get('source_url') or '',
        'sku_without_size': (
            data.get('sku_without_size') or data.get('article')
            or _article_from_sku(sku) or None
        ),
        'product_code': str(data.get('product_code') or ''),
        'title': _localized(data, 'title', f'Товар {sku}'),
        'price': decimal_value(data.get('price')),
        'currency': normalize_currency(data.get('currency') or 'CNY'),
        'current_color': catalog_metadata['current_color'] or _localized(
            data, 'current_color', data.get('current_color', '')
        ),
        'current_size': str(data.get('current_size') or 'SANS_TAILLE'),
        'gender': _normalize_gender(data.get('gender')),
        'material': data.get('material') or [],
        'in_stock_ecom': bool(data.get('in_stock_ecom', False)),
        'description': _localized(data, 'description'),
        'dimensions': _localized(data, 'dimensions'),
        'made_in': _localized(data, 'made_in'),
        'color_label': _localized(data, 'color_label'),
        'product_detail_html': _localized(data, 'product_detail_html'),
        'care_instruction_html': _localized(data, 'care_instruction_html'),
        'story_behind': _localized(data, 'story_behind', None),
        'categories_breadcrumbs': breadcrumbs,
        'category_name': catalog_metadata['category_name'] or category_name,
        'category_department_slug': catalog_metadata['category_department_slug'],
        'category_department_name': catalog_metadata['category_department_name'],
        'category_path': catalog_metadata['category_path'],
        'category_path_key': catalog_metadata['category_path_key'],
        'category_keys': catalog_metadata['category_keys'],
        'color_group': catalog_metadata['color_group'],
        'family_key': catalog_metadata['family_key'],
        'collection': catalog_metadata['collection'],
        'fetched_at': data.get('fetched_at'),
        'i18n': data.get('i18n') or {},
        # Храним оригинал без изменений, чтобы карточку можно было
        # повторно нормализовать другими правилами в будущем.
        'raw_data': raw_data,
    }
    product, created = Product.objects.update_or_create(sku=sku, defaults=defaults)

    def available_slug(model, value, max_length):
        base = (slugify(str(value)) or 'item')[:max_length]
        candidate = base
        index = 2
        while model.objects.filter(slug=candidate).exists():
            suffix = f'-{index}'
            candidate = f'{base[:max_length-len(suffix)]}{suffix}'
            index += 1
        return candidate

    if not CatalogBrand.objects.filter(slug=product.brand).exists():
        brand_names = {
            'hermes': 'Hermès', 'miu-miu': 'Miu Miu', 'the-row': 'The Row',
            'valentino-garavani': 'Valentino Garavani', 'off-white': 'Off-White',
            'dolce-gabbana': 'Dolce & Gabbana',
        }
        CatalogBrand.objects.create(
            name=brand_names.get(product.brand, product.brand.title()),
            slug=product.brand,
        )
    if product.current_color and not CatalogColor.objects.filter(name__iexact=product.current_color).exists():
        CatalogColor.objects.create(
            name=product.current_color,
            slug=available_slug(CatalogColor, product.current_color, 80),
            group_slug=product.color_group or 'other',
        )
    for material_name in product.material or []:
        material_name = str(material_name).strip()
        if material_name and not CatalogMaterial.objects.filter(name__iexact=material_name).exists():
            CatalogMaterial.objects.create(
                name=material_name,
                slug=available_slug(CatalogMaterial, material_name, 100),
            )
    category_key = product.category_path_key or (
        (product.category_keys or [''])[-1]
    )
    if product.category_name and category_key:
        CatalogCategory.objects.update_or_create(key=category_key, defaults={
            'name': product.category_name,
            'department_slug': product.category_department_slug,
            'department_name': product.category_department_name,
            'path': product.category_path or [],
        })

    stale_image_files = []
    relation_map = (
        ('images', ProductImage, product.images, lambda item, index: {
            'tag': item.get('tag') or '', 'position': item.get('position', index),
            'thumbnail': item.get('thumbnail') or '', 'medium': item.get('medium') or '',
            'large': item.get('large') or '', 'original': item.get('original') or '',
            'source_local_path': item.get('local_path') or '', 'alt': product.title,
        }),
        ('colors', ColorVariant, product.color_variants, lambda item, index: {
            'sku': str(item.get('sku') or ''), 'color': item.get('color') or '',
            'price': decimal_value(item.get('price')), 'source_url': item.get('url') or '',
            'in_stock_ecom': bool(item.get('in_stock_ecom', False)),
            'thumbnail': item.get('thumbnail') or '',
        }),
        ('sizes', SizeVariant, product.size_variants, lambda item, index: {
            'sku': str(item.get('sku') or ''), 'size': str(item.get('size') or ''),
            'price': decimal_value(item.get('price')),
            'in_stock_ecom': bool(item.get('in_stock_ecom', False)),
        }),
    )
    for key, model, manager, mapper in relation_map:
        if replace_relations:
            if model is ProductImage:
                for image in manager.all():
                    for image_file in (
                        image.local_image, image.preview_image,
                        image.gallery_image, image.detail_image,
                    ):
                        if image_file.name:
                            stale_image_files.append((image_file.storage, image_file.name))
            manager.all().delete()
        if replace_relations or not manager.exists():
            model.objects.bulk_create([
                model(product=product, **mapper(item, index))
                for index, item in enumerate(data.get(key) or [])
                if isinstance(item, dict)
            ])
    if stale_image_files:
        transaction.on_commit(
            lambda files=tuple(stale_image_files): _delete_unreferenced_image_files(files)
        )
    return product, created