"""Воспроизводимая редактура уже импортированного каталога Off-White."""

import json
from copy import deepcopy
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shop.catalog_data import product_catalog_metadata
from shop.models import Product, ProductImage
from shop.offwhite_editorial import improve_offwhite_copy


class Command(BaseCommand):
    help = 'Проверяет и при --apply применяет аудит текстов Off-White без изменения остатков и фото.'

    def add_arguments(self, parser):
        parser.add_argument('catalog', help='Импортированный catalog.normalized.json')
        parser.add_argument('--report-dir', required=True, help='Каталог для снимка до правок и отчёта')
        parser.add_argument('--apply', action='store_true', help='Применить после проверки отчёта')

    def handle(self, *args, **options):
        catalog = Path(options['catalog']).expanduser().resolve()
        report_dir = Path(options['report_dir']).expanduser().resolve()
        if not catalog.is_file():
            raise CommandError(f'Каталог не найден: {catalog}')
        cards = json.loads(catalog.read_text(encoding='utf-8'))
        if not isinstance(cards, list) or not cards:
            raise CommandError('Каталог должен содержать непустой список карточек.')
        ids = [card['id'] for card in cards]
        if len(ids) != len(set(ids)) or any(not sku.startswith('offwhite:') for sku in ids):
            raise CommandError('Ожидались уникальные Off-White SKU.')
        products = {p.sku: p for p in Product.objects.filter(brand='off-white', sku__in=ids)}
        if len(products) != len(ids):
            raise CommandError(f'В базе {len(products)} из {len(ids)} SKU; запись остановлена.')
        changes = []
        snapshot = []
        corrected = []
        for source in cards:
            sku = source['id']
            product = products[sku]
            card = improve_offwhite_copy(deepcopy(source))
            expected = (source['title']['i18n']['ru'], source['description']['i18n']['ru'])
            improved = (card['title']['i18n']['ru'], card['description']['i18n']['ru'])
            current = (product.title, product.description)
            if current not in (expected, improved):
                raise CommandError(f'{sku}: текст БД расходится с исходным каталогом; запись остановлена.')
            if sku == 'offwhite:OWUC002S25JER0011001':
                category = card['category']
                if product.category_path_key not in (
                    'women/accessories/socks', 'women/clothing/swim_beachwear',
                ):
                    raise CommandError(f'{sku}: исходная рубрика изменилась; запись остановлена.')
                category['canonical_path']['categories'] = [
                    {'code': 'OFFWHITE_CLOTHING_SWIM_BEACHWEAR', 'slug': 'clothing',
                     'ru': 'Одежда', 'level': 2},
                    {'code': 'OFFWHITE_CLOTHING_SWIM_BEACHWEAR', 'slug': 'swim_beachwear',
                     'ru': 'Бельё и пляжная одежда', 'level': 3},
                ]
                category['i18n']['ru'][-3:-1] = ['Одежда', 'Бельё и пляжная одежда']
            corrected.append(card)
            new_title = card['title']['i18n']['ru']
            new_text = card['description']['i18n']['ru']
            new_category = product_catalog_metadata(card) if sku == 'offwhite:OWUC002S25JER0011001' else None
            if current == improved and (
                not new_category or product.category_path_key == new_category['category_path_key']
            ):
                continue
            snapshot.append({
                'sku': sku, 'title': product.title, 'description': product.description,
                'raw_data': product.raw_data, 'category_path': product.category_path,
                'category_path_key': product.category_path_key, 'category_keys': product.category_keys,
                'category_name': product.category_name,
                'category_department_slug': product.category_department_slug,
                'category_department_name': product.category_department_name,
                'image_alts': list(product.images.values_list('pk', 'alt')),
            })
            changes.append({
                'sku': sku, 'before_title': product.title, 'after_title': new_title,
                'before_description': product.description, 'after_description': new_text,
                'category_before': product.category_path_key,
                'category_after': new_category['category_path_key'] if new_category else product.category_path_key,
            })
        report_dir.mkdir(parents=True, exist_ok=True)
        if snapshot or not (report_dir / 'before.json').is_file():
            self._write(report_dir / 'before.json', snapshot)
        self._write(report_dir / 'changes.json', changes)
        self._write(report_dir / 'catalog.editorial.json', corrected)
        self.stdout.write(f'Проверено {len(cards)} SKU; изменений {len(changes)}, '
                          f'названий {sum(c["before_title"] != c["after_title"] for c in changes)}, '
                          f'описаний {sum(c["before_description"] != c["after_description"] for c in changes)}.')
        if not options['apply']:
            self.stdout.write(f'Dry-run: БД не изменена. Отчёты: {report_dir}')
            return

        changes_by_sku = {change['sku']: change for change in changes}
        with transaction.atomic():
            locked = {p.sku: p for p in Product.objects.select_for_update().filter(
                brand='off-white', sku__in=changes_by_sku,
            )}
            for card in corrected:
                sku = card['id']
                if sku not in changes_by_sku:
                    continue
                product = locked[sku]
                change = changes_by_sku[sku]
                if (product.title, product.description) != (
                    change['before_title'], change['before_description'],
                ):
                    raise CommandError(f'{sku}: текст изменился во время проверки; транзакция отменена.')
                product.title = change['after_title']
                product.description = change['after_description']
                # Оставляем фактическое наличие (в том числе 9 заблокированных пар),
                # изменяем только поля текста и, для одного товара, рубрику.
                raw_data = deepcopy(product.raw_data)
                raw_data['title'] = card['title']
                raw_data['description'] = card['description']
                raw_data['category'] = card['category']
                product.raw_data = raw_data
                update_fields = ['title', 'description', 'raw_data']
                if change['category_before'] != change['category_after']:
                    metadata = product_catalog_metadata(raw_data)
                    for field in ('category_path', 'category_path_key', 'category_keys',
                                  'category_name', 'category_department_slug',
                                  'category_department_name'):
                        setattr(product, field, metadata[field])
                        update_fields.append(field)
                product.save(update_fields=update_fields)
                if change['before_title'] != change['after_title']:
                    ProductImage.objects.filter(product=product, alt=change['before_title']).update(
                        alt=change['after_title'],
                    )
        self.stdout.write(self.style.SUCCESS(f'Применено {len(changes)} карточек. Снимок: {report_dir / "before.json"}'))

    @staticmethod
    def _write(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')