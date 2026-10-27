"""Экспортирует каталог и администратора в переносимый сжатый Django fixture."""

import gzip
import json
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shop.models import Product, ProductImage


EXPORT_MODELS = (
    'auth.user',
    'shop.exchangerate',
    'shop.pricingrule',
    'shop.catalogbrand',
    'shop.catalogcolor',
    'shop.catalogmaterial',
    'shop.catalogcategory',
    'shop.sizeprofile',
    'shop.catalogsize',
    'shop.collectionbatch',
    'shop.product',
    'shop.productimage',
    'shop.banner',
    'shop.bannerimage',
    'shop.colorvariant',
    'shop.sizevariant',
    'telegram_bot.managercontact',
)


class Command(BaseCommand):
    help = 'Создаёт catalog.json.gz и manifest.json для переноса в PostgreSQL.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', default='deployment_export',
            help='Каталог результата (по умолчанию deployment_export).',
        )

    def handle(self, *args, **options):
        output = Path(options['output']).resolve()
        if output.exists() and not output.is_dir():
            raise CommandError(f'{output} существует и не является каталогом.')
        output.mkdir(parents=True, exist_ok=True)
        fixture_path = output / 'catalog.json.gz'
        with gzip.open(fixture_path, 'wt', encoding='utf-8', newline='') as fixture:
            call_command(
                'dumpdata', *EXPORT_MODELS,
                indent=None, stdout=fixture, verbosity=0,
            )
        manifest = {
            'version': 1,
            'created_at': timezone.now().isoformat(),
            'fixture': fixture_path.name,
            'products': Product.objects.count(),
            'product_images': ProductImage.objects.count(),
            'media_directory': 'media',
            'instructions': (
                'Скопируйте catalog.json.gz в imports/, а содержимое media/ — в media/. '
                'Затем выполните docker compose exec web python manage.py loaddata '
                '/app/imports/catalog.json.gz.'
            ),
        }
        (output / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        self.stdout.write(self.style.SUCCESS(
            f'Экспорт создан: {fixture_path} ({fixture_path.stat().st_size / 1024 / 1024:.1f} МиБ).'
        ))