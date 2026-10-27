import io
import json
import shutil
import tempfile
import zipfile
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .accounts import normalize_phone, normalize_telegram
from .analytics_reports import economics_breakdown, qualified_analytics_sessions
from .catalog_data import rebuild_product_catalog_metadata
from .catalog_data import normalize_color_group
from .image_processing import (
    build_product_detail, create_product_image_gallery, create_product_image_preview,
)
from .catalog_normalization import (
    catalog_quality_report, normalize_miumiu_product,
    normalize_therow_catalog, normalize_therow_product,
)
from .therow_editorial import (
    editorial_description, editorial_issue_kinds, editorial_title,
)
from .valentino_editorial import (
    editorial_issue_kinds as valentino_editorial_issue_kinds,
    editorial_title as valentino_editorial_title,
)
from .valentino_import import normalize_valentino_product
from .offwhite_import import normalize_offwhite_product
from .offwhite_editorial import improve_offwhite_copy
from .management.commands.replace_offwhite_catalog import (
    block_unverified_color_targets, keep_better_existing_text,
    reconcile_offwhite_stock, require_sized_shoes,
)
from .models import (
    AnalyticsEvent, AnalyticsSession, AnalyticsVisitor, Banner, BannerImage,
    CatalogBrand, CatalogCategory, CatalogColor, CartItem, CatalogMaterial,
    CatalogSize, CollectionBatch, CustomerAddress, CustomerProfile, Favorite,
    MarketingCampaignSpend, Order, PricingRule, Product, ProductImage,
    ProductRequest, ProductRequestItem, SizeProfile, WeeklyLook, WeeklyLookItem,
)
from .services import (
    fix_mojibake,
    import_product,
    import_zip_archive,
    parse_json_file,
    storefront_image_order,
)
from .forms import BackofficeProductImageFormSet


class StorefrontImageLayoutTests(TestCase):
    def test_product_cards_use_one_uncropped_vertical_canvas_without_frame(self):
        """Карточки равной высоты показывают целое фото без фоновой рамки."""
        css_path = Path(__file__).resolve().parent.parent / 'static' / 'shop' / 'css' / 'store.css'
        css = css_path.read_text(encoding='utf-8')

        def declarations(selector):
            match = re.search(rf'{re.escape(selector)}\{{([^}}]+)\}}', css)
            self.assertIsNotNone(match, f'CSS-правило {selector} не найдено')
            return set(match.group(1).split(';'))

        wrap = declarations('.product-image-wrap')
        link = declarations('.product-image-link')
        track = declarations('.product-image-track')
        image = declarations('.product-image-slide')
        grid = declarations('.product-grid')
        card = declarations('.product-card')
        info = declarations('.product-card-info')
        title = declarations('.product-card-info h3')
        price = declarations('.product-card-info p')

        self.assertIn('--product-image-frame:clamp(', css)
        self.assertNotIn('--product-image-canvas', css)
        self.assertTrue({'width:100%', 'aspect-ratio:4/5', 'background:transparent'} <= wrap)
        self.assertTrue({
            'width:100%', 'height:100%', 'padding:0',
            'background:transparent',
        } <= link)
        self.assertTrue({
            'position:relative', 'display:block', 'width:100%', 'height:100%',
            'overflow:hidden', 'background:transparent',
        } <= track)
        self.assertTrue({
            'width:100%', 'height:100%', 'min-width:0', 'min-height:0',
            'padding:0', 'background:transparent', 'object-fit:contain',
            'object-position:center', 'position:absolute',
        } <= image)
        self.assertIn('transform .36s ease', next(
            value for value in image if value.startswith('transition:')
        ))
        self.assertNotIn('object-fit:cover', image)
        self.assertIn(
            '@media(hover:hover){.product-image-wrap:hover '
            '.product-image-slide.active{transform:scale(1.055)}}',
            css,
        )
        self.assertTrue({'display:grid', 'grid-auto-rows:1fr', 'align-items:stretch'} <= grid)
        self.assertTrue({
            'display:grid', 'grid-template-rows:auto 1fr', 'height:100%',
        } <= card)
        self.assertTrue({
            'display:flex', 'min-height:112px', 'flex-direction:column',
        } <= info)
        self.assertTrue({
            'min-height:3.1em', 'overflow:hidden', '-webkit-line-clamp:2',
        } <= title)
        self.assertIn('margin:auto 0 0', price)
        self.assertNotIn('.home-page .product-card-info p{margin-top:', css)
        self.assertIn('.gallery-thumb{width:83px;aspect-ratio:.82;padding:0;border:0;background:transparent;', css)
        self.assertIn('.gallery-main{aspect-ratio:.83;background:transparent;', css)
        self.assertIn('.cart-item-image{height:165px;border:0;outline:0;background:transparent;box-shadow:none}', css)
        self.assertIn(
            '.home-page .product-image-wrap{aspect-ratio:4/5;background:transparent}',
            css,
        )
        self.assertNotIn(
            '.home-page .product-image-wrap{aspect-ratio:4/5;background:var(--paper)}',
            css,
        )

    def test_storefront_css_cache_version_is_current(self):
        base_path = Path(__file__).resolve().parent / 'templates' / 'shop' / 'base.html'
        base = base_path.read_text(encoding='utf-8')

        self.assertIn("shop/css/store.css' %}?v=20260921-2", base)
        self.assertIn("shop/css/forms.css' %}?v=20260921-2", base)
        self.assertIn("shop/css/finder-theme.css' %}?v=20260930-4", base)
        self.assertIn("shop/js/store.js' %}?v=20260930-2", base)

        theme_css = (
            Path(__file__).resolve().parent.parent
            / 'static' / 'shop' / 'css' / 'finder-theme.css'
        ).read_text(encoding='utf-8')
        self.assertIn('--finder-brass:#8f7d5c', theme_css)
        self.assertIn('"Cormorant Garamond"', theme_css)

    def test_account_navigation_has_desktop_and_mobile_states(self):
        root = Path(__file__).resolve().parent.parent
        theme_css = (
            root / 'static' / 'shop' / 'css' / 'finder-theme.css'
        ).read_text(encoding='utf-8')
        javascript = (
            root / 'static' / 'shop' / 'js' / 'store.js'
        ).read_text(encoding='utf-8')
        base = (
            Path(__file__).resolve().parent / 'templates' / 'shop' / 'base.html'
        ).read_text(encoding='utf-8')

        self.assertIn('data-account-menu', base)
        self.assertIn('data-account-popover', base)
        self.assertNotIn('class="mobile-account-block"', base)
        self.assertIn('method="post" action="{% url \'shop:logout\' %}"', base)
        self.assertIn('@media(max-width:980px){', theme_css)
        self.assertIn('.main-nav>.nav-collection-link{display:none}', theme_css)
        self.assertIn('max-height:calc(100dvh - 62px)', theme_css)
        self.assertIn('.main-nav{align-content:start;justify-content:space-between;', theme_css)
        self.assertIn("window.matchMedia('(max-width: 980px)')", javascript)
        self.assertIn("window.location.assign(accountButton.dataset.accountUrl)", javascript)
        self.assertIn("if (event.key !== 'Escape') return", javascript)

    def test_mobile_storefront_overrides_do_not_change_desktop_layout(self):
        css_path = Path(__file__).resolve().parent.parent / 'static' / 'shop' / 'css' / 'store.css'
        css = css_path.read_text(encoding='utf-8')
        js_path = Path(__file__).resolve().parent.parent / 'static' / 'shop' / 'js' / 'store.js'
        javascript = js_path.read_text(encoding='utf-8')

        gallery_start = css.index('.card-gallery-dots,.detail-gallery-dots{display:none}')
        mobile_start = css.index('@media(max-width:760px){', gallery_start)
        mobile_css = css[mobile_start:]
        self.assertIn('@media(max-width:760px){', mobile_css)
        self.assertIn('.product-detail{display:block;', mobile_css)
        self.assertIn('.favorites-page .product-grid-four{grid-template-columns:1fr;', mobile_css)
        self.assertIn('.catalog-products>.product-grid-three{grid-template-columns:repeat(2,minmax(0,1fr));', mobile_css)
        self.assertIn('.cart-layout{display:block}', mobile_css)
        self.assertIn('.main-nav{align-items:center;', mobile_css)
        self.assertIn('scroll-snap-type:x mandatory', mobile_css)
        self.assertIn('touch-action:pan-y', mobile_css)
        self.assertIn('.category-banner img{z-index:0;object-fit:cover;', mobile_css)
        self.assertIn('.header-actions .search-button{display:grid;order:1}', mobile_css)
        self.assertIn('.header-actions .cart-link{order:2}', mobile_css)
        self.assertIn('.header-actions .account-link{order:3}', mobile_css)
        self.assertIn('.header-actions .favorite-link{display:none}', mobile_css)
        self.assertIn('.main-nav .nav-order-link{width:min(100%,260px);', mobile_css)
        self.assertIn('font:500 22px/1.15 var(--serif)', mobile_css)
        self.assertIn("document.querySelectorAll('[data-product-carousel]')", javascript)
        self.assertIn('const enableCyclicSwipe = (surface, getIndex, setIndex', javascript)
        self.assertIn("surface.addEventListener('pointermove'", javascript)
        self.assertIn('if (gesture.horizontal) event.preventDefault()', javascript)
        self.assertIn("setIndex(getIndex() + (deltaX < 0 ? 1 : -1), true)", javascript)
        self.assertIn('currentIndex = Math.max(0, Math.min(index, slides.length - 1))', javascript)
        self.assertIn('const blockCardClick = (timeout = 420)', javascript)
        self.assertIn("track.addEventListener('pointermove'", javascript)
        self.assertNotIn('currentIndex = (index + slides.length) % slides.length', javascript[javascript.index("document.querySelectorAll('[data-detail-gallery]')"):])

    def test_footer_hides_removed_service_links(self):
        base_path = Path(__file__).resolve().parent / 'templates' / 'shop' / 'base.html'
        base = base_path.read_text(encoding='utf-8')

        self.assertNotIn('>Возврат</a>', base)
        self.assertNotIn('>Частые вопросы</a>', base)
        self.assertNotIn('>Уход за изделиями</a>', base)


def product_payload(**overrides):
    payload = {
        'url': 'https://example.com/products/bag',
        'sku': 'H-001-BLACK',
        'sku_without_size': 'H-001',
        'product_code': 'BAGS',
        'title': 'Sac de test',
        'price': 18800,
        'currency': 'CNY',
        'current_color': 'Noir',
        'current_size': 'SANS_TAILLE',
        'gender': ['女士'],
        'material': ['кожа'],
        'in_stock_ecom': True,
        'description': 'Описание<br />в две строки',
        'dimensions': '20 × 15 см',
        'made_in': 'Сделано во Франции',
        'color_label': 'é¢è²',
        'product_detail_html': '<p>Натуральная кожа</p>',
        'care_instruction_html': '<p>Беречь от воды</p>',
        'story_behind': None,
        'categories_breadcrumbs': [
            {'code': 'WOMEN', 'value': 'Женщины', 'url': '/women/'},
            {'code': 'BAGS', 'value': 'Сумки', 'url': '/women/bags/'},
        ],
        'images': [{
            'tag': 'main', 'position': 0,
            'thumbnail': 'https://example.com/thumbnail.jpg',
            'medium': 'https://example.com/medium.jpg',
            'large': 'https://example.com/large.jpg',
            'original': 'https://example.com/original.jpg',
            'local_path': 'data/images/H-001.jpg',
        }],
        'colors': [{
            'sku': 'H-001-GOLD', 'color': 'Gold', 'price': 18800,
            'url': 'https://example.com/gold', 'in_stock_ecom': True,
            'thumbnail': 'https://example.com/gold.jpg',
        }],
        'sizes': [{'sku': 'H-001-20', 'size': '20', 'price': 18800, 'in_stock_ecom': False}],
        'fetched_at': 1767225600,
        'i18n': {'ru': {'title': 'Тестовая сумка', 'description': 'Русское описание'}},
    }
    payload.update(overrides)
    return payload


def zip_upload(filename='sample.zip', unsafe=False):
    payload = product_payload()
    from PIL import Image
    image_content = io.BytesIO()
    Image.new('RGB', (2400, 1800), '#c7a982').save(image_content, format='JPEG')
    payload['images'][0]['local_path'] = 'data/images/H-001.jpg'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        if unsafe:
            archive.writestr('../product.json', json.dumps(payload, ensure_ascii=False))
        else:
            archive.writestr(
                'package/data/products/product.json',
                json.dumps(payload, ensure_ascii=False),
            )
            archive.writestr(
                'package/data/images/H-001.jpg', image_content.getvalue()
            )
            archive.writestr('package/readme.txt', 'ignored')
    return SimpleUploadedFile(
        filename, stream.getvalue(), content_type='application/zip'
    )


def therow_zip_path(directory, filename='therow-test.zip'):
    """Создаёт компактный canonical ZIP The Row для сквозного теста замены."""
    from PIL import Image

    image_paths = [f'images/therow_test-product/image-{index}.jpg' for index in range(1, 7)]
    card = {
        'id': 'therow:test-product',
        'source': {
            'shop': 'therow', 'url': 'https://www.therow.com/products/test-product',
            'source_id': 'test-product', 'fetched_at': 1789048870,
        },
        'brand': 'The Row',
        'title': {'raw': 'Test Tote', 'lang': 'en', 'i18n': {'ru': 'Сумка-тоут Test'}},
        'description': {
            'raw': 'Structured tote bag in soft calfskin leather.', 'lang': 'en',
            'i18n': {'ru': 'Структурированная сумка-тоут из мягкой телячьей кожи.'},
        },
        'gender': 'women',
        'category': {
            'raw_path': ['WOMEN', 'TOTES'], 'raw_lang': 'en',
            'i18n': {'ru': ['Женщины', 'Сумки-тоут']}, 'canonical_path': None,
        },
        'attributes': {
            'material': {'raw': None, 'lang': 'en'}, 'material_normalized': [],
            'made_in': {'raw': None, 'lang': 'en'},
            'dimensions': {'raw': None, 'lang': 'en'},
        },
        'price': {'amount': 2500, 'currency': 'USD', 'converted': None},
        'availability': {'status': 'in_stock', 'raw': 'available'},
        'media': {'images': [{
            'role': f'role-{index}',
            'urls': {'original': f'https://example.com/image-{index}.jpg'},
            'file': path,
        } for index, path in enumerate(image_paths, start=1)]},
        'variants': {
            'colors': [{
                'label': {'raw': 'Black', 'lang': 'en', 'i18n': {'ru': 'Чёрный'}},
                'sku': 'test-product', 'price': {'amount': 2500, 'currency': 'USD'},
                'availability': {'status': 'in_stock', 'raw': True},
                'url': 'https://www.therow.com/products/test-product', 'current': True,
            }],
            'sizes': [],
        },
        'related_lines': [],
        'normalized_from': {'fetched_at': 1789048870, 'translated_at': 1789454170},
    }
    archive_path = Path(directory, filename)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('catalog.json', json.dumps([card], ensure_ascii=False))
        for index, image_path in enumerate(image_paths, start=1):
            content = io.BytesIO()
            size = (100, 100) if index == 6 else (800, 1200)
            Image.new('RGB', size, (225 - index, 225, 225)).save(content, format='JPEG')
            archive.writestr(image_path, content.getvalue())
    return archive_path


def valentino_zip_path(directory, filename='valentino-test.zip'):
    """Создаёт компактный Valentino ZIP с фронтальным packshot первым."""
    from PIL import Image

    image_paths = [
        f'images/valentino_WBTEST_0NO/image-{index}.jpg'
        for index in range(1, 4)
    ]
    card = {
        'id': 'valentino:WBTEST_0NO',
        'source': {
            'shop': 'valentino',
            'url': 'https://www.valentino.com/product-test-WBTEST_0NO',
            'source_id': 'WBTEST_0NO', 'fetched_at': 1789048870,
        },
        'brand': 'Valentino Garavani',
        'title': {
            'raw': 'Borsa Piccola A Tracolla Valentino Garavani Rockstud In Vitello Granato',
            'lang': 'en', 'i18n': {'ru': 'Ошибочное название'},
        },
        'description': {
            'raw': (
                'Borsa piccola a tracolla Valentino Garavani Rockstud in vitello '
                'granato, decorata da borchie. La borsa può essere indossata a '
                'spalla o crossbody grazie alla tracolla in pelle scorrevole.'
            ),
            'lang': 'en', 'i18n': {'ru': 'Сумка из кожи цвета граната с шипами.'},
        },
        'gender': 'women',
        'category': {
            'raw_path': ['WOMEN', 'Borse a spalla'], 'raw_lang': 'en',
            'i18n': {'ru': ['ЖЕНЩИНЫ', 'Сумки на плечо']},
            'canonical_path': None,
        },
        'attributes': {
            'material': {'raw': None, 'lang': 'en'}, 'material_normalized': [],
            'made_in': {'raw': None, 'lang': 'en'},
            'dimensions': {'raw': None, 'lang': 'en'},
        },
        'price': {'amount': 2500, 'currency': 'EUR', 'converted': None},
        'availability': {'status': 'in_stock', 'raw': True},
        'media': {'images': [{
            'role': f'role-{index}',
            'urls': {'original': f'https://example.com/image-{index}.jpg'},
            'file': path,
        } for index, path in enumerate(image_paths, start=1)]},
        'variants': {
            'colors': [{
                'label': {'raw': 'NERO', 'lang': 'it', 'i18n': {'ru': 'Чёрный'}},
                'sku': 'WBTEST_0NO',
                'price': {'amount': 2500, 'currency': 'EUR'},
                'availability': {'status': 'in_stock', 'raw': True},
                'url': 'https://www.valentino.com/product-test-WBTEST_0NO',
                'current': True,
            }],
            'sizes': [],
        },
    }
    archive_path = Path(directory, filename)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('catalog.json', json.dumps([card], ensure_ascii=False))
        for index, image_path in enumerate(image_paths, start=1):
            content = io.BytesIO()
            Image.new('RGB', (800, 1200), (240 - index, 240, 238)).save(
                content, format='JPEG'
            )
            archive.writestr(image_path, content.getvalue())
    return archive_path


def offwhite_zip_path(directory, filename='offwhite-test.zip'):
    """Создаёт компактный Off-White ZIP для preflight и dry-run тестов."""
    from PIL import Image

    image_path = 'images/offwhite_TEST/0_0.jpg'
    card = {
        'id': 'offwhite:TEST',
        'source': {
            'shop': 'offwhite',
            'url': 'https://www.off---white.com/en-fr/women/clothing/t-shirts/test.html',
            'source_id': 'TEST', 'fetched_at': 1789582265,
        },
        'brand': 'OFF WHITE',
        'title': {
            'raw': 'Seamless Color Block Top', 'lang': 'en',
            'i18n': {'ru': 'Бесшовная топ-одежда с цветовыми блоками'},
        },
        'description': {
            'raw': '<p>Seamless top with a high turtleneck collar.</p>', 'lang': 'en',
            'i18n': {'ru': '<p>Топ-одежда с высоким турнувочным воротником.</p>'},
        },
        'gender': 'women',
        'category': {
            'raw_path': [], 'raw_lang': 'en', 'i18n': None, 'canonical_path': None,
        },
        'attributes': {
            'material': {'raw': '100% Polyamide (Nylon)', 'lang': 'en'},
            'material_normalized': [],
            'made_in': {'raw': None, 'lang': 'en'},
            'dimensions': {'raw': None, 'lang': 'en'},
        },
        'price': {'amount': None, 'currency': None, 'converted': None},
        'availability': {'status': 'unknown', 'raw': None},
        'media': {'images': [{
            'role': '0', 'urls': {'original': 'https://example.com/0_0.jpg'},
            'file': image_path,
        }]},
        'variants': {'colors': [{
            'label': {'raw': 'Black', 'lang': 'en', 'i18n': None},
            'sku': 'TEST', 'price': {'amount': 450, 'currency': 'EUR'},
            'availability': {'status': 'in_stock', 'raw': True},
            'url': 'https://www.off---white.com/en-fr/women/clothing/t-shirts/test.html',
            'current': True,
        }], 'sizes': []},
    }
    content = io.BytesIO()
    Image.new('RGB', (800, 1200), '#eeeeec').save(content, format='JPEG')
    archive_path = Path(directory, filename)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('catalog.json', json.dumps([card], ensure_ascii=False))
        archive.writestr(image_path, content.getvalue())
    return archive_path


def image_upload(filename, size=(2400, 1800), mode='RGB', color='#c7a982', image_format='JPEG'):
    """Создаёт настоящее загружаемое изображение для сквозных тестов storage."""
    from PIL import Image

    content = io.BytesIO()
    Image.new(mode, size, color).save(content, format=image_format)
    return SimpleUploadedFile(
        filename,
        content.getvalue(),
        content_type=f'image/{image_format.lower()}',
    )


class ProductImportTests(TestCase):
    def test_prepare_production_data_preserves_catalog_and_superuser(self):
        from django.contrib.sessions.models import Session
        from shop.models import AnalyticsVisitor, ApiRequestLog, ErrorLog

        product, _ = import_product(product_payload())
        image_count = product.images.count()
        color_count = product.color_variants.count()
        size_count = product.size_variants.count()
        admin = get_user_model().objects.create_superuser(
            username='production-admin', email='admin@example.com', password='password'
        )
        customer = get_user_model().objects.create_user(
            username='customer@example.com', email='customer@example.com'
        )
        CustomerProfile.objects.create(user=admin, phone='+79990000001')
        CustomerProfile.objects.create(user=customer, phone='+79990000002')
        Order.objects.create(
            user=customer, customer_name='Клиент', customer_email=customer.email,
            customer_phone='+79990000002', delivery_address='Москва',
        )
        ProductRequest.objects.create(
            user=customer, customer_name='Клиент', customer_email=customer.email,
            customer_phone='+79990000002',
        )
        AnalyticsVisitor.objects.create(user=customer)
        ApiRequestLog.objects.create(method='GET', path='/api/', status_code=200)
        ErrorLog.objects.create(level='ERROR', logger_name='test', message='test')
        Session.objects.create(session_key='test-session', session_data='', expire_date=timezone.now())

        call_command('prepare_production_data', yes=True, skip_vacuum=True, verbosity=0)

        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.images.count(), image_count)
        self.assertEqual(product.color_variants.count(), color_count)
        self.assertEqual(product.size_variants.count(), size_count)
        self.assertTrue(get_user_model().objects.filter(pk=admin.pk, is_superuser=True).exists())
        self.assertFalse(get_user_model().objects.filter(pk=customer.pk).exists())
        self.assertEqual(CustomerProfile.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(ProductRequest.objects.count(), 0)
        self.assertEqual(AnalyticsVisitor.objects.count(), 0)
        self.assertEqual(ApiRequestLog.objects.count(), 0)
        self.assertEqual(ErrorLog.objects.count(), 0)
        self.assertEqual(Session.objects.count(), 0)

    def test_color_group_recognizes_localized_designer_shades(self):
        self.assertEqual(normalize_color_group('Коньячный'), 'brown')
        self.assertEqual(normalize_color_group('Сапфировый'), 'blue')
        self.assertEqual(normalize_color_group('Слоновая кость'), 'white')
        self.assertEqual(normalize_color_group('Белый / Светло-голубой'), 'multicolor')

    def test_import_creates_complete_product(self):
        product, created = import_product(product_payload())

        self.assertTrue(created)
        self.assertEqual(product.brand, 'hermes')
        self.assertEqual(product.title, 'Тестовая сумка')
        self.assertEqual(product.color_label, '颜色')
        self.assertEqual(product.category_name, 'Сумки')
        self.assertEqual(product.gender, ['women'])
        self.assertEqual(product.images.count(), 1)
        self.assertEqual(product.color_variants.count(), 1)
        self.assertEqual(product.size_variants.count(), 1)
        self.assertEqual(product.images.get().source_local_path, 'data/images/H-001.jpg')
        self.assertEqual(product.raw_data['sku'], 'H-001-BLACK')
        self.assertNotRegex(product.slug, '[А-Яа-я]')

    def test_product_image_preview_is_local_webp(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            product, _ = import_product(product_payload())
            image = product.images.get()
            source = io.BytesIO()
            from PIL import Image
            Image.new('RGB', (1200, 800), '#eee9e2').save(source, format='JPEG')
            image.local_image.save('large.jpg', ContentFile(source.getvalue()), save=True)

            created = create_product_image_preview(image)
            repeated = create_product_image_preview(image)

            image.refresh_from_db()
            self.assertTrue(created)
            self.assertFalse(repeated)
            self.assertTrue(image.preview_image.name.endswith('.webp'))
            self.assertEqual(image.preview_url, image.preview_image.url)
            with Image.open(image.preview_image.path) as preview:
                self.assertEqual(preview.size, (640, 427))
                self.assertEqual(preview.format, 'WEBP')

    def test_product_gallery_is_1600_webp_and_detail_fallback(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            product, _ = import_product(product_payload())
            image = product.images.get()
            source = io.BytesIO()
            from PIL import Image
            Image.new('RGB', (2400, 1600), '#c7a982').save(source, format='JPEG')
            image.local_image.save('large.jpg', ContentFile(source.getvalue()), save=True)

            created = create_product_image_gallery(image)
            repeated = create_product_image_gallery(image)

            image.refresh_from_db()
            self.assertTrue(created)
            self.assertFalse(repeated)
            self.assertTrue(image.gallery_image.name.endswith('.webp'))
            self.assertEqual(image.detail_url, image.gallery_image.url)
            self.assertEqual(image.card_url, image.local_image.url)
            self.assertEqual(image.banner_url, image.local_image.url)
            with Image.open(image.gallery_image.path) as gallery:
                self.assertEqual(gallery.size, (1600, 1067))
                self.assertEqual(gallery.format, 'WEBP')

    def test_product_page_uses_compact_source_webp_without_reencoding(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            product, _ = import_product(product_payload())
            image = product.images.get()
            source = io.BytesIO()
            from PIL import Image
            Image.new('RGB', (1600, 1600), '#c7a982').save(
                source, format='WEBP', quality=84
            )
            image.local_image.save('compact.webp', ContentFile(source.getvalue()), save=False)
            image.gallery_image = 'products/gallery/compact.webp'
            image.save(update_fields=('local_image', 'gallery_image'))

            self.assertEqual(image.card_url, image.local_image.url)
            self.assertEqual(image.product_page_url, image.local_image.url)
            self.assertEqual(image.banner_url, image.local_image.url)

    def test_product_page_does_not_use_heavy_jpeg_source(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            product, _ = import_product(product_payload())
            image = product.images.get()
            source = io.BytesIO()
            from PIL import Image
            Image.new('RGB', (2400, 3000), '#c7a982').save(
                source, format='JPEG', quality=95
            )
            # Имитируем legacy-исходник, сохранённый до автоматической оптимизации.
            legacy_name = image.local_image.storage.save(
                'products/source/large.jpg', ContentFile(source.getvalue())
            )
            image.local_image = legacy_name
            image.gallery_image = 'products/gallery/large.webp'
            image.save(update_fields=('local_image', 'gallery_image'))

            self.assertEqual(image.card_url, image.gallery_image.url)
            self.assertEqual(image.product_page_url, image.gallery_image.url)
            self.assertEqual(image.banner_url, image.gallery_image.url)

    def test_card_uses_original_when_no_large_derivative_exists(self):
        product, _ = import_product(product_payload(brand='the-row'))
        image = product.images.get()
        image.local_image = 'products/source/the-row-original.jpg'
        image.preview_image = 'products/previews/the-row-small.webp'
        image.save(update_fields=('local_image', 'preview_image'))

        self.assertEqual(image.card_url, image.local_image.url)
        self.assertEqual(image.product_page_url, image.local_image.url)

    def test_gallery_command_requires_explicit_scope(self):
        with self.assertRaises(CommandError):
            call_command('generate_product_gallery', dry_run=True)

    def test_gallery_command_can_exclude_the_row(self):
        hermes, _ = import_product(product_payload())
        the_row, _ = import_product(product_payload(
            sku='therow:test-gallery-scope', brand='the-row'
        ))
        ProductImage.objects.filter(product__in=(hermes, the_row)).update(
            local_image='products/source.jpg'
        )
        output = io.StringIO()

        call_command(
            'generate_product_gallery', exclude_brand=['the-row'],
            dry_run=True, stdout=output,
        )

        self.assertIn('к обработке 1 изображений', output.getvalue())

    def test_purge_originals_requires_valid_gallery_and_protects_the_row(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            product, _ = import_product(product_payload())
            image = product.images.get()
            source = io.BytesIO()
            Image.new('RGB', (800, 800), '#c7a982').save(source, format='JPEG')
            image.local_image.save('original.jpg', ContentFile(source.getvalue()), save=True)
            original_path = Path(image.local_image.path)
            manifest_path = Path(media_root) / 'purged.json'

            with self.assertRaises(CommandError):
                call_command(
                    'purge_product_originals', brand=['the-row'], confirm=True,
                    manifest=manifest_path,
                )

            output = io.StringIO()
            call_command(
                'purge_product_originals', brand=['hermes'], confirm=True,
                manifest=manifest_path, stdout=output,
            )
            image.refresh_from_db()
            self.assertTrue(image.local_image.name)
            self.assertTrue(original_path.exists())
            self.assertIn('пропущено 1', output.getvalue())

            gallery = io.BytesIO()
            Image.new('RGB', (1600, 1600), '#c7a982').save(
                gallery, format='WEBP', quality=84
            )
            image.gallery_image.save('gallery.webp', ContentFile(gallery.getvalue()), save=True)
            output = io.StringIO()
            call_command(
                'purge_product_originals', brand=['hermes'], confirm=True,
                manifest=manifest_path, stdout=output,
            )
            image.refresh_from_db()
            self.assertFalse(image.local_image.name)
            self.assertFalse(original_path.exists())
            self.assertEqual(image.product_page_url, image.gallery_image.url)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            self.assertEqual(manifest['total_files'], 1)
            self.assertEqual(manifest['files'][0]['id'], image.pk)

    def test_product_detail_normalizes_small_item_on_light_background(self):
        from PIL import Image, ImageChops, ImageDraw

        source = io.BytesIO()
        image = Image.new('RGB', (1200, 1200), '#ffffff')
        ImageDraw.Draw(image).rectangle((510, 540, 690, 660), fill='#9b3434')
        image.save(source, format='PNG')
        source.seek(0)

        content = build_product_detail(source)

        self.assertIsNotNone(content)
        with Image.open(io.BytesIO(content)) as normalized:
            self.assertEqual(normalized.size, (1000, 1000))
            self.assertEqual(normalized.format, 'WEBP')
            corner = normalized.convert('RGB').getpixel((5, 5))
            self.assertTrue(all(abs(value - expected) < 8 for value, expected in zip(
                corner, (231, 229, 218)
            )))
            # После удаления пустых полей предмет занимает заметную часть кадра.
            background = Image.new('RGB', normalized.size, corner)
            bbox = ImageChops.difference(normalized.convert('RGB'), background).getbbox()
            self.assertGreater(bbox[2] - bbox[0], 500)

    def test_product_detail_skips_non_uniform_background(self):
        from PIL import Image

        source = io.BytesIO()
        image = Image.effect_noise((1200, 1200), 100).convert('RGB')
        image.save(source, format='JPEG')
        source.seek(0)

        self.assertIsNone(build_product_detail(source))

    def test_import_updates_by_sku_and_replaces_relations(self):
        original, _ = import_product(product_payload())
        updated, created = import_product(product_payload(price=19900, images=[]))

        self.assertFalse(created)
        self.assertEqual(original.pk, updated.pk)
        self.assertEqual(updated.price, 19900)
        self.assertEqual(updated.images.count(), 0)
        self.assertEqual(Product.objects.count(), 1)

    def test_replacing_images_removes_unreferenced_local_file_after_commit(self):
        product, _ = import_product(product_payload())
        image = product.images.get()
        from PIL import Image
        source = io.BytesIO()
        Image.new('RGB', (800, 800), '#c7a982').save(source, format='JPEG')
        image.local_image.save('old-image.jpg', ContentFile(source.getvalue()), save=True)
        image.preview_image.save('old-preview.webp', ContentFile(b'old-preview'), save=True)
        image.gallery_image.save('old-gallery.webp', ContentFile(b'old-gallery'), save=True)
        image.detail_image.save('old-detail.webp', ContentFile(b'old-detail'), save=True)
        old_path = Path(image.local_image.path)
        old_preview_path = Path(image.preview_image.path)
        old_gallery_path = Path(image.gallery_image.path)
        old_detail_path = Path(image.detail_image.path)
        self.assertTrue(old_path.exists())
        self.assertTrue(old_preview_path.exists())
        self.assertTrue(old_gallery_path.exists())
        self.assertTrue(old_detail_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            import_product(product_payload(images=[]))

        self.assertFalse(old_path.exists())
        self.assertFalse(old_preview_path.exists())
        self.assertFalse(old_gallery_path.exists())
        self.assertFalse(old_detail_path.exists())

    def test_parse_json_file_supports_bom_and_products_wrapper(self):
        content = json.dumps({'products': [product_payload()]}, ensure_ascii=False).encode('utf-8-sig')
        uploaded = SimpleUploadedFile('products.json', content, content_type='application/json')
        cards = parse_json_file(uploaded)
        self.assertEqual(cards[0]['sku'], 'H-001-BLACK')

    def test_known_mojibake_is_fixed_recursively(self):
        self.assertEqual(fix_mojibake({'label': 'é¢è²'}), {'label': '颜色'})

    def test_import_supports_canonical_product(self):
        canonical = {
            'id': 'canonical-001',
            'source': 'hermes',
            'brand': 'Hermès',
            'title': {'raw': '原始标题', 'lang': 'zh', 'i18n': {'ru': 'Каноническая сумка'}},
            'description': {'raw': '原始描述', 'lang': 'zh', 'i18n': {'ru': 'Описание сумки'}},
            'gender': 'women',
            'category': {
                'raw_path': [
                    {'code': 'WOMEN', 'value': '女士', 'url': '/women/'},
                    {'code': 'BAGS', 'value': '女包', 'url': '/women/bags/'},
                    {'code': 'PRODUCT', 'value': '产品', 'url': '/product/'},
                ],
                'i18n': {'ru': ['Женщины', 'Сумки', 'Каноническая сумка']},
            },
            'attributes': {
                'material': {'raw': ['皮革'], 'lang': 'zh'},
                'material_normalized': ['кожа'],
                'made_in': {'raw': '法国制造', 'i18n': {'ru': 'Сделано во Франции'}},
                'dimensions': {'raw': '20 см', 'i18n': {'ru': 'Размер: 20 см'}},
            },
            'price': {'amount': 25000, 'currency': 'CNY'},
            'availability': {'status': 'in_stock'},
            'media': {'images': [{
                'file': 'images/canonical.jpg', 'role': 'front',
                'urls': {'original': 'https://example.com/canonical.jpg'},
            }]},
            'variants': {'colors': [{
                'sku': 'CANONICAL-GOLD',
                'label': {'raw': '金色', 'i18n': {'ru': 'Золотой'}},
                'price': {'amount': 25000, 'currency': 'CNY'},
                'availability': {'status': 'in_stock'},
                'url': 'https://example.com/canonical/',
            }], 'sizes': []},
            'normalized_from': {'fetched_at': 1767225600},
        }
        product, created = import_product(canonical)
        self.assertTrue(created)
        self.assertEqual(product.sku, 'canonical-001')
        self.assertEqual(product.brand, 'hermes')
        self.assertEqual(product.category_name, 'Сумки')
        self.assertEqual(product.price, 25000)
        self.assertEqual(product.title, 'Каноническая сумка')
        self.assertEqual(product.description, 'Описание сумки')
        self.assertEqual(product.current_color, 'Золотой')
        self.assertEqual(product.gender, ['women'])
        self.assertEqual(product.material, ['кожа'])
        self.assertTrue(product.in_stock_ecom)
        self.assertEqual(product.fetched_at, 1767225600)
        self.assertEqual(product.images.count(), 1)
        self.assertEqual(product.images.get().source_local_path, 'images/canonical.jpg')
        self.assertEqual(product.color_variants.get().color, 'Золотой')

    def test_import_supports_source_url_and_size_value_from_catalog_export(self):
        canonical = {
            'id': 'hermes_cn:WATCH-001',
            'source': {'shop': 'hermes_cn', 'url': 'https://www.hermes.cn/watch/'},
            'brand': 'Hermès',
            'title': {'raw': '腕表', 'i18n': {'ru': 'Часы'}},
            'price': {'amount': 1000, 'currency': 'CNY'},
            'availability': {'status': 'in_stock'},
            'media': {'images': []},
            'attributes': {},
            'variants': {'colors': [], 'sizes': [{
                'value': '46 mm', 'system': 'mm', 'sku': 'WATCH-001-46',
                'price': None, 'availability': {'status': 'in_stock'},
            }]},
        }

        product, created = import_product(canonical)

        self.assertTrue(created)
        self.assertEqual(product.brand, 'hermes')
        self.assertEqual(product.source_url, 'https://www.hermes.cn/watch/')
        self.assertEqual(product.current_size, '46 mm')
        self.assertEqual(product.size_variants.get().size, '46 mm')

    def test_miumiu_normalization_adds_shared_canonical_taxonomy(self):
        canonical = {
            'id': 'miumiu:TEST-BAG', 'brand': 'Miu Miu', 'gender': 'women',
            'title': {'raw': 'Sac à main', 'i18n': {'ru': 'Сумка с ручками'}},
            'description': {'raw': 'Description', 'i18n': {'ru': 'Описание товара'}},
            'category': {
                'raw_path': ['Accueil', 'Sacs', 'Sacs portés main', 'Sac à main'],
                'i18n': {'ru': ['Главная', 'Сумки', 'Сумки', 'Сумка с ручками']},
            },
            'attributes': {}, 'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'EUR'},
            'media': {'images': [{'role': 'front', 'file': 'images/test.webp'}]},
            'variants': {'colors': [], 'sizes': []},
        }

        normalized = normalize_miumiu_product(canonical)
        product, created = import_product(normalized)

        self.assertTrue(created)
        self.assertEqual(product.brand, 'miu-miu')
        self.assertEqual(product.category_department_slug, 'women')
        self.assertEqual(product.category_path_key, 'women/bags/handbags')
        self.assertEqual(product.category_name, 'Сумки с ручками')
        self.assertEqual(product.sku_without_size, 'TEST-BAG')
        self.assertEqual(product.article, 'TEST-BAG')
        self.assertTrue(CatalogBrand.objects.filter(slug='miu-miu', name='Miu Miu').exists())

    def test_miumiu_normalization_splits_mixed_source_category_by_title(self):
        canonical = {
            'id': 'miumiu:TEST-SWEATSHIRT', 'brand': 'Miu Miu',
            'title': {'i18n': {'ru': 'Свитшот из хлопкового флиса'}},
            'description': {'i18n': {'ru': 'Описание'}},
            'category': {'raw_path': ['Accueil', 'Prêt-à-porter', 'T-shirts et sweatshirts']},
        }

        normalized = normalize_miumiu_product(canonical)
        nodes = normalized['category']['canonical_path']['categories']

        self.assertEqual(nodes[-1]['slug'], 'sweatshirts')
        self.assertEqual(nodes[-1]['ru'], 'Толстовки')

    def test_miumiu_normalization_translates_compound_color(self):
        normalized = normalize_miumiu_product({
            'id': 'miumiu:TEST-COLOR', 'brand': 'Miu Miu',
            'title': {'i18n': {'ru': 'Тестовый товар'}},
            'description': {'i18n': {'ru': 'Описание'}},
            'category': {'raw_path': ['Accueil', 'Sacs', 'Sacs portés main']},
            'variants': {'colors': [
                {'label': {'raw': 'Blanc/Bleu Clair', 'lang': 'fr'}},
                {'label': {'raw': 'Begonia/Cognac', 'lang': 'fr'}},
            ]},
        })

        label = normalized['variants']['colors'][0]['label']
        self.assertEqual(label['raw'], 'Blanc/Bleu Clair')
        self.assertEqual(label['i18n']['ru'], 'Белый / Светло-голубой')
        self.assertEqual(
            normalized['variants']['colors'][1]['label']['i18n']['ru'],
            'Бегониевый / Коньячный',
        )

    def test_miumiu_fragrance_category_is_not_imported_as_material(self):
        normalized = normalize_miumiu_product({
            'id': 'miumiu:TEST-FRAGRANCE', 'brand': 'Miu Miu',
            'title': {'i18n': {'ru': 'Парфюмерная вода'}},
            'description': {'i18n': {'ru': 'Описание аромата'}},
            'category': {'raw_path': ['Accueil', 'Parfums']},
            'attributes': {'material': {'raw': 'Parfums', 'lang': 'fr'}},
        })

        product, _ = import_product(normalized)

        self.assertEqual(normalized['attributes']['material_normalized'], [])
        self.assertEqual(product.material, [])

    def test_miumiu_quality_report_detects_translation_and_image_problems(self):
        report = catalog_quality_report([{
            'id': 'miumiu:BROKEN', 'brand': 'Miu Miu',
            'title': {'i18n': {'ru': 'French title'}},
            'description': {'i18n': {'ru': 'French description'}},
            'category': {},
            'media': {'images': [{'role': 'front'}]},
        }], ['images/orphan.webp'])

        self.assertEqual(report['issue_counts']['untranslated_title'], 1)
        self.assertEqual(report['issue_counts']['untranslated_description'], 1)
        self.assertEqual(report['issue_counts']['missing_canonical_taxonomy'], 1)
        self.assertEqual(report['issue_counts']['missing_image_source'], 1)
        self.assertEqual(report['orphan_images'], 1)

    def test_therow_normalization_uses_shared_taxonomy_materials_and_color(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-BAG', 'brand': 'The Row', 'gender': 'women',
            'title': {'raw': 'Small Park Tote', 'i18n': {'ru': 'Маленькая сумка Park'}},
            'description': {
                'raw': 'Small tote bag in soft calfskin leather.',
                'i18n': {'ru': 'Маленькая сумка из мягкой телячьей кожи.'},
            },
            'category': {'raw_path': ['WOMEN', 'TOTES']},
            'attributes': {},
            'variants': {'colors': [{
                'label': {'raw': 'Driftwood', 'i18n': {'ru': 'Дрейфующий кусок дерева'}},
            }]},
        })

        canonical = normalized['category']['canonical_path']
        self.assertEqual(canonical['department'], 'women')
        self.assertEqual(canonical['categories'][-1]['slug'], 'tote_bags')
        self.assertEqual(normalized['attributes']['material_normalized'], ['телячья кожа'])
        self.assertEqual(
            normalized['variants']['colors'][0]['label']['i18n']['ru'],
            'Серо-коричневый',
        )
        self.assertEqual(normalized['variants']['colors'][0]['base_color'], 'brown')

    def test_therow_unknown_accessory_becomes_unisex_shared_eyewear(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-GLASSES', 'brand': 'The Row', 'gender': 'unknown',
            'title': {'raw': 'A Sunglasses', 'i18n': {'ru': 'Солнцезащитные очки A'}},
            'description': {'raw': 'Lightweight acetate frame.', 'i18n': {'ru': 'Оправа из ацетата.'}},
            'category': {'raw_path': ['ACCESSORIES']},
            'attributes': {}, 'variants': {'colors': []},
        })

        canonical = normalized['category']['canonical_path']
        self.assertEqual(normalized['gender'], ['women', 'men'])
        self.assertEqual(canonical['categories'][-1]['slug'], 'eyewear')
        self.assertEqual(normalized['attributes']['material_normalized'], ['ацетат'])

    def test_therow_home_textile_does_not_pollute_fashion_accessories(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-TOWEL', 'brand': 'The Row', 'gender': 'unknown',
            'title': {'raw': 'Large Towel', 'i18n': {'ru': 'Большое полотенце'}},
            'description': {'raw': 'Soft cotton towel.', 'i18n': {'ru': 'Мягкое хлопковое полотенце.'}},
            'category': {'raw_path': ['ACCESSORIES']},
            'attributes': {}, 'variants': {'colors': []},
        })

        canonical = normalized['category']['canonical_path']
        self.assertEqual(canonical['department'], 'home')
        self.assertEqual(canonical['categories'][-1]['slug'], 'bath_linen')
        self.assertEqual(len(canonical['categories']), 1)
        self.assertEqual(normalized['gender'], ['women', 'men'])

    def test_therow_blanket_uses_english_source_for_home_taxonomy(self):
        normalized = normalize_therow_product({
            'id': 'therow:SOSI', 'brand': 'The Row', 'gender': 'unknown',
            'title': {'raw': 'Sosi Blanket', 'i18n': {'ru': 'Одеяло Sosi'}},
            'description': {
                'raw': 'Large cashmere blanket.',
                'i18n': {'ru': 'Большое кашемировое одеяло.'},
            },
            'category': {'raw_path': ['ACCESSORIES']},
            'attributes': {}, 'variants': {'colors': []},
        })

        canonical = normalized['category']['canonical_path']
        self.assertEqual(canonical['department'], 'home')
        self.assertEqual(canonical['categories'][-1]['slug'], 'blankets')

    def test_catalog_quality_report_rejects_incomplete_translation(self):
        report = catalog_quality_report([{
            'id': 'therow:BROKEN', 'brand': 'The Row',
            'title': {'raw': 'Test Bag', 'i18n': {'ru': 'Test Bag'}},
            'description': {
                'raw': 'Long English product description that must be translated.',
                'i18n': {'ru': 'Short'},
            },
            'category': {'canonical_path': {
                'department': 'women',
                'categories': [{'slug': 'bags', 'ru': 'Сумки'}],
            }},
        }])

        self.assertEqual(report['issue_counts']['untranslated_title'], 1)
        self.assertEqual(report['issue_counts']['untranslated_description'], 1)
        self.assertEqual(report['issue_counts']['suspicious_translation_length'], 1)

    def test_storefront_image_order_starts_with_third_photo(self):
        self.assertEqual(storefront_image_order([1, 2, 3, 4, 5]), [3, 4, 5, 1, 2])
        self.assertEqual(storefront_image_order([1, 2]), [1, 2])

    def test_valentino_editorial_uses_house_terms_and_preserves_model(self):
        title = valentino_editorial_title(
            'Borsa Piccola A Tracolla Valentino Garavani Rockstud In Vitello Granato'
        )
        normalized = normalize_valentino_product({
            'id': 'valentino:TEST-0NO', 'brand': 'Valentino Garavani',
            'gender': 'women',
            'title': {
                'raw': 'Borsa Piccola A Tracolla Valentino Garavani Rockstud In Vitello Granato',
                'i18n': {'ru': 'Сумка из кожи цвета граната'},
            },
            'description': {
                'raw': (
                    'Borsa piccola a tracolla Valentino Garavani Rockstud in vitello '
                    'granato, decorata da borchie. La borsa può essere indossata a '
                    'spalla o crossbody grazie alla tracolla in pelle scorrevole.'
                ),
                'i18n': {'ru': 'Сумка из кожи цвета граната с шипами.'},
            },
            'category': {'raw_path': ['WOMEN', 'Borse a spalla']},
            'attributes': {},
            'variants': {'colors': [{
                'sku': 'TEST-0NO', 'current': True,
                'label': {'raw': 'NERO', 'i18n': {'ru': 'Чёрный'}},
            }]},
        })

        self.assertEqual(
            title,
            'Маленькая сумка через плечо Valentino Garavani Rockstud '
            'из зернистой телячьей кожи',
        )
        self.assertIn('фирменные заклёпки', normalized['description']['i18n']['ru'])
        self.assertNotIn('гранат', normalized['description']['i18n']['ru'].casefold())
        self.assertEqual(normalized['title']['lang'], 'it')
        self.assertEqual(normalized['description']['lang'], 'it')
        self.assertEqual(
            normalized['category']['canonical_path']['categories'][-1]['slug'],
            'shoulder_bags',
        )
        self.assertEqual(
            normalized['attributes']['material_normalized'],
            ['зернистая телячья кожа'],
        )
        self.assertEqual(normalized['attributes']['color']['i18n']['ru'], 'Чёрный')
        self.assertEqual(
            valentino_editorial_issue_kinds(
                normalized['title']['i18n']['ru'],
                normalized['description']['i18n']['ru'],
            ),
            [],
        )

    def test_therow_editorial_titles_preserve_model_names(self):
        self.assertEqual(editorial_title('Vasil Jean'), 'Джинсы Vasil')
        self.assertEqual(editorial_title('Pisan Turtleneck'), 'Водолазка Pisan')
        self.assertEqual(
            editorial_title("Liisa Kitten D'Orsay"),
            'Туфли Liisa Kitten D’Orsay',
        )
        self.assertEqual(editorial_title("90's Bag"), 'Сумка 90-х')

    def test_therow_editorial_description_fixes_tailoring_and_agreement(self):
        description, basis = editorial_description(
            'средней посадки сшитые на заказ брюки из шерсти с прямого кроя.',
            raw_description=(
                'Mid-rise tailored trousers in wool with a straight leg.'
            ),
        )
        boot_description, _ = editorial_description(
            'Утилитарный ботильоны из мягкой замши с толстым подошва из креп-резины.',
            raw_description='Utilitarian ankle boot in suede with thick crepe sole.',
        )

        self.assertEqual(basis, 'new')
        self.assertEqual(
            description,
            'Брюки строгого кроя средней посадки из шерсти с прямыми штанинами.',
        )
        self.assertEqual(
            boot_description,
            'Практичные ботильоны из мягкой замши с толстой подошвой из креповой резины.',
        )
        self.assertEqual(editorial_issue_kinds('Брюки Test', description), [])

    def test_therow_editorial_description_selects_cleaner_legacy_text(self):
        description, basis = editorial_description(
            'средней посадки брюки из хлопка с прямого кроя.',
            raw_description='Mid-rise cotton pants with straight leg.',
            legacy_text='Брюки средней посадки из хлопка с прямыми штанинами.',
        )

        self.assertEqual(basis, 'legacy')
        self.assertEqual(
            description, 'Брюки средней посадки из хлопка с прямыми штанинами.',
        )

    def test_therow_belt_uses_raw_title_when_translation_is_awkward(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-BELT', 'brand': 'The Row', 'gender': 'women',
            'title': {'raw': 'Augustus Belt', 'i18n': {'ru': 'Августус Белт'}},
            'description': {'raw': 'Calfskin belt.', 'i18n': {'ru': 'Пояс из телячьей кожи.'}},
            'category': {'raw_path': ['WOMEN', 'ACCESSORIES']},
            'attributes': {}, 'variants': {'colors': []},
        })

        self.assertEqual(
            normalized['category']['canonical_path']['categories'][-1]['slug'],
            'belts',
        )

    def test_therow_catalog_combines_colors_of_same_model(self):
        products = []
        for sku, color in (('BAG-BLACK', 'Black'), ('BAG-BROWN', 'Brown')):
            products.append({
                'id': f'therow:{sku}', 'brand': 'The Row', 'gender': 'women',
                'title': {'raw': 'Model Bag', 'i18n': {'ru': 'Сумка Model'}},
                'description': {'raw': 'Leather bag.', 'i18n': {'ru': 'Кожаная сумка.'}},
                'category': {'raw_path': ['WOMEN', 'SHOULDER BAGS']},
                'attributes': {},
                'variants': {'colors': [{
                    'sku': sku, 'label': {'raw': color, 'i18n': {'ru': color}},
                }]},
            })

        normalized, errors = normalize_therow_catalog(products)

        self.assertEqual(errors, [])
        for product in normalized:
            self.assertEqual(
                [variant['sku'] for variant in product['variants']['colors']],
                ['BAG-BLACK', 'BAG-BROWN'],
            )

    def test_therow_normalization_replaces_obvious_title_calque(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-SHIRT', 'brand': 'The Row', 'gender': 'men',
            'title': {'raw': 'Fiska Shirt', 'i18n': {'ru': 'Фиска Ширт'}},
            'description': {'raw': 'Cotton shirt.', 'i18n': {'ru': 'Хлопковая рубашка.'}},
            'category': {'raw_path': ['MEN', 'TOPS']},
            'attributes': {}, 'variants': {'colors': []},
        })

        self.assertEqual(normalized['title']['i18n']['ru'], 'Рубашка Fiska')

    def test_therow_normalization_fixes_confirmed_description_calques(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-PANTS', 'brand': 'The Row', 'gender': 'women',
            'title': {'raw': 'Test Pant', 'i18n': {'ru': 'Брюки Test'}},
            'description': {
                'raw': 'Pants in washed cotton with a straight leg and relaxed fit.',
                'i18n': {
                    'ru': 'Брюки из отбеленного хлопка с прямыми ногавками и с свободным кроем.',
                },
            },
            'category': {'raw_path': ['WOMEN', 'PANTS']},
            'attributes': {}, 'variants': {'colors': []},
        })

        self.assertEqual(
            normalized['description']['i18n']['ru'],
            'Брюки из хлопка с эффектом стирки с прямыми штанинами и со свободным кроем.',
        )

    def test_therow_normalization_preserves_real_bleached_material(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-JEANS', 'brand': 'The Row', 'gender': 'women',
            'title': {'raw': 'Test Jean', 'i18n': {'ru': 'Джинсы Test'}},
            'description': {
                'raw': 'Jeans in bleached cotton denim.',
                'i18n': {'ru': 'Джинсы из отбеленного хлопкового денима.'},
            },
            'category': {'raw_path': ['WOMEN', 'PANTS']},
            'attributes': {}, 'variants': {'colors': []},
        })

        self.assertEqual(
            normalized['description']['i18n']['ru'],
            'Джинсы из отбеленного хлопкового денима.',
        )

    def test_therow_normalization_uses_source_terms_for_semantic_fixes(self):
        normalized = normalize_therow_product({
            'id': 'therow:TEST-JACKET', 'brand': 'The Row', 'gender': 'women',
            'title': {'raw': 'Test Jacket', 'i18n': {'ru': 'Пиджак Test'}},
            'description': {
                'raw': 'Tailored single-breasted jacket with notch lapel.',
                'i18n': {
                    'ru': 'Пиджак с одной проймой, сшитый на заказ, имеет воротник с вырезом.',
                },
            },
            'category': {'raw_path': ['WOMEN', 'JACKETS']},
            'attributes': {}, 'variants': {'colors': []},
        })

        self.assertEqual(
            normalized['description']['i18n']['ru'],
            'Однобортный пиджак, сшитый на заказ, имеет лацканы с вырезом.',
        )

    def test_miumiu_boutique_catalog_and_product_are_public(self):
        canonical = normalize_miumiu_product({
            'id': 'miumiu:PUBLIC-BAG', 'brand': 'Miu Miu', 'gender': 'women',
            'title': {'i18n': {'ru': 'Публичная сумка Miu Miu'}},
            'description': {'i18n': {'ru': 'Описание сумки'}},
            'category': {'raw_path': ['Accueil', 'Sacs', 'Sacs portés main']},
            'attributes': {'material_normalized': ['кожа']},
            'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'EUR'},
            'media': {'images': []}, 'variants': {'colors': [], 'sizes': []},
        })
        product, _ = import_product(canonical)

        stores = self.client.get(reverse('shop:stores'))
        catalog = self.client.get(reverse('shop:miumiu_catalog'))
        detail = self.client.get(product.get_absolute_url())

        self.assertContains(stores, reverse('shop:miumiu_catalog'))
        self.assertContains(stores, 'Miu Miu')
        self.assertContains(catalog, product.title)
        self.assertContains(catalog, 'Милан, Италия')
        self.assertContains(detail, product.title)
        self.assertContains(detail, 'Miu Miu')
        self.assertEqual(
            [item['name'] for item in catalog.context['category_tree']],
            ['Сумки'],
        )
        self.assertNotContains(catalog, '<span>Женщины</span>')

    def test_canonical_import_converts_null_relation_values_to_empty_strings(self):
        canonical = normalize_miumiu_product({
            'id': 'miumiu:NULL-VALUES', 'brand': 'Miu Miu', 'gender': 'women',
            'title': {'i18n': {'ru': 'Товар с пустыми значениями'}},
            'description': {'i18n': {'ru': 'Описание'}},
            'category': {'raw_path': ['Accueil', 'Sacs', 'Sacs portés main']},
            'attributes': {}, 'availability': {'status': 'in_stock'},
            'price': {'amount': 100, 'currency': 'EUR'},
            'media': {'images': [{'file': None, 'urls': {'original': None}}]},
            'variants': {
                'colors': [{'sku': 'NULL-VALUES', 'label': None, 'thumbnail': None}],
                'sizes': [{'sku': 'NULL-VALUES-S', 'value': None}],
            },
        })

        product, _ = import_product(canonical)

        self.assertEqual(product.images.get().original, '')
        self.assertEqual(product.color_variants.get().thumbnail, '')
        self.assertEqual(product.size_variants.get().size, '')


class OptimizedImageUploadTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.settings_override = self.settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def assert_optimized(self, field_file, expected_size):
        from PIL import Image

        self.assertTrue(field_file.name.endswith('.webp'))
        self.assertTrue(Path(field_file.path).exists())
        with Image.open(field_file.path) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, expected_size)

    def test_every_uploaded_image_source_is_saved_as_1600_webp(self):
        product, _ = import_product(product_payload())
        product_image = ProductImage.objects.create(
            product=product,
            local_image=image_upload('product.jpg'),
        )
        brand = CatalogBrand.objects.create(
            name='Upload brand', slug='upload-brand',
            image=image_upload('brand.jpg'),
        )
        banner, _ = Banner.objects.get_or_create(slot=Banner.Slot.FALL_WINTER)
        banner_image = BannerImage.objects.create(
            banner=banner,
            uploaded_image=image_upload('banner.jpg'),
        )
        product_request = ProductRequest.objects.create(
            customer_name='Покупатель', customer_email='buyer@example.com',
            customer_phone='+79991234567',
        )
        request_item = ProductRequestItem.objects.create(
            request=product_request, brand='Hermès', product_name='Сумка',
            reference_image=image_upload('reference.jpg'),
        )

        for field_file in (
            product_image.local_image, brand.image,
            banner_image.uploaded_image, request_item.reference_image,
        ):
            with self.subTest(field=field_file.field.name):
                self.assert_optimized(field_file, (1600, 1200))

    def test_small_transparent_png_is_not_enlarged_and_keeps_alpha(self):
        from PIL import Image

        brand = CatalogBrand.objects.create(
            name='Transparent brand', slug='transparent-brand',
            image=image_upload(
                'logo.png', size=(320, 180), mode='RGBA',
                color=(199, 169, 130, 80), image_format='PNG',
            ),
        )

        self.assert_optimized(brand.image, (320, 180))
        with Image.open(brand.image.path) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertLess(image.getchannel('A').getextrema()[0], 255)

    def test_replacing_and_deleting_image_removes_old_files_after_commit(self):
        brand = CatalogBrand.objects.create(
            name='Replace brand', slug='replace-brand',
            image=image_upload('first.jpg', size=(800, 600)),
        )
        old_path = Path(brand.image.path)
        self.assertTrue(old_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            brand.image = image_upload('second.jpg', size=(600, 800))
            brand.save(update_fields=('image',))
        brand.refresh_from_db()
        replacement_path = Path(brand.image.path)
        self.assertFalse(old_path.exists())
        self.assertTrue(replacement_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            brand.delete()
        self.assertFalse(replacement_path.exists())

    def test_deleting_product_image_removes_every_derivative(self):
        product, _ = import_product(product_payload())
        product_image = product.images.get()
        product_image.local_image.save(
            'source.jpg', image_upload('source.jpg'), save=True
        )
        product_image.preview_image.save(
            'preview.webp', ContentFile(b'preview'), save=False
        )
        product_image.gallery_image.save(
            'gallery.webp', ContentFile(b'gallery'), save=False
        )
        product_image.detail_image.save(
            'detail.webp', ContentFile(b'detail'), save=False
        )
        product_image.save(update_fields=(
            'preview_image', 'gallery_image', 'detail_image',
        ))
        paths = tuple(Path(getattr(product_image, field).path) for field in (
            'local_image', 'preview_image', 'gallery_image', 'detail_image',
        ))

        with self.captureOnCommitCallbacks(execute=True):
            product_image.delete()

        self.assertTrue(all(not path.exists() for path in paths))

    def test_replacing_product_source_resets_and_removes_old_derivatives(self):
        product, _ = import_product(product_payload())
        product_image = product.images.get()
        product_image.local_image.save(
            'first.jpg', image_upload('first.jpg'), save=True
        )
        product_image.preview_image.save(
            'old-preview.webp', ContentFile(b'preview'), save=False
        )
        product_image.gallery_image.save(
            'old-gallery.webp', ContentFile(b'gallery'), save=False
        )
        product_image.detail_image.save(
            'old-detail.webp', ContentFile(b'detail'), save=False
        )
        product_image.detail_processed = True
        product_image.save(update_fields=(
            'preview_image', 'gallery_image', 'detail_image', 'detail_processed',
        ))
        old_paths = tuple(Path(getattr(product_image, field).path) for field in (
            'local_image', 'preview_image', 'gallery_image', 'detail_image',
        ))

        with self.captureOnCommitCallbacks(execute=True):
            product_image.local_image = image_upload('second.jpg', size=(1800, 2400))
            product_image.save(update_fields=('local_image',))
        product_image.refresh_from_db()

        self.assertTrue(product_image.local_image.name.endswith('second.webp'))
        self.assertFalse(product_image.preview_image)
        self.assertFalse(product_image.gallery_image)
        self.assertFalse(product_image.detail_image)
        self.assertFalse(product_image.detail_processed)
        self.assertTrue(all(not path.exists() for path in old_paths))
        self.assert_optimized(product_image.local_image, (1200, 1600))

    def test_corrupt_image_is_not_written(self):
        brand = CatalogBrand(name='Broken brand', slug='broken-brand')
        brand.image = SimpleUploadedFile(
            'broken.jpg', b'not-an-image', content_type='image/jpeg'
        )

        with self.assertRaisesMessage(ValueError, 'Не удалось обработать изображение'):
            brand.save()
        self.assertFalse(any(Path(self.media_root).rglob('*')))


class ZipImportTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.settings_override = self.settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_zip_imports_card_and_attaches_local_image(self):
        report = import_zip_archive(zip_upload())

        self.assertEqual(report.json_files, 1)
        self.assertEqual(report.image_files, 1)
        self.assertEqual(report.cards, 1)
        self.assertEqual(report.created, 1)
        self.assertEqual(report.updated, 0)
        self.assertEqual(report.images_attached, 1)
        self.assertEqual(report.errors, [])
        image = Product.objects.get(sku='H-001-BLACK').images.get()
        self.assertTrue(image.local_image.name)
        self.assertTrue(Path(image.local_image.path).exists())
        self.assertTrue(image.local_image.name.endswith('.webp'))
        from PIL import Image
        with Image.open(image.local_image.path) as stored:
            self.assertEqual(stored.format, 'WEBP')
            self.assertEqual(stored.size, (1600, 1200))

        repeated = import_zip_archive(zip_upload())
        self.assertEqual(repeated.created, 0)
        self.assertEqual(repeated.updated, 1)
        self.assertEqual(repeated.images_attached, 1)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Product.objects.get().images.count(), 1)
        source_files = list(Path(image.local_image.path).parent.glob('*.webp'))
        self.assertEqual(len(source_files), 1)

    def test_zip_blocks_path_traversal(self):
        with self.assertRaisesMessage(ValueError, 'небезопасный путь'):
            import_zip_archive(zip_upload('unsafe.zip', unsafe=True))
        self.assertEqual(Product.objects.count(), 0)
        self.assertFalse(Path(self.media_root, 'product.json').exists())

    def test_import_product_zip_management_command(self):
        archive_path = Path(self.media_root, 'catalog.zip')
        archive_path.write_bytes(zip_upload().read())

        call_command('import_product_zip', str(archive_path))

        product = Product.objects.get(sku='H-001-BLACK')
        self.assertTrue(product.images.get().local_image.name)

    def test_offwhite_normalization_rebuilds_title_taxonomy_and_commercial_data(self):
        normalized = normalize_offwhite_product({
            'id': 'offwhite:TEST', 'brand': 'OFF WHITE', 'gender': 'women',
            'source': {
                'url': 'https://www.off---white.com/en-fr/women/clothing/t-shirts/test.html',
            },
            'title': {
                'raw': 'Seamless Color Block Top',
                'i18n': {'ru': 'Бесшовная топ-одежда с цветовыми блоками'},
            },
            'description': {
                'raw': 'Seamless top with a high turtleneck collar.',
                'i18n': {'ru': 'Топ-одежда с высоким турнувочным воротником.'},
            },
            'category': {'raw_path': [], 'i18n': None, 'canonical_path': None},
            'attributes': {
                'material': {'raw': '100% Polyamide (Nylon)'},
                'material_normalized': [],
            },
            'price': {'amount': None, 'currency': None},
            'availability': {'status': 'unknown', 'raw': None},
            'variants': {'colors': [{
                'label': {'raw': 'Black', 'i18n': None}, 'sku': 'TEST',
                'price': {'amount': 450, 'currency': 'EUR'},
                'availability': {'status': 'in_stock', 'raw': True},
                'current': True,
            }], 'sizes': []},
            'media': {'images': []},
        })

        self.assertEqual(normalized['brand'], 'Off-White')
        self.assertEqual(normalized['title']['i18n']['ru'], 'Топ Seamless Color Block')
        self.assertEqual(
            normalized['category']['canonical_path']['categories'][-1]['slug'],
            'tshirts_tops',
        )
        self.assertEqual(normalized['attributes']['material_normalized'], ['полиамид'])
        self.assertEqual(normalized['variants']['colors'][0]['label']['i18n']['ru'], 'Чёрный')
        self.assertNotIn('топ-одежда', normalized['description']['i18n']['ru'].casefold())
        self.assertNotIn('турнув', normalized['description']['i18n']['ru'].casefold())
        self.assertEqual(normalized['price'], {'amount': 450, 'currency': 'EUR'})
        self.assertEqual(normalized['availability']['status'], 'in_stock')

    def test_replace_offwhite_catalog_dry_run_does_not_change_database(self):
        archive_path = offwhite_zip_path(self.media_root)
        report_dir = Path(self.media_root, 'offwhite-reports')

        call_command(
            'replace_offwhite_catalog', str(archive_path), dry_run=True,
            report_dir=str(report_dir),
        )

        self.assertFalse(Product.objects.filter(brand='off-white').exists())
        report = json.loads(
            Path(report_dir, 'preflight-quality.json').read_text(encoding='utf-8')
        )
        self.assertEqual(report['source_products'], 1)
        self.assertEqual(report['normalized_products'], 1)
        self.assertEqual(report['critical_issue_counts'], {})
        self.assertTrue(Path(report_dir, 'catalog.normalized.json').is_file())
        self.assertTrue(Path(report_dir, 'blocking-items.json').is_file())

    def test_replace_offwhite_catalog_requires_confirmed_backup(self):
        archive_path = offwhite_zip_path(self.media_root)

        with self.assertRaisesMessage(CommandError, '--backup-confirmed'):
            call_command('replace_offwhite_catalog', str(archive_path))

    def test_offwhite_update_preserves_better_copy_only_for_same_source(self):
        old = Product.objects.create(
            sku='offwhite:TEST', brand='off-white', title='Исправленное название',
            description='Подробное описание модели и материала.',
            raw_data={
                'title': {'raw': 'Test Top', 'i18n': {'ru': 'Исправленное название'}},
                'description': {'raw': 'Original copy', 'i18n': {
                    'ru': 'Подробное описание модели и материала.',
                }},
            },
        )
        incoming = {
            'title': {'raw': 'Test Top', 'i18n': {'ru': 'Топ'}},
            'description': {'raw': 'Original copy', 'i18n': {'ru': 'Кратко'}},
        }
        keep_better_existing_text(incoming, old)
        self.assertEqual(incoming['title']['i18n']['ru'], old.title)
        self.assertEqual(incoming['description']['i18n']['ru'], old.description)
        incoming['description'] = {'raw': 'Updated source', 'i18n': {'ru': 'Новый текст'}}
        keep_better_existing_text(incoming, old)
        self.assertEqual(incoming['description']['i18n']['ru'], 'Новый текст')

    def test_offwhite_editorial_corrects_wrong_garment_without_touching_source(self):
        card = {
            'id': 'offwhite:TEST',
            'title': {'raw': 'Bowling Shirt', 'i18n': {'ru': 'Рубашка Bowling'}},
            'description': {'raw': 'This bowling shirt has a collar.',
                            'i18n': {'ru': '<p>Эта футболка для боулинга.</p><li>Коллар свободного кроя</li>'}},
        }
        original = card['description']['raw']
        improve_offwhite_copy(card)
        self.assertIn('рубашка', card['description']['i18n']['ru'].lower())
        self.assertNotIn('футболка', card['description']['i18n']['ru'].lower())
        self.assertNotIn('Коллар', card['description']['i18n']['ru'])
        self.assertEqual(card['description']['raw'], original)
        again = card['description']['i18n']['ru']
        improve_offwhite_copy(card)
        self.assertEqual(card['description']['i18n']['ru'], again)

    def test_offwhite_editorial_preserves_name_and_corrects_sweatshirt(self):
        card = {
            'title': {'raw': 'OW BE RIGHT BACK', 'i18n': {'ru': 'Кроссовки OW'}},
            'description': {'raw': 'This sweatshirt has a print.',
                            'i18n': {'ru': 'Эта футболка с принтом.'}},
            'category': {'i18n': {'ru': ['Обувь', 'Кроссовки', 'Кроссовки OW']}},
        }
        improve_offwhite_copy(card)
        self.assertEqual(card['title']['i18n']['ru'], 'Кроссовки OW Be Right Back')
        self.assertEqual(card['category']['i18n']['ru'][-1], 'Кроссовки OW Be Right Back')
        self.assertEqual(card['description']['i18n']['ru'], 'Эта футболка с принтом.')
        card['title']['raw'] = 'Sample Sweatshirt'
        card['title']['i18n']['ru'] = 'Свитшот Sample'
        improve_offwhite_copy(card)
        self.assertIn('свитшот', card['description']['i18n']['ru'])
        self.assertNotIn('футболка', card['description']['i18n']['ru'])

    def test_offwhite_sporty_sweatshirt_requires_exact_source(self):
        source = (
            '<p>Elevate your urban wardrobe with the Sporty Over Crew Logo Sweatshirt '
            'showcasing a refined blend of casual elegance and distinctive branding.</p>'
            '<ul type="circle"><li>Ribbed crew neckline with contrasting trim</li>'
            '<li>Long sleeves with ribbed cuffs</li><li>Signature Off-White logo at front</li>'
            '<li>Embroidered crest detail</li><li>Ribbed hemline with striped detailing</li>'
            '<li>Relaxed silhouette for contemporary fit</li></ul>'
        )
        text = (
            '<p>Улучшите свой городской гардероб спортивной футболкой Sporty Over Crew Logo, '
            'сочетающей в себе элегантность повседневной одежды и узнаваемый брендовый дизайн.'
            '</p><ul><li>Воротник-стойка с контрастной отделкой</li></ul>'
        )
        card = {
            'title': {'raw': 'Sporty Over Crew Logo Sweatshirt',
                      'i18n': {'ru': 'Свитшот Sporty Over Crew Logo'}},
            'description': {'raw': source, 'i18n': {'ru': text}},
        }
        improve_offwhite_copy(card)
        fixed = card['description']['i18n']['ru']
        self.assertIn('Свитшот Sporty Over Crew Logo сочетает', fixed)
        self.assertIn('Круглый вырез с отделкой в рубчик', fixed)
        self.assertNotIn('футболк', fixed.casefold())
        self.assertEqual(card['description']['raw'], source)
        improve_offwhite_copy(card)
        self.assertEqual(card['description']['i18n']['ru'], fixed)

        different = {
            'title': card['title'].copy(),
            'description': {'raw': source + '<p>Additional detail</p>',
                            'i18n': {'ru': text}},
        }
        improve_offwhite_copy(different)
        self.assertNotIn('Круглый вырез с отделкой в рубчик',
                         different['description']['i18n']['ru'])

    def test_offwhite_ink_hoodie_agreement_is_source_scoped_and_idempotent(self):
        card = {
            'title': {'raw': 'Ink Arrow Regular Logo Hoodie',
                      'i18n': {'ru': 'Худи Ink Arrow Regular Logo'}},
            'description': {'raw': 'The Ink Arrow Regular Logo Hoodie is crafted from cotton.',
                            'i18n': {'ru': 'Эта футболка с капюшоном. Она изготовлена из качественного хлопка.'}},
        }
        original = card['description']['raw']
        improve_offwhite_copy(card)
        fixed = card['description']['i18n']['ru']
        self.assertIn('Это худи. Модель изготовлена', fixed)
        self.assertEqual(card['description']['raw'], original)
        improve_offwhite_copy(card)
        self.assertEqual(card['description']['i18n']['ru'], fixed)
        card['title']['raw'] = 'Other Hoodie'
        card['description']['i18n']['ru'] = 'Она изготовлена из качественного хлопка.'
        improve_offwhite_copy(card)
        self.assertEqual(card['description']['i18n']['ru'], 'Она изготовлена из качественного хлопка.')

    def test_offwhite_editorial_does_not_invent_accessories(self):
        card = {
            'title': {'raw': 'Half Arrow Sneakers', 'i18n': {'ru': 'Кроссовки Half Arrow'}},
            'description': {'raw': 'Interchangeable OFF cleats included.',
                            'i18n': {'ru': '<p>Детали.</p><li>В комплекте идут сменные наколенники.</li>'}},
        }
        improve_offwhite_copy(card)
        self.assertIn('сменные шипы', card['description']['i18n']['ru'])
        self.assertNotIn('наколенники', card['description']['i18n']['ru'])

    def test_offwhite_editorial_keeps_agreement_after_replacing_top(self):
        card = {
            'title': {'raw': 'Ribbed Rowing Top', 'i18n': {'ru': 'Топ Ribbed Rowing'}},
            'description': {
                'raw': 'This ribbed rowing top is crafted from ribbed cotton and elastane.',
                'i18n': {'ru': 'Эта майка с ребрами имеет логотип и сделана из хлопка с ребрами и эластана.'},
            },
        }
        improve_offwhite_copy(card)
        self.assertIn('Этот топ в рубчик', card['description']['i18n']['ru'])
        self.assertIn('изготовлен из хлопка в рубчик', card['description']['i18n']['ru'])
        self.assertNotIn('сделана', card['description']['i18n']['ru'])

    def test_offwhite_editorial_does_not_duplicate_adjectives(self):
        card = {
            'title': {'raw': 'Out Of Office Sneakers', 'i18n': {'ru': 'Кроссовки Out Of Office'}},
            'description': {
                'raw': 'Urban-luxury design.',
                'i18n': {'ru': 'Отличаются изысканным урбанистическим луксузным дизайном.'},
            },
        }
        improve_offwhite_copy(card)
        self.assertEqual(card['description']['i18n']['ru'],
                         'Отличаются изысканным городским дизайном.')

    def test_offwhite_editorial_repairs_broken_midsole_list(self):
        card = {
            'title': {'raw': 'Out Of Office Sneakers', 'i18n': {'ru': 'Кроссовки Out Of Office'}},
            'description': {
                'raw': '<ul><li>Lightweight cushioned midsole</li><li>Treaded rubber sole</li></ul>',
                'i18n': {'ru': '<ul><li>Arrow</li>Легкая амортизирующая средняя подошва'
                                '<li>Резиновая подошва</li></ul>'},
            },
        }
        improve_offwhite_copy(card)
        self.assertIn('<li>Лёгкая амортизирующая промежуточная подошва</li>',
                      card['description']['i18n']['ru'])

    def test_offwhite_panties_override_incorrect_socks_source_category(self):
        card = {
            'id': 'offwhite:PANTIES', 'brand': 'OFF WHITE', 'gender': 'women',
            'title': {'raw': 'OFF RIB BRAZILIAN PANTIES', 'i18n': {
                'ru': 'Носки OFF RIB BRAZILIAN PANTIES',
            }},
            'description': {'raw': 'These panties are made of cotton.', 'i18n': {
                'ru': 'Трусы из хлопка.',
            }},
            'category': {'raw_path': ['Women', 'Accessories', 'Socks']},
            'attributes': {'material': {'raw': 'Cotton'}},
            'variants': {'colors': [], 'sizes': []},
            'price': {'amount': 50, 'currency': 'EUR'},
            'availability': {'status': 'out_of_stock'},
        }
        normalized = normalize_offwhite_product(card)
        self.assertEqual(
            normalized['category']['canonical_path']['categories'][-1]['slug'],
            'swim_beachwear',
        )
        improve_offwhite_copy(normalized)
        self.assertEqual(normalized['title']['i18n']['ru'], 'Трусы OFF RIB Brazilian')

    def test_improve_offwhite_texts_dry_run_and_apply_preserves_stock(self):
        from .models import ProductImage
        card = {
            'id': 'offwhite:BOWLING',
            'title': {'raw': 'Sample Bowling Shirt', 'i18n': {'ru': 'Рубашка Sample'}},
            'description': {'raw': 'A bowling shirt with a collar.',
                            'i18n': {'ru': '<p>Эта футболка с воротником.</p>'}},
            'category': {'i18n': {'ru': ['Мужчины', 'Рубашки', 'Рубашка Sample']}},
        }
        product = Product.objects.create(
            sku=card['id'], brand='off-white', title=card['title']['i18n']['ru'],
            description=card['description']['i18n']['ru'], raw_data=card,
            in_stock_ecom=True,
        )
        image = ProductImage.objects.create(product=product, alt=product.title)
        catalog = Path(self.media_root, 'offwhite-copy.json')
        catalog.write_text(json.dumps([card], ensure_ascii=False), encoding='utf-8')
        report = Path(self.media_root, 'editorial')
        call_command('improve_offwhite_texts', str(catalog), report_dir=str(report))
        product.refresh_from_db()
        self.assertIn('футболка', product.description)
        self.assertTrue(Path(report, 'before.json').is_file())
        call_command('improve_offwhite_texts', str(catalog), report_dir=str(report), apply=True)
        product.refresh_from_db()
        image.refresh_from_db()
        self.assertIn('рубашка', product.description)
        self.assertNotIn('футболка', product.description)
        self.assertTrue(product.in_stock_ecom)
        self.assertEqual(product.raw_data['description']['i18n']['ru'], product.description)
        self.assertEqual(image.alt, product.title)
        call_command('improve_offwhite_texts', str(catalog), report_dir=str(report), apply=True)
        product.refresh_from_db()
        self.assertEqual(product.raw_data['description']['i18n']['ru'], product.description)
        self.assertEqual(json.loads(Path(report, 'changes.json').read_text(encoding='utf-8')), [])
        self.assertEqual(len(json.loads(Path(report, 'before.json').read_text(encoding='utf-8'))), 1)

    def test_offwhite_shoes_without_sizes_are_not_marked_available(self):
        card = {
            'category': {'canonical_path': {'categories': [{'slug': 'shoes'}]}},
            'availability': {'status': 'in_stock', 'raw': True},
            'variants': {'sizes': [], 'colors': [
                {'current': True, 'availability': {'status': 'in_stock', 'raw': True}},
            ]},
        }
        self.assertTrue(require_sized_shoes(card))
        self.assertEqual(card['availability']['status'], 'unknown')
        self.assertEqual(card['variants']['colors'][0]['availability']['status'], 'unknown')

    def test_canonical_import_selects_first_available_size(self):
        card = {
            'id': 'offwhite:SIZE-CHECK', 'brand': 'Off-White',
            'title': {'raw': 'Test T-shirt', 'i18n': {'ru': 'Футболка Test'}},
            'price': {'amount': 100, 'currency': 'EUR'},
            'availability': {'status': 'in_stock'},
            'variants': {'colors': [], 'sizes': [
                {'value': 'XS', 'sku': 'size-xs', 'availability': {'status': 'out_of_stock'}},
                {'value': 'M', 'sku': 'size-m', 'availability': {'status': 'in_stock'}},
            ]},
            'media': {'images': []},
        }
        product, _ = import_product(card)
        self.assertEqual(product.current_size, 'M')
        self.assertEqual(
            list(product.size_variants.values_list('size', 'in_stock_ecom')),
            [('XS', False), ('M', True)],
        )

    def test_canonical_article_uses_card_id_not_first_related_color(self):
        card = {
            'id': 'offwhite:ACTUAL-COLOR', 'brand': 'Off-White',
            'title': {'raw': 'Test T-shirt', 'i18n': {'ru': 'Футболка Test'}},
            'price': {'amount': 100, 'currency': 'EUR'},
            'availability': {'status': 'in_stock'},
            'variants': {'colors': [
                {'sku': 'ANOTHER-COLOR', 'label': {'raw': 'Black'}, 'current': False},
                {'sku': 'ACTUAL-COLOR', 'label': {'raw': 'Blue'}, 'current': True},
            ], 'sizes': []},
            'media': {'images': []},
        }
        product, _ = import_product(card)
        self.assertEqual(product.sku_without_size, 'ACTUAL-COLOR')
        self.assertEqual(product.current_color, 'Blue')

    def test_offwhite_stock_reconciliation_selects_size_and_blocks_unsized_shoes(self):
        from .models import ColorVariant, SizeVariant
        shirt = Product.objects.create(
            sku='offwhite:SHIRT', brand='off-white', current_size='XS', in_stock_ecom=True,
        )
        SizeVariant.objects.create(product=shirt, sku='xs', size='XS', in_stock_ecom=False)
        SizeVariant.objects.create(product=shirt, sku='m', size='M', in_stock_ecom=True)
        shoes = Product.objects.create(
            sku='offwhite:SHOES', brand='off-white', sku_without_size='SHOES',
            gender=['men'], category_path_key='men/shoes/sneakers', in_stock_ecom=True,
            raw_data={'availability': {'status': 'in_stock'}},
        )
        SizeVariant.objects.create(product=shoes, sku='38', size='38', in_stock_ecom=True)
        ColorVariant.objects.create(product=shoes, sku='SHOES', in_stock_ecom=True)
        ColorVariant.objects.create(product=shirt, sku='SHOES', in_stock_ecom=True)
        corrected, blocked = reconcile_offwhite_stock(
            Product.objects.filter(pk__in=[shirt.pk, shoes.pk])
        )
        shirt.refresh_from_db()
        shoes.refresh_from_db()
        self.assertEqual((corrected, blocked), (1, ['offwhite:SHOES']))
        self.assertEqual(shirt.current_size, 'M')
        self.assertFalse(shoes.in_stock_ecom)
        self.assertEqual(shoes.raw_data['availability']['status'], 'unknown')
        self.assertFalse(shoes.size_variants.get().in_stock_ecom)
        self.assertFalse(shoes.color_variants.get().in_stock_ecom)
        self.assertEqual(block_unverified_color_targets([shoes.sku]), 1)
        self.assertFalse(shirt.color_variants.get().in_stock_ecom)

    def test_offwhite_color_link_requires_available_target_size(self):
        from .models import ColorVariant, SizeVariant
        source = Product.objects.create(sku='offwhite:SOURCE', brand='off-white')
        target = Product.objects.create(
            sku='offwhite:TARGET', brand='off-white', sku_without_size='TARGET',
            in_stock_ecom=False,
        )
        SizeVariant.objects.create(
            product=target, sku='target-s', size='S', in_stock_ecom=False,
        )
        link = ColorVariant.objects.create(
            product=source, sku='TARGET', in_stock_ecom=True,
        )
        self.assertEqual(block_unverified_color_targets([target.sku]), 1)
        link.refresh_from_db()
        self.assertFalse(link.in_stock_ecom)
        target.size_variants.update(in_stock_ecom=True)
        link.in_stock_ecom = True
        link.save(update_fields=('in_stock_ecom',))
        self.assertEqual(block_unverified_color_targets([target.sku]), 0)
        link.refresh_from_db()
        self.assertTrue(link.in_stock_ecom)

    def test_offwhite_color_link_uses_sku_when_article_is_shared(self):
        from .models import ColorVariant
        source = Product.objects.create(sku='offwhite:SOURCE', brand='off-white')
        unavailable = Product.objects.create(
            sku='offwhite:TARGET', brand='off-white',
            sku_without_size='SHARED', in_stock_ecom=False,
        )
        Product.objects.create(
            sku='offwhite:OTHER', brand='off-white',
            sku_without_size='SHARED', in_stock_ecom=True,
        )
        link = ColorVariant.objects.create(
            product=source, sku='TARGET', in_stock_ecom=True,
        )
        self.assertEqual(block_unverified_color_targets([unavailable.sku]), 1)
        link.refresh_from_db()
        self.assertFalse(link.in_stock_ecom)

    def test_offwhite_update_preserves_absent_products_and_replaces_variants(self):
        archive_path = offwhite_zip_path(self.media_root)
        absent_product = Product.objects.create(
            sku='offwhite:ABSENT', brand='off-white', title='Не вошёл в архив',
            in_stock_ecom=True,
        )
        from .models import ColorVariant, SizeVariant
        ColorVariant.objects.create(product=absent_product, sku='ABSENT', in_stock_ecom=True)
        SizeVariant.objects.create(product=absent_product, sku='ABSENT-S', size='S', in_stock_ecom=True)
        call_command(
            'replace_offwhite_catalog', str(archive_path),
            backup_confirmed=True, preserve_absent=True,
            report_dir=str(Path(self.media_root, 'preserve-absent')),
        )
        absent_product.refresh_from_db()
        self.assertFalse(absent_product.in_stock_ecom)
        self.assertFalse(absent_product.color_variants.get().in_stock_ecom)
        self.assertFalse(absent_product.size_variants.get().in_stock_ecom)
        self.assertEqual(Product.objects.get(sku='offwhite:TEST').images.count(), 1)
        report = json.loads(Path(self.media_root, 'preserve-absent', 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(report['preserved_absent_products'], 1)
        self.assertEqual(report['absent_products_marked_unavailable'], 1)
        self.assertEqual(report['deleted_stale_products'], 0)

    def test_replace_offwhite_catalog_metadata_only_preserves_existing_image(self):
        archive_path = offwhite_zip_path(self.media_root)
        initial_report = Path(self.media_root, 'offwhite-initial')
        metadata_report = Path(self.media_root, 'offwhite-metadata')
        call_command(
            'replace_offwhite_catalog', str(archive_path),
            backup_confirmed=True, report_dir=str(initial_report),
        )
        product = Product.objects.get(sku='offwhite:TEST')
        image = product.images.get()
        image_pk = image.pk
        image_name = image.local_image.name
        image_path = Path(image.local_image.path)
        product.title = 'Ошибочное старое название'
        product.save(update_fields=('title',))

        call_command(
            'replace_offwhite_catalog', str(archive_path),
            backup_confirmed=True, metadata_only=True,
            report_dir=str(metadata_report),
        )

        product.refresh_from_db()
        image = product.images.get()
        result = json.loads(Path(metadata_report, 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(product.title, 'Топ Seamless Color Block')
        self.assertEqual(image.pk, image_pk)
        self.assertEqual(image.local_image.name, image_name)
        self.assertTrue(image_path.is_file())
        self.assertTrue(result['metadata_only'])
        self.assertEqual(result['attached_images'], 0)

    def test_offwhite_audited_price_fix_does_not_invent_availability(self):
        normalized = normalize_offwhite_product({
            'id': 'offwhite:OMIA243C99LEA0020110',
            'brand': 'OFF WHITE', 'gender': 'men',
            'source': {
                'url': 'https://www.off---white.com/en-fr/men/shoes/sneakers/product.html',
            },
            'title': {'raw': 'Slim Out Of Office Sneakers', 'i18n': {'ru': ''}},
            'description': {
                'raw': 'The Slim Out Of Office Sneakers combine suede and mesh.',
                'i18n': {'ru': 'Кроссовки из замши и сетки.'},
            },
            'category': {'raw_path': ['Men', 'Shoes', 'Sneakers'], 'i18n': None},
            'attributes': {'material': {'raw': 'Leather and mesh'}},
            'price': None,
            'availability': {'status': 'unknown', 'raw': None},
            'variants': {'colors': [{
                'label': {'raw': 'WHITE/BLACK', 'i18n': None},
                'sku': 'OMIA243C99LEA0020110', 'price': None,
                'availability': {'status': 'unknown', 'raw': None}, 'current': True,
            }], 'sizes': []},
            'media': {'images': []},
        })

        self.assertEqual(normalized['price']['amount'], 450.0)
        self.assertEqual(normalized['price']['currency'], 'EUR')
        self.assertEqual(normalized['availability']['status'], 'unknown')
        self.assertEqual(
            normalized['normalized_from']['price_audit']['verified_at'], '2026-09-21',
        )

    def test_offwhite_clutch_uses_finder_mini_bags_taxonomy(self):
        normalized = normalize_offwhite_product({
            'id': 'offwhite:CLUTCH', 'brand': 'OFF WHITE', 'gender': 'women',
            'source': {'url': 'https://www.off---white.com/en-fr/archive/clutch.html'},
            'title': {'raw': 'Paperplane Clutch Quote', 'i18n': {'ru': ''}},
            'description': {
                'raw': 'This paper plane-shaped clutch has a ball-chain strap.',
                'i18n': {'ru': 'Клатч в форме бумажного самолёта.'},
            },
            'category': {'raw_path': ['Archive'], 'i18n': {'ru': ['Архив']}},
            'attributes': {'material': {'raw': 'Metal'}},
            'price': {'amount': 1000, 'currency': 'EUR'},
            'availability': {'status': 'in_stock', 'raw': True},
            'variants': {'colors': [], 'sizes': []},
            'media': {'images': []},
        })

        self.assertEqual(
            normalized['category']['canonical_path']['categories'][-1]['slug'],
            'mini_bags',
        )

    def test_offwhite_specific_product_type_overrides_bad_source_category(self):
        cases = (
            ('3.0 Court Low Top Sneakers', ['Women', 'Clothing', 'T-Shirts'],
             'Кроссовки 3.0 Court Low Top', 'sneakers'),
            ('White Bookish Mid Socks', ['Men', 'Clothing', 'Underwear'],
             'Носки White Bookish Mid', 'socks'),
            ('Out Of Office Sneaker Card Case', ['Special Categories', 'SALE CHECK'],
             'Картхолдер Out Of Office Sneaker', 'card_holders'),
            ('Jitney 1.4 Top Handle Bag', ['Archive'],
             'Сумка с ручками Jitney 1.4', 'handbags'),
        )
        for index, (raw_title, raw_path, expected_title, expected_slug) in enumerate(cases):
            with self.subTest(raw_title=raw_title):
                normalized = normalize_offwhite_product({
                    'id': f'offwhite:SPECIFIC-{index}',
                    'brand': 'OFF WHITE', 'gender': 'women',
                    'source': {'url': 'https://www.off---white.com/en-fr/archive/item.html'},
                    'title': {'raw': raw_title, 'i18n': {'ru': ''}},
                    'description': {
                        'raw': f'This {raw_title} is made in Italy.',
                        'i18n': {'ru': 'Изделие произведено в Италии.'},
                    },
                    'category': {'raw_path': raw_path, 'i18n': {'ru': []}},
                    'attributes': {'material': {'raw': 'Cotton'}},
                    'price': {'amount': 100, 'currency': 'EUR'},
                    'availability': {'status': 'in_stock', 'raw': True},
                    'variants': {'colors': [], 'sizes': []},
                    'media': {'images': []},
                })
                self.assertEqual(normalized['title']['i18n']['ru'], expected_title)
                self.assertEqual(
                    normalized['category']['canonical_path']['categories'][-1]['slug'],
                    expected_slug,
                )

    def test_offwhite_laces_and_wallet_are_not_sneakers(self):
        cases = (
            ('OW_Shoe Laces_1', 'These shoelaces have a star pattern.',
             ['Sneakers', "Men's Sneakers", 'Sneakers Accessories'],
             'Шнурки OW_Shoe Laces_1', 'shoe_accessories'),
            ('OW_SHOELACES_2', 'These shoelaces have a star pattern.',
             ['Sneakers', "Men's Sneakers", 'Sneakers Accessories'],
             'Шнурки OW_SHOELACES_2', 'shoe_accessories'),
            ('Sneaker Bifold Wallet', 'A wallet with multiple card slots.',
             ['Special Categories', 'SALE CHECK'],
             'Кошелёк Sneaker Bifold', 'wallets'),
            ('Shoelace Flat Sandal', 'Sandals with shoelace straps.',
             ['Sandals'], 'Сандалии Shoelace Flat', 'sandals'),
            ('OW_DUBRAE_STAR', 'This dubrae charm is crafted from brass.',
             ['Sneakers', "Men's Sneakers", 'Sneakers Accessories'],
             'Украшение для шнурков OW_DUBRAE_STAR', 'shoe_accessories'),
            ('Out Of Office Keyring', 'A keyring with a sneaker design.',
             ["Men's Other Accessories"], 'Брелок Out Of Office', 'keyrings'),
            ('Winter Boots', 'Winter boots with a warm lining.',
             ['Sneakers'], 'Ботинки Winter', 'boots'),
            ('Arrow Terry Slider', 'Terry sliders with rubber sole.',
             ['Sneakers'], 'Шлёпанцы Arrow Terry', 'sandals'),
            ('Biarritz Espadrilles', 'Canvas espadrilles.',
             ['Sneakers'], 'Сандалии Biarritz', 'sandals'),
        )
        for index, (raw_title, raw_description, raw_path, title, leaf) in enumerate(cases):
            with self.subTest(raw_title=raw_title):
                raw = {
                    'id': f'offwhite:ACCESSORY-{index}', 'brand': 'OFF WHITE',
                    'gender': 'men', 'title': {'raw': raw_title, 'i18n': {'ru': 'Кроссовки'}},
                    'description': {'raw': raw_description, 'i18n': {'ru': 'Описание товара.'}},
                    'category': {'raw_path': raw_path},
                    'attributes': {'material': {'raw': 'Leather'}},
                    'price': {'amount': 100, 'currency': 'EUR'},
                    'availability': {'status': 'in_stock'},
                    'variants': {'colors': [], 'sizes': []}, 'media': {'images': []},
                }
                card = normalize_offwhite_product(raw)
                self.assertEqual(card['title']['i18n']['ru'], title)
                self.assertEqual(card['category']['canonical_path']['categories'][-1]['slug'], leaf)
                self.assertEqual(card['category']['i18n']['ru'][-1], title)
                self.assertEqual(raw['title']['i18n']['ru'], 'Кроссовки')

    def test_existing_bad_sneaker_title_cannot_override_verified_accessory(self):
        from types import SimpleNamespace
        for raw, fixed in (('OW_Shoe Laces_1', 'Шнурки OW_Shoe Laces_1'),
                           ('Sneaker Bifold Wallet', 'Кошелёк Sneaker Bifold'),
                           ('OW_DUBRAE_STAR', 'Украшение для шнурков OW_DUBRAE_STAR'),
                           ('Out Of Office Keyring', 'Брелок Out Of Office')):
            with self.subTest(raw=raw):
                card = {'title': {'raw': raw, 'i18n': {'ru': fixed}}}
                old = SimpleNamespace(raw_data={
                    'title': {'raw': raw, 'i18n': {'ru': 'Кроссовки ' + raw}},
                })
                self.assertEqual(keep_better_existing_text(card, old)['title']['i18n']['ru'], fixed)

    def test_translation_audit_reports_wrong_type_not_shoelace_sandal(self):
        from .management.commands.audit_catalog_translations import review_card
        laces = review_card('offwhite:1', 'off-white', 'OW_Shoe Laces_1',
                            'Кроссовки OW_Shoe Laces_1', 'These shoelaces feature stars.',
                            'Шнурки со звёздами.', 'men/shoes/sneakers')
        self.assertEqual(laces['reasons'], ['shoelaces_as_sneakers'])
        wallet = review_card('offwhite:2', 'off-white', 'Sneaker Bifold Wallet',
                             'Кроссовки Bifold Wallet', 'This wallet has card slots.',
                             'Кошелёк с отделениями для карт.', 'men/shoes/sneakers')
        self.assertEqual(wallet['reasons'], ['wallet_as_sneakers'])
        dubrae = review_card('offwhite:4', 'off-white', 'OW_DUBRAE_STAR',
                             'Кроссовки OW_DUBRAE_STAR', '', '', 'men/shoes/sneakers')
        self.assertEqual(dubrae['reasons'], ['dubrae_as_sneakers'])
        keyring = review_card('offwhite:5', 'off-white', 'Out Of Office Keyring',
                              'Кроссовки Keyring', '', '', 'men/shoes/sneakers')
        self.assertEqual(keyring['reasons'], ['keyring_as_sneakers'])
        self.assertIsNone(review_card('offwhite:3', 'off-white', 'Shoelace Flat Sandal',
                                       'Сандалии Shoelace Flat', '', '', 'women/shoes/sandals'))
        self.assertIsNone(review_card('therow:1', 'the-row', 'Sock Shoe',
                                       'Туфли Sock', '', '', 'women/shoes/heels'))

    def test_offwhite_source_checked_remaining_copy_corrections(self):
        from .offwhite_editorial import improve_offwhite_copy

        cases = (
            ('Sneakers', 'Iconic zip tie and quoted "Shoe Laces".',
             '<p>Узнаваемый замок-липучка. Элементы дизайна, имитирующие шнурки.</p>',
             ('Фирменная пластиковая стяжка Zip Tie', 'Надпись «Shoe Laces»')),
            ('Open Sneakers', 'Iconic zip tie and adjustable side buckle.',
             '<p>Узнаваемая застежка-липучка. Застежка сбоку для регулировки.</p>',
             ('Фирменная пластиковая стяжка Zip Tie', 'Застежка сбоку')),
            ('Sandals', 'Adjustable hook-and-loop straps.',
             '<p>Ремешки с застёжкой-липучкой.</p>', ('застёжкой-липучкой',)),
            ('Skate Sneakers', 'These skate-inspired sneakers have a rubber sole.',
             '<p>Кроссовки, вдохновленные коньками.</p>', ('вдохновлённые скейтбордингом',)),
            ('Bookish Socks', 'Ribbed cotton-blend calf socks.',
             '<p>Чулки с ребрами из смесового хлопка.</p>', ('Носки до середины икры',)),
            ('Small Arrow', 'The classic bookish logo is on the chest.',
             '<p>Классического логотипа-цитата.</p>', ('фирменного текстового логотипа',)),
        )
        for title, english, russian, expected in cases:
            with self.subTest(title=title):
                card = {'id': 'offwhite:EDITORIAL-TEST', 'title': {'raw': title, 'i18n': {'ru': title}},
                        'description': {'raw': english, 'i18n': {'ru': russian}}}
                result = improve_offwhite_copy(card)
                corrected = result['description']['i18n']['ru']
                for phrase in expected:
                    self.assertIn(phrase, corrected)
                self.assertEqual(result['description']['raw'], english)
                self.assertEqual(improve_offwhite_copy(result)['description']['i18n']['ru'], corrected)

        card = {'id': 'offwhite:44XYA18JS26D003020',
                'title': {'raw': 'Diagonal Embroidery Slim Relaxed Jeans', 'i18n': {'ru': 'Джинсы'}},
                'description': {
                    'raw': 'The Diagonal Embroidery Slim Relaxed Jeans combine a timeless denim construction.',
                    'i18n': {'ru': 'The Diagonal Embroidery Slim Relaxed Jeans сочетают в себе '
                           'вечную конструкцию из денима, современный дизайн и изысканные логотипы бренда.'},
                }}
        text = improve_offwhite_copy(card)['description']['i18n']['ru']
        self.assertIn('Джинсы Diagonal Embroidery Slim Relaxed сочетают классический деним,', text)
        self.assertIn('современный дизайн и фирменную графику.', text)
        hoodie = {'id': 'offwhite:44MBB085Z26F002001',
                  'title': {'raw': 'Swarovski Double Eagle Skate Hoodie',
                            'i18n': {'ru': 'Худи Swarovski Double Eagle Skate'}},
                  'description': {'raw': 'Hood with drawstring. Kangaroo pockets.',
                                  'i18n': {'ru': 'Дизайн этой футболки Swarovski Double Eagle Skate. '
                                                 'Капюшон с шнурком.'}}}
        self.assertIn('Дизайн худи Swarovski Double Eagle Skate',
                      improve_offwhite_copy(hoodie)['description']['i18n']['ru'])

    def test_offwhite_reviewed_card_holders_and_tops_preserve_source_and_markup(self):
        from .offwhite_editorial import improve_offwhite_copy

        cases = (
            ('offwhite:OMND089S26LEA0011084', 'Bookish Spray Arrow Card Case',
             'Card slots and a compact card case.', 'Картхолдер Bookish Spray Arrow',
             '<p>Чехол Bookish Spray Arrow с карманами для карт.</p>',
             'Картхолдер Bookish Spray Arrow'),
            ('offwhite:518055266393078', 'Race Logo Arrow Seamless Top',
             'Seamless halter neckline. Made in Italy.', 'Топ Race Logo Arrow Seamless',
             '<p>Футболка с вырезом-халтер; у нее вырез-халтер. Произведена в Италии.</p>',
             'Топ с вырезом-халтер'),
            ('offwhite:518055266404552', 'Tech Jer Serena Long-Sleeve Top',
             'Technical jersey top with a cropped hem.', 'Топ Tech Jer Serena Long-Sleeve',
             '<p>Ознакомьтесь с современным дизайном с помощью футболки Tech Jer Serena с длинными рукавами, изготовленной из высокопроизводительной технической ткани, отражающей динамичный городской стиль.</p>',
             'Топ Tech Jer Serena с длинными рукавами изготовлен из технического трикотажа'),
            ('offwhite:44MAD013S26J001001', 'Eagle Team Soccer Top',
             'A football jersey with short sleeves.', 'Топ Eagle Team Soccer',
             '<p>Футболка Eagle Team Soccer.</p>', 'Футболка Eagle Team Soccer'),
        )
        for sku, raw_title, english, title, russian, expected in cases:
            with self.subTest(sku=sku):
                card = {'id': sku, 'title': {'raw': raw_title, 'i18n': {'ru': title}},
                        'description': {'raw': english, 'i18n': {'ru': russian}}}
                result = improve_offwhite_copy(card)
                self.assertIn(expected, result['description']['i18n']['ru'])
                self.assertEqual(result['description']['raw'], english)
                self.assertTrue(result['description']['i18n']['ru'].startswith('<p>'))
                self.assertEqual(improve_offwhite_copy(result)['description']['i18n']['ru'],
                                 result['description']['i18n']['ru'])

    def test_offwhite_reviewed_literal_copy_keeps_original_and_is_idempotent(self):
        from .offwhite_editorial import improve_offwhite_copy

        cases = (
            ('offwhite:518055266350521', 'Mix Logos Knit Beanie',
             'A beanie in ribbed knit fabric with embroidered logo patches.',
             '<p>Тщательно сделанный шарф-берет из ткани с ребрами, украшенный разнообразными логотипами.</p>',
             'Вязаная шапка в рубчик украшена нашивками с логотипами.'),
            ('offwhite:OWBA07DT25FLE0014001', 'Switch Off Crop Crewneck Sweatshirt',
             'Cropped crewneck sweatshirt. Long sleeves with ribbed cuffs.',
             '<p>Свитшот с коротким рукавом и круглым вырезом.</p>',
             'Укороченный свитшот с длинными рукавами'),
            ('offwhite:518051594835563', 'Off Stamp Rib Tank Dress',
             'Ribbed tank dress in cotton. Scoop neckline.',
             '<p>Платье с ребрами на талии.</p><ul><li>Воротник-чашечка</li></ul>',
             'Платье-майка в рубчик'),
            ('offwhite:OMAA120F25JER00C0110', 'Scribble Arrow Skate Logo T-Shirt',
             'Cotton t-shirt in relaxed fit.',
             'Применяется терминология: «свободного кроя» = свободного/непринуждённого кроя '
             '(имеется в виду форма/силуэт одежды; это НЕ «разглаженный/выглаженный», здесь речь не идёт о утюге). '
             '<p>Футболка свободного кроя.</p>',
             '<p>Футболка свободного кроя.</p>'),
        )
        for sku, raw_title, english, russian, expected in cases:
            with self.subTest(sku=sku):
                card = {'id': sku, 'title': {'raw': raw_title, 'i18n': {'ru': raw_title}},
                        'description': {'raw': english, 'i18n': {'ru': russian}}}
                result = improve_offwhite_copy(card)
                text = result['description']['i18n']['ru']
                self.assertIn(expected, text)
                self.assertEqual(result['description']['raw'], english)
                self.assertEqual(improve_offwhite_copy(result)['description']['i18n']['ru'], text)

    def test_offwhite_reviewed_repeated_source_copy_is_not_literally_mistranslated(self):
        from .offwhite_editorial import improve_offwhite_copy

        cases = (
            ('3.0 Off Court Sneakers', 'Signature leather zip-tie tag at the laces.',
             'Знак из кожи с застежкой-молнией на шнурках',
             'Фирменная кожаная стяжка Zip Tie на шнурках'),
            ('Scribble Diag Boxy T-Shirt', 'Scribble diagonal graphic. Ribbed crew neckline.',
             'Воротник-стойка с ребрами жесткости.', 'Круглый вырез с отделкой в рубчик.'),
            ('Ink Arrow Boxy T-Shirt', 'Ribbed crew neckline.',
             'Воротник-стойка с ребрами жесткости.', 'Круглый вырез с отделкой в рубчик.'),
            ('Arrow Scribble Mid Socks', 'Ribbed knit construction for enhanced comfort.',
             'Структура ткани с ребрами обеспечивает дополнительный комфорт',
             'Трикотаж в рубчик обеспечивает комфорт'),
        )
        for raw_title, english, russian, expected in cases:
            with self.subTest(raw_title=raw_title):
                card = {'id': 'offwhite:SOURCE-COPY-TEST',
                        'title': {'raw': raw_title, 'i18n': {'ru': raw_title}},
                        'description': {'raw': english, 'i18n': {'ru': '<p>' + russian + '</p>'}}}
                result = improve_offwhite_copy(card)
                self.assertIn(expected, result['description']['i18n']['ru'])
                self.assertEqual(result['description']['raw'], english)
                self.assertEqual(improve_offwhite_copy(result)['description']['i18n']['ru'],
                                 result['description']['i18n']['ru'])

    def test_offwhite_description_fixes_confirmed_translation_calques(self):
        normalized = normalize_offwhite_product({
            'id': 'offwhite:DESCRIPTION-FIXES',
            'brand': 'OFF WHITE', 'gender': 'men',
            'source': {'url': 'https://www.off---white.com/en-fr/men/accessories/belts/item.html'},
            'title': {'raw': 'Arrow Belt', 'i18n': {'ru': ''}},
            'description': {
                'raw': 'A silver buckle and a small bookish logo.',
                'i18n': {
                    'ru': 'На ремне имеется серебряный пряжка с небольшим логотипом-цитата. '
                          'Модель имеет стройную посадку и наклейку с логотипом. '
                          '<ul><li>из полированного латуни</li>'
                          '<li>Водолазный воротник</li>'
                          '<li>Иконичный зип-ленточный замок</li>'
                          '<li>Цитата «Шнурки для обуви»</li>'
                          '<li>Вкладка «Логотип» сбоку</li></ul>',
                },
            },
            'category': {'raw_path': ['Belts'], 'i18n': {'ru': []}},
            'attributes': {'material': {'raw': 'Leather'}},
            'price': {'amount': 100, 'currency': 'EUR'},
            'availability': {'status': 'in_stock', 'raw': True},
            'variants': {'colors': [], 'sizes': []},
            'media': {'images': []},
        })

        description = normalized['description']['i18n']['ru']
        self.assertIn('серебряная пряжка', description)
        self.assertIn('фирменным текстовым логотипом', description)
        self.assertIn('зауженную посадку', description)
        self.assertIn('нашивку с логотипом', description)
        self.assertIn('<li>Из полированной латуни</li>', description)
        self.assertIn('<li>Воротник-водолазка</li>', description)
        self.assertIn('<li>Фирменная пластиковая стяжка</li>', description)
        self.assertIn('<li>Надпись «Shoe Laces»</li>', description)
        self.assertIn('<li>Фирменная нашивка сбоку</li>', description)

    def test_replace_therow_catalog_preserves_product_and_reorders_full_images(self):
        user = get_user_model().objects.create_user('therow-customer')
        product = Product.objects.create(
            sku='therow:test-product', sku_without_size='old-article',
            brand='the-row', title='Старое название', category_name='Старая категория',
            description='Отредактированное описание мягкой сумки-тоут.',
            raw_data={
                'title': {'raw': 'Test Tote', 'i18n': {'ru': 'Старое название'}},
                'description': {
                    'raw': 'Structured tote bag in soft calfskin leather.',
                    'i18n': {'ru': 'Отредактированное описание мягкой сумки-тоут.'},
                },
            },
        )
        old_pk = product.pk
        Favorite.objects.create(user=user, product=product)
        archive_path = therow_zip_path(self.media_root)
        report_dir = Path(self.media_root, 'reports')

        call_command(
            'replace_therow_catalog', str(archive_path),
            report_dir=str(report_dir),
        )

        product.refresh_from_db()
        images = list(product.images.order_by('position', 'pk'))
        self.assertEqual(product.pk, old_pk)
        self.assertEqual(product.title, 'Сумка-тоут Test')
        self.assertEqual(
            product.description, 'Отредактированное описание мягкой сумки-тоут.',
        )
        self.assertEqual(
            product.raw_data['description']['i18n']['ru'],
            'Отредактированное описание мягкой сумки-тоут.',
        )
        self.assertEqual(product.category_name, 'Сумки-тоут')
        self.assertTrue(Favorite.objects.filter(user=user, product=product).exists())
        self.assertEqual(len(images), 5)
        self.assertEqual(
            [Path(image.source_local_path).stem for image in images],
            ['image-3', 'image-4', 'image-5', 'image-1', 'image-2'],
        )
        self.assertEqual([image.position for image in images], list(range(5)))
        for image in images:
            self.assertTrue(image.local_image.name.endswith('.webp'))
            self.assertTrue(image.preview_image.name.endswith('.webp'))
            self.assertTrue(image.gallery_image.name.endswith('.webp'))
            self.assertFalse(image.detail_image)
            self.assertTrue(image.detail_processed)
        result = json.loads(Path(report_dir, 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['excluded_low_resolution_images'], 1)
        self.assertEqual(result['database_images'], 5)
        self.assertEqual(result['critical_issue_counts'], {})
        self.assertEqual(result['editorial']['description_basis'], {'legacy': 1})
        self.assertEqual(result['editorial']['issue_count'], 0)
        self.assertTrue(Path(report_dir, 'old-taxonomy.json').is_file())

    def test_replace_valentino_catalog_keeps_packshot_first_and_does_not_crop(self):
        archive_path = valentino_zip_path(self.media_root)
        report_dir = Path(self.media_root, 'valentino-reports')

        call_command(
            'replace_valentino_catalog', str(archive_path),
            report_dir=str(report_dir),
        )

        product = Product.objects.get(sku='valentino:WBTEST_0NO')
        images = list(product.images.order_by('position', 'pk'))
        self.assertEqual(product.brand, 'valentino-garavani')
        self.assertEqual(
            product.title,
            'Маленькая сумка через плечо Valentino Garavani Rockstud '
            'из зернистой телячьей кожи',
        )
        self.assertEqual(product.category_path_key, 'women/bags/shoulder_bags')
        self.assertEqual(product.current_color, 'Чёрный')
        self.assertEqual(product.material, ['зернистая телячья кожа'])
        self.assertIn('фирменные заклёпки', product.description)
        self.assertEqual(product.raw_data['title']['i18n']['ru'], product.title)
        self.assertEqual(
            product.raw_data['description']['i18n']['ru'], product.description,
        )
        self.assertEqual(
            [Path(image.source_local_path).stem for image in images],
            ['image-1', 'image-2', 'image-3'],
        )
        self.assertEqual([image.position for image in images], [0, 1, 2])
        for image in images:
            self.assertTrue(image.local_image.name.endswith('.webp'))
            self.assertTrue(image.preview_image.name.endswith('.webp'))
            self.assertTrue(image.gallery_image.name.endswith('.webp'))
            self.assertFalse(image.detail_image)
            self.assertTrue(image.detail_processed)
        result = json.loads(Path(report_dir, 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['new_products'], 1)
        self.assertEqual(result['database_images'], 3)
        self.assertEqual(result['products_with_cropped_detail_image'], 0)
        self.assertEqual(result['critical_issue_counts'], {})


class AdminImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin', email='admin@example.com', password='test-password'
        )
        self.client.force_login(self.user)

    def test_admin_can_import_json(self):
        uploaded = SimpleUploadedFile(
            'product.json',
            json.dumps(product_payload(), ensure_ascii=False).encode('utf-8'),
            content_type='application/json',
        )
        response = self.client.post(
            reverse('admin:shop_product_import'),
            {'json_files': uploaded, 'replace_relations': 'on'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Импорт завершён')
        self.assertTrue(Product.objects.filter(sku='H-001-BLACK').exists())

    def test_admin_can_import_zip_and_shows_report(self):
        media_root = tempfile.mkdtemp()
        try:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse('admin:shop_product_import_zip'),
                    {
                        'zip_files': zip_upload(),
                        'replace_relations': 'on',
                        'extract_images': 'on',
                    },
                )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Результат импорта ZIP')
            self.assertContains(response, 'фото привязано')
            product = Product.objects.get(sku='H-001-BLACK')
            self.assertTrue(product.images.get().local_image.name)
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

    def test_admin_uses_elane_dashboard_and_quick_actions(self):
        response = self.client.get(reverse('admin:index'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin/index.html')
        self.assertContains(response, 'admin/css/elane-admin.css')
        self.assertContains(response, 'Панель управления')
        self.assertContains(response, 'FINDER CONTROL CENTER')
        self.assertNotContains(response, 'AURÉNE')
        self.assertContains(response, reverse('admin:shop_product_changelist'))
        self.assertContains(response, reverse('admin:shop_product_import_zip'))
        self.assertContains(response, reverse('shop:account'))

    def test_product_bulk_publish_action(self):
        product, _ = import_product(product_payload())
        product.is_active = False
        product.save(update_fields=('is_active',))

        response = self.client.post(reverse('admin:shop_product_changelist'), {
            'action': 'publish_products',
            '_selected_action': [str(product.pk)],
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertTrue(product.is_active)
        self.assertContains(response, 'Опубликовано товаров: 1')

    def test_admin_manages_product_requests_and_statuses(self):
        product_request = ProductRequest.objects.create(
            customer_name='Анна Иванова',
            customer_email='anna@example.com',
            customer_phone='+79991234567',
        )
        product_request.items.create(
            brand='Hermès', product_name='Kelly 25', article='H123456'
        )

        response = self.client.get(reverse('admin:shop_productrequest_changelist'))
        self.assertContains(response, product_request.number)
        self.assertContains(response, 'Анна Иванова')
        self.assertContains(response, 'Hermès — Kelly 25 · H123456')

        response = self.client.post(
            reverse('admin:shop_productrequest_changelist'),
            {
                'action': 'mark_processing',
                '_selected_action': [str(product_request.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        product_request.refresh_from_db()
        self.assertEqual(product_request.status, ProductRequest.Status.PROCESSING)
        self.assertContains(response, 'Статус «В обработке»')

        response = self.client.post(
            reverse('admin:shop_productrequest_changelist'),
            {
                'action': 'mark_confirmation',
                '_selected_action': [str(product_request.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        product_request.refresh_from_db()
        self.assertEqual(product_request.status, ProductRequest.Status.CONFIRMATION)
        self.assertContains(response, 'Статус «Ожидает подтверждения»')


class BackofficeTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='control-admin', email='control@example.com', password='test-password'
        )
        self.product, _ = import_product(product_payload())

    def test_control_requires_staff_and_uses_own_layout(self):
        url = reverse('shop:control_dashboard')
        response = self.client.get(url)
        self.assertRedirects(
            response, f'{reverse("shop:login")}?next={url}', fetch_redirect_response=False
        )
        buyer = get_user_model().objects.create_user(
            username='buyer-control', password='password'
        )
        self.client.force_login(buyer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'shop/backoffice/dashboard.html')
        self.assertContains(response, 'shop/backoffice/control.css')
        self.assertContains(response, 'shop/backoffice/forms.css')
        self.assertContains(response, 'CONTROL CENTER')
        self.assertContains(response, 'FINDER')
        self.assertNotContains(response, 'AURÉNE')
        self.assertNotContains(response, 'django-admin-form')

    def test_control_sidebar_links_to_telegram_accounts_for_authorized_staff(self):
        url = reverse('shop:control_dashboard')
        telegram_url = reverse('shop:control_telegram_accounts')
        self.client.force_login(self.admin)

        response = self.client.get(url)

        self.assertContains(response, f'href="{telegram_url}"')
        self.assertContains(response, '<span>Telegram</span>', html=True)

        staff = get_user_model().objects.create_user(
            username='limited-staff', password='password', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(url)
        self.assertNotContains(response, f'href="{telegram_url}"')

    @override_settings(TELEGRAM_BOT_USERNAME='testikiq_bot')
    def test_control_telegram_pages_show_contacts_and_admin_modes(self):
        from telegram_bot.models import ManagerContact, TelegramAccount

        TelegramAccount.objects.create(
            user=self.admin, chat_id=101,
            role=TelegramAccount.Role.ADMINISTRATOR,
        )
        ManagerContact.objects.create(
            kind=ManagerContact.Kind.PHONE, label='Телефон', value='+79990000000',
        )
        self.client.force_login(self.admin)

        telegram_page = self.client.get(reverse('shop:control_telegram_accounts'))
        contacts_page = self.client.get(reverse('shop:control_manager_contacts'))
        user_page = self.client.get(reverse('shop:control_user_detail', args=[self.admin.pk]))

        for response in (telegram_page, user_page):
            self.assertContains(response, 'start=customer')
            self.assertContains(response, 'start=admin')
        self.assertContains(telegram_page, 'Как пользователь')
        self.assertContains(telegram_page, 'Как администратор')
        self.assertContains(user_page, 'Открыть как пользователь')
        self.assertContains(user_page, 'Открыть как администратор')
        self.assertContains(contacts_page, 'Способы связи')
        self.assertContains(contacts_page, '+79990000000')

    def test_control_manager_contacts_formset_adds_edits_and_deletes(self):
        from telegram_bot.models import ManagerContact

        first = ManagerContact.objects.create(
            kind=ManagerContact.Kind.PHONE, label='Старый телефон',
            value='+70000000001', position=10,
        )
        removed = ManagerContact.objects.create(
            kind=ManagerContact.Kind.CUSTOM, label='Удалить',
            value='Больше не используется', position=20,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse('shop:control_manager_contacts'), {
            'form-TOTAL_FORMS': '3', 'form-INITIAL_FORMS': '2',
            'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
            'form-0-id': str(first.pk), 'form-0-position': '5',
            'form-0-kind': ManagerContact.Kind.PHONE,
            'form-0-label': 'Основной телефон', 'form-0-value': '+79990000000',
            'form-0-url': '', 'form-0-is_active': 'on',
            'form-1-id': str(removed.pk), 'form-1-position': '20',
            'form-1-kind': ManagerContact.Kind.CUSTOM,
            'form-1-label': removed.label, 'form-1-value': removed.value,
            'form-1-url': '', 'form-1-is_active': 'on', 'form-1-DELETE': 'on',
            'form-2-id': '', 'form-2-position': '30',
            'form-2-kind': ManagerContact.Kind.TELEGRAM,
            'form-2-label': 'Telegram', 'form-2-value': '@manager',
            'form-2-url': 'https://t.me/manager', 'form-2-is_active': 'on',
        })

        self.assertRedirects(response, reverse('shop:control_manager_contacts'))
        first.refresh_from_db()
        self.assertEqual(first.label, 'Основной телефон')
        self.assertEqual(first.value, '+79990000000')
        self.assertEqual(first.position, 5)
        self.assertFalse(ManagerContact.objects.filter(pk=removed.pk).exists())
        self.assertTrue(ManagerContact.objects.filter(
            kind=ManagerContact.Kind.TELEGRAM, value='@manager',
            url='https://t.me/manager', position=30,
        ).exists())

    def test_control_manager_contacts_view_permission_is_read_only(self):
        from django.contrib.auth.models import Permission
        from telegram_bot.models import ManagerContact

        contact = ManagerContact.objects.create(
            kind=ManagerContact.Kind.EMAIL, label='E-mail',
            value='manager@example.com',
        )
        staff = get_user_model().objects.create_user(
            username='contacts-viewer', password='password', is_staff=True,
        )
        staff.user_permissions.add(Permission.objects.get(
            codename='view_managercontact', content_type__app_label='telegram_bot',
        ))
        self.client.force_login(staff)
        url = reverse('shop:control_manager_contacts')

        page = self.client.get(url)
        denied = self.client.post(url, {
            'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '1',
            'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
            'form-0-id': str(contact.pk), 'form-0-position': '0',
            'form-0-kind': contact.kind, 'form-0-label': 'Изменено',
            'form-0-value': contact.value, 'form-0-url': '',
            'form-0-is_active': 'on',
        })

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Доступен только просмотр.')
        self.assertNotContains(page, 'Сохранить способы связи')
        self.assertEqual(denied.status_code, 403)
        contact.refresh_from_db()
        self.assertEqual(contact.label, 'E-mail')

    def test_main_control_sections_render(self):
        self.client.force_login(self.admin)
        fall_banner, _ = Banner.objects.get_or_create(slot=Banner.Slot.FALL_WINTER)
        urls = [
            reverse('shop:control_products'),
            reverse('shop:control_catalog_updates'),
            reverse('shop:control_product_create'),
            reverse('shop:control_product_edit', args=[self.product.pk]),
            reverse('shop:control_dictionaries'),
            reverse('shop:control_banners'),
            reverse('shop:control_banner_edit', args=[fall_banner.pk]),
            reverse('shop:control_import'),
            reverse('shop:control_orders'),
            reverse('shop:control_requests'),
            reverse('shop:control_pricing'),
            reverse('shop:control_analytics'),
            reverse('shop:control_analytics_marketing'),
            reverse('shop:control_analytics_catalog'),
            reverse('shop:control_analytics_behavior'),
            reverse('shop:control_analytics_sales'),
            reverse('shop:control_analytics_finance'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    @override_settings(AURENE_API_KEYS_JSON=json.dumps({
        'parser': {
            'secret': 'parser-secret-with-at-least-thirty-two-bytes',
            'scopes': ['catalog:read', 'catalog:write'],
        },
    }))
    def test_catalog_updates_shows_brand_and_parser_summary(self):
        from .models import ApiRequestLog

        ApiRequestLog.objects.create(
            key_id='parser', method='POST',
            path='/api/v1/catalog/commercial/bulk/', status_code=200,
            result_code='ok', duration_ms=125,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('shop:control_catalog_updates'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_section'], 'updates')
        self.assertTrue(response.context['parser_ready'])
        self.assertContains(response, 'Обновление цен и наличия')
        self.assertContains(response, 'Hermès')
        self.assertContains(response, 'POST /api/v1/catalog/commercial/bulk/')
        self.assertContains(response, '<span>Обновление</span>', html=True)
        self.assertContains(response, '125 мс')

    def test_catalog_updates_requires_product_view_permission(self):
        staff = get_user_model().objects.create_user(
            username='updates-viewer', password='password', is_staff=True,
        )
        self.client.force_login(staff)
        url = reverse('shop:control_catalog_updates')

        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(
            self.client.get(reverse('shop:control_dashboard')),
            f'href="{url}"',
        )

    def test_analytics_dashboard_reports_traffic_catalog_and_leads(self):
        visitor = AnalyticsVisitor.objects.create()
        analytics_session = AnalyticsSession.objects.create(
            visitor=visitor, source='telegram', medium='social',
            campaign='september', content='creative-a', device_type='mobile',
            page_views=2, duration_seconds=45, engaged=True,
        )
        AnalyticsEvent.objects.create(
            session=analytics_session, visitor=visitor, event_type='page_view', path='/'
        )
        AnalyticsEvent.objects.create(
            session=analytics_session, visitor=visitor, event_type='product_view',
            product=self.product, product_sku=self.product.article,
            brand=self.product.brand,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('shop:control_analytics'))
        catalog = self.client.get(reverse('shop:control_analytics_catalog'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['analytics_section'], 'overview')
        self.assertContains(response, '<h1>Обзор</h1>', html=True)
        self.assertContains(response, 'telegram / social')
        self.assertContains(catalog, self.product.title)
        self.assertContains(catalog, 'Воронка каталога')
        self.assertNotContains(response, 'Полный отчёт по товарам и брендам')

    def test_analytics_dashboard_has_today_charts_and_sortable_tables(self):
        today_visitor = AnalyticsVisitor.objects.create()
        today_session = AnalyticsSession.objects.create(
            visitor=today_visitor, source='direct', device_type='desktop'
        )
        AnalyticsEvent.objects.create(
            session=today_session, visitor=today_visitor,
            event_type='page_view', path='/catalog/',
        )
        yesterday_visitor = AnalyticsVisitor.objects.create()
        yesterday_session = AnalyticsSession.objects.create(
            visitor=yesterday_visitor, source='yandex', device_type='mobile'
        )
        yesterday = timezone.now() - timedelta(days=1)
        AnalyticsSession.objects.filter(pk=yesterday_session.pk).update(
            started_at=yesterday, last_seen_at=yesterday,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('shop:control_analytics_behavior'), {'days': 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['days'], 1)
        self.assertEqual(response.context['period_label'], 'Сегодня')
        self.assertEqual(response.context['session_count'], 1)
        self.assertContains(response, 'Активность по часам')
        self.assertContains(response, 'data-analytics-chart="line"')
        self.assertContains(response, 'data-analytics-chart="donut"')
        self.assertContains(response, 'shop/backoffice/analytics.js')

    def test_one_account_in_multiple_browsers_is_one_visitor(self):
        customer = get_user_model().objects.create_user(
            username='one-person@example.com', email='one-person@example.com',
            password='password',
        )
        browsers = (self.client_class(), self.client_class())
        for browser in browsers:
            browser.force_login(customer)
            browser.get('/', HTTP_USER_AGENT='Mozilla/5.0')
            browser.post(
                reverse('shop:analytics_event'),
                data=json.dumps({'type': 'page_view', 'path': '/'}),
                content_type='application/json',
            )
        technical_visitor = AnalyticsVisitor.objects.create()
        AnalyticsSession.objects.create(visitor=technical_visitor, source='smoke')

        self.assertEqual(AnalyticsVisitor.objects.filter(user=customer).count(), 1)
        self.assertEqual(AnalyticsSession.objects.filter(user=customer).count(), 2)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('shop:control_analytics'), {'days': 1})
        self.assertEqual(response.context['unique_visitors'], 1)
        self.assertEqual(response.context['session_count'], 2)

    def test_customer_page_has_personal_analytics_only(self):
        customer = get_user_model().objects.create_user(
            username='personal@example.com', email='personal@example.com',
            password='password',
        )
        other = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='password',
        )
        visitor = AnalyticsVisitor.objects.create(user=customer)
        session = AnalyticsSession.objects.create(
            visitor=visitor, user=customer, source='telegram', medium='social',
            device_type=AnalyticsSession.Device.MOBILE, page_views=1,
            duration_seconds=42,
        )
        AnalyticsEvent.objects.create(
            visitor=visitor, session=session, user=customer,
            event_type='page_view', path='/catalog/', page_title='Каталог',
        )
        AnalyticsEvent.objects.create(
            visitor=visitor, session=session, user=customer,
            event_type='product_view', path=self.product.get_absolute_url(),
            product=self.product,
        )
        other_visitor = AnalyticsVisitor.objects.create(user=other)
        other_session = AnalyticsSession.objects.create(
            visitor=other_visitor, user=other, source='yandex',
        )
        AnalyticsEvent.objects.create(
            visitor=other_visitor, session=other_session, user=other,
            event_type='page_view', path='/',
        )
        ProductRequest.objects.create(
            user=customer, customer_name='Клиент', customer_email=customer.email,
            customer_phone='+79990000001',
        )
        Order.objects.create(
            user=customer, customer_name='Клиент', customer_email=customer.email,
            customer_phone='+79990000001', delivery_address='Москва',
            estimated_total=Decimal('15000'), actual_revenue=Decimal('15000'),
            cost_of_goods=Decimal('10000'), status=Order.Status.PURCHASED,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('shop:control_user_detail', args=[customer.pk]), {'days': 1}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['user_analytics']['sessions'], 1)
        self.assertEqual(response.context['user_analytics']['page_views'], 1)
        self.assertEqual(response.context['user_analytics']['product_views'], 1)
        self.assertEqual(response.context['user_analytics']['leads'], 1)
        self.assertEqual(response.context['user_analytics']['orders'], 1)
        self.assertEqual(response.context['user_analytics']['revenue'], Decimal('15000'))
        self.assertContains(response, 'Статистика пользователя')
        self.assertContains(response, 'telegram / social')
        self.assertContains(response, self.product.title)
        self.assertNotContains(response, 'yandex')

    def test_analytics_endpoint_tracks_utm_device_and_product_event(self):
        response = self.client.get(
            '/?utm_source=yandex&utm_medium=cpc&utm_campaign=brand&utm_content=ad-1',
            HTTP_USER_AGENT='Mozilla/5.0 (iPhone; Mobile)',
        )
        self.assertEqual(response.status_code, 200)
        event_response = self.client.post(
            reverse('shop:analytics_event'),
            data=json.dumps({
                'type': 'product_view', 'product_id': self.product.pk,
                'metadata': {'duration_seconds': 12},
            }),
            content_type='application/json',
        )

        self.assertEqual(event_response.status_code, 200)
        tracked = AnalyticsEvent.objects.get(event_type='product_view')
        self.assertEqual(tracked.product, self.product)
        self.assertEqual(tracked.session.source, 'yandex')
        self.assertEqual(tracked.session.medium, 'cpc')
        self.assertEqual(tracked.session.campaign, 'brand')
        self.assertEqual(tracked.session.content, 'ad-1')
        self.assertEqual(tracked.session.device_type, 'mobile')
        self.assertTrue(tracked.session.engaged)

    def test_analytics_dashboard_calculates_paid_economics_and_attribution(self):
        visitor = AnalyticsVisitor.objects.create()
        session = AnalyticsSession.objects.create(
            visitor=visitor, source='yandex', medium='cpc',
            campaign='launch', content='creative-1', ad_id='ad-7',
        )
        AnalyticsEvent.objects.create(
            session=session, visitor=visitor, event_type='internal_search',
            metadata={'query': 'kelly', 'result_count': 3},
        )
        AnalyticsEvent.objects.create(
            session=session, visitor=visitor, event_type='product_click',
            product=self.product,
        )
        lead = ProductRequest.objects.create(
            customer_name='Анна', customer_email='anna@example.com',
            customer_phone='+79990000000', source='yandex',
            utm_campaign='launch', utm_content='creative-1', ad_id='ad-7',
            first_source='telegram', first_utm_campaign='awareness',
            analytics_visitor=visitor, analytics_session=session,
        )
        lead.items.create(brand=self.product.brand, product_name=self.product.title)
        order = Order.objects.create(
            customer_name='Анна', customer_email='anna@example.com',
            customer_phone='+79990000000', delivery_address='Москва',
            estimated_total=Decimal('10000'), actual_revenue=Decimal('10000'),
            cost_of_goods=Decimal('6000'), currency='RUB',
            status=Order.Status.PURCHASED, source='yandex',
            utm_campaign='launch', utm_content='creative-1', ad_id='ad-7',
            first_source='telegram', first_utm_campaign='awareness',
            analytics_visitor=visitor, analytics_session=session,
        )
        order.items.create(
            product=self.product, product_title=self.product.title,
            brand=self.product.brand, sku=self.product.sku,
            unit_price=Decimal('10000'), currency='RUB', quantity=1,
            price_breakdown={
                'version': 1, 'pricing_mode': 'fixed_rub', 'steps': [],
            },
        )
        today = timezone.localdate()
        month_start = today.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        MarketingCampaignSpend.objects.create(
            period_start=month_start, period_end=month_end,
            source='yandex', campaign='launch', content='creative-1',
            ad_id='ad-7', amount=Decimal(month_end.day * 100),
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('shop:control_analytics_finance'), {
            'date_from': today.isoformat(), 'date_to': today.isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['custom_period'])
        self.assertEqual(response.context['lead_cr'], 100)
        self.assertEqual(response.context['order_cr'], 100)
        self.assertEqual(response.context['order_count'], 1)
        self.assertEqual(response.context['revenue'], Decimal('10000'))
        self.assertEqual(response.context['cost_of_goods'], Decimal('6000'))
        self.assertEqual(response.context['gross_profit'], Decimal('4000'))
        self.assertEqual(response.context['total_spend'], Decimal('100.00'))
        self.assertEqual(response.context['profit_after_ads'], Decimal('3900.00'))
        self.assertTrue(response.context['economics_chart_ready'])
        self.assertEqual(response.context['economics_chart_total'], Decimal('10000'))
        self.assertEqual(
            [row['amount'] for row in response.context['economics_breakdown']],
            [Decimal('6000'), Decimal('100.00'), Decimal('3900.00')],
        )
        self.assertEqual(
            [row['percent'] for row in response.context['economics_breakdown']],
            [60.0, 1.0, 39.0],
        )
        self.assertEqual(response.context['cpl'], Decimal('100.00'))
        self.assertEqual(response.context['cac'], Decimal('100.00'))
        self.assertEqual(response.context['roas'], Decimal('100'))
        self.assertEqual(response.context['campaign_report'][0]['source'], 'yandex')
        self.assertEqual(response.context['first_touch_report'][0]['source'], 'telegram')
        self.assertEqual(response.context['product_report'][0]['profit'], Decimal('4000'))
        self.assertEqual(response.context['brand_report'][0]['profit'], Decimal('4000'))
        self.assertEqual(response.context['search_report'][0]['clicks'], 1)
        self.assertEqual(response.context['search_report'][0]['sales'], 1)
        marketing = self.client.get(reverse('shop:control_analytics_marketing'), {
            'date_from': today.isoformat(), 'date_to': today.isoformat(),
        })
        sales = self.client.get(reverse('shop:control_analytics_sales'), {
            'date_from': today.isoformat(), 'date_to': today.isoformat(),
        })
        finance = self.client.get(reverse('shop:control_analytics_finance'), {
            'date_from': today.isoformat(), 'date_to': today.isoformat(),
        })
        self.assertContains(marketing, 'Last-touch атрибуция')
        self.assertContains(sales, 'Причины отказов')
        self.assertContains(finance, 'data-chart-source="analytics-economics-data"')
        self.assertContains(finance, 'Чистая прибыль')

    def test_economics_includes_saved_pricing_formula_items(self):
        today = timezone.localdate()
        order = Order.objects.create(
            customer_name='Анна', customer_email='anna@example.com',
            customer_phone='+79990000000', delivery_address='Москва',
            estimated_total=Decimal('13000'), actual_revenue=Decimal('13000'),
            cost_of_goods=Decimal('10000'), currency='RUB',
            status=Order.Status.PURCHASED,
        )
        order.items.create(
            product=self.product, product_title=self.product.title,
            unit_price=Decimal('13000'), currency='RUB', quantity=1,
            price_breakdown={
                'version': 1, 'pricing_mode': 'calculated',
                'base_rubles': '10000', 'total': '13000',
                'steps': [
                    {'name': 'Комиссия', 'amount': '1000'},
                    {'name': 'Налог', 'amount': '2000'},
                ],
            },
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('shop:control_analytics_finance'), {
            'date_from': today.isoformat(), 'date_to': today.isoformat(),
        })

        rows = response.context['economics_breakdown']
        self.assertEqual([row['label'] for row in rows], [
            'Себестоимость товаров', 'Комиссия', 'Налог', 'Реклама', 'Чистая прибыль',
        ])
        self.assertEqual([row['amount'] for row in rows], [
            Decimal('10000'), Decimal('1000'), Decimal('2000'),
            Decimal('0'), Decimal('0'),
        ])
        self.assertTrue(response.context['pricing_details_complete'])

    def test_old_order_without_price_snapshot_does_not_estimate_net_profit(self):
        today = timezone.localdate()
        order = Order.objects.create(
            customer_name='Анна', customer_email='anna@example.com',
            customer_phone='+79990000000', delivery_address='Москва',
            estimated_total=Decimal('13000'), actual_revenue=Decimal('13000'),
            cost_of_goods=Decimal('10000'), currency='RUB',
            status=Order.Status.PURCHASED,
        )
        order.items.create(
            product=self.product, product_title=self.product.title,
            unit_price=Decimal('13000'), currency='RUB', quantity=1,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('shop:control_analytics_finance'), {
            'date_from': today.isoformat(), 'date_to': today.isoformat(),
        })

        self.assertFalse(response.context['pricing_details_complete'])
        self.assertIsNone(response.context['net_profit'])
        self.assertFalse(response.context['economics_chart_ready'])
        self.assertEqual(response.context['economics_breakdown'], [])
        self.assertContains(response, 'старые заказы без сохранённого состава цены')

    def test_sectioned_analytics_navigation_and_access(self):
        sections = (
            ('shop:control_analytics', 'overview', 'Главные показатели бизнеса'),
            ('shop:control_analytics_marketing', 'marketing', 'Источник → кампания'),
            ('shop:control_analytics_catalog', 'catalog', 'Состояние каталога'),
            ('shop:control_analytics_behavior', 'behavior', 'Переходы между страницами'),
            ('shop:control_analytics_sales', 'sales', 'Воронка работы с клиентом'),
            ('shop:control_analytics_finance', 'finance', 'Экономика заказа'),
        )
        buyer = get_user_model().objects.create_user(
            username='analytics-buyer', password='password'
        )
        self.client.force_login(buyer)
        for url_name, _, _ in sections:
            with self.subTest(url_name=url_name, user='buyer'):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)

        self.client.force_login(self.admin)
        for url_name, section, heading in sections:
            with self.subTest(url_name=url_name, user='admin'):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['analytics_section'], section)
                self.assertContains(response, heading)
                self.assertContains(response, '<summary><i>◫</i><span>Аналитика</span>', html=False)
                self.assertContains(response, 'class="control-nav-group" open')
        overview = self.client.get(reverse('shop:control_analytics'))
        self.assertContains(overview, 'data-chart-switch="analytics-overview-revenue"')
        self.assertNotContains(overview, 'Источник → кампания')
        self.assertNotContains(overview, 'Экономика заказа')
        period_overview = self.client.get(reverse('shop:control_analytics'), {'days': 90})
        self.assertContains(
            period_overview,
            f'{reverse("shop:control_analytics_finance")}?days=90',
        )

    def test_weekly_look_editor_requires_permission_and_can_hide_block(self):
        url = reverse('shop:control_weekly_look')
        staff = get_user_model().objects.create_user(
            username='weekly-staff', password='password', is_staff=True,
        )
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        editor = self.client.get(url)
        self.assertContains(editor, 'Скрыть образ недели')
        self.assertContains(editor, 'data-banner-picker')
        self.assertContains(editor, 'control-nav-group')
        self.assertContains(editor, 'class="control-nav-group"', count=1)
        self.assertNotContains(editor, 'class="control-nav-group" open')
        self.assertContains(self.client.get(reverse('shop:home')), 'data-weekly-look')
        self.assertRedirects(self.client.post(url, {'action': 'toggle'}), url)
        self.assertFalse(WeeklyLook.objects.get(pk=1).is_visible)
        home = self.client.get(reverse('shop:home'))
        self.assertNotContains(home, 'data-weekly-look')
        self.assertContains(home, 'FINDER Selection')
        self.client.post(url, {'action': 'toggle'})
        self.assertContains(self.client.get(reverse('shop:home')), 'data-weekly-look')

    def test_weekly_look_editor_grants_image_search_to_authorized_staff(self):
        staff = get_user_model().objects.create_user(
            username='weekly-editor', password='password', is_staff=True,
        )
        staff.user_permissions.add(Permission.objects.get(
            content_type__app_label='shop', codename='change_weeklylook',
        ))
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse('shop:control_weekly_look')), 'Добавить вещь')
        self.assertEqual(self.client.get(reverse('shop:control_banner_image_search')).status_code, 200)
        self.assertEqual(self.client.get(reverse('shop:control_banners')).status_code, 403)

    def test_weekly_look_edit_saves_product_photo_and_text(self):
        url = reverse('shop:control_weekly_look')
        self.client.force_login(self.admin)
        self.client.get(url)
        image = self.product.images.first()
        self.assertIsNotNone(image)
        data = {
            'is_visible': 'on', 'title': 'Редакторский образ',
            'description': 'Подборка месяца', 'headline': 'Новый заголовок',
            'text': 'Новый текст',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '4',
            'items-0-catalog_image': str(image.pk), 'items-0-ORDER': '0',
        }
        response = self.client.post(url, data)
        self.assertRedirects(response, url)
        self.assertEqual(WeeklyLookItem.objects.get().catalog_image_id, image.pk)
        home = self.client.get(reverse('shop:home'))
        self.assertContains(home, 'Редакторский образ')
        self.assertContains(home, 'Новый заголовок')
        self.assertEqual(len(home.context['weekly_look_items']), 1)
        self.assertContains(home, 'weekly-look-hotspot', count=1)
        data['items-INITIAL_FORMS'] = '1'
        data['items-0-id'] = str(WeeklyLookItem.objects.get().pk)
        data['items-0-DELETE'] = 'on'
        self.assertRedirects(self.client.post(url, data), url)
        self.assertFalse(WeeklyLookItem.objects.exists())

    def test_weekly_look_upload_replaces_catalog_photo_and_requires_product(self):
        url = reverse('shop:control_weekly_look')
        self.client.force_login(self.admin)
        image = self.product.images.first()
        data = {
            'is_visible': 'on', 'title': 'Образ недели',
            'description': 'Подборка', 'headline': 'Заголовок', 'text': 'Описание',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '4',
            'items-0-ORDER': '0',
        }
        media_root = tempfile.mkdtemp()
        try:
            with self.settings(MEDIA_ROOT=media_root):
                invalid = self.client.post(url, {
                    **data, 'items-0-uploaded_image': image_upload('look.jpg'),
                })
                self.assertEqual(invalid.status_code, 200)
                self.assertContains(invalid, 'Выберите товар для загруженного фото.')
                self.assertFalse(WeeklyLookItem.objects.exists())
                result = self.client.post(url, {
                    **data, 'items-0-catalog_image': str(image.pk),
                    'items-0-uploaded_image': image_upload('look.jpg'),
                })
                self.assertRedirects(result, url)
                item = WeeklyLookItem.objects.get()
                self.assertTrue(item.uploaded_image.name.endswith('.webp'))
                self.assertContains(self.client.get(reverse('shop:home')), item.uploaded_image.url)
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

    def test_weekly_look_reorders_selected_items(self):
        self.client.force_login(self.admin)
        second, _ = import_product(product_payload(sku='LOOK-SECOND', title='Вторая вещь'))
        first_image = self.product.images.first()
        second_image = second.images.first()
        self.assertIsNotNone(second_image)
        url = reverse('shop:control_weekly_look')
        data = {
            'is_visible': 'on', 'title': 'Образ недели', 'description': 'Подборка',
            'headline': 'Заголовок', 'text': 'Описание',
            'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '4',
            'items-0-catalog_image': str(first_image.pk), 'items-0-ORDER': '1',
            'items-1-catalog_image': str(second_image.pk), 'items-1-ORDER': '0',
        }
        self.assertRedirects(self.client.post(url, data), url)
        self.assertEqual(
            list(WeeklyLookItem.objects.values_list('catalog_image_id', flat=True)),
            [second_image.pk, first_image.pk],
        )
        self.assertEqual(
            [item.catalog_image_id for item in self.client.get(reverse('shop:home')).context['weekly_look_items']],
            [second_image.pk, first_image.pk],
        )

    def test_order_finance_and_lead_milestones_are_saved(self):
        order = Order.objects.create(
            customer_name='Анна', customer_email='anna@example.com',
            customer_phone='+79990000000', delivery_address='Москва',
            estimated_total=Decimal('12500'), status=Order.Status.PURCHASED,
        )
        self.assertEqual(order.actual_revenue, Decimal('12500'))
        self.assertIsNotNone(order.paid_at)
        self.assertIsNone(order.gross_profit)

        lead = ProductRequest.objects.create(
            customer_name='Иван', customer_email='ivan@example.com',
            customer_phone='+79991111111', lead_status=ProductRequest.LeadStatus.OFFER,
        )
        self.assertIsNotNone(lead.contacted_at)
        self.assertIsNotNone(lead.qualified_at)
        self.assertIsNotNone(lead.offered_at)
        self.assertIsNone(lead.ordered_at)
        lead.lead_status = ProductRequest.LeadStatus.ORDER
        lead.save(update_fields=('lead_status',))
        self.assertIsNotNone(lead.ordered_at)

    def test_economics_diagram_shows_expenses_and_loss_when_unprofitable(self):
        result = economics_breakdown(
            Decimal('1000'), Decimal('900'), Decimal('300'),
            Decimal('-200'), True,
        )

        self.assertTrue(result['economics_chart_ready'])
        self.assertEqual(result['economics_chart_label'], 'Все расходы')
        self.assertEqual(result['economics_chart_total'], Decimal('1200'))
        self.assertEqual(result['economics_loss'], Decimal('200'))
        self.assertEqual(
            [row['amount'] for row in result['economics_breakdown']],
            [Decimal('900'), Decimal('300')],
        )

    def test_economics_diagram_does_not_estimate_missing_costs(self):
        result = economics_breakdown(
            Decimal('1000'), None, Decimal('100'), None, False,
        )

        self.assertFalse(result['economics_chart_ready'])
        self.assertEqual(result['economics_breakdown'], [])

    def test_custom_analytics_period_excludes_other_dates(self):
        today_visitor = AnalyticsVisitor.objects.create()
        today_session = AnalyticsSession.objects.create(visitor=today_visitor, source='direct')
        AnalyticsEvent.objects.create(
            visitor=today_visitor, session=today_session,
            event_type='page_view', path='/',
        )
        old_visitor = AnalyticsVisitor.objects.create()
        old_session = AnalyticsSession.objects.create(visitor=old_visitor, source='old')
        old_event = AnalyticsEvent.objects.create(
            visitor=old_visitor, session=old_session,
            event_type='page_view', path='/',
        )
        old_time = timezone.now() - timedelta(days=3)
        AnalyticsSession.objects.filter(pk=old_session.pk).update(
            started_at=old_time, last_seen_at=old_time,
        )
        AnalyticsEvent.objects.filter(pk=old_event.pk).update(created_at=old_time)
        self.client.force_login(self.admin)
        today = timezone.localdate().isoformat()

        response = self.client.get(reverse('shop:control_analytics_behavior'), {
            'date_from': today, 'date_to': today,
        })

        self.assertEqual(response.context['session_count'], 1)
        self.assertTrue(response.context['custom_period'])
        self.assertContains(response, 'Активность по часам')

    def test_new_utm_click_starts_new_attribution_session(self):
        self.client.get('/?utm_source=yandex&utm_campaign=first')
        first = AnalyticsSession.objects.get()

        self.client.get('/?utm_source=vk&utm_campaign=retarget')

        sessions = AnalyticsSession.objects.order_by('started_at')
        self.assertEqual(sessions.count(), 2)
        self.assertEqual(sessions.first().visitor_id, sessions.last().visitor_id)
        self.assertEqual(first.source, 'yandex')
        self.assertEqual(sessions.last().source, 'vk')
        self.assertEqual(sessions.last().campaign, 'retarget')

    def test_control_banner_can_use_catalog_and_uploaded_images(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('shop:control_banners'))
        self.assertContains(response, 'Осень–зима')
        self.assertContains(response, 'Сумки')
        self.assertNotContains(response, 'Мастерство в деталях')
        banner = Banner.objects.get(slot=Banner.Slot.FALL_WINTER)
        catalog_image = self.product.images.get()
        uploaded = SimpleUploadedFile(
            'banner.gif',
            (
                b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
                b'\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00'
                b'\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
            ),
            content_type='image/gif',
        )

        media_root = tempfile.mkdtemp()
        try:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse('shop:control_banner_edit', args=[banner.pk]), {
                        'image_count': '2', 'interval_seconds': '8',
                        'personalize_by_gender': 'on',
                        'images-TOTAL_FORMS': '5', 'images-INITIAL_FORMS': '0',
                        'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '20',
                        'images-0-catalog_image': str(catalog_image.pk),
                        'images-0-audience': BannerImage.Audience.WOMEN,
                        'images-0-position': '0', 'images-0-alt': 'Фото из каталога',
                        'images-1-uploaded_image': uploaded,
                        'images-1-audience': BannerImage.Audience.MEN,
                        'images-1-position': '1', 'images-1-alt': 'Загруженное фото',
                    }
                )

            if response.status_code == 200:
                self.fail(
                    f'Banner form errors: {response.context["form"].errors}; '
                    f'formset errors: {response.context["image_formset"].errors}; '
                    f'non-form: {response.context["image_formset"].non_form_errors()}'
                )
            self.assertRedirects(
                response, reverse('shop:control_banner_edit', args=[banner.pk])
            )
            banner.refresh_from_db()
            self.assertEqual(banner.image_count, 2)
            self.assertEqual(banner.interval_seconds, 8)
            self.assertFalse(banner.autoplay)
            self.assertTrue(banner.personalize_by_gender)
            self.assertEqual(banner.images.count(), 2)
            self.assertEqual(banner.images.first().catalog_image_id, catalog_image.pk)
            self.assertEqual(banner.images.first().audience, BannerImage.Audience.WOMEN)
            self.assertEqual(banner.images.last().audience, BannerImage.Audience.MEN)
            self.assertTrue(banner.images.last().uploaded_image.name)
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

    def test_banner_without_gender_personalization_makes_images_universal(self):
        self.client.force_login(self.admin)
        banner = Banner.objects.get(slot=Banner.Slot.BAGS)
        catalog_image = self.product.images.get()

        response = self.client.post(
            reverse('shop:control_banner_edit', args=[banner.pk]), {
                'image_count': '1', 'interval_seconds': '5',
                'images-TOTAL_FORMS': '1', 'images-INITIAL_FORMS': '0',
                'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '20',
                'images-0-catalog_image': str(catalog_image.pk),
                'images-0-audience': BannerImage.Audience.WOMEN,
                'images-0-ORDER': '0',
            },
        )

        self.assertRedirects(
            response, reverse('shop:control_banner_edit', args=[banner.pk])
        )
        banner.refresh_from_db()
        self.assertFalse(banner.personalize_by_gender)
        self.assertEqual(banner.images.get().audience, BannerImage.Audience.ALL)

    def test_deleting_uploaded_banner_image_removes_file_after_commit(self):
        self.client.force_login(self.admin)
        banner = Banner.objects.get(slot=Banner.Slot.BAGS)
        uploaded = SimpleUploadedFile(
            'obsolete-banner.gif',
            (
                b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00'
                b'\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00'
                b'\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
            ),
            content_type='image/gif',
        )
        media_root = tempfile.mkdtemp()
        try:
            with self.settings(MEDIA_ROOT=media_root):
                image = BannerImage.objects.create(
                    banner=banner, uploaded_image=uploaded, audience=BannerImage.Audience.ALL
                )
                stored_path = Path(image.uploaded_image.path)
                self.assertTrue(stored_path.exists())
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        reverse('shop:control_banner_edit', args=[banner.pk]), {
                            'image_count': '1', 'interval_seconds': '5',
                            'images-TOTAL_FORMS': '1', 'images-INITIAL_FORMS': '1',
                            'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '20',
                            'images-0-id': str(image.pk),
                            'images-0-audience': BannerImage.Audience.ALL,
                            'images-0-ORDER': '0', 'images-0-DELETE': 'on',
                        },
                    )

                self.assertRedirects(
                    response, reverse('shop:control_banner_edit', args=[banner.pk])
                )
                self.assertFalse(BannerImage.objects.filter(pk=image.pk).exists())
                self.assertFalse(stored_path.exists())
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

    def test_media_editors_show_one_new_form_and_hide_technical_fields(self):
        self.client.force_login(self.admin)
        product_editor = self.client.get(
            reverse('shop:control_product_edit', args=[self.product.pk])
        )
        product_formset = product_editor.context['image_formset']

        self.assertEqual(
            product_formset.total_form_count(), product_formset.initial_form_count() + 1
        )
        self.assertContains(product_editor, 'data-media-add')
        self.assertContains(product_editor, 'Добавить ещё фото')
        self.assertContains(product_editor, 'data-media-remove')
        self.assertNotContains(product_editor, 'name="images-0-alt"')
        self.assertNotContains(product_editor, 'name="images-0-tag"')
        self.assertNotContains(product_editor, 'name="images-0-position"')

        banner = Banner.objects.get(slot=Banner.Slot.FALL_WINTER)
        banner_editor = self.client.get(
            reverse('shop:control_banner_edit', args=[banner.pk])
        )
        banner_formset = banner_editor.context['image_formset']
        self.assertEqual(
            banner_formset.total_form_count(), banner_formset.initial_form_count() + 1
        )
        self.assertContains(banner_editor, 'data-banner-audience')
        self.assertContains(banner_editor, 'Учитывать пол посетителя')
        self.assertNotContains(banner_editor, 'name="images-0-alt"')
        self.assertNotContains(banner_editor, 'name="images-0-position"')

    def test_product_photo_order_metadata_and_deletion_are_automatic(self):
        first = self.product.images.get()
        second = ProductImage.objects.create(
            product=self.product, original='https://example.com/second.jpg',
            position=9, tag='technical', alt='Ручной alt',
        )
        data = {
            'images-TOTAL_FORMS': '3', 'images-INITIAL_FORMS': '2',
            'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '10',
            'images-0-id': str(first.pk), 'images-0-ORDER': '1',
            'images-1-id': str(second.pk), 'images-1-ORDER': '0',
            # Пустая строка с ORDER имитирует работу JavaScript в браузере.
            'images-2-ORDER': '2',
        }
        formset = BackofficeProductImageFormSet(
            data, instance=self.product, prefix='images'
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        ordered = list(self.product.images.order_by('position', 'id'))
        self.assertEqual([image.pk for image in ordered], [second.pk, first.pk])
        self.assertEqual([image.position for image in ordered], [0, 1])
        self.assertEqual([image.tag for image in ordered], ['main', 'gallery'])
        self.assertTrue(all(image.alt == self.product.title for image in ordered))
        self.assertEqual(self.product.images.count(), 2)

        delete_data = {
            'images-TOTAL_FORMS': '2', 'images-INITIAL_FORMS': '2',
            'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '10',
            'images-0-id': str(second.pk), 'images-0-ORDER': '0',
            'images-0-DELETE': 'on',
            'images-1-id': str(first.pk), 'images-1-ORDER': '1',
        }
        formset = BackofficeProductImageFormSet(
            delete_data, instance=self.product, prefix='images'
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        first.refresh_from_db()
        self.assertFalse(ProductImage.objects.filter(pk=second.pk).exists())
        self.assertEqual(first.position, 0)
        self.assertEqual(first.tag, 'main')

    def test_catalog_bulk_actions(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('shop:control_products'), {
            'selected': [str(self.product.pk)], 'action': 'hide',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        self.assertContains(response, 'Обновлено товаров: 1')

    def test_main_catalog_bulk_action_cannot_change_collection_product(self):
        collection_product, _ = import_product(product_payload(
            sku='COLLECTION-BULK-GUARD',
            sku_without_size='COLLECTION-BULK-GUARD',
            title='Защищённый товар COLLECTION',
        ))
        collection_product.catalog_scope = Product.CatalogScope.COLLECTION
        collection_product.is_active = True
        collection_product.save(update_fields=('catalog_scope', 'is_active'))
        self.client.force_login(self.admin)

        self.client.post(reverse('shop:control_products'), {
            'selected': [str(collection_product.pk)], 'action': 'hide',
        })

        collection_product.refresh_from_db()
        self.assertTrue(collection_product.is_active)

    def test_collection_control_has_separate_navigation_and_catalog(self):
        collection_product, _ = import_product(product_payload(
            sku='COLLECTION-CONTROL-1', sku_without_size='COLLECTION-CONTROL-1',
            title='Локальная карточка',
        ))
        collection_product.catalog_scope = Product.CatalogScope.COLLECTION
        collection_product.title = 'Локальная карточка'
        collection_product.save(update_fields=('catalog_scope', 'title'))
        self.client.force_login(self.admin)

        collection_page = self.client.get(reverse('shop:control_collection'))
        main_catalog = self.client.get(reverse('shop:control_products'))

        self.assertContains(collection_page, '<span>COLLECTION</span>', html=True)
        self.assertContains(collection_page, 'Локальная карточка')
        self.assertContains(collection_page, 'Массовая публикация')
        self.assertNotContains(main_catalog, 'Локальная карточка')
        self.assertContains(main_catalog, 'Основной каталог')

    def test_duplicate_search_covers_both_catalogs(self):
        collection_product, _ = import_product(product_payload(
            sku='DUPLICATE-COLLECTION', sku_without_size='DUPLICATE-COLLECTION',
            title='Похожая сумка для проверки',
        ))
        collection_product.catalog_scope = Product.CatalogScope.COLLECTION
        collection_product.title = 'Похожая сумка для проверки'
        collection_product.save(update_fields=('catalog_scope', 'title'))
        self.product.title = 'Похожая сумка из магазина'
        self.product.save(update_fields=('title',))
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('shop:control_product_duplicates'), {'q': 'Похожая сумка'},
        )

        self.assertEqual(response.status_code, 200)
        catalogs = {item['catalog'] for item in response.json()['results']}
        self.assertEqual(catalogs, {'Основной каталог', 'FINDER COLLECTION'})

    def test_collection_bulk_upload_creates_hidden_batch_drafts(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('shop:control_collection_bulk'), {
            'batch-name': 'Поступление сентября',
            'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '20',
            'items-0-title': 'Первая локальная сумка',
            'items-0-brand': 'Hermès', 'items-0-article': 'LOCAL-001',
            'items-0-description': 'Уже в Москве',
            'items-1-title': 'Вторая локальная сумка',
            'items-1-brand': 'Hermès', 'items-1-article': 'LOCAL-002',
            'items-1-description': '',
        })

        batch = CollectionBatch.objects.get(name='Поступление сентября')
        products = list(batch.products.order_by('pk'))
        self.assertEqual(len(products), 2)
        self.assertTrue(all(
            item.catalog_scope == Product.CatalogScope.COLLECTION
            and not item.is_active and item.in_stock_ecom
            for item in products
        ))
        self.assertRedirects(
            response,
            reverse('shop:control_product_edit', args=[products[0].pk]),
        )
        storefront = self.client.get(reverse('shop:collection'))
        self.assertNotContains(storefront, 'Первая локальная сумка')

    def test_collection_bulk_upload_saves_multiple_optimized_photos(self):
        media_root = tempfile.mkdtemp()
        self.client.force_login(self.admin)
        try:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse('shop:control_collection_bulk'), {
                        'batch-name': 'Партия с фотографиями',
                        'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
                        'items-MIN_NUM_FORMS': '1', 'items-MAX_NUM_FORMS': '20',
                        'items-0-title': 'Товар с двумя фотографиями',
                        'items-0-brand': 'Hermès',
                        'items-0-article': 'LOCAL-PHOTOS-001',
                        'items-0-description': 'Описание',
                        'items-0-images': [
                            image_upload('collection-front.jpg', size=(800, 600)),
                            image_upload('collection-back.jpg', size=(600, 800)),
                        ],
                    },
                )

                product = Product.objects.get(sku_without_size='LOCAL-PHOTOS-001')
                images = list(product.images.order_by('position'))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(images), 2)
                self.assertEqual([item.tag for item in images], ['main', 'gallery'])
                self.assertTrue(all(item.local_image.name.endswith('.webp') for item in images))
                self.assertTrue(all(item.preview_image.name.endswith('.webp') for item in images))
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

    def test_collection_batch_publishes_only_ready_products(self):
        batch = CollectionBatch.objects.create(
            name='Готовность публикации', created_by=self.admin,
        )
        ready, _ = import_product(product_payload(
            sku='COLLECTION-READY', sku_without_size='COLLECTION-READY',
            title='Готовый локальный товар',
        ))
        ready.catalog_scope = Product.CatalogScope.COLLECTION
        ready.collection_batch = batch
        ready.collection_reviewed_at = timezone.now()
        ready.is_active = False
        ready.save(update_fields=(
            'catalog_scope', 'collection_batch', 'collection_reviewed_at', 'is_active',
        ))
        draft = Product.objects.create(
            sku='collection:hermes:NOT-READY', sku_without_size='NOT-READY',
            brand='hermes', title='Незавершённый локальный товар',
            catalog_scope=Product.CatalogScope.COLLECTION,
            collection_batch=batch, is_active=False, in_stock_ecom=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('shop:control_collection_batch', args=[batch.pk]), follow=True,
        )

        ready.refresh_from_db()
        draft.refresh_from_db()
        self.assertTrue(ready.is_active)
        self.assertFalse(draft.is_active)
        self.assertContains(response, 'Опубликовано готовых карточек: 1')
        self.assertContains(response, 'Остались незавершённые карточки: 1')

    def test_collection_batch_full_editor_moves_through_products(self):
        batch = CollectionBatch.objects.create(
            name='Последовательное редактирование', created_by=self.admin,
        )
        first = self.product
        second, _ = import_product(product_payload(
            sku='COLLECTION-NEXT-2', sku_without_size='COLLECTION-NEXT-2',
            images=[{
                'tag': 'main', 'position': 0,
                'original': 'https://example.com/collection-next.jpg',
            }],
            colors=[], sizes=[], i18n={},
        ))
        Product.objects.filter(pk__in=(first.pk, second.pk)).update(
            catalog_scope=Product.CatalogScope.COLLECTION,
            collection_batch=batch,
            collection_reviewed_at=None,
            is_active=False,
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.client.force_login(self.admin)

        def editor_data(product):
            category = CatalogCategory.objects.get(key=product.category_path_key)
            brand = CatalogBrand.objects.get(slug=product.brand)
            images = list(product.images.order_by('position', 'pk'))
            data = {
                'title': product.title, 'brand': brand.name,
                'sku': product.sku, 'sku_without_size': product.sku_without_size,
                'source_url': product.source_url, 'price': str(product.price),
                'currency': product.currency, 'pricing_mode': product.pricing_mode,
                'fixed_price_rub': product.fixed_price_rub or '',
                'current_color': product.current_color,
                'color_group_reference': product.color_group,
                'current_size': product.current_size, 'size_profile': '',
                'gender_choice': 'women',
                'materials': list(CatalogMaterial.objects.filter(
                    name__in=product.material,
                ).values_list('pk', flat=True)),
                'category_reference': f'{category.department_name} / {category.name}',
                'collection': product.collection, 'description': product.description,
                'dimensions': product.dimensions, 'made_in': product.made_in,
                'in_stock_ecom': 'on',
                'images-TOTAL_FORMS': str(len(images)),
                'images-INITIAL_FORMS': str(len(images)),
                'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '10',
            }
            for index, image in enumerate(images):
                data[f'images-{index}-id'] = str(image.pk)
                data[f'images-{index}-ORDER'] = str(index)
            return data

        first_response = self.client.post(
            reverse('shop:control_product_edit', args=[first.pk]),
            editor_data(first),
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.collection_reviewed_at)
        self.assertRedirects(
            first_response,
            reverse('shop:control_product_edit', args=[second.pk]),
        )

        second_response = self.client.post(
            reverse('shop:control_product_edit', args=[second.pk]),
            editor_data(second),
        )
        second.refresh_from_db()
        self.assertIsNotNone(second.collection_reviewed_at)
        self.assertRedirects(
            second_response,
            reverse('shop:control_collection_batch', args=[batch.pk]),
        )

    def test_control_creates_product_manually(self):
        self.client.force_login(self.admin)
        material = CatalogMaterial.objects.get(name='кожа')
        category = CatalogCategory.objects.get(key=self.product.category_path_key)
        profile = SizeProfile.objects.get(slug='women-clothing')
        sizes = list(CatalogSize.objects.filter(profile=profile, label__in=('S', 'M')))
        response = self.client.post(reverse('shop:control_product_create'), {
            'title': 'Новая ручная карточка',
            'brand': 'Hermès',
            'sku': 'MANUAL-001',
            'sku_without_size': 'MANUAL-001',
            'product_code': 'BAGS',
            'source_url': '',
            'price': '1500',
            'currency': 'CNY',
            'pricing_mode': 'fixed_rub',
            'fixed_price_rub': '199000',
            'current_color': self.product.current_color,
            'current_size': 'SANS_TAILLE',
            'size_profile': str(profile.pk),
            'selected_sizes': [str(size.pk) for size in sizes],
            'gender': '["women"]',
            'materials': [str(material.pk)],
            'category_reference': f'{category.department_name} / {category.name}',
            'collection': 'new',
            'description': 'Описание',
            'dimensions': '',
            'made_in': 'Франция',
            'in_stock_ecom': 'on',
            'is_active': 'on',
            'images-TOTAL_FORMS': '1',
            'images-INITIAL_FORMS': '0',
            'images-MIN_NUM_FORMS': '0',
            'images-MAX_NUM_FORMS': '1000',
            'images-0-position': '0',
            'images-0-tag': '',
            'images-0-alt': '',
            'images-0-original': '',
        })

        product = Product.objects.get(sku='MANUAL-001')
        self.assertRedirects(
            response, reverse('shop:control_product_edit', args=[product.pk])
        )
        self.assertEqual(product.title, 'Новая ручная карточка')
        self.assertTrue(product.is_active)
        self.assertTrue(product.in_stock_ecom)
        self.assertEqual(product.display_price, 199000)
        self.assertEqual(product.material, ['кожа'])
        self.assertEqual(set(product.size_variants.values_list('size', flat=True)), {'S', 'M'})

    def test_control_adds_dictionary_values_and_editor_has_search_suggestions(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('shop:control_dictionaries'), {
            'action': 'brand', 'brand-name': 'Chanel', 'brand-slug': 'chanel',
        })
        self.assertRedirects(response, reverse('shop:control_dictionaries'))
        self.assertTrue(CatalogBrand.objects.filter(slug='chanel', name='Chanel').exists())
        response = self.client.post(reverse('shop:control_dictionaries'), {
            'action': 'material', 'material-name': 'Кашемир', 'material-slug': 'cashmere',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CatalogMaterial.objects.filter(name='Кашемир').exists())
        editor = self.client.get(reverse('shop:control_product_create'))
        self.assertContains(editor, 'list="brand-options"')
        self.assertContains(editor, 'list="color-options"')
        self.assertContains(editor, 'Чёрный')
        self.assertContains(editor, 'data-multiple-search="materials"')
        self.assertContains(editor, 'data-size-buttons')

    def test_fixed_price_survives_pricing_changes_and_reimport(self):
        self.product.pricing_mode = Product.PricingMode.FIXED_RUB
        self.product.fixed_price_rub = 250000
        self.product.save(update_fields=('pricing_mode', 'fixed_price_rub'))
        PricingRule.objects.create(
            name='Большая наценка', adjustment_type='fixed', value=999999, position=99
        )
        self.assertEqual(self.product.display_price, 250000)
        import_product(product_payload(price=99999))
        self.product.refresh_from_db()
        self.assertEqual(self.product.pricing_mode, Product.PricingMode.FIXED_RUB)
        self.assertEqual(self.product.fixed_price_rub, 250000)
        self.assertEqual(self.product.display_price, 250000)

    def test_control_imports_json(self):
        self.product.delete()
        self.client.force_login(self.admin)
        uploaded = SimpleUploadedFile(
            'product.json', json.dumps(product_payload(), ensure_ascii=False).encode(),
            content_type='application/json',
        )
        response = self.client.post(reverse('shop:control_import'), {
            'import_type': 'json', 'json-json_files': uploaded,
            'json-replace_relations': 'on',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(sku='H-001-BLACK').exists())
        self.assertContains(response, 'Импорт завершён')

    def test_control_updates_order_and_request_workflows(self):
        order = Order.objects.create(
            customer_name='Анна', customer_email='anna@example.com',
            customer_phone='+79990000000', delivery_address='Москва',
            estimated_total=1000, currency='RUB',
        )
        product_request = ProductRequest.objects.create(
            customer_name='Иван', customer_email='ivan@example.com',
            customer_phone='+79991111111',
        )
        self.client.force_login(self.admin)
        order_response = self.client.post(
            reverse('shop:control_order_detail', args=[order.pk]),
            {'status': Order.Status.PROCESSING, 'manager_comment': 'Связались'},
        )
        request_response = self.client.post(
            reverse('shop:control_request_detail', args=[product_request.pk]),
            {'status': ProductRequest.Status.CONFIRMATION, 'manager_comment': 'Подобрано'},
        )
        self.assertEqual(order_response.status_code, 302)
        self.assertEqual(request_response.status_code, 302)
        order.refresh_from_db()
        product_request.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)
        self.assertEqual(order.manager_comment, 'Связались')
        self.assertEqual(product_request.status, ProductRequest.Status.CONFIRMATION)
        self.assertEqual(product_request.manager_comment, 'Подобрано')

    def test_banner_editor_is_lightweight_and_searches_catalog_images(self):
        self.client.force_login(self.admin)
        banner, _ = Banner.objects.get_or_create(slot=Banner.Slot.FALL_WINTER)
        image = self.product.images.first()

        editor = self.client.get(reverse('shop:control_banner_edit', args=[banner.pk]))
        search = self.client.get(reverse('shop:control_banner_image_search'), {
            'q': self.product.sku_without_size,
        })

        self.assertEqual(editor.status_code, 200)
        self.assertLess(len(editor.content), 250_000)
        self.assertContains(editor, 'data-banner-picker')
        self.assertNotContains(editor, f'<option value="{image.pk}">')
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()['results'][0]['id'], image.pk)
        self.assertEqual(search.json()['results'][0]['article'], self.product.sku_without_size)

    def test_product_editor_uses_clear_fields_and_one_expandable_image_form(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('shop:control_product_create'))
        form = response.context['form']

        self.assertEqual(response.status_code, 200)
        self.assertTrue(form.fields['sku_without_size'].required)
        self.assertEqual(form.fields['sku_without_size'].label, 'Артикул')
        self.assertEqual(
            [value for value, _ in form.fields['gender_choice'].choices],
            ['women', 'men', 'unisex'],
        )
        self.assertEqual(
            {value for value, _ in form.fields['currency'].widget.choices},
            {'CNY', 'USD', 'EUR', 'RUB'},
        )
        self.assertEqual(response.context['image_formset'].total_form_count(), 1)
        self.assertContains(response, 'data-media-limit="10"')
        self.assertContains(response, 'Добавить ещё фото')
        self.assertNotContains(response, '<span>SKU</span>')
        self.assertNotContains(response, '<span>Код категории</span>')
        self.assertContains(response, 'Основной цвет')

    def test_manual_product_generates_internal_sku_from_required_article(self):
        self.client.force_login(self.admin)
        category = CatalogCategory.objects.get(key=self.product.category_path_key)
        response = self.client.post(reverse('shop:control_product_create'), {
            'title': 'Ручной товар', 'brand': 'Hermès',
            'sku_without_size': 'ARTICLE-2026', 'source_url': '',
            'price': '1000', 'currency': 'CNY', 'pricing_mode': 'calculated',
            'fixed_price_rub': '', 'current_color': self.product.current_color,
            'color_group_reference': 'black', 'current_size': 'SANS_TAILLE',
            'size_profile': '', 'gender_choice': 'unisex', 'materials': [],
            'category_reference': f'{category.department_name} / {category.name}',
            'collection': '', 'description': '', 'dimensions': '', 'made_in': '',
            'in_stock_ecom': 'on', 'is_active': 'on',
            'images-TOTAL_FORMS': '10', 'images-INITIAL_FORMS': '0',
            'images-MIN_NUM_FORMS': '0', 'images-MAX_NUM_FORMS': '10',
            **{
                f'images-{index}-position': str(index)
                for index in range(10)
            },
        })

        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku_without_size='ARTICLE-2026')
        self.assertTrue(product.sku.startswith('manual:hermes:ARTICLE-2026'))
        self.assertEqual(product.product_code, category.key)
        self.assertEqual(product.gender, ['women', 'men'])

    def test_brand_image_can_be_edited_and_appears_in_boutiques(self):
        self.client.force_login(self.admin)
        brand = CatalogBrand.objects.get(slug='hermes')
        upload = SimpleUploadedFile(
            'brand.gif', b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;',
            content_type='image/gif',
        )
        media_root = tempfile.mkdtemp()
        try:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse('shop:control_brand_edit', args=[brand.pk]),
                    {'name': 'Hermès', 'slug': 'hermes', 'is_active': 'on', 'image': upload},
                )
                self.assertEqual(response.status_code, 302)
                brand.refresh_from_db()
                boutiques = self.client.get(reverse('shop:stores'))
                self.assertContains(boutiques, brand.image.url)
        finally:
            shutil.rmtree(media_root, ignore_errors=True)

    def test_customer_page_searches_personal_data_and_shows_history(self):
        customer = get_user_model().objects.create_user(
            username='buyer@example.com', email='buyer@example.com',
            first_name='Анна', last_name='Иванова', password='password',
        )
        CustomerProfile.objects.create(
            user=customer, phone='+79991234567', telegram='@anna_shop',
            delivery_address='Москва, Тверская улица',
        )
        Order.objects.create(
            user=customer, customer_name='Анна Иванова', customer_email=customer.email,
            customer_phone='+79991234567', delivery_address='Москва', currency='RUB',
        )
        Favorite.objects.create(user=customer, product=self.product)
        CartItem.objects.create(user=customer, product=self.product, quantity=2)
        self.client.force_login(self.admin)

        users = self.client.get(reverse('shop:control_users'), {'q': 'Тверская'})
        detail = self.client.get(reverse('shop:control_user_detail', args=[customer.pk]))

        self.assertContains(users, 'Анна Иванова')
        self.assertContains(detail, '+79991234567')
        self.assertContains(detail, '@anna_shop')
        self.assertContains(detail, self.product.title)
        self.assertContains(detail, 'Корзина (2)')
        self.assertContains(detail, f'Артикул: {self.product.article} · 2 шт.')
        self.assertContains(detail, 'ORD-')


class StorefrontTests(TestCase):
    def setUp(self):
        self.product, _ = import_product(product_payload())

    def test_main_pages_render(self):
        urls = [
            reverse('shop:home'), reverse('shop:catalog'), reverse('shop:hermes_catalog'),
            reverse('shop:collection'),
            reverse('shop:stores'), self.product.get_absolute_url(), reverse('shop:cart'),
            reverse('shop:favorites'),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_header_exposes_server_backed_things_panel(self):
        response = self.client.get(reverse('shop:home'))

        self.assertContains(response, 'data-things-panel')
        self.assertContains(response, 'data-things-tab="cart"')
        self.assertContains(response, 'data-things-tab="favorites"')
        self.assertContains(response, f'data-endpoint="{reverse("shop:things_panel")}"')

    def test_things_panel_renders_real_cart_and_favorites(self):
        self.client.post(reverse('shop:cart_add', args=[self.product.pk]))
        self.client.post(reverse('shop:favorite_toggle', args=[self.product.pk]))

        response = self.client.get(reverse('shop:things_panel'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-things-content="cart"')
        self.assertContains(response, 'data-things-content="favorites"')
        self.assertContains(response, f'action="{reverse("shop:cart_remove", args=[self.product.pk])}"')
        self.assertContains(response, f'action="{reverse("shop:favorite_toggle", args=[self.product.pk])}"')

    def test_product_cards_hide_quick_view_but_endpoint_remains_compatible(self):
        catalog = self.client.get(reverse('shop:catalog'))
        quick_url = reverse('shop:product_quick_view', args=[self.product.pk])

        self.assertNotContains(catalog, 'data-quick-view-url')
        self.assertNotContains(catalog, 'data-quick-view-dialog')
        self.assertNotContains(catalog, 'announcement-bar')
        self.assertNotContains(catalog, 'data-favorite-link')
        self.assertContains(catalog, 'data-things-tab="favorites"')
        quick = self.client.get(quick_url)
        self.assertEqual(quick.status_code, 200)
        self.assertContains(quick, 'data-quick-view-product')
        self.assertContains(quick, f'action="{reverse("shop:cart_add", args=[self.product.pk])}"')
        self.assertContains(quick, self.product.title)

    def test_healthcheck_is_lightweight_and_does_not_create_analytics(self):
        response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(AnalyticsVisitor.objects.count(), 0)
        self.assertEqual(AnalyticsSession.objects.count(), 0)

    def test_analytics_ignores_non_html_missing_pages_and_known_scanners(self):
        for path, agent in (
            ('/robots.txt', 'Mozilla/5.0'),
            ('/favicon.ico', 'Mozilla/5.0'),
            ('/.aws/credentials', 'Mozilla/5.0'),
            ('/', 'Googlebot/2.1'),
            ('/', 'Mozilla/5.0 SafeLine-CE/v9'),
        ):
            with self.subTest(path=path, agent=agent):
                self.client.get(path, HTTP_USER_AGENT=agent)
        self.assertFalse(AnalyticsVisitor.objects.exists())
        self.assertFalse(AnalyticsSession.objects.exists())

        response = self.client.get('/', HTTP_USER_AGENT='Mozilla/5.0 (iPhone; Mobile)')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AnalyticsVisitor.objects.count(), 1)
        self.assertIn('aurene_visitor', response.cookies)

    def test_mobile_menu_contains_only_five_category_links(self):
        from html.parser import HTMLParser

        class MenuLinks(HTMLParser):
            def __init__(self):
                super().__init__()
                self.inside = False
                self.links = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'nav' and 'data-main-nav' in attrs:
                    self.inside = True
                if self.inside and tag == 'a':
                    self.links.append(attrs.get('href', ''))

            def handle_endtag(self, tag):
                if tag == 'nav' and self.inside:
                    self.inside = False

        menu = MenuLinks()
        menu.feed(self.client.get(reverse('shop:home')).content.decode())
        self.assertEqual(len(menu.links), 6)  # Collection остаётся только на desktop.
        self.assertEqual(menu.links[:4], [
            reverse('shop:catalog') + '?gender=men',
            reverse('shop:catalog') + '?gender=women',
            reverse('shop:catalog') + '?category=accessories',
            reverse('shop:catalog') + '?collection=new',
        ])
        self.assertEqual(menu.links[-1], reverse('shop:product_request_create'))
        css = (Path(__file__).resolve().parent.parent / 'static/shop/css/finder-theme.css').read_text(encoding='utf-8')
        self.assertIn('.main-nav>.nav-collection-link{display:none}', css)
        self.assertIn('.product-card .product-image-wrap{border:1px solid var(--finder-line);', css)
        self.assertIn('.product-card .product-image-link{padding:0;', css)
        self.assertIn('.product-image-slide{object-fit:contain;object-position:50% 50%}', css)

    def test_desktop_header_order_and_plain_links_close_category_menu(self):
        root = Path(__file__).resolve().parent.parent
        css = (root / 'static/shop/css/finder-theme.css').read_text(encoding='utf-8')
        javascript = (root / 'static/shop/js/store.js').read_text(encoding='utf-8')
        self.assertIn('.header-actions .cart-link{order:2}', css)
        self.assertIn('.account-menu-shell{position:relative;display:grid;order:3;', css)
        self.assertIn("mainNav?.querySelectorAll('a:not([data-mega-trigger])')", javascript)
        self.assertIn("link.addEventListener('mouseenter', closeMegaMenu)", javascript)
        self.assertIn("link.addEventListener('focus', closeMegaMenu)", javascript)

    def test_mobile_navigation_fills_screen_and_centers_framed_order_link(self):
        css = (Path(__file__).resolve().parent.parent / 'static/shop/css/finder-theme.css').read_text(encoding='utf-8')
        self.assertIn(
            '.main-nav{align-content:start;justify-content:space-between;align-items:stretch;gap:clamp(8px,1.5dvh,18px);padding:28px 20px',
            css,
        )
        self.assertIn(
            '.main-nav>a:not(.nav-collection-link){width:100%;min-height:clamp(68px,10dvh,96px);margin:0;padding:8px 4px;border:0;justify-content:center;text-align:center;font:500 clamp(38px,13.5vw,56px)',
            css,
        )
        self.assertIn(
            '.main-nav>a.nav-order-link:not(.nav-collection-link){width:min(100%,340px);min-height:72px;margin:0 auto;padding:10px 14px;border:1px solid var(--finder-ink);',
            css,
        )
        self.assertNotIn('.main-nav{align-content:center;justify-content:safe center;', css)

    def test_analytics_endpoint_rejects_cross_site_posts(self):
        browser = Client(enforce_csrf_checks=True)
        page = browser.get('/', HTTP_USER_AGENT='Mozilla/5.0 (iPhone; Mobile)')
        url = reverse('shop:analytics_event')
        event = json.dumps({'type': 'page_view', 'path': '/'})
        rejected = browser.post(url, event, content_type='application/json')
        self.assertEqual(rejected.status_code, 403)
        self.assertFalse(AnalyticsEvent.objects.exists())
        accepted = browser.post(
            url, event, content_type='application/json',
            HTTP_X_CSRFTOKEN=page.cookies['csrftoken'].value,
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(AnalyticsEvent.objects.count(), 1)

    def test_unconfirmed_server_only_analytics_is_excluded_from_reports(self):
        visitor = AnalyticsVisitor.objects.create()
        session = AnalyticsSession.objects.create(visitor=visitor)
        AnalyticsEvent.objects.create(
            visitor=visitor, session=session, event_type='cart_view', path='/cart/',
        )
        self.assertFalse(qualified_analytics_sessions(AnalyticsSession.objects.all()).exists())
        AnalyticsEvent.objects.create(
            visitor=visitor, session=session, event_type='page_view', path='/cart/',
        )
        self.assertEqual(qualified_analytics_sessions(AnalyticsSession.objects.all()).count(), 1)

    def test_finder_collection_is_separate_from_main_storefront(self):
        collection_product, _ = import_product(product_payload(
            sku='FINDER-COLLECTION-001', sku_without_size='FINDER-COLLECTION-001',
            title='Товар только для FINDER COLLECTION',
            images=[{
                'tag': 'main', 'position': 0,
                'original': 'https://example.com/collection-only.jpg',
            }],
            colors=[], sizes=[], i18n={},
        ))
        collection_product.catalog_scope = Product.CatalogScope.COLLECTION
        collection_product.save(update_fields=('catalog_scope',))

        collection = self.client.get(reverse('shop:collection'))
        filtered_collection = self.client.get(
            reverse('shop:collection'), {'gender': 'women'},
        )
        catalog = self.client.get(reverse('shop:catalog'))
        boutique = self.client.get(reverse('shop:hermes_catalog'))
        home = self.client.get(reverse('shop:home'))
        detail = self.client.get(collection_product.get_absolute_url())

        self.assertContains(collection, 'FINDER COLLECTION')
        self.assertContains(collection, 'Найдено. Отобрано. Уже здесь.')
        self.assertContains(collection, 'которые уже находятся в России')
        self.assertContains(collection, collection_product.title)
        self.assertNotContains(collection, self.product.title)
        self.assertEqual(filtered_collection.context['catalog_heading'], 'FINDER COLLECTION')
        for response in (catalog, boutique, home):
            self.assertNotContains(response, collection_product.title)
        self.assertNotContains(catalog, collection_product.title)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, f'href="{reverse("shop:collection")}"')
        self.assertContains(detail, '>FINDER COLLECTION</a>')
        self.assertContains(detail, 'nav-collection-link active')

    def test_collection_header_link_is_immediately_before_order(self):
        response = self.client.get(reverse('shop:home'))
        html = response.content.decode()
        header = html[html.index('<header'):html.index('</header>')]
        collection_link = f'href="{reverse("shop:collection")}"'
        request_link = f'href="{reverse("shop:product_request_create")}"'

        collection_position = header.index(collection_link)
        request_position = header.index(request_link)
        self.assertLess(collection_position, request_position)
        self.assertEqual(
            header[collection_position:request_position].count('<a '), 1,
        )
        self.assertContains(response, '>Collection</a>')

    def test_mobile_header_and_profile_expose_requested_navigation(self):
        home = self.client.get(reverse('shop:home'))
        header = home.content.decode().split('</header>', 1)[0]

        self.assertNotIn('mobile-nav-search', header)
        self.assertLess(header.index('search-button'), header.index('cart-link'))
        self.assertLess(header.index('cart-link'), header.index('account-link'))
        self.assertNotIn('favorite-link', header)
        self.assertContains(home, 'data-things-tab="favorites"')

        user = get_user_model().objects.create_user(
            username='profile-favorites@example.com', password='SecurePass-2026',
        )
        self.client.force_login(user)
        account = self.client.get(reverse('shop:account'))
        self.assertNotContains(account, f'href="{reverse("shop:favorites")}">Избранное')
        self.assertContains(account, f'href="{reverse("shop:cart")}">Корзина')

    def test_home_has_product_carousel_and_interactive_weekly_look(self):
        response = self.client.get(reverse('shop:home'))

        self.assertContains(response, 'data-product-carousel>', count=2)
        self.assertContains(response, 'data-product-carousel-track', count=2)
        self.assertContains(response, 'data-weekly-look')
        selection_count = len(response.context['selection_products'])
        self.assertContains(response, 'weekly-look-hotspot', count=selection_count)
        self.assertContains(response, 'data-look-index=', count=selection_count * 2)
        self.assertContains(response, 'Образ недели')
        self.assertContains(response, 'Готовая подборка вещей, которые работают вместе.')
        self.assertContains(response, 'FINDER Selection')

    def test_catalog_paginates_eighteen_products_per_page(self):
        response = self.client.get(reverse('shop:catalog'))

        self.assertEqual(response.context['page_obj'].paginator.per_page, 18)

    def test_catalog_load_more_preserves_filters_and_has_fallback_link(self):
        for index in range(18):
            import_product(product_payload(
                sku=f'LOAD-MORE-{index}', sku_without_size=f'LOAD-MORE-{index}',
                title=f'Дополнительный товар {index}', colors=[], sizes=[], i18n={},
            ))
        first = self.client.get(reverse('shop:catalog'), {'ordering': 'new'})
        self.assertContains(first, 'data-catalog-grid')
        self.assertContains(first, 'data-catalog-load-more')
        self.assertContains(first, 'Показать ещё')
        self.assertContains(first, 'ordering=new&amp;page=2')
        second = self.client.get(reverse('shop:catalog'), {'ordering': 'new', 'page': '2'})
        self.assertEqual(second.context['page_obj'].number, 2)
        self.assertNotContains(second, 'data-catalog-load-more')
        first_ids = {item.pk for item in first.context['page_obj'].object_list}
        second_ids = {item.pk for item in second.context['page_obj'].object_list}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 19)

    def test_related_products_use_mobile_carousel_contract(self):
        related, _ = import_product(product_payload(
            sku='RELATED-CAROUSEL-001', sku_without_size='RELATED-CAROUSEL-001',
            title='Похожий товар для карусели', colors=[], sizes=[], i18n={},
        ))
        related.category_path_key = self.product.category_path_key
        related.save(update_fields=('category_path_key',))

        response = self.client.get(self.product.get_absolute_url())

        self.assertContains(response, 'class="section related-products"')
        self.assertContains(response, 'data-product-carousel>')
        self.assertContains(response, 'data-product-carousel-track')
        self.assertContains(response, related.title)

    def test_catalog_cards_and_product_page_use_high_quality_image_urls(self):
        image = self.product.images.get()
        image.preview_image = 'products/previews/product-small.webp'
        image.gallery_image = 'products/gallery/product-large.webp'
        image.save(update_fields=('preview_image', 'gallery_image'))

        catalog = self.client.get(reverse('shop:hermes_catalog'))
        detail = self.client.get(self.product.get_absolute_url())

        self.assertContains(catalog, 'src="/media/products/gallery/product-large.webp"')
        self.assertNotContains(catalog, 'src="/media/products/previews/product-small.webp"')
        self.assertContains(detail, 'data-image="/media/products/gallery/product-large.webp"')
        self.assertContains(
            detail,
            'src="/media/products/gallery/product-large.webp" '
            f'alt="{self.product.title} — фото 1" fetchpriority="high"',
        )

    def test_out_of_stock_product_is_hidden_from_public_storefront(self):
        unavailable, _ = import_product(product_payload(
            sku='OUT-OF-STOCK-001', sku_without_size='OUT-OF-STOCK-001',
            title='Недоступный товар', in_stock_ecom=False, colors=[], sizes=[],
            i18n={},
        ))
        self.assertTrue(unavailable.is_active)
        self.assertFalse(unavailable.in_stock_ecom)

        catalog = self.client.get(reverse('shop:hermes_catalog'))
        detail = self.client.get(unavailable.get_absolute_url())
        favorites = self.client.post(
            reverse('shop:favorite_toggle', args=[unavailable.pk])
        )

        self.assertNotContains(catalog, unavailable.title)
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(favorites.status_code, 404)
        self.assertTrue(Product.objects.filter(pk=unavailable.pk).exists())

    def test_catalog_snapshot_is_invalidated_when_product_visibility_changes(self):
        catalog_url = reverse('shop:hermes_catalog')
        self.assertContains(self.client.get(catalog_url), self.product.title)

        self.product.is_active = False
        self.product.save(update_fields=('is_active', 'updated_at'))

        self.assertNotContains(self.client.get(catalog_url), self.product.title)

    def test_repeated_page_view_does_not_touch_recent_analytics_session(self):
        self.client.get(reverse('shop:home'))
        analytics_session = AnalyticsSession.objects.get()
        previous_last_seen = analytics_session.last_seen_at

        self.client.get(reverse('shop:catalog'))

        analytics_session.refresh_from_db()
        self.assertEqual(analytics_session.last_seen_at, previous_last_seen)

    def test_product_stays_visible_when_another_size_is_available(self):
        product, _ = import_product(product_payload(
            sku='SIZED-CURRENT-M', sku_without_size='SIZED-MODEL',
            title='Товар с доступным размером', current_size='M',
            in_stock_ecom=False, colors=[], i18n={},
            sizes=[
                {'sku': 'SIZED-CURRENT-M', 'size': 'M', 'price': 18800,
                 'in_stock_ecom': False},
                {'sku': 'SIZED-AVAILABLE-L', 'size': 'L', 'price': 18800,
                 'in_stock_ecom': True},
            ],
        ))

        catalog = self.client.get(reverse('shop:hermes_catalog'))
        detail = self.client.get(product.get_absolute_url())
        favorite = self.client.post(
            reverse('shop:favorite_toggle', args=[product.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        cart_add = self.client.post(
            reverse('shop:cart_add', args=[product.pk]),
            {'size_sku': 'SIZED-AVAILABLE-L'},
        )
        cart = self.client.get(reverse('shop:cart'))

        self.assertContains(catalog, product.title)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Добавить в корзину')
        self.assertContains(detail, 'data-size-option', count=1)
        self.assertContains(detail, 'value="SIZED-AVAILABLE-L"')
        self.assertContains(detail, '<span>L</span>', html=True)
        self.assertNotContains(detail, 'value="SIZED-CURRENT-M"')
        self.assertEqual(favorite.status_code, 200)
        self.assertEqual(cart_add.status_code, 302)
        self.assertEqual(self.client.session['cart'][str(product.pk)], 1)
        self.assertEqual(cart.context['cart_items'][0]['selected_size'], 'L')
        self.assertEqual(cart.context['cart_items'][0]['selected_sku'], 'SIZED-AVAILABLE-L')
        self.assertContains(cart, 'L')

        invalid_size = self.client.post(
            reverse('shop:cart_add', args=[product.pk]),
            {'size_sku': 'SIZED-CURRENT-M'},
        )
        self.assertRedirects(invalid_size, product.get_absolute_url())
        self.assertEqual(self.client.session['cart'][str(product.pk)], 1)
        self.assertEqual(
            self.client.session['cart_options'][str(product.pk)]['size_sku'],
            'SIZED-AVAILABLE-L',
        )

    def test_current_available_sku_is_visible_despite_unavailable_other_sizes(self):
        product, _ = import_product(product_payload(
            sku='SIZED-NONE-S', sku_without_size='SIZED-NONE',
            title='Товар без доступных размеров', current_size='S',
            in_stock_ecom=True, colors=[], i18n={},
            sizes=[
                {'sku': 'SIZED-NONE-S', 'size': 'S', 'price': 18800,
                 'in_stock_ecom': False},
                {'sku': 'SIZED-NONE-M', 'size': 'M', 'price': 18800,
                 'in_stock_ecom': False},
            ],
        ))

        self.assertContains(self.client.get(reverse('shop:hermes_catalog')), product.title)
        self.assertEqual(self.client.get(product.get_absolute_url()).status_code, 200)

    def test_stores_page_has_local_hermes_card(self):
        response = self.client.get(reverse('shop:stores'))
        self.assertContains(response, '<h1>Бутики</h1>', html=True)
        self.assertNotContains(response, '>Магазины<')
        self.assertContains(response, 'Hermès')
        self.assertContains(response, 'Париж, Франция')
        self.assertContains(response, 'shop/brands/hermes/hermes-logo.svg')
        self.assertContains(response, f'href="{reverse("shop:hermes_catalog")}"')
        self.assertContains(response, 'aria-current="page"')
        self.assertNotContains(response, 'hermes.com')

    def test_footer_has_no_removed_links_or_subscription(self):
        response = self.client.get(reverse('shop:home'))
        self.assertNotContains(response, 'Карьера')
        self.assertNotContains(response, 'Карта сайта')
        self.assertNotContains(response, 'Подписка')
        self.assertNotContains(response, 'footer-subscribe')

    def test_home_personal_order_banner_links_to_request_page(self):
        response = self.client.get(reverse('shop:home'))

        self.assertContains(response, 'shop/images/personal-order-banner.webp')
        self.assertContains(response, 'Персональный заказ')
        self.assertContains(response, 'Не нашли нужную вещь?')
        self.assertContains(response, 'Найдём её в бутиках мира и доставим для вас')
        self.assertContains(response, '<section class="category-mosaic"')
        self.assertNotContains(response, '<section class="category-mosaic page-width"')
        self.assertContains(
            response,
            f'<a class="personal-order-banner" '
            f'href="{reverse("shop:product_request_create")}"',
        )
        self.assertNotContains(response, 'Мастерство в деталях')

    def test_home_prioritizes_winter_products_and_builds_five_slide_hero(self):
        winter_products = []
        for index in range(5):
            winter, _ = import_product(product_payload(
                sku=f'HOME-WINTER-{index}',
                sku_without_size=f'HOME-WINTER-{index}',
                title=f'Осенне-зимний товар {index}',
                fetched_at=1767225700 + index,
                images=[{
                    'tag': 'main', 'position': 0,
                    'original': f'https://example.com/winter-{index}.jpg',
                }],
                colors=[], sizes=[], i18n={},
            ))
            winter.collection = 'fall-winter'
            winter.save(update_fields=('collection',))
            winter_products.append(winter)
        newest_other, _ = import_product(product_payload(
            sku='HOME-OTHER-NEWEST', sku_without_size='HOME-OTHER-NEWEST',
            title='Самый новый несезонный товар', fetched_at=1999999999,
            images=[{'tag': 'main', 'position': 0, 'original': 'https://example.com/other.jpg'}],
            colors=[], sizes=[], i18n={},
        ))

        response = self.client.get(reverse('shop:home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['hero_slides']), 5)
        self.assertTrue(all(
            image.product.collection == 'fall-winter'
            for image in response.context['hero_slides']
        ))
        self.assertEqual(len(response.context['products']), 4)
        self.assertTrue(all(
            product.collection == 'fall-winter'
            for product in response.context['products']
        ))
        self.assertNotIn(newest_other, response.context['products'])
        self.assertNotIn(
            newest_other,
            [image.product for image in response.context['hero_slides']],
        )
        self.assertContains(response, 'data-hero-slider')
        hero_html = response.content.decode().split('<section class="bags-banner', 1)[0]
        self.assertEqual(hero_html.count('data-hero-slide>'), 5)
        self.assertEqual(hero_html.count('data-hero-dot>'), 5)

    def test_home_personalizes_products_hero_and_links_by_profile_gender(self):
        women, _ = import_product(product_payload(
            sku='HOME-PERSONAL-WOMEN', sku_without_size='HOME-PERSONAL-WOMEN',
            title='Женская сезонная новинка', gender=['women'], fetched_at=1767225900,
            images=[{'position': 0, 'original': 'https://example.com/personal-women.jpg'}],
            colors=[], sizes=[], i18n={},
        ))
        women.collection = 'fall-winter'
        women.save(update_fields=('collection',))
        men, _ = import_product(product_payload(
            sku='HOME-PERSONAL-MEN', sku_without_size='HOME-PERSONAL-MEN',
            title='Мужская сезонная новинка', gender=['men'], fetched_at=1767226000,
            images=[{'position': 0, 'original': 'https://example.com/personal-men.jpg'}],
            colors=[], sizes=[], i18n={},
        ))
        men.collection = 'fall-winter'
        men.save(update_fields=('collection',))
        user = get_user_model().objects.create_user(
            username='personal@example.com', email='personal@example.com',
            password='SecurePass-2026',
        )
        CustomerProfile.objects.create(user=user, gender=CustomerProfile.Gender.WOMEN)
        self.client.force_login(user)

        response = self.client.get(reverse('shop:home'))

        self.assertEqual(response.context['preferred_gender'], 'women')
        self.assertContains(response, women.title)
        self.assertNotContains(response, men.title)
        self.assertTrue(all(
            image.product.gender[0] == 'women' for image in response.context['hero_slides']
        ))
        self.assertContains(response, 'collection=fall-winter&amp;gender=women')
        self.assertContains(response, 'collection=new&amp;gender=women')

    def test_home_uses_banner_settings_and_selected_catalog_images(self):
        catalog_image = self.product.images.get()
        catalog_image.preview_image = 'products/previews/banner-small.webp'
        catalog_image.gallery_image = 'products/gallery/banner-large.webp'
        catalog_image.save(update_fields=('preview_image', 'gallery_image'))
        fall_banner = Banner.objects.get(slot=Banner.Slot.FALL_WINTER)
        fall_banner.image_count = 1
        fall_banner.interval_seconds = 9
        fall_banner.autoplay = False
        fall_banner.save()
        BannerImage.objects.create(
            banner=fall_banner, catalog_image=catalog_image, position=0,
        )
        bags_banner = Banner.objects.get(slot=Banner.Slot.BAGS)
        bags_banner.image_count = 1
        bags_banner.interval_seconds = 7
        bags_banner.autoplay = True
        bags_banner.save()
        BannerImage.objects.create(
            banner=bags_banner, catalog_image=catalog_image, position=0,
        )

        response = self.client.get(reverse('shop:home'))

        self.assertEqual(len(response.context['hero_slides']), 1)
        self.assertEqual(len(response.context['bag_slides']), 1)
        self.assertContains(response, 'data-hero-interval="9000"')
        self.assertContains(response, 'data-hero-autoplay="false"')
        self.assertContains(response, 'data-hero-interval="7000"')
        self.assertContains(response, 'data-hero-autoplay="true"')
        hero_html = response.content.decode().split('</section>', 1)[0]
        bags_html = response.content.decode().split(
            '<section class="bags-banner', 1
        )[1].split('</section>', 1)[0]
        self.assertIn('src="/media/products/gallery/banner-large.webp"', hero_html)
        self.assertIn('src="/media/products/gallery/banner-large.webp"', bags_html)
        self.assertNotIn('banner-small.webp', hero_html)
        self.assertNotIn('banner-small.webp', bags_html)

    def test_home_category_promos_use_clothing_and_accessory_products(self):
        def create_clothing(sku, gender):
            product, _ = import_product(product_payload(
                sku=sku, sku_without_size=sku, title=f'Одежда {gender}',
                gender=[gender], colors=[], sizes=[], i18n={},
                images=[{
                    'position': 0,
                    'original': f'https://example.com/{sku}.jpg',
                }],
            ))
            product.category_department_slug = gender
            product.category_path_key = f'{gender}/clothing/outerwear'
            product.category_keys = [
                f'{gender}/clothing', f'{gender}/clothing/outerwear',
            ]
            product.save(update_fields=(
                'category_department_slug', 'category_path_key', 'category_keys',
            ))
            return product

        women_clothing = create_clothing('PROMO-WOMEN-CLOTHING', 'women')
        men_clothing = create_clothing('PROMO-MEN-CLOTHING', 'men')
        accessory, _ = import_product(product_payload(
            sku='PROMO-WOMEN-ACCESSORY', sku_without_size='PROMO-WOMEN-ACCESSORY',
            title='Кожаный брелок для баннера', gender=['women'],
            fetched_at=1, colors=[], sizes=[], i18n={},
            images=[{
                'position': 0,
                'original': 'https://example.com/promo-accessory.jpg',
            }],
        ))
        accessory.category_department_slug = 'women'
        accessory.category_path_key = 'women/accessories/bag_accessories'
        accessory.category_keys = [
            'women/accessories', 'women/accessories/bag_accessories',
        ]
        accessory.save(update_fields=(
            'category_department_slug', 'category_path_key', 'category_keys',
        ))

        response = self.client.get(reverse('shop:home'))

        self.assertEqual(response.context['women_promo'], women_clothing)
        self.assertEqual(response.context['men_promo'], men_clothing)
        self.assertEqual(response.context['accessory_promo'], accessory)
        for context_name in ('women_promo', 'men_promo'):
            self.assertIn('/clothing/', response.context[context_name].category_path_key)
        self.assertIn(
            '/accessories/', response.context['accessory_promo'].category_path_key
        )

    def test_home_fills_selection_and_bag_banner_with_five_items(self):
        for index in range(1, 5):
            product, _ = import_product(product_payload(
                sku=f'HOME-BAG-{index}', sku_without_size=f'HOME-BAG-{index}',
                title=f'Сумка для баннера {index}', fetched_at=1767226000 + index,
                colors=[], sizes=[], i18n={},
                images=[{
                    'position': 0,
                    'original': f'https://example.com/home-bag-{index}.jpg',
                }],
            ))
            product.category_name = 'Сумки'
            product.category_path_key = 'women/bags/main'
            product.category_keys = ['women/bags', 'women/bags/main']
            product.save(update_fields=(
                'category_name', 'category_path_key', 'category_keys',
            ))

        response = self.client.get(reverse('shop:home'))

        self.assertEqual(len(response.context['selection_products']), 4)
        self.assertEqual(len({
            product.pk for product in response.context['selection_products']
        }), 4)
        self.assertEqual(len(response.context['bag_slides']), 5)
        self.assertTrue(all(
            image.product.category_name == 'Сумки'
            for image in response.context['bag_slides']
        ))
        self.assertContains(response, 'aria-label="Фотографии баннера сумок"')

    def test_boutiques_link_is_in_footer_and_order_link_remains_in_header(self):
        response = self.client.get(reverse('shop:home'))
        html = response.content.decode()
        header = html[html.index('<header'):html.index('</header>')]
        footer = html[html.index('<footer'):html.index('</footer>')]

        self.assertIn(
            f'href="{reverse("shop:product_request_create")}"', header,
        )
        self.assertNotIn(f'href="{reverse("shop:stores")}"', header)
        self.assertIn(
            f'class="footer-boutiques-link" href="{reverse("shop:stores")}"',
            footer,
        )
        self.assertIn('>Бутики</a>', footer)
        self.assertContains(response, '>Найти вещь</a>')

    def test_header_marks_only_current_section_and_has_no_winter_link(self):
        home = self.client.get(reverse('shop:home'))
        women = self.client.get(reverse('shop:catalog'), {'gender': 'women'})
        newest = self.client.get(reverse('shop:catalog'), {'collection': 'new'})
        accessories = self.client.get(
            reverse('shop:catalog'), {'category': 'accessories'}
        )

        self.assertNotContains(home, '>Осень–зима</a>')
        self.assertNotContains(home, 'nav-order-link active')
        self.assertNotContains(home, '>Бренды</a>')
        self.assertNotContains(home, '>Магазины</a>')
        self.assertContains(women, 'href="/catalog/?gender=women" class="active"')
        self.assertContains(newest, 'collection=new" class="active"')
        self.assertContains(accessories, 'category=accessories" class="active"')
        self.assertNotContains(
            accessories,
            'href="/stores/" class="active"',
        )

    def test_home_uses_finder_editorial_layout(self):
        response = self.client.get(reverse('shop:home'))

        self.assertContains(response, 'FINDER')
        self.assertNotContains(response, 'AURÉNE')
        self.assertNotContains(response, 'ÉLANE')
        self.assertContains(response, 'Из бутиков мира')
        self.assertContains(response, 'Образ недели')
        self.assertContains(response, 'class="category-stack"')
        self.assertContains(response, '<strong>Женское</strong>', html=True)
        self.assertContains(response, '<strong>Мужское</strong>', html=True)
        self.assertContains(response, 'class="category-banner category-accessories"')
        self.assertContains(response, 'Оставить заявку')

    @override_settings(
        TELEGRAM_CHANNEL_USERNAME='finder_style',
        TELEGRAM_BOT_USERNAME='finder_orders_bot',
    )
    def test_home_and_footer_promote_configured_telegram_channel(self):
        response = self.client.get(reverse('shop:home'))

        self.assertContains(response, 'class="telegram-editorial page-width"')
        self.assertContains(response, 'href="https://t.me/finder_style"')
        self.assertContains(response, '@finder_style')
        self.assertContains(response, 'Перейти в канал')

    @override_settings(
        TELEGRAM_CHANNEL_USERNAME='',
        TELEGRAM_BOT_USERNAME='finder_orders_bot',
    )
    def test_storefront_uses_bot_as_public_telegram_fallback(self):
        response = self.client.get(reverse('shop:home'))

        self.assertContains(response, 'href="https://t.me/finder_orders_bot"')
        self.assertContains(response, 'Открыть Telegram-бота')

    @override_settings(
        TELEGRAM_CHANNEL_USERNAME='bad username',
        TELEGRAM_BOT_USERNAME='',
    )
    def test_storefront_hides_invalid_public_telegram_username(self):
        response = self.client.get(reverse('shop:home'))

        self.assertNotContains(response, 'class="telegram-editorial page-width"')
        self.assertNotContains(response, 'https://t.me/bad username')

    def test_accessories_header_section_combines_women_and_men(self):
        women_accessory, _ = import_product(product_payload(
            sku='HEADER-WOMEN-ACCESSORY', sku_without_size='HEADER-WOMEN-ACCESSORY',
            title='Женские перчатки для раздела', i18n={}, colors=[], gender=['women'],
        ))
        women_accessory.category_department_slug = 'women'
        women_accessory.category_department_name = 'Женщины'
        women_accessory.category_name = 'Перчатки'
        women_accessory.category_keys = ['women/gloves']
        women_accessory.save(update_fields=(
            'category_department_slug', 'category_department_name',
            'category_name', 'category_keys',
        ))
        men_accessory, _ = import_product(product_payload(
            sku='HEADER-MEN-ACCESSORY', sku_without_size='HEADER-MEN-ACCESSORY',
            title='Мужской галстук для раздела', i18n={}, colors=[], gender=['men'],
        ))
        men_accessory.category_department_slug = 'men'
        men_accessory.category_department_name = 'Мужчины'
        men_accessory.category_name = 'Галстуки'
        men_accessory.category_keys = ['men/ties']
        men_accessory.save(update_fields=(
            'category_department_slug', 'category_department_name',
            'category_name', 'category_keys',
        ))

        response = self.client.get(
            reverse('shop:catalog'), {'category': 'accessories'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Аксессуары</h1>', html=True)
        self.assertContains(response, women_accessory.title)
        self.assertContains(response, men_accessory.title)
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        category_tree = response.context['category_tree']
        self.assertEqual(
            [(item['key'], item['name']) for item in category_tree],
            [
                ('women/accessories', 'Женские аксессуары'),
                ('men/accessories', 'Мужские аксессуары'),
            ],
        )
        self.assertTrue(all(item['is_open'] for item in category_tree))
        category_names = {
            child['name']
            for item in category_tree
            for child in item['children']
        }
        self.assertNotIn('Одежда', category_names)
        self.assertNotIn('Обувь', category_names)
        self.assertNotIn('Сумки', category_names)

    def test_color_variants_never_link_to_source_store(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertNotContains(response, 'href="https://example.com/gold"')
        self.assertNotContains(response, 'ещё не загружена')
        self.assertNotContains(response, 'Недоступные цвета появятся')

    def test_color_variant_links_to_local_product_when_imported(self):
        local_variant, _ = import_product(product_payload(
            sku='H-001-GOLD',
            sku_without_size='H-001-GOLD',
            title='Локальная золотая сумка',
            current_color='Gold',
            colors=[],
        ))
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, f'href="{local_variant.get_absolute_url()}"')
        self.assertNotContains(response, 'href="https://example.com/gold"')

    def test_catalog_filters_and_searches(self):
        catalog_url = reverse('shop:hermes_catalog')
        response = self.client.get(catalog_url, {'q': 'Тестовая', 'category': 'Сумки'})
        self.assertContains(response, self.product.title)
        response = self.client.get(catalog_url, {'gender': 'women'})
        self.assertContains(response, self.product.title)
        response = self.client.get(catalog_url, {'gender': 'men'})
        self.assertNotContains(response, self.product.title)
        response = self.client.get(catalog_url, {'price_min': '999999'})
        self.assertNotContains(response, self.product.title)

    def test_hermes_boutique_uses_shared_catalog_and_brand_assets(self):
        response = self.client.get(reverse('shop:hermes_catalog'))

        self.assertTemplateUsed(response, 'shop/brands/hermes/catalog.html')
        self.assertTemplateUsed(response, 'shop/catalog/catalog.html')
        self.assertContains(response, 'shop/css/catalog.css')
        self.assertContains(response, 'shop/brands/hermes/hermes.css')
        self.assertContains(response, 'shop/brands/hermes/hermes-logo.svg')
        detail = self.client.get(self.product.get_absolute_url())
        self.assertTemplateUsed(detail, 'shop/catalog/product_detail.html')
        self.assertNotContains(detail, 'product-brand-logo')
        self.assertNotContains(detail, 'hermes-logo.svg')

    def test_brand_filter_only_lists_brands_available_in_current_section(self):
        miumiu = normalize_miumiu_product({
            'id': 'miumiu:WOMEN-ONLY', 'brand': 'Miu Miu', 'gender': 'women',
            'title': {'i18n': {'ru': 'Женская сумка Miu Miu'}},
            'description': {'i18n': {'ru': 'Описание сумки'}},
            'category': {'raw_path': ['Accueil', 'Sacs', 'Sacs portés main']},
            'attributes': {}, 'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'EUR'},
            'media': {'images': []}, 'variants': {'colors': [], 'sizes': []},
        })
        import_product(miumiu)
        CatalogBrand.objects.create(name='Без аксессуаров', slug='without-accessories')
        other, _ = import_product(product_payload(
            sku='WITHOUT-ACCESSORIES', sku_without_size='WITHOUT-ACCESSORIES',
            title='Женская одежда другого бренда', brand='without-accessories',
            gender=['women'], colors=[], sizes=[], i18n={},
        ))
        other.category_path_key = 'women/clothing/outerwear'
        other.category_keys = ['women/clothing', 'women/clothing/outerwear']
        other.category_name = 'Верхняя одежда'
        other.save(update_fields=('category_path_key', 'category_keys', 'category_name'))

        women = self.client.get(reverse('shop:catalog'), {'gender': 'women'})
        men = self.client.get(reverse('shop:catalog'), {'gender': 'men'})
        accessories = self.client.get(
            reverse('shop:catalog'), {'category': 'accessories'}
        )
        forced_miumiu_men = self.client.get(reverse('shop:catalog'), {
            'gender': 'men', 'brand': 'miu-miu',
        })

        self.assertIn('miu-miu', {item['slug'] for item in women.context['brands']})
        self.assertNotIn('miu-miu', {item['slug'] for item in men.context['brands']})
        self.assertNotIn(
            'without-accessories',
            {item['slug'] for item in accessories.context['brands']},
        )
        self.assertNotIn(
            'miu-miu', {item['slug'] for item in forced_miumiu_men.context['brands']}
        )
        self.assertEqual(forced_miumiu_men.context['page_obj'].paginator.count, 0)

    def test_shoe_sizes_are_limited_by_product_gender(self):
        def shoe(sku, gender, sizes):
            data = {
                'id': f'hermes_cn:{sku}', 'brand': 'Hermès', 'gender': gender,
                'title': {'i18n': {'ru': f'Обувь {gender}'}},
                'category': {
                    'i18n': {'ru': [
                        'Женщины' if gender == 'women' else 'Мужчины',
                        'Обувь', 'Сандалии',
                    ]},
                    'canonical_path': {
                        'department': gender,
                        'department_ru': 'Женщины' if gender == 'women' else 'Мужчины',
                        'categories': [
                            {'slug': 'shoes', 'ru': 'Обувь', 'level': 2},
                            {'slug': 'sandals', 'ru': 'Сандалии', 'level': 3},
                        ],
                    },
                },
                'attributes': {}, 'availability': {'status': 'in_stock'},
                'price': {'amount': 1000, 'currency': 'CNY'},
                'media': {'images': []}, 'variants': {
                    'colors': [],
                    'sizes': [
                        {
                            'value': size, 'sku': f'{sku}-{size}',
                            'availability': {'status': 'in_stock'},
                        }
                        for size in sizes
                    ],
                },
            }
            return import_product(data)[0]

        women = shoe('WOMEN-SHOES', 'women', ('38', '42', '43'))
        men = shoe('MEN-SHOES', 'men', ('38', '39', '43'))

        women_detail = self.client.get(women.get_absolute_url())
        men_detail = self.client.get(men.get_absolute_url())

        self.assertEqual(
            [item.size for item in women_detail.context['available_sizes']],
            ['38', '42'],
        )
        self.assertEqual(
            [item.size for item in men_detail.context['available_sizes']],
            ['39', '43'],
        )
        incompatible = women.size_variants.get(size='43')
        response = self.client.post(
            reverse('shop:cart_add', args=[women.pk]),
            {'size_sku': incompatible.sku},
        )
        self.assertRedirects(response, women.get_absolute_url())
        self.assertNotIn(str(women.pk), self.client.session.get('cart', {}))

    def test_common_catalog_combines_brands_and_boutiques_isolate_them(self):
        miumiu = normalize_miumiu_product({
            'id': 'miumiu:COMMON-BAG', 'brand': 'Miu Miu', 'gender': 'women',
            'title': {'i18n': {'ru': 'Общая сумка Miu Miu'}},
            'description': {'i18n': {'ru': 'Описание сумки'}},
            'category': {'raw_path': ['Accueil', 'Sacs', 'Sacs portés main']},
            'attributes': {'material_normalized': ['кожа']},
            'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'EUR'},
            'media': {'images': []}, 'variants': {'colors': [], 'sizes': []},
        })
        miumiu_product, _ = import_product(miumiu)

        common = self.client.get(reverse('shop:catalog'))
        by_miumiu = self.client.get(reverse('shop:catalog'), {'brand': 'miu-miu'})
        hermes_boutique = self.client.get(reverse('shop:hermes_catalog'))
        miumiu_boutique = self.client.get(reverse('shop:miumiu_catalog'))

        self.assertContains(common, self.product.title)
        self.assertContains(common, miumiu_product.title)
        self.assertContains(common, 'name="brand"')
        self.assertNotContains(common, 'hermes-logo.svg')
        self.assertContains(by_miumiu, miumiu_product.title)
        self.assertNotContains(by_miumiu, self.product.title)
        self.assertContains(hermes_boutique, self.product.title)
        self.assertNotContains(hermes_boutique, miumiu_product.title)
        self.assertContains(miumiu_boutique, miumiu_product.title)
        self.assertNotContains(miumiu_boutique, self.product.title)
        self.assertContains(hermes_boutique, 'hermes-logo.svg')
        self.assertContains(miumiu_boutique, 'miumiu-logo.svg')

    def test_brand_filter_preserves_gender_category_and_collection(self):
        miumiu = normalize_miumiu_product({
            'id': 'miumiu:FILTER-ACCESSORY', 'brand': 'Miu Miu',
            'gender': 'women',
            'title': {'i18n': {'ru': 'Женский аксессуар Miu Miu'}},
            'description': {'i18n': {'ru': 'Описание аксессуара'}},
            'category': {
                'raw_path': ['Accueil', 'Accessoires', 'Ceintures'],
            },
            'attributes': {'material_normalized': ['кожа']},
            'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'EUR'},
            'media': {'images': []}, 'variants': {'colors': [], 'sizes': []},
        })
        product, _ = import_product(miumiu)

        women = self.client.get(reverse('shop:catalog'), {'gender': 'women'})
        accessories = self.client.get(
            reverse('shop:catalog'), {'category': 'accessories'}
        )
        newest = self.client.get(reverse('shop:catalog'), {'collection': 'new'})
        filtered = self.client.get(reverse('shop:catalog'), {
            'gender': 'women', 'category': 'accessories', 'brand': 'miu-miu',
        })

        self.assertContains(women, 'name="gender" value="women"')
        self.assertContains(accessories, 'name="category" value="accessories"')
        self.assertContains(newest, 'name="collection" value="new"')
        self.assertEqual(filtered.context['selected_gender'], 'women')
        self.assertEqual(filtered.context['selected_category'], 'accessories')
        self.assertEqual(filtered.context['selected_brand'], 'miu-miu')
        self.assertContains(filtered, product.title)
        self.assertTrue(all(
            item.brand == 'miu-miu' and 'women' in item.gender
            for item in filtered.context['page_obj']
        ))

    def test_new_active_brand_automatically_gets_common_and_boutique_catalogs(self):
        CatalogBrand.objects.create(name='Новый бренд', slug='new-brand')
        product, _ = import_product(product_payload(
            sku='NEW-BRAND-001', sku_without_size='NEW-BRAND-001',
            title='Товар нового бренда', brand='new-brand', i18n={}, colors=[],
        ))

        common = self.client.get(reverse('shop:catalog'))
        boutique = self.client.get(
            reverse('shop:brand_catalog', kwargs={'brand_slug': 'new-brand'})
        )
        detail = self.client.get(product.get_absolute_url())

        self.assertContains(common, product.title)
        self.assertContains(common, 'Новый бренд')
        self.assertContains(boutique, product.title)
        self.assertNotContains(boutique, self.product.title)
        self.assertContains(boutique, 'Коллекция бренда')
        self.assertNotContains(boutique, 'hermes-logo.svg')
        self.assertContains(detail, 'Новый бренд')
        self.assertContains(
            detail, reverse('shop:brand_catalog', kwargs={'brand_slug': 'new-brand'})
        )

    def test_the_row_boutique_uses_new_york_location(self):
        CatalogBrand.objects.create(name='The Row', slug='the-row')
        product, _ = import_product(product_payload(
            sku='THEROW-LOCATION', sku_without_size='THEROW-LOCATION',
            title='Товар The Row', brand='the-row', i18n={}, colors=[],
        ))

        response = self.client.get(
            reverse('shop:brand_catalog', kwargs={'brand_slug': 'the-row'})
        )

        self.assertContains(response, product.title)
        self.assertContains(response, 'Нью-Йорк, США')

    def test_inactive_brand_is_hidden_from_common_catalog_and_direct_urls(self):
        brand = CatalogBrand.objects.get(slug='hermes')
        brand.is_active = False
        brand.save(update_fields=('is_active',))

        common = self.client.get(reverse('shop:catalog'))
        boutique = self.client.get(reverse('shop:hermes_catalog'))
        detail = self.client.get(self.product.get_absolute_url())

        self.assertNotContains(common, self.product.title)
        self.assertEqual(boutique.status_code, 404)
        self.assertEqual(detail.status_code, 404)

    def test_hermes_catalog_excludes_other_brands(self):
        other, _ = import_product(product_payload(
            sku='OTHER-001',
            title='Товар другого магазина',
            brand='another-store',
            i18n={},
            colors=[],
        ))

        response = self.client.get(reverse('shop:hermes_catalog'))

        self.assertContains(response, self.product.title)
        self.assertNotContains(response, other.title)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_color_and_material_filters_return_matching_products(self):
        second, _ = import_product(product_payload(
            sku='H-002-RED',
            title='Красная шёлковая модель',
            current_color='Красный',
            material=['шёлк'],
            i18n={},
            colors=[],
        ))
        catalog_url = reverse('shop:hermes_catalog')

        by_color = self.client.get(catalog_url, {'color': 'red'})
        self.assertContains(by_color, second.title)
        self.assertNotContains(by_color, self.product.title)
        self.assertEqual(by_color.context['page_obj'].paginator.count, 1)
        self.assertContains(by_color, 'value="red" checked')

        by_material = self.client.get(catalog_url, {'material': 'кожа'})
        self.assertContains(by_material, self.product.title)
        self.assertNotContains(by_material, second.title)
        self.assertEqual(by_material.context['page_obj'].paginator.count, 1)
        self.assertContains(by_material, 'value="кожа" checked')

        combined = self.client.get(catalog_url, {
            'color': 'Красный', 'material': 'шёлк',
        })
        self.assertContains(combined, second.title)
        self.assertNotContains(combined, self.product.title)
        self.assertEqual(combined.context['page_obj'].paginator.count, 1)

    def test_category_is_not_changed_by_brand_separation(self):
        original_category = 'Женские сумки/столовые сумки'
        self.product.category_name = original_category
        self.product.save(update_fields=['category_name'])

        response = self.client.get(reverse('shop:hermes_catalog'))

        self.product.refresh_from_db()
        self.assertEqual(self.product.category_name, original_category)
        self.assertContains(response, 'Сумки')

    def test_catalog_builds_category_tree_and_filters_subcategory(self):
        canonical = {
            'id': 'hermes_cn:SANDAL-001',
            'brand': 'Hermès',
            'title': {'raw': '凉鞋', 'i18n': {'ru': 'Синие сандалии'}},
            'gender': 'women',
            'category': {
                'raw_path': [],
                'i18n': {'ru': ['Женщины', 'Обувь', 'Сандалии', 'Синие сандалии']},
                'canonical_path': {
                    'department': 'women', 'department_ru': 'Женщины',
                    'categories': [
                        {'slug': 'shoes', 'ru': 'Обувь', 'level': 2},
                        {'slug': 'sandals', 'ru': 'Сандалии', 'level': 3},
                    ],
                },
            },
            'attributes': {}, 'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'CNY'},
            'media': {'images': []},
            'variants': {'sizes': [], 'colors': [{
                'sku': 'SANDAL-001', 'label': {'i18n': {'ru': 'Тибетский синий'}},
                'base_color': 'синий', 'availability': {'status': 'in_stock'},
            }]},
        }
        sandal, _ = import_product(canonical)

        response = self.client.get(reverse('shop:hermes_catalog'), {
            'category': 'women/shoes/sandals',
        })

        sandal.refresh_from_db()
        self.assertEqual(sandal.category_department_slug, 'women')
        self.assertEqual(sandal.category_keys, ['women/shoes', 'women/shoes/sandals'])
        self.assertEqual(sandal.color_group, 'blue')
        self.assertEqual(sandal.current_color, 'Тибетский синий')
        self.assertContains(response, 'Женщины')
        self.assertContains(response, 'Обувь')
        self.assertContains(response, 'Сандалии')
        self.assertContains(response, sandal.title)
        self.assertContains(response, 'category=women%2Fshoes%2Fsandals')

        department = self.client.get(reverse('shop:hermes_catalog'), {'category': 'women'})
        self.assertContains(department, sandal.title)

    def test_compound_source_categories_are_split_by_actual_product_type(self):
        def home_product(sku, title, code, categories):
            return {
                'id': f'hermes_cn:{sku}', 'brand': 'Hermès',
                'title': {'i18n': {'ru': title}},
                'category': {
                    'i18n': {'ru': ['Дом', 'Составная категория', title]},
                    'canonical_path': {
                        'department': 'home', 'department_ru': 'Дом и стиль жизни',
                        'categories': [
                            {'code': code, 'slug': slug, 'ru': name, 'level': 2}
                            for slug, name in categories
                        ],
                    },
                },
                'attributes': {}, 'availability': {'status': 'in_stock'},
                'price': {'amount': 1000, 'currency': 'CNY'},
                'media': {'images': []},
                'variants': {'sizes': [], 'colors': []},
            }

        decor_categories = (
            ('decorative_objects', 'Декоративные предметы'), ('games', 'Игры'),
        )
        furniture_categories = (
            ('furniture', 'Мебель'), ('lighting', 'Освещение'), ('rugs', 'Ковры'),
        )
        ashtray, _ = import_product(home_product(
            'ASHTRAY', 'Пепельница «Тигр»',
            'HOME_DECORATIVE_OBJECTS_GAMES', decor_categories,
        ))
        chess, _ = import_product(home_product(
            'CHESS', 'Набор шахмат Samarcande',
            'HOME_DECORATIVE_OBJECTS_GAMES', decor_categories,
        ))
        lamp, _ = import_product(home_product(
            'LAMP', 'Настольная лампа Sarazine',
            'HOME_FURNITURE_LIGHTING_RUGS', furniture_categories,
        ))
        rug, _ = import_product(home_product(
            'RUG', 'Шерстяной ковёр',
            'HOME_FURNITURE_LIGHTING_RUGS', furniture_categories,
        ))
        chair, _ = import_product(home_product(
            'CHAIR', 'Кресло Sellier',
            'HOME_FURNITURE_LIGHTING_RUGS', furniture_categories,
        ))

        self.assertEqual(ashtray.category_keys, ['home/decorative_objects'])
        self.assertEqual(ashtray.category_name, 'Декоративные предметы')
        self.assertEqual(chess.category_keys, ['home/games'])
        self.assertEqual(lamp.category_keys, ['home/lighting'])
        self.assertEqual(rug.category_keys, ['home/rugs'])
        self.assertEqual(chair.category_keys, ['home/furniture'])

        games = self.client.get(reverse('shop:hermes_catalog'), {
            'category': 'home/games',
        })
        self.assertContains(games, chess.title)
        self.assertNotContains(games, ashtray.title)

    def test_petit_h_category_has_an_explanatory_name(self):
        petit_h, _ = import_product({
            'id': 'hermes_cn:PETIT-H', 'brand': 'Hermès',
            'title': {'i18n': {'ru': 'Дизайнерский объект'}},
            'category': {
                'i18n': {'ru': ['Дом', 'Petit h', 'Дизайнерский объект']},
                'canonical_path': {
                    'department': 'home', 'department_ru': 'Дом и стиль жизни',
                    'categories': [{
                        'code': 'Petit_H', 'slug': 'petit_h',
                        'ru': 'Petit h', 'level': 2,
                    }],
                },
            },
            'attributes': {}, 'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'CNY'},
            'media': {'images': []},
            'variants': {'sizes': [], 'colors': []},
        })

        self.assertEqual(petit_h.category_name, 'Petit h — дизайнерские объекты')
        response = self.client.get(reverse('shop:hermes_catalog'))
        self.assertContains(response, 'Petit h — дизайнерские объекты')

    def test_category_counts_respect_other_active_filters(self):
        second, _ = import_product(product_payload(
            sku='H-002-SAME-CATEGORY',
            title='Вторая модель в той же категории',
            current_color='Красный',
            i18n={},
            colors=[],
        ))
        self.product.collection = 'fall-winter'
        self.product.save(update_fields=['collection'])
        second.collection = 'spring-summer'
        second.save(update_fields=['collection'])
        category_key = self.product.category_keys[-1]

        response = self.client.get(reverse('shop:hermes_catalog'), {
            'collection': 'fall-winter',
            'category': category_key,
        })

        def find_category(items):
            for item in items:
                if item['key'] == category_key:
                    return item
                found = find_category(item['children'])
                if found:
                    return found
            return None

        category = find_category(response.context['category_tree'])
        self.assertIsNotNone(category)
        self.assertEqual(category['count'], 1)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertContains(response, self.product.title)
        self.assertNotContains(response, second.title)

    def test_unknown_named_color_is_grouped_as_other(self):
        product, _ = import_product(product_payload(
            sku='H-OTHER-COLOR', current_color='Тёплый тон', colors=[], i18n={},
        ))

        response = self.client.get(reverse('shop:hermes_catalog'), {'color': 'other'})

        self.assertEqual(product.color_group, 'other')
        self.assertContains(response, product.title)
        self.assertContains(response, 'Другой')

    def test_season_collections_are_separate_and_new_prioritizes_fall_winter(self):
        def seasonal_product(sku, code, name, fetched_at):
            return {
                'id': f'hermes_cn:{sku}', 'brand': 'Hermès',
                'title': {'i18n': {'ru': name}}, 'gender': 'women',
                'category': {
                    'i18n': {'ru': ['Женщины', name, name]},
                    'canonical_path': {
                        'department': 'women', 'department_ru': 'Женщины',
                        'categories': [{
                            'code': code, 'slug': 'season_collection',
                            'ru': name, 'level': 2,
                        }],
                    },
                },
                'attributes': {}, 'availability': {'status': 'in_stock'},
                'price': {'amount': 1000, 'currency': 'CNY'},
                'media': {'images': []},
                'variants': {'sizes': [], 'colors': []},
                'normalized_from': {'fetched_at': fetched_at},
            }

        winter, _ = import_product(seasonal_product(
            'WINTER-1', 'PRE_COLLECTION_FW', 'Коллекция осень-зима', 2000000000,
        ))
        summer, _ = import_product(seasonal_product(
            'SUMMER-1', 'PRE_COLLECTION_PE', 'Коллекция весна-лето', 2000000001,
        ))
        catalog_url = reverse('shop:hermes_catalog')

        winter_response = self.client.get(catalog_url, {'collection': 'fall-winter'})
        summer_response = self.client.get(catalog_url, {'collection': 'spring-summer'})
        newest_response = self.client.get(catalog_url, {'collection': 'new'})

        self.assertEqual(winter.collection, 'fall-winter')
        self.assertEqual(summer.collection, 'spring-summer')
        self.assertContains(winter_response, winter.title)
        self.assertNotContains(winter_response, summer.title)
        self.assertContains(summer_response, summer.title)
        self.assertNotContains(summer_response, winter.title)
        self.assertEqual(newest_response.context['ordering'], 'new')
        self.assertEqual(newest_response.context['page_obj'][0].pk, winter.pk)
        self.assertContains(newest_response, '<h1>Новые поступления</h1>', html=True)
        self.assertNotContains(newest_response, 'hermes-collection-nav')

    def test_winter_products_are_first_on_initial_and_new_catalog_pages(self):
        winter, _ = import_product(product_payload(
            sku='CATALOG-WINTER-FIRST', sku_without_size='CATALOG-WINTER-FIRST',
            title='Осенне-зимний товар в начале', fetched_at=1700000000,
            colors=[], sizes=[], i18n={},
        ))
        winter.collection = 'fall-winter'
        winter.save(update_fields=('collection',))
        newer_other, _ = import_product(product_payload(
            sku='CATALOG-OTHER-NEWER', sku_without_size='CATALOG-OTHER-NEWER',
            title='Более новый несезонный товар', fetched_at=1900000000,
            colors=[], sizes=[], i18n={},
        ))

        for params in ({}, {'collection': 'new'}):
            with self.subTest(params=params):
                response = self.client.get(reverse('shop:hermes_catalog'), params)
                products = list(response.context['page_obj'])
                self.assertEqual(products[0].pk, winter.pk)
                self.assertLess(
                    next(i for i, item in enumerate(products) if item.pk == winter.pk),
                    next(i for i, item in enumerate(products) if item.pk == newer_other.pk),
                )

    def test_seasonal_products_receive_clothing_categories(self):
        def seasonal_clothing(sku, title, gender):
            return {
                'id': f'hermes_cn:{sku}', 'brand': 'Hermès',
                'title': {'i18n': {'ru': title}}, 'gender': gender,
                'category': {
                    'i18n': {'ru': [
                        'Женщины' if gender == 'women' else 'Мужчины',
                        'Коллекция осень-зима', title,
                    ]},
                    'canonical_path': {
                        'department': gender,
                        'department_ru': 'Женщины' if gender == 'women' else 'Мужчины',
                        'categories': [{
                            'code': 'COLLECTION_FW', 'slug': 'season_collection',
                            'ru': 'Коллекция осень-зима', 'level': 2,
                        }],
                    },
                },
                'attributes': {}, 'availability': {'status': 'in_stock'},
                'price': {'amount': 1000, 'currency': 'CNY'},
                'media': {'images': []},
                'variants': {'sizes': [], 'colors': []},
            }

        coat, _ = import_product(seasonal_clothing(
            'WOMEN-COAT', 'Кашемировое пальто', 'women',
        ))
        tshirt, _ = import_product(seasonal_clothing(
            'MEN-TSHIRT', 'Футболка с принтом', 'men',
        ))

        self.assertEqual(coat.category_keys, [
            'women/clothing', 'women/clothing/outerwear',
        ])
        self.assertEqual(coat.category_name, 'Верхняя одежда')
        self.assertEqual(tshirt.category_keys, [
            'men/clothing', 'men/clothing/tshirts_tops',
        ])
        self.assertEqual(tshirt.category_name, 'Футболки и топы')
        women = self.client.get(reverse('shop:hermes_catalog'), {'gender': 'women'})
        men = self.client.get(reverse('shop:hermes_catalog'), {'gender': 'men'})
        self.assertContains(women, 'Одежда')
        self.assertContains(women, 'Верхняя одежда')
        self.assertContains(men, 'Одежда')
        self.assertContains(men, 'Футболки и топы')

    def test_gender_catalog_uses_grouped_fashion_navigation(self):
        def accessory_product(sku, title, code, categories):
            return {
                'id': f'hermes_cn:{sku}', 'brand': 'Hermès',
                'title': {'i18n': {'ru': title}}, 'gender': 'women',
                'category': {
                    'i18n': {'ru': ['Женщины', 'Аксессуары', title]},
                    'canonical_path': {
                        'department': 'women', 'department_ru': 'Женщины',
                        'categories': [
                            {'code': code, 'slug': slug, 'ru': name, 'level': 2}
                            for slug, name in categories
                        ],
                    },
                },
                'attributes': {}, 'availability': {'status': 'in_stock'},
                'price': {'amount': 1000, 'currency': 'CNY'},
                'media': {'images': []},
                'variants': {'sizes': [], 'colors': []},
            }

        scarf_categories = (
            ('twilly', 'Твилли (малые платки)'),
            ('other_small_formats', 'Другие малые форматы'),
        )
        import_product(accessory_product(
            'TWILLY', 'Twilly Маленький шелковый шарф',
            'WOMEN_TWILYS_OTHER_SMALL_FORMATS', scarf_categories,
        ))
        import_product(accessory_product(
            'SCARF-45', 'Квадратный шарф 45',
            'WOMEN_TWILYS_OTHER_SMALL_FORMATS', scarf_categories,
        ))
        import_product(accessory_product(
            'BRACELET', 'Браслет Clic H',
            'WOMEN_BRACELETS', (('bracelets', 'Браслеты'),),
        ))
        coat, _ = import_product(product_payload(
            sku='NAV-COAT', sku_without_size='NAV-COAT',
            title='Кашемировое пальто', i18n={}, colors=[], gender=['women'],
        ))
        coat.category_department_slug = 'women'
        coat.category_department_name = 'Женщины'
        coat.category_path = [
            {'slug': 'clothing', 'name': 'Одежда', 'level': 2,
             'key': 'women/clothing', 'parent_key': 'women'},
            {'slug': 'outerwear', 'name': 'Верхняя одежда', 'level': 3,
             'key': 'women/clothing/outerwear', 'parent_key': 'women/clothing'},
        ]
        coat.category_keys = ['women/clothing', 'women/clothing/outerwear']
        coat.save(update_fields=(
            'category_department_slug', 'category_department_name',
            'category_path', 'category_keys',
        ))
        shoes, _ = import_product(product_payload(
            sku='NAV-SHOES', sku_without_size='NAV-SHOES',
            title='Женские кроссовки', i18n={}, colors=[], gender=['women'],
        ))
        shoes.category_department_slug = 'women'
        shoes.category_department_name = 'Женщины'
        shoes.category_path = [
            {'slug': 'shoes', 'name': 'Обувь', 'level': 2,
             'key': 'women/shoes', 'parent_key': 'women'},
            {'slug': 'sneakers', 'name': 'Кроссовки', 'level': 3,
             'key': 'women/shoes/sneakers', 'parent_key': 'women/shoes'},
        ]
        shoes.category_keys = ['women/shoes', 'women/shoes/sneakers']
        shoes.save(update_fields=(
            'category_department_slug', 'category_department_name',
            'category_path', 'category_keys',
        ))

        response = self.client.get(reverse('shop:hermes_catalog'), {'gender': 'women'})
        self.assertEqual(len(response.context['category_tree']), 1)
        root = response.context['category_tree'][0]
        self.assertEqual(root['name'], 'Женщины')
        self.assertEqual(
            [item['name'] for item in root['children'][:5]],
            ['Одежда', 'Обувь', 'Сумки', 'Шарфы и платки', 'Аксессуары'],
        )

        scarves = next(item for item in root['children'] if item['key'] == 'women/scarves')
        twilly = next(
            item for item in scarves['children']
            if item['key'] == 'women/scarves/twilly_small'
        )
        accessories = next(
            item for item in root['children'] if item['key'] == 'women/accessories'
        )
        jewelry = next(
            item for item in accessories['children']
            if item['key'] == 'women/accessories/jewelry'
        )

        self.assertEqual(twilly['count'], 2)
        self.assertIn('Браслеты', [item['name'] for item in jewelry['children']])
        self.assertNotContains(response, 'Другие малые форматы')

        bags = self.client.get(reverse('shop:hermes_catalog'), {
            'gender': 'women', 'category': 'women/bags',
        })
        self.assertContains(bags, self.product.title)
        self.assertEqual(
            bags.context['page_obj'].paginator.count,
            next(item['count'] for item in root['children'] if item['key'] == 'women/bags'),
        )

    def test_boutique_and_new_arrivals_use_grouped_fashion_navigation(self):
        bracelet, _ = import_product(product_payload(
            sku='NAV-WOMEN-BRACELET', sku_without_size='NAV-WOMEN-BRACELET',
            title='Женский браслет', i18n={}, colors=[], gender=['women'],
        ))
        bracelet.category_department_slug = 'women'
        bracelet.category_department_name = 'Женщины'
        bracelet.category_path = [{
            'slug': 'bracelets', 'name': 'Браслеты', 'level': 2,
            'key': 'women/bracelets', 'parent_key': 'women',
        }]
        bracelet.category_keys = ['women/bracelets']
        bracelet.save(update_fields=(
            'category_department_slug', 'category_department_name',
            'category_path', 'category_keys',
        ))
        belt, _ = import_product(product_payload(
            sku='NAV-MEN-BELT', sku_without_size='NAV-MEN-BELT',
            title='Мужской ремень', i18n={}, colors=[], gender=['men'],
        ))
        belt.category_department_slug = 'men'
        belt.category_department_name = 'Мужчины'
        belt.category_path = [{
            'slug': 'belts', 'name': 'Ремни', 'level': 2,
            'key': 'men/belts', 'parent_key': 'men',
        }]
        belt.category_keys = ['men/belts']
        belt.save(update_fields=(
            'category_department_slug', 'category_department_name',
            'category_path', 'category_keys',
        ))

        for params in ({}, {'collection': 'new'}):
            with self.subTest(params=params):
                response = self.client.get(reverse('shop:hermes_catalog'), params)
                roots = {item['key']: item for item in response.context['category_tree']}

                self.assertIn('women', roots)
                self.assertIn('men', roots)
                women_children = roots['women']['children']
                men_children = roots['men']['children']
                self.assertIn('Сумки', [item['name'] for item in women_children])
                self.assertIn('Аксессуары', [item['name'] for item in women_children])
                self.assertNotIn('Браслеты', [item['name'] for item in women_children])
                self.assertIn('Аксессуары', [item['name'] for item in men_children])
                self.assertNotIn('Ремни', [item['name'] for item in men_children])

                women_accessories = next(
                    item for item in women_children if item['key'] == 'women/accessories'
                )
                women_jewelry = next(
                    item for item in women_accessories['children']
                    if item['key'] == 'women/accessories/jewelry'
                )
                self.assertIn(
                    'Браслеты', [item['name'] for item in women_jewelry['children']]
                )
                self.assertNotContains(response, 'Другие малые форматы')

    def test_catalog_uses_category_links_hides_extra_colors_and_has_no_sizes(self):
        color_names = ('Черный', 'Белый', 'Серый', 'Бежевый', 'Красный')
        color_groups = ('чёрный', 'белый', 'серый', 'бежевый', 'красный')
        for index, (name, base_color) in enumerate(zip(color_names, color_groups), start=1):
            data = {
                'id': f'hermes_cn:COLOR-{index}', 'brand': 'Hermès',
                'title': {'i18n': {'ru': f'Товар {name}'}}, 'gender': 'women',
                'category': {
                    'i18n': {'ru': ['Женщины', 'Обувь', 'Товар']},
                    'canonical_path': {
                        'department': 'women', 'department_ru': 'Женщины',
                        'categories': [{'slug': 'shoes', 'ru': 'Обувь', 'level': 2}],
                    },
                },
                'attributes': {}, 'availability': {'status': 'in_stock'},
                'price': {'amount': 1000, 'currency': 'CNY'},
                'media': {'images': []},
                'variants': {'sizes': [{
                    'value': '42', 'sku': f'COLOR-{index}',
                    'availability': {'status': 'in_stock'},
                }], 'colors': [{
                    'sku': f'COLOR-{index}', 'label': {'i18n': {'ru': name}},
                    'base_color': base_color, 'availability': {'status': 'in_stock'},
                }]},
            }
            import_product(data)

        response = self.client.get(reverse('shop:hermes_catalog'))
        detail = self.client.get(Product.objects.get(sku='hermes_cn:COLOR-1').get_absolute_url())
        html = response.content.decode()

        self.assertContains(response, 'hermes-category-link')
        self.assertContains(response, 'Смотреть всё')
        self.assertNotContains(response, 'input type="radio" name="category"')
        self.assertContains(response, 'Вся палитра')
        self.assertEqual(html.count('color-extra'), len(response.context['colors']) - 4)
        self.assertNotContains(response, '<legend>Размер</legend>')
        self.assertNotContains(response, 'name="size"')
        self.assertContains(detail, 'data-size-option')
        self.assertContains(detail, 'name="size_sku"')
        self.assertContains(detail, 'Таблица размеров')
        self.assertContains(detail, 'data-size-guide-open')

    def test_materials_are_ranked_grouped_and_collapsed_after_five(self):
        material_values = (
            'телячья кожа', 'воловья кожа', 'шёлк', 'шёлк', 'хлопок',
            'металл', 'кашемир', 'дерево',
        )
        for index, material in enumerate(material_values, start=1):
            import_product(product_payload(
                sku=f'MATERIAL-{index}',
                sku_without_size=f'MATERIAL-{index}',
                title=f'Материал {index}',
                current_color='', colors=[], i18n={}, material=[material],
            ))

        response = self.client.get(reverse('shop:hermes_catalog'))
        materials = response.context['materials']
        html = response.content.decode()

        self.assertGreater(len(materials), 5)
        self.assertEqual(
            [item['count'] for item in materials],
            sorted((item['count'] for item in materials), reverse=True),
        )
        self.assertEqual(sum(item['value'] == 'кожа' for item in materials), 1)
        self.assertEqual(
            next(item['count'] for item in materials if item['value'] == 'кожа'),
            3,
        )
        visible_html, hidden_html = html.split(
            '<div class="hermes-material-extra" data-material-extra hidden>', 1
        )
        self.assertEqual(visible_html.count('hermes-material-choice'), 5)
        self.assertEqual(
            hidden_html.count('hermes-material-choice'), len(materials) - 5
        )
        self.assertEqual(html.count('hermes-material-choice'), len(materials))
        self.assertContains(response, 'data-material-extra hidden')
        self.assertContains(response, 'Все материалы')

    def test_color_variants_are_grouped_in_catalog_and_linked_on_detail(self):
        base = {
            'brand': 'Hermès', 'gender': 'women',
            'category': {
                'i18n': {'ru': ['Женщины', 'Обувь', 'Сандалии', 'Модель']},
                'canonical_path': {
                    'department': 'women', 'department_ru': 'Женщины',
                    'categories': [
                        {'slug': 'shoes', 'ru': 'Обувь', 'level': 2},
                        {'slug': 'sandals', 'ru': 'Сандалии', 'level': 3},
                    ],
                },
            },
            'attributes': {}, 'availability': {'status': 'in_stock'},
            'price': {'amount': 1000, 'currency': 'CNY'},
            'media': {'images': []}, 'variants': {'sizes': [], 'colors': []},
        }
        colors = [
            {'sku': 'FAMILY-BLACK', 'label': {'i18n': {'ru': 'Черный'}},
             'base_color': 'чёрный', 'availability': {'status': 'in_stock'}},
            {'sku': 'FAMILY-RED', 'label': {'i18n': {'ru': 'Hermès Red'}},
             'base_color': 'красный', 'availability': {'status': 'in_stock'}},
        ]
        black_data = {**base, 'id': 'hermes_cn:FAMILY-BLACK',
                      'title': {'i18n': {'ru': 'Сандалии модели'}},
                      'variants': {'sizes': [], 'colors': colors}}
        red_data = {**base, 'id': 'hermes_cn:FAMILY-RED',
                    'title': {'i18n': {'ru': 'Сандалии модели'}},
                    'variants': {'sizes': [], 'colors': colors}}
        black, _ = import_product(black_data)
        red, _ = import_product(red_data)
        rebuild_product_catalog_metadata(Product.objects.filter(pk__in=(black.pk, red.pk)))

        catalog = self.client.get(reverse('shop:hermes_catalog'), {
            'category': 'women/shoes/sandals',
        })
        by_red = self.client.get(reverse('shop:hermes_catalog'), {'color': 'red'})
        detail = self.client.get(black.get_absolute_url())

        self.assertEqual(catalog.context['page_obj'].paginator.count, 1)
        self.assertContains(catalog, 'product-card-colors')
        self.assertContains(catalog, 'Чёрный')
        self.assertContains(catalog, 'Красный')
        self.assertEqual(by_red.context['page_obj'].paginator.count, 1)
        self.assertContains(by_red, red.title)
        self.assertContains(detail, red.get_absolute_url())
        self.assertEqual(
            sum(option['is_available_locally'] for option in detail.context['color_options']),
            2,
        )

        self.assertEqual(
            detail.content.decode().count('variant-color-extra'),
            max(0, len(detail.context['color_options']) - 4),
        )

    def test_cart_add_update_and_remove(self):
        add_url = reverse('shop:cart_add', args=[self.product.pk])
        self.assertEqual(self.client.get(add_url).status_code, 405)
        self.client.post(add_url)
        self.assertEqual(self.client.session['cart'][str(self.product.pk)], 1)
        self.client.post(reverse('shop:cart_update', args=[self.product.pk]), {'quantity': 3})
        self.assertEqual(self.client.session['cart'][str(self.product.pk)], 3)
        response = self.client.get(reverse('shop:cart'))
        self.assertEqual(response.context['cart_total'], self.product.display_price * 3)
        self.assertContains(response, '726 708')
        self.assertContains(response, 'class="cart-control-label">Количество</span>')
        self.assertContains(response, '<dt>Цвет</dt>', html=False)
        self.assertContains(response, '<dt>Размер</dt>', html=False)
        self.assertContains(response, '<span>Цена</span>', html=False)
        self.assertContains(response, '<b>Удалить</b>', html=False)
        self.assertNotContains(response, 'class="cart-favorite-form"')
        self.assertNotContains(response, '>В избранное</span>')
        self.client.post(reverse('shop:cart_remove', args=[self.product.pk]))
        self.assertNotIn(str(self.product.pk), self.client.session['cart'])

    def test_cart_labels_missing_color_and_size_explicitly(self):
        self.product.current_color = ''
        self.product.current_size = ''
        self.product.save(update_fields=('current_color', 'current_size'))
        self.client.post(reverse('shop:cart_add', args=[self.product.pk]))

        response = self.client.get(reverse('shop:cart'))

        self.assertContains(response, '<dt>Цвет</dt><dd>Не указан</dd>', html=True)
        self.assertContains(response, '<dt>Размер</dt><dd>Не указан</dd>', html=True)

    def test_authenticated_cart_persists_and_is_restored(self):
        user = get_user_model().objects.create_user(
            username='cart@example.com', password='SecurePass-2026'
        )
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.product.pk]))
        self.client.post(
            reverse('shop:cart_update', args=[self.product.pk]), {'quantity': 3}
        )

        saved = CartItem.objects.get(user=user, product=self.product)
        self.assertEqual(saved.quantity, 3)
        self.client.logout()
        self.client.force_login(user)

        response = self.client.get(reverse('shop:cart'))
        self.assertContains(response, self.product.title)
        self.assertEqual(response.context['cart_items'][0]['quantity'], 3)

    def test_product_detail_displays_imported_article(self):
        self.product.sku_without_size = None
        self.product.sku = 'imported-brand:ARTICLE-777'
        self.product.save(update_fields=('sku_without_size', 'sku'))

        response = self.client.get(self.product.get_absolute_url())

        self.assertContains(response, 'Артикул: ARTICLE-777')

    def test_favorite_toggle_persists_and_returns_json(self):
        toggle_url = reverse('shop:favorite_toggle', args=[self.product.pk])
        self.assertEqual(self.client.get(toggle_url).status_code, 405)

        added = self.client.post(
            toggle_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )

        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.json(), {
            'product_id': self.product.pk,
            'is_favorite': True,
            'favorite_count': 1,
        })
        self.assertEqual(self.client.session['favorites'], [str(self.product.pk)])
        catalog = self.client.get(reverse('shop:hermes_catalog'))
        self.assertContains(catalog, 'aria-label="Удалить из избранного"')
        catalog_header = catalog.content.decode().split('</header>', 1)[0]
        self.assertNotIn('data-favorite-link', catalog_header)
        self.assertContains(catalog, 'data-things-tab="favorites"')
        detail = self.client.get(self.product.get_absolute_url())
        self.assertContains(detail, '>Удалить из избранного</span>')

        removed = self.client.post(
            toggle_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertFalse(removed.json()['is_favorite'])
        self.assertEqual(removed.json()['favorite_count'], 0)
        self.assertEqual(self.client.session['favorites'], [])

    def test_favorites_page_and_non_javascript_toggle(self):
        toggle_url = reverse('shop:favorite_toggle', args=[self.product.pk])
        favorites_url = reverse('shop:favorites')
        response = self.client.post(toggle_url, {'next': favorites_url})
        self.assertRedirects(response, favorites_url)

        favorites = self.client.get(favorites_url)
        self.assertContains(favorites, self.product.title)
        self.assertContains(favorites, 'Избранное (1)')
        self.assertContains(favorites, 'data-favorite-products')

        response = self.client.post(toggle_url, {'next': favorites_url})
        self.assertRedirects(response, favorites_url)
        empty = self.client.get(favorites_url)
        self.assertContains(empty, 'В избранном пока ничего нет')
        self.assertNotContains(empty, 'data-favorite-products')

    def test_authenticated_favorite_is_saved_in_database(self):
        user = get_user_model().objects.create_user(
            username='favorite@example.com', password='SecurePass-2026'
        )
        self.client.force_login(user)

        self.client.post(reverse('shop:favorite_toggle', args=[self.product.pk]))

        self.assertTrue(Favorite.objects.filter(user=user, product=self.product).exists())
        self.client.logout()
        self.client.force_login(user)
        response = self.client.get(reverse('shop:favorites'))
        self.assertContains(response, self.product.title)

    def test_product_card_gallery_is_rendered_for_multiple_images(self):
        images = product_payload()['images'] + [{
            'tag': 'detail', 'position': 1,
            'thumbnail': 'https://example.com/second-thumbnail.jpg',
            'medium': 'https://example.com/second-medium.jpg',
            'large': 'https://example.com/second-large.jpg',
            'original': 'https://example.com/second-original.jpg',
            'local_path': 'data/images/H-001-second.jpg',
        }]
        import_product(product_payload(images=images))
        self.product.images.update(alt='Устаревшее ручное описание')

        response = self.client.get(reverse('shop:hermes_catalog'))

        self.assertContains(response, 'data-card-gallery-image')
        self.assertContains(response, 'data-card-gallery-prev')
        self.assertContains(response, 'data-card-gallery-next')
        self.assertContains(response, 'data-card-gallery-dot', count=2)
        self.assertContains(response, 'data-card-gallery-slide', count=2)
        self.assertContains(response, 'src="https://example.com/second-original.jpg"')
        self.assertNotContains(response, 'Устаревшее ручное описание')
        self.assertContains(response, '<form class="favorite-form"', html=False)

        detail = self.client.get(self.product.get_absolute_url())
        self.assertContains(detail, 'data-detail-gallery')
        self.assertContains(detail, 'data-detail-gallery-prev')
        self.assertContains(detail, 'data-detail-gallery-dot', count=2)
        self.assertNotContains(detail, 'Устаревшее ручное описание')
        self.assertContains(detail, 'data-detail-gallery-next')
        self.assertContains(detail, 'data-alt="Тестовая сумка"')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountTests(TestCase):
    registration_data = {
        'first_name': 'Анна',
        'last_name': 'Иванова',
        'email': 'anna@example.com',
        'phone': '+7 999 123-45-67',
        'gender': 'women',
        'password1': 'SecurePass-2026',
        'password2': 'SecurePass-2026',
    }

    @override_settings(TELEGRAM_BOT_USERNAME='finder_demo_bot')
    def test_public_forms_use_shared_design_system_and_helpful_placeholders(self):
        login = self.client.get(reverse('shop:login'))
        registration = self.client.get(reverse('shop:register'))

        for response in (login, registration):
            self.assertContains(response, 'shop/css/forms.css')
            self.assertContains(response, 'shop/js/store.js')
        self.assertContains(login, 'placeholder="name@example.com"')
        self.assertContains(login, 'autocomplete="current-password"')
        self.assertContains(registration, 'placeholder="Ваше имя"')
        self.assertContains(registration, 'placeholder="Ваша фамилия"')
        self.assertContains(registration, 'placeholder="Не менее 8 символов"')
        self.assertContains(registration, 'Telegram (необязательно)')
        self.assertContains(registration, 'Запустить бота')

    @override_settings(TELEGRAM_BOT_USERNAME='')
    def test_registration_hides_telegram_actions_without_configured_bot(self):
        response = self.client.get(reverse('shop:register'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Telegram (необязательно)')
        self.assertNotContains(response, 'Запустить бота')

    def test_registration_gender_is_optional_and_saved_when_selected(self):
        response = self.register()

        self.assertRedirects(response, reverse('shop:verify_email'))
        profile = CustomerProfile.objects.get(user__email='anna@example.com')
        self.assertEqual(profile.gender, CustomerProfile.Gender.WOMEN)

        self.client.post(reverse('shop:logout'))
        optional_data = {
            **self.registration_data,
            'email': 'without-gender@example.com',
            'phone': '+7 999 123-45-68',
            'gender': '',
        }
        response = self.client.post(reverse('shop:register'), optional_data)

        self.assertRedirects(response, reverse('shop:verify_email'))
        self.assertEqual(
            CustomerProfile.objects.get(user__email='without-gender@example.com').gender,
            '',
        )

    def test_profile_can_change_gender_preference(self):
        user = get_user_model().objects.create_user(
            username='gender@example.com', email='gender@example.com',
            first_name='Анна', last_name='Иванова', password='SecurePass-2026',
        )
        profile = CustomerProfile.objects.create(
            user=user, phone='+79991234567', gender=CustomerProfile.Gender.WOMEN,
        )
        self.client.force_login(user)

        response = self.client.post(reverse('shop:account'), {
            'first_name': 'Анна', 'last_name': 'Иванова',
            'email': 'gender@example.com', 'phone': '+79991234567',
            'gender': CustomerProfile.Gender.MEN,
        })

        self.assertRedirects(response, reverse('shop:account'))
        profile.refresh_from_db()
        self.assertEqual(profile.gender, CustomerProfile.Gender.MEN)

    def test_header_account_link_depends_on_authentication(self):
        response = self.client.get(reverse('shop:home'))
        self.assertContains(response, f'href="{reverse("shop:login")}"')
        self.assertContains(response, f'href="{reverse("shop:register")}"')
        self.assertContains(response, 'Войти или зарегистрироваться')
        self.assertContains(response, 'data-account-menu')
        self.assertContains(response, 'Создать аккаунт')

        user = get_user_model().objects.create_user(
            username='user@example.com', email='user@example.com',
            first_name='Анна', password='SecurePass-2026'
        )
        self.client.force_login(user)
        response = self.client.get(reverse('shop:home'))
        self.assertContains(response, f'data-account-url="{reverse("shop:account")}"')
        self.assertContains(response, f'href="{reverse("shop:account")}?tab=profile"')
        self.assertContains(response, f'href="{reverse("shop:orders")}"')
        self.assertContains(response, f'href="{reverse("shop:product_request_list")}"')
        self.assertContains(response, 'account-status')
        self.assertContains(response, 'Анна')
        self.assertContains(response, f'action="{reverse("shop:logout")}"')

    def test_customer_phone_fields_use_tel_input_type(self):
        registration = self.client.get(reverse('shop:register'))
        self.assertContains(registration, 'name="phone"')
        self.assertContains(registration, 'type="tel"')

        user = get_user_model().objects.create_user(
            username='phone@example.com', email='phone@example.com',
            password='SecurePass-2026',
        )
        CustomerProfile.objects.create(user=user, phone='+79991234567')
        self.client.force_login(user)
        profile = self.client.get(reverse('shop:account'), {'tab': 'profile'})
        self.assertContains(profile, 'name="phone"')
        self.assertContains(profile, 'type="tel"')

    def test_account_tabs_and_saved_addresses_are_server_backed(self):
        user = get_user_model().objects.create_user(
            username='addresses@example.com', email='addresses@example.com',
            password='SecurePass-2026',
        )
        profile = CustomerProfile.objects.create(user=user, phone='+79991234567')
        self.client.force_login(user)

        first = self.client.post(reverse('shop:account_address_add'), {
            'label': 'Дом', 'city': 'Москва', 'street': 'Тверская, 1',
            'apartment': 'кв. 10', 'postal_code': '125009',
        })
        self.assertRedirects(first, f'{reverse("shop:account")}?tab=addresses')
        home = CustomerAddress.objects.get(user=user)
        self.assertTrue(home.is_default)

        second = self.client.post(reverse('shop:account_address_add'), {
            'label': 'Офис', 'city': 'Москва', 'street': 'Арбат, 2',
            'apartment': '', 'postal_code': '', 'is_default': 'on',
        })
        self.assertRedirects(second, f'{reverse("shop:account")}?tab=addresses')
        home.refresh_from_db()
        office = CustomerAddress.objects.get(user=user, label='Офис')
        self.assertFalse(home.is_default)
        self.assertTrue(office.is_default)
        self.assertEqual(CustomerAddress.objects.filter(user=user, is_default=True).count(), 1)

        page = self.client.get(reverse('shop:account'), {'tab': 'addresses'})
        self.assertContains(page, 'Дом')
        self.assertContains(page, 'Офис')
        self.assertContains(page, 'Добавить адрес')
        profile.refresh_from_db()
        self.assertEqual(profile.delivery_address, office.full_address)

        self.client.post(reverse('shop:account_address_delete', args=[office.pk]))
        home.refresh_from_db()
        self.assertTrue(home.is_default)

    def test_admin_button_is_visible_only_to_staff(self):
        regular = get_user_model().objects.create_user(
            username='buyer@example.com', email='buyer@example.com', password='SecurePass-2026'
        )
        self.client.force_login(regular)
        response = self.client.get(reverse('shop:account'))
        self.assertNotContains(response, 'Панель управления')
        self.assertNotContains(response, f'href="{reverse("admin:index")}"')

        regular.is_staff = True
        regular.save(update_fields=('is_staff',))
        response = self.client.get(reverse('shop:account'))
        self.assertContains(response, 'Панель управления')
        self.assertContains(response, f'href="{reverse("shop:control_dashboard")}"')

    def test_admin_login_uses_storefront_login_and_returns_staff_to_admin(self):
        admin_url = reverse('admin:index')
        response = self.client.get(reverse('admin:login'), {'next': admin_url})
        self.assertRedirects(
            response,
            f'{reverse("shop:login")}?next={admin_url}',
            fetch_redirect_response=False,
        )

        staff = get_user_model().objects.create_user(
            username='staff@example.com', email='staff@example.com',
            password='SecurePass-2026', is_staff=True,
        )
        response = self.client.post(reverse('shop:login'), {
            'username': staff.username,
            'password': 'SecurePass-2026',
            'next': admin_url,
        })
        self.assertRedirects(response, reverse('shop:admin_access_verify'))
        code = self.code_from_last_email()
        response = self.client.post(reverse('shop:admin_access_verify'), {'code': code})
        self.assertRedirects(response, admin_url)

    def test_authenticated_non_staff_cannot_enter_admin(self):
        buyer = get_user_model().objects.create_user(
            username='buyer@example.com', email='buyer@example.com', password='SecurePass-2026'
        )
        self.client.force_login(buyer)

        response = self.client.get(
            reverse('admin:index'), follow=True,
        )

        self.assertRedirects(response, reverse('shop:account'))
        self.assertContains(response, 'нет прав для доступа')

    def test_account_requires_login_and_keeps_next_url(self):
        response = self.client.get(reverse('shop:account'))

        self.assertRedirects(
            response,
            f'{reverse("shop:login")}?next={reverse("shop:account")}',
        )

    def test_registration_creates_and_logs_in_user(self):
        response = self.client.post(reverse('shop:register'), self.registration_data)

        self.assertRedirects(response, reverse('shop:verify_email'))
        user = get_user_model().objects.get(email='anna@example.com')
        self.assertEqual(user.username, 'anna@example.com')
        self.assertEqual(user.first_name, 'Анна')
        self.assertTrue(user.check_password('SecurePass-2026'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(user.customer_profile.phone, '+79991234567')
        self.assertEqual(user.customer_profile.telegram, '')
        self.assertFalse(user.customer_profile.email_verified)
        self.assertTrue(user.customer_profile.email_verification_code_hash)
        self.assertNotIn(self.code_from_last_email(), user.customer_profile.email_verification_code_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('FINDER', mail.outbox[0].subject)
        self.assertNotIn('AURÉNE', mail.outbox[0].subject)

    def test_registration_ignores_untrusted_telegram_username(self):
        response = self.client.post(reverse('shop:register'), {
            **self.registration_data,
            'telegram': 'https://t.me/Anna_shop?start=profile',
        })

        self.assertRedirects(response, reverse('shop:verify_email'))
        profile = CustomerProfile.objects.get(user__email='anna@example.com')
        self.assertEqual(profile.telegram, '')

    def test_invalid_untrusted_telegram_value_is_ignored(self):
        response = self.client.post(reverse('shop:register'), {
            **self.registration_data,
            'telegram': '@bad!',
        })

        self.assertRedirects(response, reverse('shop:verify_email'))
        self.assertTrue(get_user_model().objects.filter(email='anna@example.com').exists())

    def test_registration_rejects_duplicate_email_case_insensitively(self):
        get_user_model().objects.create_user(
            username='anna@example.com', email='anna@example.com', password='SecurePass-2026'
        )
        data = {**self.registration_data, 'email': 'ANNA@example.com'}

        response = self.client.post(reverse('shop:register'), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'уже зарегистрирован')
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_registration_rejects_email_matching_existing_username(self):
        get_user_model().objects.create_user(
            username='manager@example.com', email='', password='SecurePass-2026'
        )
        data = {**self.registration_data, 'email': 'MANAGER@example.com'}

        response = self.client.post(reverse('shop:register'), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'уже зарегистрирован')
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_login_by_email_and_post_only_logout(self):
        user = get_user_model().objects.create_user(
            username='anna@example.com', email='anna@example.com', password='SecurePass-2026'
        )

        response = self.client.post(reverse('shop:login'), {
            'username': 'ANNA@example.com', 'password': 'SecurePass-2026',
        })
        self.assertRedirects(response, reverse('shop:account'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(self.client.get(reverse('shop:logout')).status_code, 405)
        response = self.client.post(reverse('shop:logout'))
        self.assertRedirects(response, reverse('shop:home'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_does_not_redirect_to_external_next_url(self):
        get_user_model().objects.create_user(
            username='anna@example.com', email='anna@example.com', password='SecurePass-2026'
        )

        response = self.client.post(reverse('shop:login'), {
            'username': 'anna@example.com',
            'password': 'SecurePass-2026',
            'next': 'https://malicious.example/steal',
        })

        self.assertRedirects(response, reverse('shop:account'))

    def test_profile_updates_storefront_user_email_and_login(self):
        user = get_user_model().objects.create_user(
            username='anna@example.com', email='anna@example.com', password='SecurePass-2026'
        )
        CustomerProfile.objects.create(user=user, phone='+79991234567', email_verified=True)
        self.client.force_login(user)

        response = self.client.post(reverse('shop:account'), {
            'first_name': 'Мария', 'last_name': 'Петрова',
            'email': 'maria@example.com', 'phone': '8 (999) 765-43-21',
        })

        self.assertRedirects(response, reverse('shop:verify_email'))
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Мария')
        self.assertEqual(user.email, 'maria@example.com')
        self.assertEqual(user.username, 'maria@example.com')
        self.assertEqual(user.customer_profile.phone, '+79997654321')
        self.assertFalse(user.customer_profile.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.client.post(reverse('shop:logout'))
        self.assertTrue(self.client.login(username='maria@example.com', password='SecurePass-2026'))

    def test_profile_saves_telegram_address_and_contact_method(self):
        user = get_user_model().objects.create_user(
            username='profile@example.com',
            email='profile@example.com',
            first_name='Анна',
            last_name='Иванова',
            password='SecurePass-2026',
        )
        profile = CustomerProfile.objects.create(
            user=user, phone='+79991234567', email_verified=True
        )
        self.client.force_login(user)

        response = self.client.post(reverse('shop:account'), {
            'first_name': 'Анна',
            'last_name': 'Иванова',
            'email': 'profile@example.com',
            'phone': '+7 999 123-45-67',
            'delivery_address': 'Москва, ул. Арбат, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
        })

        self.assertRedirects(response, reverse('shop:account'))
        profile.refresh_from_db()
        self.assertEqual(profile.telegram, '')
        self.assertEqual(profile.delivery_address, 'Москва, ул. Арбат, д. 10')
        self.assertEqual(profile.preferred_contact, 'phone')

    def test_profile_contact_method_syncs_existing_orders_and_requests(self):
        user = get_user_model().objects.create_user(
            username='sync@example.com', email='sync@example.com', password='SecurePass-2026',
        )
        profile = CustomerProfile.objects.create(user=user, phone='+79991234567')
        order = Order.objects.create(
            user=user, customer_name='Иван Иванов', customer_email=user.email,
            customer_phone=profile.phone, delivery_address='Москва',
            preferred_contact=CustomerProfile.ContactMethod.PHONE,
        )
        personal = ProductRequest.objects.create(
            user=user, customer_name='Иван Иванов', customer_email=user.email,
            customer_phone=profile.phone,
            preferred_contact=CustomerProfile.ContactMethod.PHONE,
        )
        other_user = get_user_model().objects.create_user(
            username='other-sync@example.com', email='other-sync@example.com',
        )
        other = Order.objects.create(
            user=other_user, customer_name='Другой клиент', customer_email=other_user.email,
            customer_phone='+79990000000', delivery_address='Москва',
            preferred_contact=CustomerProfile.ContactMethod.PHONE,
        )

        profile.preferred_contact = CustomerProfile.ContactMethod.WHATSAPP
        profile.save(update_fields=('preferred_contact',))

        order.refresh_from_db()
        personal.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(order.preferred_contact, CustomerProfile.ContactMethod.WHATSAPP)
        self.assertEqual(personal.preferred_contact, CustomerProfile.ContactMethod.WHATSAPP)
        self.assertEqual(other.preferred_contact, CustomerProfile.ContactMethod.PHONE)

    def code_from_last_email(self):
        match = re.search(r'\b(\d{6})\b', mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def register(self, **overrides):
        return self.client.post(
            reverse('shop:register'), {**self.registration_data, **overrides}
        )

    def test_phone_normalization_supports_common_russian_formats(self):
        self.assertEqual(normalize_phone('+7 (999) 123-45-67'), '+79991234567')
        self.assertEqual(normalize_phone('8 (999) 123-45-67'), '+79991234567')
        self.assertEqual(normalize_phone('7 999 123 45 67'), '+79991234567')
        self.assertEqual(normalize_phone('999 123 45 67'), '+79991234567')
        self.assertEqual(normalize_phone('+44 20 7946 0958'), '+442079460958')

    def test_phone_normalization_rejects_incomplete_and_too_long_numbers(self):
        invalid_numbers = (
            '+7', '8', '123', '+7 999 123-45-67-89',
            '8 999 123-45-67-89', '+44 20 7946 0958 1234',
            '999 123 45 678', '+7 call 9991234567', '++79991234567',
        )
        for phone in invalid_numbers:
            with self.subTest(phone=phone), self.assertRaises(ValueError):
                normalize_phone(phone)

    def test_registration_rejects_too_long_russian_phone(self):
        response = self.register(phone='+7 999 123-45-67-89')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'должен содержать 10 цифр после +7')
        self.assertFalse(get_user_model().objects.filter(email='anna@example.com').exists())

    def test_telegram_normalization_supports_username_and_link(self):
        self.assertEqual(normalize_telegram('@anna_shop'), '@anna_shop')
        self.assertEqual(normalize_telegram('t.me/anna_shop'), '@anna_shop')
        self.assertEqual(normalize_telegram(''), '')
        with self.assertRaises(ValueError):
            normalize_telegram('@bad!')

    def test_registration_rejects_duplicate_normalized_phone(self):
        user = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='SecurePass-2026'
        )
        CustomerProfile.objects.create(user=user, phone='+79991234567')

        response = self.register(phone='8 (999) 123-45-67')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'номер телефона уже используется')

    def test_correct_email_code_verifies_address(self):
        self.register()
        code = self.code_from_last_email()

        response = self.client.post(reverse('shop:verify_email'), {'code': code})

        self.assertRedirects(response, reverse('shop:account'))
        profile = CustomerProfile.objects.get(user__email='anna@example.com')
        self.assertTrue(profile.email_verified)
        self.assertEqual(profile.email_verification_code_hash, '')

    def test_wrong_and_expired_email_codes_are_rejected(self):
        self.register()
        profile = CustomerProfile.objects.get(user__email='anna@example.com')

        response = self.client.post(reverse('shop:verify_email'), {'code': '111111'})
        self.assertContains(response, 'Неверный код')
        profile.refresh_from_db()
        self.assertEqual(profile.email_verification_attempts, 1)

        profile.email_verification_expires_at = timezone.now() - timedelta(seconds=1)
        profile.save(update_fields=('email_verification_expires_at',))
        response = self.client.post(
            reverse('shop:verify_email'), {'code': self.code_from_last_email()}
        )
        self.assertContains(response, 'Срок действия кода истёк')
        self.assertFalse(CustomerProfile.objects.get(pk=profile.pk).email_verified)

    def test_email_code_is_blocked_after_five_wrong_attempts(self):
        self.register()
        correct_code = self.code_from_last_email()
        for _ in range(5):
            self.client.post(reverse('shop:verify_email'), {'code': '111111'})

        response = self.client.post(
            reverse('shop:verify_email'), {'code': correct_code}
        )

        self.assertContains(response, 'Превышено количество попыток')
        profile = CustomerProfile.objects.get(user__email='anna@example.com')
        self.assertFalse(profile.email_verified)
        self.assertEqual(profile.email_verification_attempts, 5)

    def test_resend_is_post_only_and_rate_limited(self):
        self.register()
        self.assertEqual(
            self.client.get(reverse('shop:resend_email_verification')).status_code, 405
        )

        response = self.client.post(reverse('shop:resend_email_verification'))

        self.assertRedirects(response, reverse('shop:verify_email'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(any(
            'Новый код можно запросить через' in message.message
            for message in response.wsgi_request._messages
        ))

        profile = CustomerProfile.objects.get(user__email='anna@example.com')
        profile.email_verification_sent_at = timezone.now() - timedelta(minutes=2)
        profile.save(update_fields=('email_verification_sent_at',))
        response = self.client.post(reverse('shop:resend_email_verification'))
        self.assertRedirects(response, reverse('shop:verify_email'))
        self.assertEqual(len(mail.outbox), 2)

    def test_password_change_keeps_user_logged_in(self):
        user = get_user_model().objects.create_user(
            username='anna@example.com', email='anna@example.com', password='SecurePass-2026'
        )
        self.client.force_login(user)

        response = self.client.post(reverse('shop:account_password'), {
            'old_password': 'SecurePass-2026',
            'new_password1': 'NewSecurePass-2026',
            'new_password2': 'NewSecurePass-2026',
        })

        self.assertRedirects(response, reverse('shop:account'))
        user.refresh_from_db()
        self.assertTrue(user.check_password('NewSecurePass-2026'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)


class ProductRequestTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='buyer@example.com',
            email='buyer@example.com',
            first_name='Анна',
            last_name='Иванова',
            password='SecurePass-2026',
        )
        self.profile = CustomerProfile.objects.create(
            user=self.user,
            phone='+79991234567',
            email_verified=True,
        )

    @staticmethod
    def formset_data(items):
        data = {
            'items-TOTAL_FORMS': str(len(items)),
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '10',
        }
        for index, item in enumerate(items):
            data.update({
                f'items-{index}-brand': item.get('brand', ''),
                f'items-{index}-product_name': item.get('product_name', ''),
                f'items-{index}-article': item.get('article', ''),
                f'items-{index}-color': item.get('color', ''),
                f'items-{index}-size': item.get('size', ''),
            })
        return data

    def test_guest_can_open_and_fill_personal_order_form(self):
        response = self.client.get(reverse('shop:product_request_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Расскажите, что вы ищете.')
        self.assertContains(response, 'name="customer_name"')
        self.assertContains(response, 'name="contact"')

    def test_guest_personal_order_is_created_automatically_after_login(self):
        response = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([{'brand': 'Hermès', 'product_name': 'Kelly 25'}]),
        )

        resume_url = f'{reverse("shop:product_request_create")}?resume=1'
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('shop:login')))
        self.assertIn('next=', response.url)
        self.assertFalse(ProductRequest.objects.exists())

        response = self.client.post(reverse('shop:login'), {
            'username': self.user.username,
            'password': 'SecurePass-2026',
            'next': resume_url,
        }, follow=True)

        product_request = ProductRequest.objects.get()
        self.assertRedirects(response, product_request.get_absolute_url())
        self.assertEqual(product_request.user, self.user)
        self.assertEqual(product_request.items.get().product_name, 'Kelly 25')
        self.assertNotIn('pending_product_request', self.client.session)

        self.client.get(resume_url)
        self.assertEqual(ProductRequest.objects.count(), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_guest_personal_order_is_created_after_registration_and_email_verification(self):
        response = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([{'brand': 'Chanel', 'product_name': 'Classic Flap'}]),
        )
        resume_url = f'{reverse("shop:product_request_create")}?resume=1'
        self.assertTrue(response.url.startswith(reverse('shop:login')))

        response = self.client.post(reverse('shop:register'), {
            'first_name': 'Мария',
            'last_name': 'Соколова',
            'email': 'new-request@example.com',
            'phone': '+7 999 555-44-33',
            'gender': '',
            'password1': 'SecurePass-2026',
            'password2': 'SecurePass-2026',
            'next': resume_url,
        })
        self.assertRedirects(response, reverse('shop:verify_email'))
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)

        response = self.client.post(
            reverse('shop:verify_email'), {'code': code}, follow=True,
        )

        product_request = ProductRequest.objects.get()
        self.assertRedirects(response, product_request.get_absolute_url())
        self.assertEqual(product_request.user.email, 'new-request@example.com')
        self.assertEqual(product_request.customer_phone, '+79995554433')
        self.assertEqual(product_request.items.get().product_name, 'Classic Flap')
        self.assertNotIn('pending_product_request', self.client.session)

    def test_pending_personal_order_resumes_after_profile_completion(self):
        self.profile.phone = None
        self.profile.save(update_fields=('phone',))
        response = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([{'brand': 'Dior', 'product_name': 'Lady Dior'}]),
        )
        resume_url = f'{reverse("shop:product_request_create")}?resume=1'
        self.assertTrue(response.url.startswith(reverse('shop:login')))

        response = self.client.post(reverse('shop:login'), {
            'username': self.user.username,
            'password': 'SecurePass-2026',
            'next': resume_url,
        })
        self.assertRedirects(response, resume_url, fetch_redirect_response=False)
        response = self.client.get(resume_url)
        profile_url = (
            f'{reverse("shop:account")}?tab=profile&next=%2Frequest-product%2F%3Fresume%3D1'
        )
        self.assertRedirects(response, profile_url, fetch_redirect_response=False)

        response = self.client.post(reverse('shop:account'), {
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'email': self.user.email,
            'phone': '+7 999 123-45-67',
            'delivery_address': '',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
            'gender': '',
            'next': resume_url,
        }, follow=True)

        product_request = ProductRequest.objects.get()
        self.assertRedirects(response, product_request.get_absolute_url())
        self.assertEqual(product_request.customer_phone, '+79991234567')
        self.assertNotIn('pending_product_request', self.client.session)

    def test_request_page_allows_contact_to_be_entered_when_profile_has_no_phone(self):
        self.profile.phone = None
        self.profile.save(update_fields=('phone',))
        self.client.force_login(self.user)

        response = self.client.get(reverse('shop:product_request_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="contact"')
        self.assertContains(response, f'value="{self.user.email}"')

    def test_request_form_matches_compact_finder_reference(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('shop:product_request_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="request-title">Найти вещь</h1>', html=True)
        self.assertContains(response, 'Расскажите, что вы ищете.')
        self.assertContains(response, 'Отправить запрос')
        self.assertContains(response, 'name="query"')
        self.assertContains(response, 'placeholder="Бренд, модель, цвет, размер"')
        self.assertContains(response, 'name="customer_name"')
        self.assertContains(response, 'name="contact"')
        self.assertContains(response, 'name="reference_image"')
        self.assertContains(response, 'type="file"')
        self.assertContains(response, 'Приложить фото')
        self.assertContains(response, 'name="consent"')
        self.assertContains(response, 'Согласен на обработку персональных данных')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'product_request.js')
        self.assertNotContains(response, 'Добавить ещё товар')

    def test_guest_can_submit_reference_form_with_photo(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse('shop:product_request_create'), {
                'query': 'Hermès, Kelly 25, чёрная, размер 25',
                'customer_name': 'Анна',
                'contact': '@anna_shop',
                'reference_image': image_upload('finder-reference.jpg', size=(800, 600)),
                'consent': 'on',
            })

            product_request = ProductRequest.objects.get()
            item = product_request.items.get()
            self.assertRedirects(response, reverse('shop:home'))
            self.assertIsNone(product_request.user)
            self.assertEqual(product_request.customer_name, 'Анна')
            self.assertEqual(product_request.customer_telegram, '@anna_shop')
            self.assertEqual(
                product_request.preferred_contact,
                CustomerProfile.ContactMethod.TELEGRAM,
            )
            self.assertEqual(item.product_name, 'Hermès, Kelly 25, чёрная, размер 25')
            self.assertTrue(item.reference_image.name.endswith('.webp'))

    def test_reference_form_requires_consent(self):
        response = self.client.post(reverse('shop:product_request_create'), {
            'query': 'Chanel Classic Flap',
            'customer_name': 'Мария',
            'contact': 'maria@example.com',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Подтвердите согласие')
        self.assertFalse(ProductRequest.objects.exists())

    def test_user_can_submit_without_optional_article_color_and_size(self):
        self.profile.telegram = '@anna_shop'
        self.profile.delivery_address = 'Москва, ул. Тверская, д. 1'
        self.profile.preferred_contact = CustomerProfile.ContactMethod.WHATSAPP
        self.profile.save(update_fields=(
            'telegram', 'delivery_address', 'preferred_contact',
        ))
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([{
                'brand': 'Hermès', 'product_name': 'Kelly 25',
            }]),
        )

        product_request = ProductRequest.objects.get()
        self.assertRedirects(response, product_request.get_absolute_url())
        self.assertEqual(product_request.user, self.user)
        self.assertEqual(product_request.customer_telegram, '@anna_shop')
        self.assertEqual(
            product_request.preferred_contact,
            CustomerProfile.ContactMethod.WHATSAPP,
        )
        self.assertEqual(
            product_request.delivery_address,
            'Москва, ул. Тверская, д. 1',
        )

    def test_personal_order_saves_attribution_and_ignores_removed_link_field(self):
        self.client.force_login(self.user)
        self.client.get(
            reverse('shop:product_request_create')
            + '?utm_source=vk&utm_medium=cpc&utm_campaign=retarget&utm_content=banner-2'
        )
        data = self.formset_data([{
            'brand': 'Chanel', 'product_name': 'Classic Flap',
        }])
        data['items-0-source_url'] = 'https://example.com/classic-flap'
        response = self.client.post(reverse('shop:product_request_create'), data)

        lead = ProductRequest.objects.get()
        self.assertRedirects(response, lead.get_absolute_url())
        self.assertEqual(lead.lead_status, ProductRequest.LeadStatus.NEW)
        self.assertEqual(lead.source, 'vk')
        self.assertEqual(lead.utm_medium, 'cpc')
        self.assertEqual(lead.utm_campaign, 'retarget')
        self.assertEqual(lead.utm_content, 'banner-2')
        self.assertEqual(lead.first_source, 'vk')
        self.assertIsNotNone(lead.analytics_visitor_id)
        self.assertIsNotNone(lead.analytics_session_id)
        self.assertEqual(lead.items.get().source_url, '')

    def test_user_can_add_and_submit_several_items_with_all_details(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([
                {
                    'brand': 'Hermès', 'product_name': 'Kelly 25',
                    'article': 'H123456', 'color': 'Чёрный', 'size': '25',
                },
                {
                    'brand': 'Chanel', 'product_name': 'Classic Flap',
                    'article': 'A01112', 'color': 'Чёрный',
                },
            ]),
        )

        product_request = ProductRequest.objects.get()
        self.assertRedirects(response, product_request.get_absolute_url())
        self.assertEqual(product_request.items.count(), 2)
        self.assertEqual(
            list(product_request.items.values_list(
                'brand', 'product_name', 'article', 'color', 'size'
            )),
            [
                ('Hermès', 'Kelly 25', 'H123456', 'Чёрный', '25'),
                ('Chanel', 'Classic Flap', 'A01112', 'Чёрный', ''),
            ],
        )
        detail = self.client.get(product_request.get_absolute_url())
        self.assertContains(detail, 'Hermès')
        self.assertContains(detail, 'H123456')
        self.assertContains(detail, 'Chanel')
        self.assertContains(detail, 'A01112')

    def test_brand_and_product_name_are_required(self):
        self.client.force_login(self.user)

        missing_brand = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([{'brand': '', 'product_name': 'Kelly 25'}]),
        )
        self.assertEqual(missing_brand.status_code, 200)
        self.assertIn('brand', missing_brand.context['formset'].errors[0])
        self.assertFalse(ProductRequest.objects.exists())

        missing_name = self.client.post(
            reverse('shop:product_request_create'),
            self.formset_data([{'brand': 'Hermès', 'product_name': ''}]),
        )
        self.assertEqual(missing_name.status_code, 200)
        self.assertIn('product_name', missing_name.context['formset'].errors[0])
        self.assertFalse(ProductRequest.objects.exists())

    def test_deleted_additional_item_is_not_saved(self):
        self.client.force_login(self.user)
        data = self.formset_data([
            {'brand': 'Hermès', 'product_name': 'Kelly 25'},
            {'brand': 'Chanel', 'product_name': 'Удалённый товар'},
        ])
        data['items-1-DELETE'] = 'on'

        response = self.client.post(reverse('shop:product_request_create'), data)

        product_request = ProductRequest.objects.get()
        self.assertRedirects(response, product_request.get_absolute_url())
        self.assertEqual(product_request.items.count(), 1)
        self.assertEqual(product_request.items.get().product_name, 'Kelly 25')

    def test_user_sees_only_own_requests(self):
        own = ProductRequest.objects.create(
            user=self.user,
            customer_name='Анна Иванова',
            customer_email=self.user.email,
            customer_phone=self.profile.phone,
        )
        own.items.create(brand='Hermès', product_name='Мой товар')
        other_user = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='SecurePass-2026'
        )
        foreign = ProductRequest.objects.create(
            user=other_user,
            customer_name='Другой клиент',
            customer_email=other_user.email,
            customer_phone='+79997654321',
        )
        foreign.items.create(brand='Chanel', product_name='Чужой товар')
        self.client.force_login(self.user)

        listing = self.client.get(reverse('shop:product_request_list'))
        self.assertContains(listing, own.number)
        self.assertNotContains(listing, foreign.number)
        self.assertContains(listing, 'Hermès — Мой товар')
        self.assertNotContains(listing, 'Chanel — Чужой товар')
        self.assertEqual(self.client.get(foreign.get_absolute_url()).status_code, 404)
        detail = self.client.get(own.get_absolute_url())
        self.assertContains(detail, 'Мой товар')
        self.assertContains(detail, own.number)


class CheckoutTests(TestCase):
    def setUp(self):
        self.product, _ = import_product(product_payload())
        self.user = get_user_model().objects.create_user(
            username='checkout@example.com',
            email='checkout@example.com',
            first_name='Анна',
            last_name='Иванова',
            password='SecurePass-2026',
        )
        self.profile = CustomerProfile.objects.create(
            user=self.user,
            phone='+79991234567',
            telegram='@anna_shop',
            email_verified=True,
        )

    def add_to_cart(self, quantity=1):
        session = self.client.session
        session['cart'] = {str(self.product.pk): quantity}
        session.save()

    def test_cart_shows_estimated_total_note_and_checkout_link(self):
        self.add_to_cart(2)

        response = self.client.get(reverse('shop:cart'))

        self.assertContains(response, 'Стоимость товаров')
        self.assertNotContains(response, 'Подытог')
        self.assertContains(response, 'Это предварительная стоимость заказа')
        self.assertContains(response, reverse('shop:checkout'))

    def test_checkout_exposes_three_progressive_steps(self):
        self.add_to_cart()

        response = self.client.get(reverse('shop:checkout'))

        self.assertContains(response, 'data-checkout-steps')
        self.assertContains(response, 'data-checkout-step="1"')
        self.assertContains(response, 'data-checkout-step="2"')
        self.assertContains(response, 'data-checkout-step="3"')
        self.assertContains(response, 'data-checkout-next', count=2)
        self.assertContains(response, 'data-checkout-back', count=2)

    def test_checkout_displays_delivery_tariffs_and_initial_total(self):
        self.add_to_cart()
        response = self.client.get(reverse('shop:checkout'))
        self.assertEqual(response.context['checkout_delivery'], Decimal('590'))
        self.assertEqual(response.context['checkout_total'], self.product.display_price + 590)
        for method, price in [('courier', '590'), ('express', '1 490'), ('international', '3 900')]:
            with self.subTest(method=method):
                self.assertContains(response, f'value="{method}"')
                self.assertContains(response, f'{price} ₽')
        self.assertContains(response, 'data-delivery-value')
        self.assertContains(response, 'name="payment_method"')

    def test_delivery_and_discount_are_saved_and_visible_in_order_api_and_account(self):
        from .api import serialize_order

        for method, cost in [('courier', 590), ('express', 1490), ('international', 3900)]:
            with self.subTest(method=method):
                self.add_to_cart(2)
                self.client.force_login(self.user)
                response = self.client.post(reverse('shop:checkout'), {
                    'delivery_address': 'Москва, ул. Тверская, д. 10',
                    'preferred_contact': CustomerProfile.ContactMethod.PHONE,
                    'delivery_method': method,
                    'payment_method': Order.PaymentMethod.SBP,
                    'promo_code': 'welcome10',
                })
                order = Order.objects.latest('pk')
                discount = (order.subtotal * Decimal('0.10')).quantize(Decimal('0.01'))
                self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
                self.assertEqual(order.delivery_method, method)
                self.assertEqual(order.delivery_cost, cost)
                self.assertEqual(order.payment_method, Order.PaymentMethod.SBP)
                self.assertEqual(order.estimated_total, order.subtotal - discount + cost)
                data = serialize_order(order)
                self.assertEqual(data['delivery_method'], method)
                self.assertEqual(data['delivery_cost'], f'{cost:.2f}')
                self.assertEqual(data['payment_method'], 'sbp')
                detail = self.client.get(order.get_absolute_url())
                account = self.client.get(reverse('shop:account'), {'tab': 'orders'})
                for page in (detail, account):
                    self.assertContains(page, order.get_delivery_method_display())
                    self.assertContains(page, order.get_payment_method_display())
                    self.assertContains(page, order.promo_code)

    def test_guest_delivery_selection_survives_login(self):
        self.add_to_cart()
        self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
            'delivery_method': Order.DeliveryMethod.INTERNATIONAL,
            'payment_method': Order.PaymentMethod.COD,
        })
        self.assertEqual(self.client.session['pending_checkout']['delivery_method'], 'international')
        self.client.force_login(self.user)
        response = self.client.get(reverse('shop:checkout') + '?resume=1')
        order = Order.objects.get()
        self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
        self.assertEqual(order.delivery_cost, Decimal('3900'))
        self.assertEqual(order.payment_method, Order.PaymentMethod.COD)

    def test_invalid_delivery_method_does_not_create_order(self):
        self.add_to_cart()
        self.client.force_login(self.user)
        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
            'delivery_method': 'not-a-tariff',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('delivery_method', response.context['form'].errors)
        self.assertFalse(Order.objects.exists())

    def test_guest_can_open_checkout_and_empty_cart_is_rejected(self):
        self.add_to_cart()
        checkout_url = reverse('shop:checkout')
        response = self.client.get(checkout_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Затем войдите или зарегистрируйтесь')

        self.client.force_login(self.user)
        session = self.client.session
        session['cart'] = {}
        session.save()
        response = self.client.get(checkout_url, follow=True)
        self.assertRedirects(response, reverse('shop:cart'))
        self.assertContains(response, 'Добавьте товары в корзину')

    def test_guest_checkout_is_created_automatically_after_login(self):
        self.add_to_cart(2)
        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10, кв. 5',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
        })

        resume_url = f'{reverse("shop:checkout")}?resume=1'
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('shop:login')))
        self.assertFalse(Order.objects.exists())

        response = self.client.post(reverse('shop:login'), {
            'username': self.user.username,
            'password': 'SecurePass-2026',
            'next': resume_url,
        }, follow=True)

        order = Order.objects.get()
        self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.items.get().quantity, 2)
        self.assertNotIn('pending_checkout', self.client.session)

    def test_pending_checkout_resumes_after_profile_completion(self):
        self.profile.phone = None
        self.profile.save(update_fields=('phone',))
        self.add_to_cart()
        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Казань, ул. Баумана, д. 15',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
        })
        resume_url = f'{reverse("shop:checkout")}?resume=1'
        self.assertTrue(response.url.startswith(reverse('shop:login')))

        response = self.client.post(reverse('shop:login'), {
            'username': self.user.username,
            'password': 'SecurePass-2026',
            'next': resume_url,
        })
        self.assertRedirects(response, resume_url, fetch_redirect_response=False)
        response = self.client.get(resume_url)
        profile_url = (
            f'{reverse("shop:account")}?tab=profile&next=%2Fcart%2Fcheckout%2F%3Fresume%3D1'
        )
        self.assertRedirects(response, profile_url, fetch_redirect_response=False)

        response = self.client.post(reverse('shop:account'), {
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'email': self.user.email,
            'phone': '+7 999 123-45-67',
            'delivery_address': '',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
            'gender': '',
            'next': resume_url,
        }, follow=True)

        order = Order.objects.get()
        self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
        self.assertEqual(order.customer_phone, '+79991234567')
        self.assertEqual(order.delivery_address, 'Казань, ул. Баумана, д. 15')
        self.assertNotIn('pending_checkout', self.client.session)

        self.client.get(resume_url)
        self.assertEqual(Order.objects.count(), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_guest_checkout_is_created_after_registration_and_email_verification(self):
        self.add_to_cart(2)
        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Санкт-Петербург, Невский проспект, д. 20',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
        })
        resume_url = f'{reverse("shop:checkout")}?resume=1'
        self.assertTrue(response.url.startswith(reverse('shop:login')))

        response = self.client.post(reverse('shop:register'), {
            'first_name': 'Елена',
            'last_name': 'Орлова',
            'email': 'new-checkout@example.com',
            'phone': '+7 999 222-11-00',
            'gender': '',
            'password1': 'SecurePass-2026',
            'password2': 'SecurePass-2026',
            'next': resume_url,
        })
        self.assertRedirects(response, reverse('shop:verify_email'))
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)

        response = self.client.post(
            reverse('shop:verify_email'), {'code': code}, follow=True,
        )

        order = Order.objects.get()
        self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
        self.assertEqual(order.user.email, 'new-checkout@example.com')
        self.assertEqual(order.customer_phone, '+79992221100')
        self.assertEqual(order.items.get().quantity, 2)
        self.assertNotIn('pending_checkout', self.client.session)

    def test_checkout_displays_profile_contacts_and_saved_address(self):
        self.profile.delivery_address = 'Москва, ул. Тверская, д. 1'
        self.profile.preferred_contact = CustomerProfile.ContactMethod.TELEGRAM
        self.profile.save(update_fields=('delivery_address', 'preferred_contact'))
        self.add_to_cart()
        self.client.force_login(self.user)

        response = self.client.get(reverse('shop:checkout'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'checkout@example.com')
        self.assertContains(response, '+79991234567')
        self.assertContains(response, '@anna_shop')
        self.assertContains(response, 'Москва, ул. Тверская, д. 1')
        self.assertEqual(
            response.context['form']['preferred_contact'].value(),
            CustomerProfile.ContactMethod.TELEGRAM,
        )

    def test_checkout_creates_order_saves_address_and_clears_cart(self):
        self.add_to_cart(2)
        self.client.force_login(self.user)

        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10, кв. 5',
            'preferred_contact': CustomerProfile.ContactMethod.TELEGRAM,
        })

        order = Order.objects.get()
        self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
        self.assertEqual(order.customer_name, 'Анна Иванова')
        self.assertEqual(order.customer_email, 'checkout@example.com')
        self.assertEqual(order.customer_phone, '+79991234567')
        self.assertEqual(order.customer_telegram, '@anna_shop')
        self.assertEqual(order.preferred_contact, CustomerProfile.ContactMethod.TELEGRAM)
        self.assertEqual(order.delivery_address, 'Москва, ул. Тверская, д. 10, кв. 5')
        self.assertEqual(order.estimated_total, self.product.display_price * 2)
        self.assertEqual(order.currency, 'RUB')
        self.assertIsNotNone(order.analytics_visitor_id)
        self.assertIsNotNone(order.analytics_session_id)
        self.assertEqual(order.first_source, 'direct')
        self.assertEqual(order.source, 'direct')
        item = order.items.get()
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.product_title, self.product.title)
        self.assertEqual(item.sku, self.product.sku)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.unit_price, self.product.display_price)
        self.assertEqual(item.currency, 'RUB')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.delivery_address, order.delivery_address)
        self.assertEqual(self.profile.preferred_contact, 'telegram')
        self.assertEqual(self.client.session.get('cart'), {})

        success = self.client.get(reverse('shop:order_success', args=[order.pk]))
        self.assertContains(success, 'Ваш заказ оформлен')
        self.assertContains(success, 'личный менеджер свяжется с вами')
        self.assertContains(success, reverse('shop:orders'))
        self.assertContains(success, 'Продолжить покупки')

    def test_welcome10_promo_is_applied_and_saved_on_order(self):
        self.add_to_cart(2)
        self.client.force_login(self.user)

        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
            'promo_code': 'welcome10',
        })

        order = Order.objects.get()
        subtotal = self.product.display_price * 2
        discount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'))
        self.assertRedirects(response, reverse('shop:order_success', args=[order.pk]))
        self.assertEqual(order.subtotal, subtotal)
        self.assertEqual(order.discount_amount, discount)
        self.assertEqual(order.estimated_total, subtotal - discount)
        self.assertEqual(order.promo_code, 'WELCOME10')

    def test_repeat_order_restores_available_items_to_cart(self):
        self.client.force_login(self.user)
        order = Order.objects.create(
            user=self.user, customer_name='Анна Иванова',
            customer_email=self.user.email, customer_phone=self.profile.phone,
            delivery_address='Москва', estimated_total=self.product.display_price,
            subtotal=self.product.display_price, currency='RUB',
        )
        order.items.create(
            product=self.product, product_title=self.product.title,
            brand=self.product.brand, sku=self.product.sku,
            unit_price=self.product.display_price, currency='RUB', quantity=2,
        )

        response = self.client.post(reverse('shop:order_repeat', args=[order.pk]))

        self.assertRedirects(response, reverse('shop:cart'))
        self.assertEqual(self.client.session['cart'][str(self.product.pk)], 2)
        self.assertEqual(
            CartItem.objects.get(user=self.user, product=self.product).quantity, 2,
        )

    def test_telegram_contact_requires_telegram_in_profile(self):
        self.profile.telegram = ''
        self.profile.save(update_fields=('telegram',))
        self.add_to_cart()
        self.client.force_login(self.user)

        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.TELEGRAM,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Добавьте Telegram в личном кабинете')
        self.assertFalse(Order.objects.exists())
        self.assertTrue(self.client.session.get('cart'))

    def test_orders_page_combines_cart_and_personal_orders(self):
        self.add_to_cart()
        self.client.force_login(self.user)
        self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
        })
        personal = ProductRequest.objects.create(
            user=self.user,
            customer_name='Анна Иванова',
            customer_email=self.user.email,
            customer_phone=self.profile.phone,
            customer_telegram=self.profile.telegram,
        )
        personal.items.create(brand='Hermès', product_name='Birkin 25')

        response = self.client.get(reverse('shop:orders'))

        self.assertContains(response, 'Личный кабинет')
        self.assertContains(response, 'История покупок')
        self.assertContains(response, 'Персональный заказ')
        self.assertContains(response, Order.objects.get().number)
        self.assertContains(response, personal.number)

    def test_user_cannot_open_another_users_order(self):
        self.add_to_cart()
        self.client.force_login(self.user)
        self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 10',
            'preferred_contact': CustomerProfile.ContactMethod.EMAIL,
        })
        order = Order.objects.get()
        another = get_user_model().objects.create_user(
            username='another@example.com', password='SecurePass-2026'
        )
        self.client.force_login(another)

        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('shop:order_success', args=[order.pk])).status_code,
            404,
        )
