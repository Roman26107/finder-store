"""Read-only cross-brand audit: compare Russian product types with source titles."""

import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from shop.dolce_import import _repair_title
from shop.models import Product
from shop.services import parse_json_bytes


# A finding means "review", not permission to replace the translation automatically.
# Match a product noun, not a substring in a model name (e.g. Sock Shoe).
TITLE_MISMATCHES = (
    ('dubrae_as_sneakers', r'^ow_dubrae_[a-z0-9_]+$', r'^кроссовки\b'),
    ('keyring_as_sneakers', r'\bkeyrings?\b$', r'^кроссовки\b'),
    ('shoelaces_as_sneakers', r'(?<![a-z])(?:shoe[ _-]*laces?|shoelaces?)(?:_\d+)?$', r'^кроссовки\b'),
    ('wallet_as_sneakers', r'\bwallets?\b', r'^кроссовки\b'),
    ('socks_as_shoes', r'\bsocks?\b$', r'^(?:кроссовки|туфли|ботинки)\b'),
    ('sweatshirt_as_tshirt', r'\b(?:sweatshirts?|sweat-shirts?)\b', r'^футболка\b'),
    ('pants_as_skirt', r'\b(?:pantalons?|trousers|pants)\b', r'^(?:юбка|шорты)\b'),
)


def review_card(sku, brand, raw_title, ru_title, raw_description, ru_description, category):
    if not raw_title or not ru_title:
        return None
    reasons = [name for name, source, translated in TITLE_MISMATCHES
               if re.search(source, raw_title, re.I) and re.search(translated, ru_title, re.I)]
    if not reasons:
        return None
    return {
        'sku': sku, 'brand': brand, 'reasons': reasons,
        'raw_title': raw_title, 'ru_title': ru_title,
        'raw_description': raw_description, 'ru_description': ru_description,
        'category': category,
    }


class Command(BaseCommand):
    help = 'Аудит перевода всех локальных брендов и Dolce ZIP без изменения БД и архива.'

    def add_arguments(self, parser):
        parser.add_argument('--dolce-archive', help='Путь к исходному ZIP Dolce & Gabbana.')
        parser.add_argument('--report', required=True, help='Путь для JSON-отчёта.')

    def handle(self, *args, **options):
        report_path = Path(options['report']).expanduser().resolve()
        counts = Counter()
        findings = []
        for product in Product.objects.all().only(
            'sku', 'brand', 'title', 'description', 'raw_data', 'category_path_key',
        ).iterator(chunk_size=300):
            counts[product.brand] += 1
            raw = product.raw_data if isinstance(product.raw_data, dict) else {}
            source_title = (raw.get('title') or {}).get('raw') or ''
            source_description = (raw.get('description') or {}).get('raw') or ''
            finding = review_card(
                product.sku, product.brand, source_title, product.title,
                source_description, product.description, product.category_path_key,
            )
            if finding:
                findings.append(finding)

        archive_path = options.get('dolce_archive')
        if archive_path:
            archive_path = Path(archive_path).expanduser().resolve()
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    cards = parse_json_bytes(archive.read('catalog.json'), 'catalog.json')
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                raise CommandError(f'Не удалось прочитать Dolce catalog.json: {exc}') from exc
            if not isinstance(cards, list) or any(
                not isinstance(card, dict) or not str(card.get('id') or '').startswith('dolcegabbana:')
                for card in cards
            ):
                raise CommandError('Неверный формат или бренд Dolce catalog.json')
            for card in cards:
                counts['dolce-gabbana'] += 1
                title = card.get('title') or {}
                description = card.get('description') or {}
                # Review the title that would actually be imported, without touching the ZIP.
                candidate = {'title': {'raw': title.get('raw'),
                                       'i18n': dict(title.get('i18n') or {})}}
                _repair_title(candidate)
                finding = review_card(
                    card['id'], 'dolce-gabbana', title.get('raw') or '',
                    (candidate['title'].get('i18n') or {}).get('ru') or '',
                    description.get('raw') or '', (description.get('i18n') or {}).get('ru') or '',
                    (card.get('category') or {}).get('raw_path') or [],
                )
                if finding:
                    findings.append(finding)

        payload = {
            'checked_by_brand': dict(sorted(counts.items())),
            'method': 'Selective product-type mismatch rules, not a full translation review.',
            'findings': sorted(findings, key=lambda item: (item['brand'], item['sku'])),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(f'Проверено {sum(counts.values())} карточек; кандидатов {len(findings)}. Отчёт: {report_path}')