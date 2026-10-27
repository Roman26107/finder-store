"""Отдельный импорт большого ZIP Dolce & Gabbana с обязательным preflight."""

import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from shop.catalog_data import rebuild_product_catalog_metadata
from shop.dolce_import import BRAND, prepare_archive
from shop.models import CatalogBrand, ColorVariant, Product, SizeVariant
from shop.services import _attach_archive_images, import_product


class Command(BaseCommand):
    help = 'Проверяет или импортирует ZIP Dolce & Gabbana, пропуская неоднозначные карточки.'

    def add_arguments(self, parser):
        parser.add_argument('archive')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--backup-confirmed', action='store_true')
        parser.add_argument('--editorial-review-confirmed', action='store_true')
        parser.add_argument(
            '--local-owner-approved-publication', action='store_true',
            help='Только локальная SQLite: владелец разрешил импорт допущенных SKU без утверждения о полной вычитке.',
        )
        parser.add_argument(
            '--production-owner-approved-publication', action='store_true',
            help='Только production PostgreSQL: явное решение владельца опубликовать проверенный состав без заявления о полной вычитке.',
        )
        parser.add_argument('--report', default='')
        parser.add_argument('--batch-size', '--local-batch-size', dest='local_batch_size', type=int, default=0,
                            help='При публикации по решению владельца фиксировать импорт порциями; бренд скрыт до окончания.')

    def handle(self, *args, **options):
        path = Path(options['archive']).resolve()
        if not path.is_file() or path.suffix.lower() != '.zip':
            raise CommandError(f'ZIP не найден: {path}')
        try:
            with zipfile.ZipFile(path) as archive:
                cards, rejected, paths, source_audit = prepare_archive(archive)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise CommandError(f'Preflight не пройден: {exc}') from exc
        missing = [
            {'id': card['id'], 'file': image['file']}
            for card in cards for image in card['media']['images']
            if image['file'] not in paths
        ]
        # Verify the actual JPEG payloads, not just ZIP member names.
        image_paths = {image['file'] for card in cards for image in card['media']['images']}
        invalid = []
        with zipfile.ZipFile(path) as archive:
            for name in image_paths:
                try:
                    with archive.open(name) as source, Image.open(source) as image:
                        if min(image.size) < 300:
                            invalid.append({'file': name, 'reason': 'Размер меньше 300 пикселей'})
                        else:
                            image.verify()
                except (OSError, ValueError, UnidentifiedImageError, KeyError) as exc:
                    invalid.append({'file': name, 'reason': str(exc)[:180]})
        summary = {
            'source_archive': str(path),
            'archive_cards': len(cards) + len(rejected),
            'accepted': len(cards), 'rejected': len(rejected),
            'rejection_reasons': dict(Counter(item['reason'] for item in rejected)),
            'rejected_items': rejected,
            'images_expected': sum(len(card['media']['images']) for card in cards),
            'images_in_archive': len(paths),
            'images_not_imported': len(paths - image_paths),
            'missing_images': missing,
            'source_missing_image_references': source_audit['missing_image_references'],
            'source_in_stock_without_sizes': source_audit['in_stock_without_sizes'],
            'translation_audit': source_audit['translation'],
            'invalid_images': invalid,
            'in_stock_without_sizes': [card['id'] for card in cards
                if card['availability']['status'] == 'in_stock'
                and not card['variants'].get('sizes')
                and card['category']['canonical_path']['categories'][0]['slug'] in ('shoes', 'clothing')],
        }
        if options['report']:
            report = Path(options['report']).resolve()
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(
            f"Preflight: {summary['accepted']} карточек, {summary['images_expected']} фото, "
            f"отклонено {summary['rejected']}: {summary['rejection_reasons']}"
        )
        if not cards or missing or invalid or summary['in_stock_without_sizes']:
            raise CommandError('Не пройдена проверка фото или размеров; импорт отменён.')
        if options['dry_run']:
            return
        if not options['backup_confirmed']:
            raise CommandError('Для импорта подтвердите внешнюю резервную копию: --backup-confirmed')
        if options['local_owner_approved_publication'] and options['production_owner_approved_publication']:
            raise CommandError('Выберите только один режим публикации по решению владельца.')
        if options['local_owner_approved_publication']:
            if not settings.DEBUG or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
                raise CommandError('Публикация по решению владельца разрешена только на локальной SQLite при DEBUG.')
            if any(item['accepted'] for item in source_audit['translation']['manual_review']):
                raise CommandError('Допущенные карточки содержат неразобранные замечания по тексту.')
            self.stdout.write(self.style.WARNING(
                'Локальная публикация по решению владельца; полная редакторская вычитка НЕ подтверждена.'
            ))
        elif options['production_owner_approved_publication']:
            if (settings.DEBUG or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql'):
                raise CommandError('Серверная публикация по решению владельца разрешена только в production PostgreSQL.')
            if (len(cards), len(rejected), summary['images_expected']) != (6847, 448, 32805):
                raise CommandError('Состав архива отличается от проверенного набора 6847/448/32805.')
            if any(item['accepted'] for item in source_audit['translation']['manual_review']):
                raise CommandError('Допущенные карточки содержат неразобранные замечания по тексту.')
            if Product.objects.filter(brand=BRAND).exists():
                raise CommandError('Режим первой публикации требует пустой серверный каталог Dolce.')
            self.stdout.write(self.style.WARNING(
                'Публикация на сервере по решению владельца; полная редакторская вычитка НЕ подтверждена.'
            ))
        elif not options['editorial_review_confirmed']:
            raise CommandError('Импорт запрещён до вычитки переводов: --editorial-review-confirmed')
        batch_size = options['local_batch_size']
        if batch_size < 0 or (batch_size and not (options['local_owner_approved_publication'] or options['production_owner_approved_publication'])):
            raise CommandError('--batch-size допустим только для публикации по решению владельца и не может быть отрицательным.')

        old_ids = set(Product.objects.filter(brand=BRAND).values_list('sku', flat=True))
        incoming_ids = {card['id'] for card in cards}
        rejected_ids = {item['id'] for item in rejected}
        # Не удалять исторические заказы каскадом вместе с ранее импортированными SKU.
        existing_rejected = Product.objects.filter(brand=BRAND, sku__in=rejected_ids)
        if existing_rejected.exists():
            raise CommandError('Отклонённые SKU уже есть в БД: перед удалением проверьте связи с заказами.')
        # SKU, отсутствующие в новом файле, оставляем в БД для аудита, но не продаём.
        # Не удаляем товары других марок и не заменяем чужой SKU.
        collision = Product.objects.filter(sku__in=incoming_ids).exclude(brand=BRAND).first()
        if collision:
            raise CommandError(f'Архив пересекается с чужим SKU: {collision.sku}')

        attached = 0
        try:
            with zipfile.ZipFile(path) as archive:
                entries = {info.filename.lower(): info for info in archive.infolist()
                           if PurePosixPath(info.filename).suffix.lower() == '.jpg'}
                basenames = defaultdict(list)
                for name, entry in entries.items():
                    basenames[PurePosixPath(name).name].append(entry)

                def import_batch(batch, offset):
                    nonlocal attached
                    with transaction.atomic():
                        for index, card in enumerate(batch, offset + 1):
                            product, _ = import_product(card)
                            if batch_size and index == 1:
                                # Never expose a partly imported brand between commits.
                                CatalogBrand.objects.filter(slug=BRAND).update(is_active=False)
                            count = _attach_archive_images(archive, product, entries, basenames)
                            expected = len(card['media']['images'])
                            if count != expected:
                                raise CommandError(f"{card['id']}: привязано {count} из {expected} фото")
                            attached += count
                            if index % 100 == 0:
                                self.stdout.write(f'Импортировано {index}/{len(cards)}; фото {attached}', ending='\n')

                if batch_size:
                    for offset in range(0, len(cards), batch_size):
                        import_batch(cards[offset:offset + batch_size], offset)
                    with transaction.atomic():
                        rebuild_product_catalog_metadata(Product.objects.filter(brand=BRAND))
                        absent = Product.objects.filter(sku__in=(old_ids - incoming_ids), brand=BRAND)
                        absent.update(in_stock_ecom=False)
                        SizeVariant.objects.filter(product__in=absent).update(in_stock_ecom=False)
                        ColorVariant.objects.filter(product__in=absent).update(in_stock_ecom=False)
                        CatalogBrand.objects.filter(slug=BRAND).update(is_active=True)
                else:
                    with transaction.atomic():
                        import_batch(cards, 0)
                        rebuild_product_catalog_metadata(Product.objects.filter(brand=BRAND))
                        absent = Product.objects.filter(sku__in=(old_ids - incoming_ids), brand=BRAND)
                        absent.update(in_stock_ecom=False)
                        SizeVariant.objects.filter(product__in=absent).update(in_stock_ecom=False)
                        ColorVariant.objects.filter(product__in=absent).update(in_stock_ecom=False)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise CommandError(f'Импорт прерван: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(
            f'Импортировано {len(cards)} товаров, {attached} фото; отклонено {len(rejected)}.'
        ))