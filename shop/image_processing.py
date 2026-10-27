"""Создание локальных оптимизированных изображений для витрины."""

from io import BytesIO
from pathlib import Path
from statistics import median, pstdev
from threading import Lock

from django.core.files.base import ContentFile
from django.utils.text import slugify
from PIL import (
    Image, ImageChops, ImageDraw, ImageFile, ImageFilter, ImageOps,
    UnidentifiedImageError,
)


PRODUCT_PREVIEW_SIZE = (640, 640)
PRODUCT_PREVIEW_QUALITY = 78
PRODUCT_GALLERY_SIZE = (1600, 1600)
PRODUCT_GALLERY_QUALITY = 84
PRODUCT_DETAIL_SIZE = (1000, 1000)
PRODUCT_DETAIL_QUALITY = 84
PRODUCT_DETAIL_BACKGROUND = (231, 229, 218)
TRUNCATED_IMAGE_LOCK = Lock()


def _flat_light_background_bbox(image):
    """Находит товар только на уверенно однотонном светлом студийном фоне."""
    analysis = image.copy()
    analysis.thumbnail((400, 400), Image.Resampling.LANCZOS)
    if analysis.mode != 'RGB':
        analysis = analysis.convert('RGB')
    width, height = analysis.size
    step = max(1, min(width, height) // 100)
    edge_pixels = []
    for x in range(0, width, step):
        edge_pixels.extend((analysis.getpixel((x, 0)), analysis.getpixel((x, height - 1))))
    for y in range(0, height, step):
        edge_pixels.extend((analysis.getpixel((0, y)), analysis.getpixel((width - 1, y))))
    background = tuple(
        int(median(pixel[channel] for pixel in edge_pixels))
        for channel in range(3)
    )
    edge_spread = max(
        pstdev(pixel[channel] for pixel in edge_pixels)
        for channel in range(3)
    )
    if min(background) < 205 or edge_spread >= 7:
        return None

    difference = ImageChops.difference(
        analysis, Image.new('RGB', analysis.size, background)
    ).convert('L')
    mask = difference.point(lambda value: 255 if value > 12 else 0)
    mask = mask.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MaxFilter(5))
    box = mask.getbbox()
    if not box:
        return None
    box_width = box[2] - box[0]
    box_height = box[3] - box[1]
    if box_width * box_height < width * height * .002:
        return None

    padding = max(box_width, box_height) * .14
    expanded = (
        max(0, int(box[0] - padding)),
        max(0, int(box[1] - padding)),
        min(width, int(box[2] + padding)),
        min(height, int(box[3] + padding)),
    )
    scale_x = image.width / width
    scale_y = image.height / height
    source_box = (
        int(expanded[0] * scale_x), int(expanded[1] * scale_y),
        int(expanded[2] * scale_x), int(expanded[3] * scale_y),
    )
    return background, source_box


def _render_product_detail(source, analysis_source=None):
    analysis_source = analysis_source or source
    with Image.open(analysis_source) as image:
        analysis = ImageOps.exif_transpose(image)
        if analysis.mode != 'RGB':
            analysis = analysis.convert('RGB')
        detected = _flat_light_background_bbox(analysis)
        if detected is None:
            return None
        _, analysis_box = detected

    with Image.open(source) as image:
        detail = ImageOps.exif_transpose(image)
        if detail.mode != 'RGB':
            detail = detail.convert('RGB')
        scale_x = detail.width / analysis.width
        scale_y = detail.height / analysis.height
        source_box = (
            int(analysis_box[0] * scale_x), int(analysis_box[1] * scale_y),
            int(analysis_box[2] * scale_x), int(analysis_box[3] * scale_y),
        )
        detail = detail.crop(source_box)
        detail.thumbnail((880, 880), Image.Resampling.LANCZOS)
        # Меняем только фон, связанный с углами кадра. Белые части товара,
        # отделённые контуром/тенью, не затрагиваются обычной заменой цвета.
        for corner in (
            (0, 0), (detail.width - 1, 0),
            (0, detail.height - 1), (detail.width - 1, detail.height - 1),
        ):
            ImageDraw.floodfill(
                detail, corner, PRODUCT_DETAIL_BACKGROUND,
                thresh=16,
            )
        canvas = Image.new('RGB', PRODUCT_DETAIL_SIZE, PRODUCT_DETAIL_BACKGROUND)
        offset = (
            (PRODUCT_DETAIL_SIZE[0] - detail.width) // 2,
            (PRODUCT_DETAIL_SIZE[1] - detail.height) // 2,
        )
        canvas.paste(detail, offset)
        output = BytesIO()
        canvas.save(
            output, format='WEBP', quality=PRODUCT_DETAIL_QUALITY,
            method=2,
        )
    return output.getvalue()


def build_product_detail(source, analysis_source=None):
    """Создаёт нормализованное detail-фото либо None для сложного фона."""
    try:
        return _render_product_detail(source, analysis_source)
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def _render_product_preview(source):
    with Image.open(source) as image:
        preview = ImageOps.exif_transpose(image)
        preview.thumbnail(PRODUCT_PREVIEW_SIZE, Image.Resampling.LANCZOS)
        if preview.mode not in ('RGB', 'RGBA'):
            preview = preview.convert('RGBA' if 'transparency' in preview.info else 'RGB')
        output = BytesIO()
        preview.save(
            output, format='WEBP', quality=PRODUCT_PREVIEW_QUALITY,
            method=4, optimize=True,
        )
    return output.getvalue()


def build_product_preview(source):
    """Возвращает байты WebP-превью; функция безопасна для рабочего пула."""
    try:
        return _render_product_preview(source)
    except OSError:
        # Иногда поставщик обрезает JPEG на несколько завершающих байтов.
        # Браузеры такие файлы показывают. Повторяем декодирование в локальной
        # критической секции, не ослабляя проверку Pillow для всего Django.
        try:
            if hasattr(source, 'seek'):
                source.seek(0)
            with TRUNCATED_IMAGE_LOCK:
                previous = ImageFile.LOAD_TRUNCATED_IMAGES
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                try:
                    return _render_product_preview(source)
                finally:
                    ImageFile.LOAD_TRUNCATED_IMAGES = previous
        except (OSError, ValueError, UnidentifiedImageError):
            return None
    except (ValueError, UnidentifiedImageError):
        return None


def _render_product_gallery(source):
    with Image.open(source) as image:
        gallery = ImageOps.exif_transpose(image)
        gallery.thumbnail(PRODUCT_GALLERY_SIZE, Image.Resampling.LANCZOS)
        if gallery.mode not in ('RGB', 'RGBA'):
            gallery = gallery.convert('RGBA' if 'transparency' in gallery.info else 'RGB')
        output = BytesIO()
        gallery.save(
            output, format='WEBP', quality=PRODUCT_GALLERY_QUALITY,
            method=4, optimize=True,
        )
    return output.getvalue()


def build_product_gallery(source):
    """Возвращает gallery-WebP до 1600 px, не меняя композицию изображения."""
    try:
        return _render_product_gallery(source)
    except OSError:
        try:
            if hasattr(source, 'seek'):
                source.seek(0)
            with TRUNCATED_IMAGE_LOCK:
                previous = ImageFile.LOAD_TRUNCATED_IMAGES
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                try:
                    return _render_product_gallery(source)
                finally:
                    ImageFile.LOAD_TRUNCATED_IMAGES = previous
        except (OSError, ValueError, UnidentifiedImageError):
            return None
    except (ValueError, UnidentifiedImageError):
        return None


def product_preview_relative_name(product_image):
    sku = slugify(product_image.product.sku)[:80] or f'product-{product_image.product_id}'
    stem = slugify(Path(product_image.local_image.name).stem)[:80] or 'image'
    return f'{sku}/{product_image.pk}-{stem}.webp'


def product_gallery_relative_name(product_image):
    sku = slugify(product_image.product.sku)[:80] or f'product-{product_image.product_id}'
    stem = slugify(Path(product_image.local_image.name).stem)[:80] or 'image'
    return f'{sku}/{product_image.pk}-{stem}.webp'


def product_detail_relative_name(product_image):
    sku = slugify(product_image.product.sku)[:80] or f'product-{product_image.product_id}'
    stem = slugify(Path(product_image.local_image.name).stem)[:80] or 'image'
    return f'{sku}/{product_image.pk}-{stem}.webp'


def save_product_image_preview(product_image, content):
    """Последовательно сохраняет подготовленное превью и обновляет модель."""
    if not content:
        return False
    relative_name = product_preview_relative_name(product_image)
    storage_name = product_image.preview_image.field.generate_filename(
        product_image, relative_name
    )
    product_image.preview_image.storage.delete(storage_name)
    product_image.preview_image.save(relative_name, ContentFile(content), save=True)
    return True


def save_product_image_gallery(product_image, content, save_model=True):
    """Последовательно сохраняет оптимизированное фото большой галереи."""
    if not content:
        return False
    relative_name = product_gallery_relative_name(product_image)
    storage_name = product_image.gallery_image.field.generate_filename(
        product_image, relative_name
    )
    product_image.gallery_image.storage.delete(storage_name)
    product_image.gallery_image.save(relative_name, ContentFile(content), save=False)
    if save_model:
        product_image.save(update_fields=('gallery_image',))
    return True


def save_product_image_detail(product_image, content, save_model=True):
    """Сохраняет detail-файл; сложные изображения оставляет без производной копии."""
    had_detail = bool(product_image.detail_image and product_image.detail_image.name)
    product_image.detail_processed = True
    if product_image.detail_image:
        product_image.detail_image.delete(save=False)
    if not content:
        if had_detail:
            product_image.detail_image = ''
        if save_model:
            product_image.save(update_fields=('detail_image', 'detail_processed'))
        return False
    relative_name = product_detail_relative_name(product_image)
    storage_name = product_image.detail_image.field.generate_filename(
        product_image, relative_name
    )
    product_image.detail_image.storage.delete(storage_name)
    product_image.detail_image.save(relative_name, ContentFile(content), save=False)
    if save_model:
        product_image.save(update_fields=('detail_image', 'detail_processed'))
    return True


def create_product_image_preview(product_image, force=False):
    """Создаёт WebP-превью и возвращает True, если файл был записан."""
    if not product_image.local_image:
        return False
    if product_image.preview_image and not force:
        try:
            if product_image.preview_image.storage.exists(product_image.preview_image.name):
                return False
        except OSError:
            pass

    try:
        product_image.local_image.open('rb')
        content = build_product_preview(product_image.local_image)
    finally:
        try:
            product_image.local_image.close()
        except (AttributeError, OSError):
            pass
    return save_product_image_preview(product_image, content)


def create_product_image_gallery(product_image, force=False):
    """Создаёт WebP до 1600 px для большой галереи товара."""
    if not product_image.local_image:
        return False
    if product_image.gallery_image and not force:
        try:
            if product_image.gallery_image.storage.exists(product_image.gallery_image.name):
                return False
        except OSError:
            pass

    try:
        product_image.local_image.open('rb')
        content = build_product_gallery(product_image.local_image)
    finally:
        try:
            product_image.local_image.close()
        except (AttributeError, OSError):
            pass
    return save_product_image_gallery(product_image, content)


def create_product_image_detail(product_image, force=False):
    """Создаёт безопасно кадрированное изображение для большой галереи."""
    if not product_image.local_image:
        return False
    if product_image.detail_processed and not force:
        try:
            if product_image.detail_image.storage.exists(product_image.detail_image.name):
                return False
        except OSError:
            pass
    try:
        product_image.local_image.open('rb')
        if product_image.preview_image:
            product_image.preview_image.open('rb')
        content = build_product_detail(
            product_image.local_image,
            product_image.preview_image if product_image.preview_image else None,
        )
    finally:
        try:
            product_image.local_image.close()
        except (AttributeError, OSError):
            pass
        try:
            product_image.preview_image.close()
        except (AttributeError, OSError):
            pass
    return save_product_image_detail(product_image, content)