import os
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand

from shop.image_processing import build_product_detail, save_product_image_detail
from shop.models import ProductImage


class Command(BaseCommand):
    help = 'Создаёт нормализованные WebP-фото для большой галереи товара.'

    def add_arguments(self, parser):
        parser.add_argument('--brand', help='Slug бренда, например miu-miu.')
        parser.add_argument('--product', type=int, help='Обработать один товар по ID.')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--workers', type=int, default=min(8, os.cpu_count() or 4))

    def handle(self, *args, **options):
        queryset = ProductImage.objects.exclude(local_image='').select_related('product').only(
            'id', 'local_image', 'preview_image', 'detail_image', 'detail_processed',
            'product_id', 'product__sku',
        )
        if options['brand']:
            queryset = queryset.filter(product__brand=options['brand'])
        if options['product']:
            queryset = queryset.filter(product_id=options['product'])
        if not options['force']:
            queryset = queryset.filter(detail_processed=False)
        queryset = queryset.order_by('position', 'pk')
        if options['limit'] is not None:
            queryset = queryset[:max(0, options['limit'])]
        images = list(queryset)

        def prepare(image):
            try:
                analysis_path = image.preview_image.path if image.preview_image else None
                return build_product_detail(image.local_image.path, analysis_path)
            except (NotImplementedError, OSError):
                image.local_image.open('rb')
                if image.preview_image:
                    image.preview_image.open('rb')
                try:
                    return build_product_detail(
                        image.local_image,
                        image.preview_image if image.preview_image else None,
                    )
                finally:
                    image.local_image.close()
                    if image.preview_image:
                        image.preview_image.close()

        created = skipped = processed = 0
        pending_updates = []
        work_batch_size = 500

        def flush_updates():
            if pending_updates:
                ProductImage.objects.bulk_update(
                    pending_updates, ('detail_image', 'detail_processed'), batch_size=100
                )
                pending_updates.clear()

        with ThreadPoolExecutor(max_workers=max(1, options['workers'])) as executor:
            for offset in range(0, len(images), work_batch_size):
                batch = images[offset:offset + work_batch_size]
                for image, content in zip(batch, executor.map(prepare, batch)):
                    previous_name = image.detail_image.name
                    was_processed = image.detail_processed
                    if save_product_image_detail(image, content, save_model=False):
                        created += 1
                    else:
                        skipped += 1
                    if (
                        image.detail_image.name != previous_name
                        or image.detail_processed != was_processed
                    ):
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
                # Каждая порция полностью зафиксирована до постановки следующей.
                flush_updates()
        flush_updates()
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Готово: обработано {len(images)}, создано {created}, пропущено {skipped}.'
        ))