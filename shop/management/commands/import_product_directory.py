import json
from pathlib import Path, PurePosixPath

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import get_valid_filename, slugify

from shop.catalog_data import rebuild_product_catalog_metadata
from shop.image_processing import create_product_image_detail, create_product_image_preview
from shop.models import Product
from shop.services import import_product


class Command(BaseCommand):
    help = 'Импортирует canonical JSON и локальные изображения из распакованного каталога.'

    def add_arguments(self, parser):
        parser.add_argument('directory', help='Каталог с JSON и папкой images.')
        parser.add_argument('--json', default='catalog.canonical.json', help='Имя JSON внутри каталога.')
        parser.add_argument('--no-images', action='store_true')
        parser.add_argument('--keep-relations', action='store_true')

    def handle(self, *args, **options):
        directory = Path(options['directory']).expanduser().resolve()
        json_path = directory / options['json']
        if not json_path.is_file():
            raise CommandError(f'Не найден файл: {json_path}')
        try:
            products = json.loads(json_path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError(f'Не удалось прочитать {json_path}: {exc}') from exc
        if not isinstance(products, list):
            raise CommandError('В JSON ожидается массив товаров.')

        created = updated = attached = 0
        brands = set()
        replace_relations = not options['keep_relations']
        for index, card in enumerate(products, start=1):
            if not isinstance(card, dict):
                raise CommandError(f'Карточка {index}: ожидается объект.')
            try:
                with transaction.atomic():
                    product, was_created = import_product(card, replace_relations=replace_relations)
                    brands.add(product.brand)
                    if not options['no_images']:
                        attached += self._attach_images(directory, product)
            except (OSError, TypeError, ValueError) as exc:
                raise CommandError(f'Карточка {index} ({card.get("id")}): {exc}') from exc
            created += int(was_created)
            updated += int(not was_created)
            if index % 100 == 0:
                self.stdout.write(
                    f'Обработано {index}/{len(products)}; создано {created}; '
                    f'обновлено {updated}; изображений {attached}.',
                    ending='\r',
                )
                self.stdout.flush()

        if brands:
            rebuild_product_catalog_metadata(Product.objects.filter(brand__in=brands))
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Импорт завершён: карточек {len(products)}, создано {created}, '
            f'обновлено {updated}, изображений привязано {attached}.'
        ))

    @staticmethod
    def _attach_images(directory, product):
        attached = 0
        sku_directory = slugify(product.sku)[:80] or f'product-{product.pk}'
        for image in product.images.all():
            source_name = str(image.source_local_path or '').replace('\\', '/')
            source_path = PurePosixPath(source_name)
            if not source_name or source_path.is_absolute() or '..' in source_path.parts:
                continue
            source = directory.joinpath(*source_path.parts)
            if not source.is_file():
                continue
            filename = get_valid_filename(source.name)
            webp_name = f'{Path(filename).stem}.webp'
            relative_name = f'import/{sku_directory}/{webp_name}'
            storage_name = image.local_image.field.generate_filename(image, relative_name)
            image.local_image.storage.delete(storage_name)
            with source.open('rb') as source_file:
                image.local_image.save(relative_name, File(source_file), save=True)
            create_product_image_preview(image, force=True)
            create_product_image_detail(image, force=True)
            attached += 1
        return attached