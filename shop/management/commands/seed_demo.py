"""Create a small, repeatable catalogue with locally drawn product illustrations."""

from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from shop.models import (
    Banner, BannerImage, CatalogBrand, ColorVariant, Product, ProductImage, SizeVariant,
    WeeklyLook, WeeklyLookItem,
)
from shop.pricing import clear_pricing_cache


PRODUCTS = (
    ('bag', 'Сумка Forma', 'women', 'bags', 'Сумки', 'handbags', 'Сумки с ручками', 18500, '#705848'),
    ('coat', 'Пальто Line', 'women', 'clothing', 'Одежда', 'outerwear', 'Верхняя одежда', 24900, '#b09a81'),
    ('sneakers', 'Кеды Studio', 'men', 'shoes', 'Обувь', 'sneakers', 'Кроссовки', 12900, '#dedbd3'),
    ('shirt', 'Рубашка Outline', 'men', 'clothing', 'Одежда', 'shirts', 'Рубашки', 8900, '#708484'),
    ('bag-small', 'Сумка Mini Forma', 'women', 'bags', 'Сумки', 'handbags', 'Сумки с ручками', 14500, '#444b44'),
    ('scarf', 'Шарф Texture', 'women', 'accessories', 'Аксессуары', 'scarves', 'Шарфы', 5900, '#ad876b'),
    ('loafers', 'Лоферы Field', 'men', 'shoes', 'Обувь', 'loafers_mules', 'Лоферы и мюли', 16900, '#52423a'),
    ('sweater', 'Джемпер Soft', 'women', 'clothing', 'Одежда', 'knitwear', 'Трикотаж', 9900, '#b5b8a5'),
)


def draw_product(kind, color, variant=0):
    image = Image.new('RGB', (960, 1200), '#e8e7e2')
    draw = ImageDraw.Draw(image)
    offset = variant * 18
    draw.ellipse((210, 940, 750, 990), fill='#d9d8d2')
    if kind.startswith('bag'):
        draw.arc((345 + offset, 300, 615 + offset, 600), 180, 360, fill=color, width=26)
        draw.rounded_rectangle((230 + offset, 450, 730 + offset, 935), radius=45, fill=color)
        draw.rectangle((445 + offset, 580, 515 + offset, 620), fill='#bca67b')
    elif kind in {'sneakers', 'loafers'}:
        draw.rounded_rectangle((170, 650 + offset, 800, 900 + offset), radius=85, fill=color)
        draw.rounded_rectangle((170, 865 + offset, 800, 915 + offset), radius=20, fill='#c9c5ba')
        for y in range(690, 820, 35):
            draw.line((480, y + offset, 630, y + offset), fill='#a39d8f', width=10)
    elif kind == 'scarf':
        draw.polygon(((300, 290), (570, 290), (690, 900), (420, 950)), fill=color)
        for x in range(425, 685, 22):
            draw.line((x, 910, x + 12, 990), fill=color, width=8)
    else:
        draw.polygon(((320, 320), (400, 280), (560, 280), (640, 320),
                      (800, 520), (670, 620), (625, 510), (650, 950),
                      (310, 950), (335, 510), (290, 620), (160, 520)), fill=color)
        draw.arc((400, 245, 560, 370), 0, 180, fill='#e8e7e2', width=22)
        if kind in {'coat', 'shirt'}:
            draw.line((480, 365, 480, 935), fill='#e0d8c9', width=5)
            for y in range(450, 900, 100):
                draw.ellipse((495, y, 507, y + 12), fill='#e0d8c9')
    buffer = BytesIO()
    image.save(buffer, 'WEBP', quality=85)
    return buffer.getvalue()


class Command(BaseCommand):
    help = 'Создать демонстрационные товары и баннеры без внешних загрузок и аккаунтов.'

    @transaction.atomic
    def handle(self, *args, **options):
        CatalogBrand.objects.get_or_create(slug='demo', defaults={'name': 'FINDER Studio'})
        images = []
        for index, row in enumerate(PRODUCTS):
            kind, title, department, group, group_ru, leaf, leaf_ru, price, color = row
            department_ru = 'Женщины' if department == 'women' else 'Мужчины'
            breadcrumbs = [
                {'code': department, 'value': department_ru},
                {'code': group, 'value': group_ru},
                {'code': leaf, 'value': leaf_ru},
            ]
            raw = {
                'id': f'demo:{kind}', 'brand': 'demo',
                'availability': {'status': 'in_stock'},
                'category': {'canonical_path': {
                    'department': department, 'department_ru': department_ru,
                    'categories': [
                        {'slug': group, 'ru': group_ru, 'level': 2},
                        {'slug': leaf, 'ru': leaf_ru, 'level': 3},
                    ],
                }},
            }
            product, _ = Product.objects.update_or_create(sku=f'demo:{kind}', defaults={
                'brand': 'demo', 'title': title, 'price': Decimal(price), 'currency': 'RUB',
                'pricing_mode': Product.PricingMode.FIXED_RUB, 'fixed_price_rub': Decimal(price),
                'current_color': 'Натуральный', 'current_size': 'SANS_TAILLE',
                'gender': [department], 'material': ['Демонстрационный материал'],
                'description': 'Вымышленный товар для демонстрации интерфейса. Не предназначен для продажи.',
                'categories_breadcrumbs': breadcrumbs, 'raw_data': raw,
                'in_stock_ecom': True, 'is_active': True, 'is_featured': index < 4,
                'collection': 'fall-winter',
                'catalog_scope': Product.CatalogScope.COLLECTION if index >= 6 else Product.CatalogScope.STORE,
                'collection_reviewed_at': timezone.now(),
            })
            for position in range(3):
                photo, _ = ProductImage.objects.get_or_create(product=product, position=position)
                if not photo.local_image or not photo.local_image.storage.exists(photo.local_image.name):
                    photo.local_image.save(f'{kind}-{position}.webp', ContentFile(draw_product(kind, color, position)), save=False)
                photo.alt = f'{title}, вид {position + 1}'
                photo.save()
                if position == 0:
                    images.append(photo)
            sizes = ('40', '41', '42', '43') if group == 'shoes' else ('S', 'M', 'L') if group == 'clothing' else ()
            for size in sizes:
                SizeVariant.objects.update_or_create(product=product, sku=f'{product.sku}:{size}', defaults={
                    'size': size, 'price': price, 'in_stock_ecom': size != sizes[-1],
                })
            if sizes:
                product.current_size = sizes[0]
                product.save(update_fields=('current_size',))
            ColorVariant.objects.update_or_create(product=product, sku=product.sku, defaults={
                'color': product.current_color, 'price': price, 'in_stock_ecom': True,
            })
        for slot, photo in ((Banner.Slot.FALL_WINTER, images[1]), (Banner.Slot.BAGS, images[0])):
            banner, _ = Banner.objects.get_or_create(slot=slot)
            BannerImage.objects.get_or_create(banner=banner, catalog_image=photo, defaults={'position': 0})
        look, _ = WeeklyLook.objects.get_or_create(pk=1)
        for position, photo in enumerate(images[:4]):
            WeeklyLookItem.objects.update_or_create(look=look, position=position, defaults={'catalog_image': photo})
        clear_pricing_cache()
        self.stdout.write(self.style.SUCCESS('Демокаталог готов: 8 товаров, 24 иллюстрации. Пользователи не создавались.'))