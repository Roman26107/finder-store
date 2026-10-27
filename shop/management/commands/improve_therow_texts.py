import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from shop.models import Product
from shop.therow_editorial import (
    description_defect_score, editorial_description, editorial_issue_kinds,
    editorial_title,
)


class Command(BaseCommand):
    help = 'Выполняет редакторскую нормализацию названий и описаний The Row.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Применить проверенные изменения. Без флага выполняется dry-run.',
        )
        parser.add_argument(
            '--legacy-db', default='',
            help='SQLite-база со старой версией переводов для выбора лучшей основы.',
        )
        parser.add_argument('--report-dir', default='', help='Каталог для JSON-отчётов.')

    def handle(self, *args, **options):
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        report_dir = (
            Path(options['report_dir']).expanduser().resolve()
            if options['report_dir']
            else Path(settings.MEDIA_ROOT) / 'import-reports' / f'the-row-text-{timestamp}'
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        legacy = self._legacy_texts(options['legacy_db'])
        products = list(Product.objects.filter(brand='the-row').order_by('sku'))
        if not products:
            raise CommandError('В базе нет товаров The Row.')

        basis_counts = Counter()
        issue_counts = Counter()
        changes = []
        snapshot = []
        for product in products:
            raw_data = product.raw_data or {}
            raw_title = str((raw_data.get('title') or {}).get('raw') or '').strip()
            raw_description = str((raw_data.get('description') or {}).get('raw') or '').strip()
            legacy_title, legacy_description = legacy.get(product.sku, ('', ''))
            title = editorial_title(
                raw_title,
                raw_description=raw_description,
                category_name=product.category_name,
            )
            description, basis = editorial_description(
                product.description,
                raw_description=raw_description,
                legacy_text=legacy_description,
            )
            issues = editorial_issue_kinds(title, description)
            basis_counts[basis] += 1
            issue_counts.update(issues)
            snapshot.append({
                'id': product.pk,
                'sku': product.sku,
                'title': product.title,
                'description': product.description,
                'legacy_title': legacy_title,
                'legacy_description': legacy_description,
                'raw_title': raw_title,
                'raw_description': raw_description,
            })
            changes.append({
                'id': product.pk,
                'sku': product.sku,
                'old_title': product.title,
                'title': title,
                'old_description': product.description,
                'description': description,
                'description_basis': basis,
                'old_description_score': description_defect_score(product.description),
                'new_description_score': description_defect_score(description),
                'issues': issues,
            })

        summary = {
            'products': len(products),
            'changed_titles': sum(row['old_title'] != row['title'] for row in changes),
            'changed_descriptions': sum(
                row['old_description'] != row['description'] for row in changes
            ),
            'description_basis': dict(sorted(basis_counts.items())),
            'issue_counts': dict(sorted(issue_counts.items())),
            'remaining_issue_products': sum(bool(row['issues']) for row in changes),
        }
        self._write(report_dir / 'before.json', snapshot)
        self._write(report_dir / 'editorial-report.json', {
            'summary': summary,
            'items': changes,
        })
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))

        if issue_counts:
            raise CommandError(
                f'Редакторская проверка не пройдена: {dict(issue_counts)}. '
                f'Отчёт: {report_dir / "editorial-report.json"}'
            )
        if not options['apply']:
            self.stdout.write(self.style.SUCCESS(
                f'Dry-run завершён; база не изменена. Отчёт: {report_dir}'
            ))
            return

        by_id = {product.pk: product for product in products}
        with transaction.atomic():
            for row in changes:
                product = by_id[row['id']]
                product.title = row['title']
                product.description = row['description']
                raw_data = dict(product.raw_data or {})
                title_data = dict(raw_data.get('title') or {})
                title_i18n = dict(title_data.get('i18n') or {})
                title_i18n['ru'] = row['title']
                title_data['i18n'] = title_i18n
                raw_data['title'] = title_data
                description_data = dict(raw_data.get('description') or {})
                description_i18n = dict(description_data.get('i18n') or {})
                description_i18n['ru'] = row['description']
                description_data['i18n'] = description_i18n
                raw_data['description'] = description_data
                category = dict(raw_data.get('category') or {})
                category_i18n = dict(category.get('i18n') or {})
                category_path = list(category_i18n.get('ru') or [])
                if category_path:
                    category_path[-1] = row['title']
                    category_i18n['ru'] = category_path
                    category['i18n'] = category_i18n
                    raw_data['category'] = category
                product.raw_data = raw_data
            Product.objects.bulk_update(
                products, ('title', 'description', 'raw_data'), batch_size=200,
            )
        self.stdout.write(self.style.SUCCESS(
            f'Обновлено карточек The Row: {len(products)}. Отчёт: {report_dir}'
        ))

    @staticmethod
    def _legacy_texts(path):
        if not path:
            return {}
        database = Path(path).expanduser().resolve()
        if not database.is_file():
            raise CommandError(f'Резервная база не найдена: {database}')
        connection = sqlite3.connect(database)
        try:
            return {
                sku: (title or '', description or '')
                for sku, title, description in connection.execute(
                    "SELECT sku, title, description FROM shop_product WHERE brand='the-row'"
                )
            }
        except sqlite3.Error as exc:
            raise CommandError(f'Не удалось прочитать резервную базу: {exc}') from exc
        finally:
            connection.close()

    @staticmethod
    def _write(path, payload):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
        )