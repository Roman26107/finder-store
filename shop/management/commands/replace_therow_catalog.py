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
from shop.services import (
    _attach_archive_images, _validate_zip_entries, import_product,
)
from shop.therow_import import compact_quality_report, prepare_therow_archive
from shop.therow_editorial import editorialize_therow_card


class Command(BaseCommand):
    help = (
        'Полностью заменяет каталог The Row из ZIP после проверки переводов, '
        'таксономии и качества изображений.'
    )

    def add_arguments(self, parser):
        parser.add_argument('archive', help='Путь к ZIP-архиву The Row.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Только проверить архив и создать отчёты, не изменяя базу и media.',
        )
        parser.add_argument(
            '--allow-count-change', action='store_true',
            help='Разрешить уменьшение каталога более чем на 20%%.',
        )
        parser.add_argument(
            '--report-dir', default='',
            help='Каталог для резервного и итогового JSON-отчётов.',
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
            else Path(settings.MEDIA_ROOT) / 'import-reports' / f'the-row-{timestamp}'
        )
        report_dir.mkdir(parents=True, exist_ok=True)

        old_products = list(Product.objects.filter(brand='the-row').order_by('sku'))
        old_count = len(old_products)
        old_snapshot = [{
            'id': product.pk,
            'sku': product.sku,
            'title': product.title,
            'category_department_slug': product.category_department_slug,
            'category_department_name': product.category_department_name,
            'category_name': product.category_name,
            'category_path': product.category_path,
            'category_path_key': product.category_path_key,
            'category_keys': product.category_keys,
            'description': product.description,
            'raw_title': str(((product.raw_data or {}).get('title') or {}).get('raw') or ''),
            'raw_description': str(
                ((product.raw_data or {}).get('description') or {}).get('raw') or ''
            ),
        } for product in old_products]
        self._write_json(report_dir / 'old-taxonomy.json', {
            'products': old_count,
            'items': old_snapshot,
        })

        self.stdout.write(f'Проверка архива: {archive_path}')
        try:
            with zipfile.ZipFile(archive_path) as archive:
                cards, quality = prepare_therow_archive(archive)
        except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CommandError(f'Архив не прошёл проверку: {exc}') from exc

        quality_path = report_dir / 'preflight-quality.json'
        self._write_json(quality_path, compact_quality_report(quality))
        if quality['critical_issue_counts']:
            raise CommandError(
                'Обнаружены критические проблемы: '
                f'{quality["critical_issue_counts"]}. Отчёт: {quality_path}'
            )

        new_ids = {str(card.get('id') or '') for card in cards}
        if any(not sku.startswith('therow:') for sku in new_ids):
            raise CommandError('Архив содержит карточки, не относящиеся к The Row.')
        if (
            old_count and len(cards) < old_count * .8
            and not options['allow_count_change']
        ):
            raise CommandError(
                f'Новый каталог содержит {len(cards)} карточек вместо {old_count}. '
                'Для намеренного сильного уменьшения используйте --allow-count-change.'
            )

        old_by_sku = {item['sku']: item for item in old_snapshot}
        editorial_basis = defaultdict(int)
        editorial_issues = []
        edited_cards = []
        for card in cards:
            sku = str(card.get('id') or '')
            old = old_by_sku.get(sku) or {}
            raw_description = str(
                ((card.get('description') or {}).get('raw') or '')
            )
            source_unchanged = bool(
                old and old.get('raw_description') == raw_description
            )
            edited, editorial = editorialize_therow_card(
                card,
                legacy_description=old.get('description', '') if source_unchanged else '',
                prefer_legacy=source_unchanged,
            )
            editorial_basis[editorial['description_basis']] += 1
            if editorial['issues']:
                editorial_issues.append({
                    'sku': sku,
                    'issues': editorial['issues'],
                    'title': editorial['title'],
                    'description': editorial['description'],
                })
            edited_cards.append(edited)
        cards = edited_cards
        quality['editorial'] = {
            'description_basis': dict(editorial_basis),
            'issue_count': len(editorial_issues),
            'issues': editorial_issues,
        }
        self._write_json(quality_path, compact_quality_report(quality))
        if editorial_issues:
            raise CommandError(
                f'Редакторская проверка не пройдена для {len(editorial_issues)} карточек. '
                f'Отчёт: {quality_path}'
            )

        self.stdout.write(self.style.SUCCESS(
            f'Preflight пройден: карточек {len(cards)}, полноразмерных фото '
            f'{quality["eligible_images"]}, исключено 100×100: '
            f'{quality["excluded_low_resolution_images"]}, критических замечаний 0.'
        ))
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f'Dry-run завершён, данные не изменены. Отчёты: {report_dir}'
            ))
            return

        entries_by_name = {}
        basename_index = defaultdict(list)
        created = updated = attached = 0
        try:
            with transaction.atomic():
                with zipfile.ZipFile(archive_path) as archive:
                    for name, entry in _validate_zip_entries(archive):
                        if PurePosixPath(name.lower()).suffix not in {
                            '.jpg', '.jpeg', '.png', '.webp',
                        }:
                            continue
                        entries_by_name[name.lower()] = entry
                        basename_index[PurePosixPath(name.lower()).name].append(entry)

                    for index, card in enumerate(cards, start=1):
                        expected_images = len((card.get('media') or {}).get('images') or [])
                        try:
                            product, was_created = import_product(
                                card, replace_relations=True,
                            )
                            product_attached = _attach_archive_images(
                                archive, product, entries_by_name, basename_index,
                            )
                            if product_attached != expected_images:
                                raise ValueError(
                                    f'привязано фото {product_attached} из {expected_images}'
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
                                f'заменено {updated}; фото {attached}.',
                                ending='\r' if index < len(cards) else '\n',
                            )
                            self.stdout.flush()

                stale = Product.objects.filter(brand='the-row').exclude(sku__in=new_ids)
                stale_count = stale.count()
                if stale_count:
                    stale.delete()
                rebuild_product_catalog_metadata(Product.objects.filter(brand='the-row'))
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CommandError(f'Не удалось повторно открыть ZIP: {exc}') from exc

        imported = Product.objects.filter(brand='the-row', sku__in=new_ids)
        actual_count = imported.count()
        imported_images = ProductImage.objects.filter(product__in=imported)
        actual_images = imported_images.count()
        missing_local = imported_images.filter(local_image='').count()
        cropped_details = imported_images.exclude(detail_image='').count()
        invalid_positions = 0
        for product in imported.prefetch_related('images'):
            positions = [image.position for image in product.images.all()]
            if positions != list(range(len(positions))):
                invalid_positions += 1

        final_report = {
            **compact_quality_report(quality),
            'archive': str(archive_path),
            'old_products': old_count,
            'new_products': actual_count,
            'created_products': created,
            'updated_products': updated,
            'deleted_stale_products': stale_count,
            'attached_images': attached,
            'database_images': actual_images,
            'products_with_missing_local_image': missing_local,
            'products_with_cropped_detail_image': cropped_details,
            'products_with_invalid_image_positions': invalid_positions,
        }
        final_path = report_dir / 'result.json'
        self._write_json(final_path, final_report)
        if (
            actual_count != len(cards) or actual_images != quality['eligible_images']
            or missing_local or cropped_details or invalid_positions
        ):
            raise CommandError(f'Постпроверка импорта не пройдена. Отчёт: {final_path}')

        self.stdout.write(self.style.SUCCESS(
            f'The Row заменён: товаров {actual_count}, фото {actual_images}, '
            f'удалено отсутствующих SKU {stale_count}. Отчёт: {final_path}'
        ))

    @staticmethod
    def _write_json(path, payload):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
        )