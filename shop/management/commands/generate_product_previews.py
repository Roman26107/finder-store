import os
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand

from shop.image_processing import build_product_preview, save_product_image_preview
from shop.models import ProductImage


class Command(BaseCommand):
    help = 'Создаёт локальные WebP-превью для изображений товаров.'

    def add_arguments(self, parser):
        parser.add_argument('--brand', help='Slug бренда, например miu-miu.')
        parser.add_argument('--force', action='store_true', help='Пересоздать существующие превью.')
        parser.add_argument('--limit', type=int, help='Обработать не больше указанного числа файлов.')
        parser.add_argument(
            '--workers', type=int, default=min(8, os.cpu_count() or 4),
            help='Число параллельных обработчиков изображений.',
        )

    def handle(self, *args, **options):
        queryset = ProductImage.objects.exclude(local_image='').select_related('product').only(
            'id', 'position', 'local_image', 'preview_image',
            'product_id', 'product__sku',
        )
        if options['brand']:
            queryset = queryset.filter(product__brand=options['brand'])
        if not options['force']:
            queryset = queryset.filter(preview_image='')
        # Сначала создаём главные изображения: каталог ускоряется ещё до того,
        # как завершится обработка дополнительных ракурсов галереи.
        queryset = queryset.order_by('position', 'pk')
        if options['limit'] is not None:
            queryset = queryset[:max(0, options['limit'])]

        total = queryset.count()
        created = skipped = 0
        images = list(queryset)

        def prepare(image):
            try:
                return build_product_preview(image.local_image.path)
            except (NotImplementedError, OSError):
                image.local_image.open('rb')
                try:
                    return build_product_preview(image.local_image)
                finally:
                    image.local_image.close()

        workers = max(1, options['workers'])
        with ThreadPoolExecutor(max_workers=workers) as executor:
            prepared = executor.map(prepare, images)
            for index, (image, content) in enumerate(zip(images, prepared), start=1):
                if save_product_image_preview(image, content):
                    created += 1
                else:
                    skipped += 1
                if index % 100 == 0:
                    self.stdout.write(
                        f'Обработано {index}/{total}; создано {created}; пропущено {skipped}.',
                        ending='\r',
                    )
                    self.stdout.flush()
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Готово: обработано {total}, создано {created}, пропущено {skipped}.'
        ))