import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from PIL import Image, UnidentifiedImageError

from shop.catalog_normalization import (
    catalog_quality_report, normalize_miumiu_catalog, normalize_therow_catalog,
)
from shop.offwhite_import import normalize_offwhite_catalog


class Command(BaseCommand):
    help = 'Добавляет общую таксономию FINDER во внешний canonical-каталог.'

    def add_arguments(self, parser):
        parser.add_argument('directory', help='Каталог с catalog.json и папкой images.')
        parser.add_argument('--output', default='catalog.canonical.json', help='Имя нормализованного JSON.')
        parser.add_argument(
            '--brand', choices=('auto', 'miumiu', 'therow', 'offwhite'), default='auto',
            help='Формат каталога. По умолчанию определяется по первой карточке.',
        )

    def handle(self, *args, **options):
        directory = Path(options['directory']).expanduser().resolve()
        source = directory / 'catalog.json'
        images_directory = directory / 'images'
        if not source.is_file():
            raise CommandError(f'Не найден файл: {source}')
        if not images_directory.is_dir():
            raise CommandError(f'Не найдена папка изображений: {images_directory}')

        try:
            products = json.loads(source.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError(f'Не удалось прочитать {source}: {exc}') from exc
        if not isinstance(products, list):
            raise CommandError('В catalog.json ожидается массив товаров.')

        brand = options['brand']
        if brand == 'auto' and products:
            brand_name = str(products[0].get('brand') or '').strip().casefold()
            detected = {
                'the row': 'therow', 'miu miu': 'miumiu',
                'off white': 'offwhite', 'off-white': 'offwhite',
            }
            brand = detected.get(brand_name)
            if not brand:
                raise CommandError(
                    f'Неизвестный бренд {brand_name or "без названия"!r}; '
                    'укажите поддерживаемый --brand явно.'
                )
        normalizer = {
            'therow': normalize_therow_catalog,
            'miumiu': normalize_miumiu_catalog,
            'offwhite': normalize_offwhite_catalog,
        }[brand]
        normalized, errors = normalizer(products)
        if errors:
            report_path = directory / 'normalization-errors.json'
            report_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding='utf-8')
            raise CommandError(f'Не нормализовано карточек: {len(errors)}. Отчёт: {report_path}')

        # Некоторые карточки содержат рабочие удалённые URL, но не содержат
        # локального пути. Если файлы есть в архиве, восстанавливаем ссылки.
        for product in normalized:
            sku = str(product.get('id') or '').split(':', 1)[-1]
            folder_prefix = {
                'therow': 'therow', 'miumiu': 'miumiu', 'offwhite': 'offwhite',
            }[brand]
            folder = images_directory / f'{folder_prefix}_{sku}'
            for index, image in enumerate((product.get('media') or {}).get('images') or []):
                if image.get('file') or not folder.is_dir():
                    continue
                role = str(image.get('role') or index)
                candidates = sorted(folder.glob(f'{role}_*.*'))
                if len(candidates) == 1:
                    image['file'] = candidates[0].relative_to(directory).as_posix()

        all_image_paths = [path for path in images_directory.rglob('*') if path.is_file()]
        eligible_image_paths = []
        excluded_images = set()
        invalid_images = []
        for path in all_image_paths:
            relative_path = path.relative_to(directory).as_posix()
            try:
                with Image.open(path) as image:
                    width, height = image.size
                    image.verify()
            except (OSError, ValueError, UnidentifiedImageError) as exc:
                excluded_images.add(relative_path)
                invalid_images.append({'file': relative_path, 'error': str(exc)[:500]})
                continue
            if min(width, height) < 300:
                excluded_images.add(relative_path)
            else:
                eligible_image_paths.append(relative_path)
        if excluded_images:
            for product in normalized:
                media = product.get('media') or {}
                media['images'] = [
                    image for image in media.get('images') or []
                    if image.get('file') not in excluded_images
                ]

        report = catalog_quality_report(normalized, eligible_image_paths)
        report.update({
            'source_images_on_disk': len(all_image_paths),
            'excluded_low_resolution_images': len(excluded_images) - len(invalid_images),
            'invalid_images': invalid_images,
        })
        if invalid_images:
            report['issue_counts']['invalid_image_file'] = len(invalid_images)
        output = directory / options['output']
        output.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
        report_path = directory / 'quality-report.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(
            f'Нормализовано {len(normalized)} карточек. JSON: {output}'
        ))
        self.stdout.write(
            f'Изображений: {report["images_on_disk"]}; замечаний: '
            f'{sum(report["issue_counts"].values())}. Отчёт: {report_path}'
        )