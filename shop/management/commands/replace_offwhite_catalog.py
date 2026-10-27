import json
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shop.availability import available_size_variants, is_product_available, is_shoe_product
from shop.catalog_data import rebuild_product_catalog_metadata
from shop.models import ColorVariant, Product, ProductImage, SizeVariant
from shop.offwhite_editorial import improve_offwhite_copy
from shop.offwhite_import import (
    BRAND_SLUG, compact_quality_report, prepare_offwhite_archive,
)
from shop.services import _attach_archive_images, _validate_zip_entries, import_product


def keep_better_existing_text(card, old_product):
    """Keep edited Russian copy when the upstream English source has not changed."""
    if old_product is None:
        return card
    previous = old_product.raw_data or {}
    for field in ('title', 'description'):
        incoming = card.get(field) or {}
        older = previous.get(field) or {}
        if not isinstance(incoming, dict) or not isinstance(older, dict):
            continue
        if incoming.get('raw') != older.get('raw'):
            continue
        previous_text = (older.get('i18n') or {}).get('ru') or ''
        new_text = (incoming.get('i18n') or {}).get('ru') or ''
        if field == 'title':
            # The existing title may be manually edited; the English source is identical.
            # Never restore the known mistranslation after normalization fixed its type.
            if previous_text.startswith('Кроссовки ') and new_text.startswith((
                'Шнурки ', 'Кошелёк ', 'Украшение для шнурков ', 'Брелок ',
            )):
                continue
            if previous_text:
                incoming.setdefault('i18n', {})['ru'] = previous_text
        elif previous_text and len(previous_text.strip()) > len(new_text.strip()) * 1.15:
            incoming.setdefault('i18n', {})['ru'] = previous_text
    return card


def require_sized_shoes(card):
    """Do not sell shoes when the export confirms stock but supplies no sizes."""
    category = (card.get('category') or {}).get('canonical_path') or {}
    nodes = category.get('categories') or []
    if not any(node.get('slug') == 'shoes' for node in nodes):
        return False
    variants = card.get('variants') or {}
    if variants.get('sizes') or (card.get('availability') or {}).get('status') != 'in_stock':
        return False
    card['availability']['status'] = 'unknown'
    for color in variants.get('colors') or []:
        if color.get('current'):
            color['availability'] = {'status': 'unknown', 'raw': None}
    return True


def reconcile_offwhite_stock(products):
    """Publish only confirmed compatible shoe sizes and select an available size."""
    corrected_sizes = 0
    unavailable_shoes = []
    for product in products.prefetch_related('size_variants', 'color_variants').iterator(chunk_size=100):
        available = available_size_variants(product)
        if is_shoe_product(product) and product.in_stock_ecom and not available:
            product.in_stock_ecom = False
            raw_data = dict(product.raw_data or {})
            raw_data['availability'] = {
                **(raw_data.get('availability') or {}), 'status': 'unknown',
            }
            product.raw_data = raw_data
            product.save(update_fields=('in_stock_ecom', 'raw_data'))
            product.size_variants.filter(in_stock_ecom=True).update(in_stock_ecom=False)
            product.color_variants.filter(
                sku=product.sku_without_size, in_stock_ecom=True,
            ).update(in_stock_ecom=False)
            unavailable_shoes.append(product.sku)
        elif available and product.current_size != available[0].size and not any(
            variant.size == product.current_size for variant in available
        ):
            product.current_size = available[0].size
            product.save(update_fields=('current_size',))
            corrected_sizes += 1
    return corrected_sizes, unavailable_shoes


def block_unverified_color_targets(product_skus):
    """Hide links to colors whose own product cannot currently be purchased."""
    targets = Product.objects.filter(
        brand=BRAND_SLUG, sku__in=product_skus,
    ).prefetch_related('size_variants')
    by_article = {}
    for target in targets.iterator(chunk_size=100):
        article = target.sku.split(':', 1)[-1]
        by_article[article] = (
            by_article.get(article, False) or is_product_available(target)
        )
    unavailable_articles = [article for article, available in by_article.items() if not available]
    if not unavailable_articles:
        return 0
    return ColorVariant.objects.filter(
        product__brand=BRAND_SLUG, sku__in=unavailable_articles, in_stock_ecom=True,
    ).update(in_stock_ecom=False)


class Command(BaseCommand):
    help = (
        'Атомарно заменяет каталог Off-White из ZIP после проверки переводов, '
        'таксономии, цен и изображений.'
    )

    def add_arguments(self, parser):
        parser.add_argument('archive', help='Путь к ZIP-архиву Off-White.')
        parser.add_argument('--dry-run', action='store_true', help='Только аудит без изменения БД и media.')
        parser.add_argument('--allow-count-change', action='store_true')
        parser.add_argument(
            '--preserve-absent', action='store_true',
            help='Обновить SKU из ZIP; отсутствующие сохранить, но снять с продажи до подтверждения наличия.',
        )
        parser.add_argument('--backup-confirmed', action='store_true', help='Подтвердить наличие внешнего backup БД и media.')
        parser.add_argument(
            '--metadata-only', action='store_true',
            help='Атомарно обновить только карточки и таксономию, сохранив существующие изображения и варианты.',
        )
        parser.add_argument('--report-dir', default='', help='Каталог для preflight и итоговых отчётов.')

    def handle(self, *args, **options):
        archive_path = Path(options['archive']).expanduser().resolve()
        if not archive_path.is_file() or archive_path.suffix.lower() != '.zip':
            raise CommandError(f'ZIP-архив не найден: {archive_path}')
        if not options['dry_run'] and not options['backup_confirmed']:
            raise CommandError(
                'Реальный импорт запрещён без --backup-confirmed. '
                'Сначала создайте и проверьте внешний backup БД и media.'
            )

        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        report_dir = (
            Path(options['report_dir']).expanduser().resolve()
            if options['report_dir']
            else Path(settings.MEDIA_ROOT) / 'import-reports' / f'off-white-{timestamp}'
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        old_products = list(Product.objects.filter(brand=BRAND_SLUG).order_by('sku'))
        old_by_sku = {product.sku: product for product in old_products}
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
                cards, quality = prepare_offwhite_archive(archive)
        except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CommandError(f'Архив не прошёл проверку: {exc}') from exc

        quality_path = report_dir / 'preflight-quality.json'
        self._write_json(quality_path, compact_quality_report(quality))
        normalized_path = report_dir / 'catalog.normalized.json'
        blocking_path = report_dir / 'blocking-items.json'
        critical_types = set(quality['critical_issue_counts'])
        self._write_json(blocking_path, {
            'critical_issue_counts': quality['critical_issue_counts'],
            'items': [
                issue for issue in quality['issues']
                if issue.get('type') in critical_types
            ],
        })
        if quality['critical_issue_counts']:
            raise CommandError(
                f'Обнаружены критические проблемы: {quality["critical_issue_counts"]}. '
                f'Отчёт: {quality_path}; блокеры: {blocking_path}'
            )
        new_ids = {str(card.get('id') or '') for card in cards}
        cards = [improve_offwhite_copy(keep_better_existing_text(card, old_by_sku.get(card.get('id')))) for card in cards]
        unverified_shoe_sizes = [card['id'] for card in cards if require_sized_shoes(card)]
        self._write_json(normalized_path, cards)
        if any(not sku.startswith('offwhite:') for sku in new_ids):
            raise CommandError('Архив содержит карточки, не относящиеся к Off-White.')
        if old_count and len(cards) < old_count * .8 and not (
            options['allow_count_change'] or options['preserve_absent']
        ):
            raise CommandError(
                f'Новый каталог содержит {len(cards)} карточек вместо {old_count}. '
                'Для намеренного сильного уменьшения используйте --allow-count-change.'
            )
        metadata_only = options['metadata_only']
        if metadata_only and not options['dry_run']:
            old_ids = {product.sku for product in old_products}
            missing_ids = sorted(new_ids - old_ids)
            extra_ids = sorted(old_ids - new_ids)
            existing_images = ProductImage.objects.filter(product__brand=BRAND_SLUG)
            missing_local_images = existing_images.filter(local_image='').count()
            if (
                missing_ids or extra_ids
                or existing_images.count() != quality['eligible_images']
                or missing_local_images
            ):
                raise CommandError(
                    'Режим --metadata-only разрешён только для уже полностью импортированного '
                    'каталога с точным совпадением SKU и изображений. '
                    f'Отсутствуют SKU: {len(missing_ids)}; лишние SKU: {len(extra_ids)}; '
                    f'изображений в БД: {existing_images.count()} из {quality["eligible_images"]}; '
                    f'без локального файла: {missing_local_images}.'
                )

        self.stdout.write(self.style.SUCCESS(
            f'Preflight пройден: карточек {len(cards)}, фото {quality["eligible_images"]}, '
            'критических замечаний 0.'
        ))
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f'Dry-run завершён, данные не изменены. Отчёты: {report_dir}'
            ))
            return

        created = updated = attached = stale_count = unavailable_count = corrected_sizes = 0
        blocked_color_links = 0
        unavailable_shoes = []
        try:
            with transaction.atomic():
                if metadata_only:
                    for index, card in enumerate(cards, start=1):
                        try:
                            _product, was_created = import_product(card, replace_relations=False)
                        except (OSError, TypeError, ValueError) as exc:
                            raise CommandError(
                                f'Карточка {index}/{len(cards)} ({card.get("id")}): {exc}'
                            ) from exc
                        created += int(was_created)
                        updated += int(not was_created)
                        if index % 50 == 0 or index == len(cards):
                            self.stdout.write(
                                f'Обработано {index}/{len(cards)}; создано {created}; '
                                f'обновлено {updated}; фото {attached}.',
                                ending='\r' if index < len(cards) else '\n',
                            )
                            self.stdout.flush()
                else:
                    with zipfile.ZipFile(archive_path) as archive:
                        entries_by_name = {}
                        basename_index = defaultdict(list)
                        for name, entry in _validate_zip_entries(archive):
                            if PurePosixPath(name.lower()).suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
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
                                    raise ValueError(f'привязано фото {product_attached} из {expected}')
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
                                self.stdout.flush()
                if options['preserve_absent']:
                    absent = Product.objects.filter(brand=BRAND_SLUG).exclude(sku__in=new_ids)
                    unavailable_count = absent.filter(in_stock_ecom=True).count()
                    absent.update(in_stock_ecom=False)
                    ColorVariant.objects.filter(product__in=absent).update(in_stock_ecom=False)
                    SizeVariant.objects.filter(product__in=absent).update(in_stock_ecom=False)
                else:
                    stale = Product.objects.filter(brand=BRAND_SLUG).exclude(sku__in=new_ids)
                    stale_count = stale.count()
                    if stale_count:
                        stale.delete()
                rebuild_product_catalog_metadata(Product.objects.filter(brand=BRAND_SLUG))
                corrected_sizes, unavailable_shoes = reconcile_offwhite_stock(
                    Product.objects.filter(brand=BRAND_SLUG, sku__in=new_ids)
                )
                blocked_color_links = block_unverified_color_targets(
                    Product.objects.filter(brand=BRAND_SLUG).values_list('sku', flat=True)
                )
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise CommandError(f'Не удалось повторно открыть ZIP: {exc}') from exc

        imported = Product.objects.filter(brand=BRAND_SLUG, sku__in=new_ids)
        images = ProductImage.objects.filter(product__in=imported)
        invalid_positions = sum(
            [image.position for image in product.images.all()] != list(range(product.images.count()))
            for product in imported.prefetch_related('images')
        )
        final_report = {
            **compact_quality_report(quality), 'archive': str(archive_path),
            'old_products': old_count, 'new_products': imported.count(),
            'created_products': created, 'updated_products': updated,
            'deleted_stale_products': stale_count, 'attached_images': attached,
            'preserved_absent_products': len(set(old_by_sku) - new_ids) if options['preserve_absent'] else 0,
            'absent_products_marked_unavailable': unavailable_count,
            'shoe_skus_without_confirmed_sizes': unverified_shoe_sizes,
            'shoes_without_compatible_available_sizes': unavailable_shoes,
            'corrected_current_sizes': corrected_sizes,
            'blocked_unverified_color_links': blocked_color_links,
            'metadata_only': metadata_only,
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
            f'Off-White импортирован: товаров {final_report["new_products"]}, '
            f'фото {final_report["database_images"]}. Отчёт: {final_path}'
        ))

    @staticmethod
    def _write_json(path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')