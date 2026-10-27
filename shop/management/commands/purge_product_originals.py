import hashlib
import json
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from PIL import Image, UnidentifiedImageError

from shop.models import ProductImage


PROTECTED_BRANDS = {'the-row'}


class Command(BaseCommand):
    help = 'Удаляет локальные оригиналы только после проверки gallery-WebP.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--brand', action='append', required=True,
            help='Slug бренда; можно повторять. The Row удалять запрещено.',
        )
        parser.add_argument(
            '--manifest',
            help='Путь к JSON-manifest. Обязателен при фактическом удалении.',
        )
        parser.add_argument(
            '--confirm', action='store_true',
            help='Подтвердить необратимое удаление файлов.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Проверить область удаления, не читать и не удалять файлы.',
        )

    def handle(self, *args, **options):
        brands = sorted(set(options['brand']))
        protected = PROTECTED_BRANDS.intersection(brands)
        if protected:
            raise CommandError(
                f'Удаление оригиналов запрещено для: {", ".join(sorted(protected))}.'
            )
        if not options['dry_run'] and not options['confirm']:
            raise CommandError('Для удаления требуется явный флаг --confirm.')
        if not options['dry_run'] and not options['manifest']:
            raise CommandError('Для удаления требуется путь --manifest.')

        queryset = ProductImage.objects.filter(
            product__brand__in=brands,
        ).exclude(local_image='').select_related('product').only(
            'id', 'local_image', 'gallery_image', 'product__brand', 'product__sku',
        ).order_by('pk')
        total = queryset.count()
        total_bytes = 0
        for image in queryset.iterator(chunk_size=500):
            try:
                total_bytes += image.local_image.size
            except (FileNotFoundError, OSError, ValueError):
                pass

        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f'Dry-run: оригиналов {total}; объём {total_bytes} байт; '
                f'бренды={brands}.'
            ))
            return

        images = list(queryset)
        names_in_scope = {image.local_image.name for image in images}
        duplicate_names = ProductImage.objects.exclude(local_image='').values(
            'local_image'
        ).annotate(references=Count('id')).filter(references__gt=1)
        shared_names = {
            item['local_image'] for item in duplicate_names
            if item['local_image'] in names_in_scope
        }

        manifest_items = []
        skipped = []
        for index, image in enumerate(images, 1):
            local_name = image.local_image.name
            gallery_name = image.gallery_image.name
            reason = self._gallery_problem(image)
            if local_name in shared_names:
                reason = 'оригинал используется строкой вне выбранной области'
            if reason:
                skipped.append({'id': image.pk, 'local_image': local_name, 'reason': reason})
                continue
            try:
                with image.local_image.storage.open(local_name, 'rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                size = image.local_image.storage.size(local_name)
            except (FileNotFoundError, OSError, ValueError) as exc:
                skipped.append({
                    'id': image.pk, 'local_image': local_name,
                    'reason': f'оригинал недоступен: {exc}',
                })
                continue
            manifest_items.append({
                'id': image.pk,
                'brand': image.product.brand,
                'sku': image.product.sku,
                'local_image': local_name,
                'gallery_image': gallery_name,
                'size': size,
                'sha256': digest,
            })
            if index % 500 == 0:
                self.stdout.write(f'Проверено {index}/{len(images)}.', ending='\r')
                self.stdout.flush()

        manifest_path = Path(options['manifest']).resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            'created_at': datetime.now().astimezone().isoformat(),
            'brands': brands,
            'files': manifest_items,
            'skipped': skipped,
            'total_files': len(manifest_items),
            'total_bytes': sum(item['size'] for item in manifest_items),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
        )

        deleted = deleted_bytes = failed = 0
        images_by_pk = {image.pk: image for image in images}
        for item in manifest_items:
            image = images_by_pk[item['id']]
            storage = image.local_image.storage
            name = item['local_image']
            try:
                storage.delete(name)
                if storage.exists(name):
                    raise OSError('storage сообщает, что файл всё ещё существует')
                ProductImage.objects.filter(pk=image.pk, local_image=name).update(local_image='')
            except OSError as exc:
                failed += 1
                self.stderr.write(f'Не удалён {name}: {exc}')
                continue
            deleted += 1
            deleted_bytes += item['size']
            if deleted % 500 == 0:
                self.stdout.write(f'Удалено {deleted}/{len(manifest_items)}.', ending='\r')
                self.stdout.flush()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Готово: удалено {deleted} оригиналов ({deleted_bytes} байт); '
            f'пропущено {len(skipped)}; ошибок удаления {failed}; '
            f'manifest={manifest_path}.'
        ))

    @staticmethod
    def _gallery_problem(image):
        if not image.gallery_image or not image.gallery_image.name:
            return 'нет gallery_image'
        try:
            with image.gallery_image.storage.open(image.gallery_image.name, 'rb') as source:
                with Image.open(source) as gallery:
                    if gallery.format != 'WEBP':
                        return f'gallery имеет формат {gallery.format}'
                    if max(gallery.size) != 1600:
                        return f'gallery имеет сторону не 1600 px: {gallery.size}'
                    gallery.verify()
        except (FileNotFoundError, OSError, ValueError, UnidentifiedImageError) as exc:
            return f'gallery недоступен или повреждён: {exc}'
        return ''