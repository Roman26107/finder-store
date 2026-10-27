import json
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shop.catalog_data import rebuild_product_catalog_metadata
from shop.models import Product, ProductImage
from shop.services import _attach_archive_images, _validate_zip_entries, import_product
from shop.valentino_import import compact_quality_report, prepare_valentino_archive


BRAND_SLUG = 'valentino-garavani'


class Command(BaseCommand):
    help = (
        'Атомарно заменяет каталог Valentino Garavani из ZIP после проверки '
        'текстов, таксономии и полноразмерных изображений.'
    )

    def add_arguments(self, parser):
        parser.add_argument('archive', help='Путь к ZIP-архиву Valentino.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Проверить архив и создать отчёты, не изменяя базу и media.',
        )
        parser.add_argument(
            '--allow-count-change', action='store_true',
            help='Разрешить уменьшение существующего каталога более чем на 20%%.',
        )
        parser.add_argument(
            '--report-dir', default='',
            help='Каталог для preflight и итогового JSON-отчёта.',
        )

    def handle(self, *args, **options):
        archive_path = Path(options['archive']).expanduser().resolve()
        if not archive_path.is_file():
            raise CommandError(f'Архив не найден: {archive_path}')
        if archive_path.suffix.lower() != '.zip':
            raise CommandError('Требуется файл с расширением .zip.')

        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        report_dir = (
            Path(options['report_dir']).expanduser().resolve()
            if options['report_dir']
            else Path(settings.MEDIA_ROOT) / 'import-reports' / f'valentino-{timestamp}'
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        old_products = list(Product.objects.filter(brand=BRAND_SLUG).order_by('sku'))
        old_count = len(old_products)
        self._write_json(report_dir / 'old-catalog.json', {
            'products': old_count,
            'items': [{
                'id': product.pk, 'sku': product.sku, 'title': product.title,
                'description': product.description,
                'category_path_key': product.category_path_key,
            } for product in old_products],
        })

        self.stdout.write(f'Проверка архива: {archive_path}')
        try:
            with zipfile.ZipFile(archive_path) as archive:
                cards, quality = prepare_valentino_archive(archive)
        except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CommandError(f'Архив не прошёл проверку: {exc}') from exc

        quality_path = report_dir / 'preflight-quality.json'
        self._write_json(quality_path, compact_quality_report(quality))
        if quality['critical_issue_counts']:
            raise CommandError(
                f'Обнаружены критические проблемы: {quality["critical_issue_counts"]}. '
                f'Отчёт: {quality_path}'
            )
        new_ids = {str(card.get('id') or '') for card in cards}
        if any(not sku.startswith('valentino:') for sku in new_ids):
            raise CommandError('Архив содержит карточки, не относящиеся к Valentino.')
        if old_count and len(cards) < old_count * .8 and not options['allow_count_change']:
            raise CommandError(
                f'Новый каталог содержит {len(cards)} карточек вместо {old_count}. '
                'Для намеренного сильного уменьшения используйте --allow-count-change.'
            )

        self.stdout.write(self.style.SUCCESS(
            f'Preflight пройден: карточек {len(cards)}, полноразмерных фото '
            f'{quality["eligible_images"]}, критических замечаний 0.'
        ))
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f'Dry-run завершён, данные не изменены. Отчёты: {report_dir}'
            ))
            return

        created = updated = attached = stale_count = 0
        try:
            with transaction.atomic():
                with zipfile.ZipFile(archive_path) as archive:
                    entries_by_name = {}
                    basename_index = defaultdict(list)
                    for name, entry in _validate_zip_entries(archive):
                        if PurePosixPath(name.lower()).suffix not in {
                            '.jpg', '.jpeg', '.png', '.webp',
                        }:
                            continue
                        entries_by_name[name.lower()] = entry
                        basename_index[PurePosixPath(name.lower()).name].append(entry)
                    for index, card in enumerate(cards, start=1):
                        expected = len((card.get('media') or {}).get('images') or [])
                        try:
                            product, was_created = import_product(card, replace_relations=True)
                            product_attached = _attach_archive_images(
                                archive, product, entries_by_name, basename_index,
                            )
                            if product_attached != expected:
                                raise ValueError(
                                    f'привязано фото {product_attached} из {expected}'
                                )
                        except (OSError, TypeError, ValueError) as exc:
                            raise CommandError(
                                f'Карточка {index}/{len(cards)} ({card.get("id")}): {exc}'
                            ) from exc
                        created += int(was_created)
                        updated += int(not was_created)
                        attached += product_attached
                        if index % 50 == 0 or index == len(cards):
                            self.stdout.write(
                                f'Обработано {index}/{len(cards)}; создано {created}; '
                                f'обновлено {updated}; фото {attached}.',
                                ending='\r' if index < len(cards) else '\n',
                            )
                stale = Product.objects.filter(brand=BRAND_SLUG).exclude(sku__in=new_ids)
                stale_count = stale.count()
                if stale_count:
                    stale.delete()
                rebuild_product_catalog_metadata(Product.objects.filter(brand=BRAND_SLUG))
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CommandError(f'Не удалось повторно открыть ZIP: {exc}') from exc

        imported = Product.objects.filter(brand=BRAND_SLUG, sku__in=new_ids)
        images = ProductImage.objects.filter(product__in=imported)
        invalid_positions = 0
        for product in imported.prefetch_related('images'):
            positions = [image.position for image in product.images.all()]
            if positions != list(range(len(positions))):
                invalid_positions += 1
        final_report = {
            **compact_quality_report(quality), 'archive': str(archive_path),
            'old_products': old_count, 'new_products': imported.count(),
            'created_products': created, 'updated_products': updated,
            'deleted_stale_products': stale_count, 'attached_images': attached,
            'database_images': images.count(),
            'products_with_missing_local_image': images.filter(local_image='').count(),
            'products_with_cropped_detail_image': images.exclude(detail_image='').count(),
            'products_with_invalid_image_positions': invalid_positions,
        }
        final_path = report_dir / 'result.json'
        self._write_json(final_path, final_report)
        if (
            final_report['new_products'] != len(cards)
            or final_report['database_images'] != quality['eligible_images']
            or final_report['products_with_missing_local_image']
            or final_report['products_with_cropped_detail_image']
            or invalid_positions
        ):
            raise CommandError(f'Постпроверка импорта не пройдена. Отчёт: {final_path}')
        self.stdout.write(self.style.SUCCESS(
            f'Valentino Garavani импортирован: товаров {final_report["new_products"]}, '
            f'фото {final_report["database_images"]}. Отчёт: {final_path}'
        ))

    @staticmethod
    def _write_json(path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
