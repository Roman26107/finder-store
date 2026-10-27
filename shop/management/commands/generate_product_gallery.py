import os
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand, CommandError

from shop.image_processing import build_product_gallery, save_product_image_gallery
from shop.models import ProductImage


class Command(BaseCommand):
    help = 'Создаёт WebP до 1600 px для большой галереи товаров.'

    def add_arguments(self, parser):
        parser.add_argument('--brand', action='append', help='Slug бренда; можно повторять.')
        parser.add_argument(
            '--exclude-brand', action='append',
            help='Исключить slug бренда; можно повторять.',
        )
        parser.add_argument('--product', type=int, help='Обработать один товар по ID.')
        parser.add_argument('--force', action='store_true', help='Пересоздать существующие файлы.')
        parser.add_argument('--limit', type=int, help='Обработать не больше указанного числа файлов.')
        parser.add_argument('--dry-run', action='store_true', help='Только показать объём работы.')
        parser.add_argument(
            '--workers', type=int, default=min(8, os.cpu_count() or 4),
            help='Число параллельных обработчиков изображений.',
        )

    def handle(self, *args, **options):
        brands = options['brand'] or []
        excluded_brands = options['exclude_brand'] or []
        if not brands and not excluded_brands and not options['product']:
            raise CommandError(
                'Укажите --brand, --exclude-brand или --product, чтобы избежать '
                'случайной обработки всего каталога.'
            )

        queryset = ProductImage.objects.exclude(local_image='').select_related('product').only(
            'id', 'position', 'local_image', 'gallery_image',
            'product_id', 'product__sku', 'product__brand',
        )
        if brands:
            queryset = queryset.filter(product__brand__in=brands)
        if excluded_brands:
            queryset = queryset.exclude(product__brand__in=excluded_brands)
        if options['product']:
            queryset = queryset.filter(product_id=options['product'])
        if not options['force']:
            queryset = queryset.filter(gallery_image='')
        queryset = queryset.order_by('position', 'pk')
        if options['limit'] is not None:
            queryset = queryset[:max(0, options['limit'])]

        total = queryset.count()
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f'Dry-run: к обработке {total} изображений; '
                f'бренды={brands or "все"}; исключены={excluded_brands or "нет"}.'
            ))
            return

        images = list(queryset)

        def prepare(image):
            try:
                return build_product_gallery(image.local_image.path)
            except (NotImplementedError, OSError):
                image.local_image.open('rb')
                try:
                    return build_product_gallery(image.local_image)
                finally:
                    image.local_image.close()

        created = skipped = processed = 0
        pending_updates = []

        def flush_updates():
            if pending_updates:
                ProductImage.objects.bulk_update(
                    pending_updates, ('gallery_image',), batch_size=100
                )
                pending_updates.clear()

        workers = max(1, options['workers'])
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for offset in range(0, len(images), 500):
                batch = images[offset:offset + 500]
                for image, content in zip(batch, executor.map(prepare, batch)):
                    previous_name = image.gallery_image.name
                    if save_product_image_gallery(image, content, save_model=False):
                        created += 1
                    else:
                        skipped += 1
                    if image.gallery_image.name != previous_name:
                        pending_updates.append(image)
                    if len(pending_updates) >= 100:
                        flush_updates()
                    processed += 1
                    if processed % 100 == 0:
                        self.stdout.write(
                            f'Обработано {processed}/{len(images)}; создано {created}; '
                            f'пропущено {skipped}.', ending='\r',
                        )
                        self.stdout.flush()
                flush_updates()
        flush_updates()
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Готово: обработано {len(images)}, создано {created}, пропущено {skipped}.'
        ))