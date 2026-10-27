"""Единая оптимизация пользовательских изображений до записи в storage."""

from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models.fields.files import ImageFieldFile
from django.db.models import ImageField
from PIL import Image, ImageOps, UnidentifiedImageError


UPLOAD_IMAGE_SIZE = (1600, 1600)
UPLOAD_IMAGE_QUALITY = 84
UPLOAD_IMAGE_MAX_BYTES = 40 * 1024 * 1024
UPLOAD_IMAGE_MAX_PIXELS = 50_000_000


def validate_optimized_image(value):
    """Отклоняет чрезмерные файлы в форме до попытки записи в storage."""
    if getattr(value, 'size', 0) > UPLOAD_IMAGE_MAX_BYTES:
        raise ValidationError('Изображение не должно превышать 40 МБ.')
    try:
        if hasattr(value, 'seek'):
            value.seek(0)
        with Image.open(value) as image:
            if image.width * image.height > UPLOAD_IMAGE_MAX_PIXELS:
                raise ValidationError('Разрешение изображения слишком большое.')
    except Image.DecompressionBombError as exc:
        raise ValidationError('Разрешение изображения слишком большое.') from exc
    except (OSError, UnidentifiedImageError) as exc:
        raise ValidationError('Загрузите корректное изображение.') from exc
    finally:
        if hasattr(value, 'seek'):
            try:
                value.seek(0)
            except OSError:
                pass


def optimized_webp(source, size=UPLOAD_IMAGE_SIZE, quality=UPLOAD_IMAGE_QUALITY, method=4):
    """Возвращает WebP без увеличения исходника, учитывая EXIF и прозрачность."""
    try:
        source_size = getattr(source, 'size', None)
        if source_size is not None and source_size > UPLOAD_IMAGE_MAX_BYTES:
            raise ValueError('Изображение не должно превышать 40 МБ.')
        if hasattr(source, 'seek'):
            source.seek(0)
        with Image.open(source) as image:
            if image.width * image.height > UPLOAD_IMAGE_MAX_PIXELS:
                raise ValueError('Разрешение изображения слишком большое.')
            converted = ImageOps.exif_transpose(image)
            converted.thumbnail(size, Image.Resampling.LANCZOS)
            has_alpha = converted.mode in ('RGBA', 'LA') or 'transparency' in converted.info
            target_mode = 'RGBA' if has_alpha else 'RGB'
            if converted.mode != target_mode:
                converted = converted.convert(target_mode)
            output = BytesIO()
            converted.save(
                output,
                format='WEBP',
                quality=quality,
                method=method,
                optimize=True,
            )
            return output.getvalue()
    except Image.DecompressionBombError as exc:
        raise ValueError('Разрешение изображения слишком большое.') from exc
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError('Не удалось обработать изображение.') from exc
    finally:
        if hasattr(source, 'seek'):
            try:
                source.seek(0)
            except OSError:
                pass


class OptimizedImageFieldFile(ImageFieldFile):
    """Всегда сохраняет исходную загрузку как WebP с гранью не более 1600 px."""

    def save(self, name, content, save=True):
        path = Path(name or 'image')
        stem = path.stem or 'image'
        normalized_name = str(path.with_name(f'{stem}.webp')).replace('\\', '/')
        normalized = ContentFile(optimized_webp(content), name=f'{stem}.webp')
        return super().save(normalized_name, normalized, save=save)


class OptimizedImageField(ImageField):
    attr_class = OptimizedImageFieldFile
    default_validators = [validate_optimized_image]
