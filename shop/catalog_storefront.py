"""Единая мультибрендовая витрина FINDER и брендовых бутиков."""

from decimal import Decimal, InvalidOperation
from time import monotonic

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from .availability import available_products, available_size_variants, order_size_variant
from .models import CatalogBrand, Product
from .catalog_data import COLOR_GROUP_BY_SLUG, COLOR_GROUPS


CATALOG_TEMPLATE = 'shop/catalog/catalog.html'
PRODUCT_TEMPLATE = 'shop/catalog/product_detail.html'
CATALOG_METADATA_CACHE_TTL_SECONDS = 30
_CATALOG_METADATA_CACHE = {}
CATEGORY_DEPARTMENT_ORDER = {
    'women': 0,
    'men': 1,
    'jewelry': 2,
    'fragrances_makeup': 3,
    'watches': 4,
    'leather_goods': 5,
    'home': 6,
    'equestrian': 7,
    'gifts': 8,
}

FASHION_NAVIGATION = {
    'women': {
        'key': 'women', 'name': 'Женщины', 'all_gender': True,
        'children': [
            {
                'key': 'women/clothing', 'name': 'Одежда',
                'children': [
                    {'key': 'women/clothing/outerwear', 'name': 'Верхняя одежда', 'source_keys': ('women/clothing/outerwear',)},
                    {'key': 'women/clothing/tshirts_tops', 'name': 'Футболки и топы', 'source_keys': ('women/clothing/tshirts_tops',)},
                    {'key': 'women/clothing/shirts_blouses', 'name': 'Рубашки и блузы', 'source_keys': ('women/clothing/shirts_blouses',)},
                    {'key': 'women/clothing/knitwear', 'name': 'Трикотаж', 'source_keys': ('women/clothing/knitwear',)},
                    {'key': 'women/clothing/sweatshirts', 'name': 'Толстовки', 'source_keys': ('women/clothing/sweatshirts',)},
                    {'key': 'women/clothing/trousers_shorts', 'name': 'Брюки и шорты', 'source_keys': ('women/clothing/trousers_shorts',)},
                    {'key': 'women/clothing/dresses_skirts', 'name': 'Платья и юбки', 'source_keys': ('women/clothing/dresses_skirts',)},
                    {'key': 'women/clothing/swim_beachwear', 'name': 'Купальники и пляжная одежда', 'source_keys': ('women/clothing/swim_beachwear',)},
                ],
            },
            {
                'key': 'women/shoes', 'name': 'Обувь',
                'children': [
                    {'key': 'women/shoes/sneakers', 'name': 'Кроссовки', 'source_keys': ('women/shoes/sneakers',)},
                    {'key': 'women/shoes/sandals', 'name': 'Сандалии', 'source_keys': ('women/shoes/sandals',)},
                    {'key': 'women/shoes/loafers_mules', 'name': 'Лоферы и мюли', 'source_keys': ('women/shoes/loafers_mules',)},
                    {'key': 'women/shoes/boots', 'name': 'Сапоги и ботильоны', 'source_keys': ('women/shoes/boots',)},
                    {'key': 'women/shoes/heels', 'name': 'Туфли на каблуке', 'source_keys': ('women/shoes/heels',)},
                    {'key': 'women/shoes/ballet_flats', 'name': 'Балетки', 'source_keys': ('women/shoes/ballet_flats',)},
                ],
            },
            {
                'key': 'women/bags', 'name': 'Сумки',
                'children': [
                    {'key': 'women/bags/main', 'name': 'Сумки', 'source_keys': ('women/bags', 'leather_goods/bags', 'leather_goods/clutches', 'women/bags/handbags', 'women/bags/mini_bags', 'women/bags/hobo_bags', 'women/bags/tote_bags', 'women/bags/shoulder_bags', 'women/bags/backpacks')},
                    {'key': 'women/bags/pouches', 'name': 'Несессеры и дорожные аксессуары', 'source_keys': ('leather_goods/pouches', 'leather_goods/storage_cases', 'leather_goods/travel_bags')},
                ],
            },
            {
                'key': 'women/scarves', 'name': 'Шарфы и платки',
                'children': [
                    {'key': 'women/scarves/silk', 'name': 'Шёлковые шарфы и платки', 'source_keys': ('women/scarves',)},
                    {'key': 'women/scarves/cashmere', 'name': 'Кашемировые шарфы', 'source_keys': ('women/cashmere_scarves',)},
                    {'key': 'women/scarves/stoles', 'name': 'Палантины', 'source_keys': ('women/stoles',)},
                    {'key': 'women/scarves/twilly_small', 'name': 'Твилли и малые форматы', 'source_keys': ('women/twilly', 'women/other_small_formats')},
                    {'key': 'women/scarves/accessories', 'name': 'Аксессуары для платков', 'source_keys': ('women/scarf_accessories',)},
                ],
            },
            {
                'key': 'women/accessories', 'name': 'Аксессуары',
                'children': [
                    {
                        'key': 'women/accessories/jewelry',
                        'name': 'Ювелирные изделия и украшения',
                        'children': [
                            {'key': 'women/accessories/jewelry/bracelets', 'name': 'Браслеты', 'source_keys': ('women/bracelets', 'jewelry/bracelets', 'women/accessories/bracelets')},
                            {'key': 'women/accessories/jewelry/earrings', 'name': 'Серьги', 'source_keys': ('women/earrings', 'jewelry/earrings', 'women/accessories/earrings')},
                            {'key': 'women/accessories/jewelry/necklaces', 'name': 'Ожерелья и подвески', 'source_keys': ('women/necklaces', 'jewelry/necklaces', 'women/accessories/necklaces')},
                            {'key': 'women/accessories/jewelry/rings', 'name': 'Кольца и броши', 'source_keys': ('women/rings', 'jewelry/rings', 'women/accessories/rings_brooches')},
                            {'key': 'women/accessories/jewelry/fashion', 'name': 'Другие украшения', 'source_keys': ('jewelry/fashion_jewelry', 'men/fashion_jewelry', 'women/accessories/fashion_jewelry')},
                        ],
                    },
                    {'key': 'women/accessories/belts', 'name': 'Ремни', 'source_keys': ('women/belts', 'men/belts', 'women/accessories/belts')},
                    {'key': 'women/accessories/hats', 'name': 'Головные уборы', 'source_keys': ('women/hats', 'women/accessories/hats')},
                    {'key': 'women/accessories/gloves', 'name': 'Перчатки', 'source_keys': ('women/gloves', 'women/accessories/gloves')},
                    {'key': 'women/accessories/hair', 'name': 'Аксессуары для волос', 'source_keys': ('women/hair_accessories', 'women/accessories/hair_accessories')},
                    {'key': 'women/accessories/wallets', 'name': 'Кошельки', 'source_keys': ('leather_goods/wallets', 'women/accessories/wallets')},
                    {'key': 'women/accessories/card_holders', 'name': 'Картхолдеры', 'source_keys': ('leather_goods/card_holders', 'women/accessories/card_holders')},
                    {'key': 'women/accessories/bag_accessories', 'name': 'Аксессуары для сумок и брелоки', 'source_keys': ('women/accessories/bag_accessories',)},
                    {'key': 'women/accessories/eyewear', 'name': 'Очки', 'source_keys': ('women/accessories/eyewear',)},
                    {'key': 'women/accessories/tech_accessories', 'name': 'Чехлы для устройств', 'source_keys': ('women/accessories/tech_accessories',)},
                    {'key': 'women/accessories/bag_charms', 'name': 'Подвески для сумок', 'source_keys': ('leather_goods/charms',)},
                    {'key': 'women/accessories/bag_straps', 'name': 'Ремни для сумок', 'source_keys': ('leather_goods/straps',)},
                    {'key': 'women/accessories/watches', 'name': 'Часы', 'departments': ('watches',)},
                ],
            },
            {'key': 'women/beauty', 'name': 'Красота и ароматы', 'source_keys': ('women/beauty', 'women/beauty/fragrances'), 'departments': ('fragrances_makeup',)},
            {'key': 'women/equestrian', 'name': 'Конный спорт', 'departments': ('equestrian',)},
            {'key': 'women/home', 'name': 'Для дома', 'departments': ('home',)},
            {'key': 'women/gifts', 'name': 'Подарки', 'departments': ('gifts',)},
        ],
    },
    'men': {
        'key': 'men', 'name': 'Мужчины', 'all_gender': True,
        'children': [
            {
                'key': 'men/clothing', 'name': 'Одежда',
                'children': [
                    {'key': 'men/clothing/outerwear', 'name': 'Верхняя одежда', 'source_keys': ('men/clothing/outerwear',)},
                    {'key': 'men/clothing/tshirts_tops', 'name': 'Футболки и топы', 'source_keys': ('men/clothing/tshirts_tops', 'men/tshirts')},
                    {'key': 'men/clothing/shirts_blouses', 'name': 'Рубашки', 'source_keys': ('men/clothing/shirts_blouses',)},
                    {'key': 'men/clothing/knitwear', 'name': 'Трикотаж', 'source_keys': ('men/clothing/knitwear',)},
                    {'key': 'men/clothing/sweatshirts', 'name': 'Толстовки', 'source_keys': ('men/clothing/sweatshirts',)},
                    {'key': 'men/clothing/trousers_shorts', 'name': 'Брюки и шорты', 'source_keys': ('men/clothing/trousers_shorts',)},
                    {'key': 'men/clothing/jumpsuits', 'name': 'Комбинезоны', 'source_keys': ('men/clothing/jumpsuits',)},
                ],
            },
            {
                'key': 'men/shoes', 'name': 'Обувь',
                'children': [
                    {'key': 'men/shoes/sneakers', 'name': 'Кроссовки', 'source_keys': ('men/shoes/sneakers',)},
                    {'key': 'men/shoes/sandals', 'name': 'Сандалии', 'source_keys': ('men/shoes/sandals',)},
                    {'key': 'men/shoes/loafers_mules', 'name': 'Лоферы и мюли', 'source_keys': ('men/shoes/loafers_mules',)},
                    {'key': 'men/shoes/derbies_oxfords', 'name': 'Дерби и оксфорды', 'source_keys': ('men/shoes/derbies_oxfords',)},
                    {'key': 'men/shoes/boots', 'name': 'Сапоги и ботильоны', 'source_keys': ('men/shoes/boots',)},
                ],
            },
            {
                'key': 'men/bags', 'name': 'Сумки',
                'children': [
                    {'key': 'men/bags/main', 'name': 'Сумки', 'source_keys': ('men/bags', 'leather_goods/bags', 'leather_goods/clutches')},
                    {'key': 'men/bags/pouches', 'name': 'Несессеры и дорожные аксессуары', 'source_keys': ('leather_goods/pouches', 'leather_goods/storage_cases', 'leather_goods/travel_bags')},
                ],
            },
            {
                'key': 'men/scarves', 'name': 'Шарфы и платки',
                'children': [
                    {'key': 'men/scarves/main', 'name': 'Шарфы', 'source_keys': ('men/mufflers', 'women/cashmere_scarves')},
                    {'key': 'men/scarves/small', 'name': 'Малые форматы', 'source_keys': ('men/ties',), 'title_any': ('шарф', 'платок')},
                ],
            },
            {
                'key': 'men/accessories', 'name': 'Аксессуары',
                'children': [
                    {'key': 'men/accessories/jewelry', 'name': 'Ювелирные изделия и украшения', 'source_keys': ('men/fashion_jewelry', 'men/accessories/fashion_jewelry')},
                    {'key': 'men/accessories/belts', 'name': 'Ремни', 'source_keys': ('men/belts',)},
                    {'key': 'men/accessories/ties', 'name': 'Галстуки', 'source_keys': ('men/ties',), 'title_none': ('шарф', 'платок')},
                    {'key': 'men/accessories/bow_ties', 'name': 'Галстуки-бабочки', 'source_keys': ('men/bow_ties',)},
                    {'key': 'men/accessories/hats', 'name': 'Головные уборы', 'source_keys': ('men/hats', 'women/hats')},
                    {'key': 'men/accessories/gloves', 'name': 'Перчатки', 'source_keys': ('men/gloves',)},
                    {'key': 'men/accessories/wallets', 'name': 'Кошельки', 'source_keys': ('leather_goods/wallets', 'men/accessories/wallets')},
                    {'key': 'men/accessories/card_holders', 'name': 'Картхолдеры', 'source_keys': ('leather_goods/card_holders', 'men/accessories/card_holders')},
                    {'key': 'men/accessories/shoe_accessories', 'name': 'Аксессуары для обуви', 'source_keys': ('men/accessories/shoe_accessories',)},
                    {'key': 'men/accessories/keyrings', 'name': 'Брелоки', 'source_keys': ('men/accessories/keyrings',)},
                    {'key': 'men/accessories/watches', 'name': 'Часы', 'departments': ('watches',)},
                ],
            },
            {'key': 'men/beauty', 'name': 'Красота и ароматы', 'source_keys': ('men/beauty', 'men/beauty/fragrances'), 'departments': ('fragrances_makeup',)},
            {'key': 'men/equestrian', 'name': 'Конный спорт', 'departments': ('equestrian',)},
            {'key': 'men/home', 'name': 'Для дома', 'departments': ('home',)},
            {'key': 'men/gifts', 'name': 'Подарки', 'departments': ('gifts',)},
        ],
    },
}


ACCESSORIES_CATEGORY_KEY = 'accessories'


def _gender_accessories_specs():
    """Возвращает женскую и мужскую fashion-ветки аксессуаров."""
    return tuple(
        (gender, next(
            child for child in FASHION_NAVIGATION[gender]['children']
            if child['key'] == f'{gender}/accessories'
        ))
        for gender in ('women', 'men')
    )


def _material_key(value):
    normalized = str(value or '').strip().casefold()
    if 'кожа' in normalized or 'leather' in normalized:
        return 'кожа'
    return normalized


def _material_name(value):
    return value[:1].upper() + value[1:] if value else value


def _active_brand_slugs():
    return CatalogBrand.objects.filter(is_active=True).values_list('slug', flat=True)


def _brand_queryset(brand_slug=None, catalog_scope=Product.CatalogScope.STORE):
    queryset = Product.objects.filter(
        is_active=True, catalog_scope=catalog_scope,
    )
    if brand_slug:
        return queryset.filter(brand=brand_slug)
    return queryset.filter(brand__in=_active_brand_slugs())


def products(
    brand_slug=None, related=('images', 'color_variants', 'size_variants'),
    catalog_scope=Product.CatalogScope.STORE,
):
    """Возвращает доступные товары только с нужными конкретному экрану связями."""
    return available_products(
        _brand_queryset(brand_slug, catalog_scope)
    ).prefetch_related(*related)


def catalog_metadata_products(
    brand_slug=None, catalog_scope=Product.CatalogScope.STORE,
):
    return available_products(_brand_queryset(brand_slug, catalog_scope)).only(
        'id', 'brand', 'sku', 'slug', 'title', 'gender', 'family_key', 'current_color',
        'color_group', 'category_department_slug', 'category_department_name',
        'category_path', 'category_keys', 'category_name', 'material', 'collection',
        'fetched_at', 'created_at', 'is_featured', 'price', 'currency',
        'pricing_mode', 'fixed_price_rub',
    )


def catalog_metadata_snapshot(
    brand_slug=None, catalog_scope=Product.CatalogScope.STORE,
):
    """Короткий снимок фасетов без удержания устаревших копий в памяти."""
    key = (brand_slug, catalog_scope)
    now = monotonic()
    cached = _CATALOG_METADATA_CACHE.get(key)
    if cached and now - cached[0] < CATALOG_METADATA_CACHE_TTL_SECONDS:
        return cached[1]
    snapshot = tuple(catalog_metadata_products(brand_slug, catalog_scope))
    _CATALOG_METADATA_CACHE[key] = (now, snapshot)
    return snapshot


def clear_catalog_metadata_cache():
    _CATALOG_METADATA_CACHE.clear()


def _material_matches(values, selected):
    """JSONField хранит список; сравниваем его элементы одинаково на любой БД."""
    selected = _material_key(selected)
    return any(_material_key(value) == selected for value in (values or []))


def _filter_by_material(queryset, selected):
    matching_ids = [
        product_id
        for product_id, values in queryset.values_list('id', 'material')
        if _material_matches(values, selected)
    ]
    return queryset.filter(pk__in=matching_ids)


def _family_key(product):
    return f'{product.brand}:{product.family_key or product.sku}'


def _group_by_family(product_list):
    grouped = []
    seen = set()
    for product in product_list:
        key = _family_key(product)
        if key in seen:
            continue
        seen.add(key)
        grouped.append(product)
    return grouped


def _interleave_brands(product_list, brand_order):
    """Равномерно смешивает бренды, сохраняя сортировку внутри каждого."""
    grouped = {brand: [] for brand in brand_order}
    for product in product_list:
        grouped.setdefault(product.brand, []).append(product)
    result = []
    index = 0
    while any(index < len(items) for items in grouped.values()):
        for brand in grouped:
            if index < len(grouped[brand]):
                result.append(grouped[brand][index])
        index += 1
    return result


def _fashion_spec(category_key):
    def find(spec):
        if spec['key'] == category_key:
            return spec
        for child in spec.get('children', ()):
            found = find(child)
            if found:
                return found
        return None

    for root in FASHION_NAVIGATION.values():
        found = find(root)
        if found:
            return found
    return None


def _matches_fashion_spec(product, spec, gender):
    if gender not in (product.gender or []):
        return False
    if spec.get('all_gender'):
        return True
    children = spec.get('children', ())
    if children:
        return any(_matches_fashion_spec(product, child, gender) for child in children)
    keys = set(product.category_keys or [])
    source_keys = spec.get('source_keys', ())
    departments = spec.get('departments', ())
    source_matches = bool(keys.intersection(source_keys))
    department_matches = product.category_department_slug in departments
    if source_keys and departments:
        if not source_matches and not department_matches:
            return False
    elif source_keys and not source_matches:
        return False
    elif departments and not department_matches:
        return False
    title = product.title.casefold()
    if spec.get('title_any') and not any(value in title for value in spec['title_any']):
        return False
    if spec.get('title_none') and any(value in title for value in spec['title_none']):
        return False
    return bool(spec.get('source_keys') or spec.get('departments'))


def _fashion_product_index(product_list):
    """Индексирует метаданные для быстрого построения виртуальной навигации."""
    by_gender = {}
    by_category = {}
    by_department = {}
    for product in product_list:
        for gender in product.gender or ():
            by_gender.setdefault(gender, {})[product.pk] = product
        for key in product.category_keys or ():
            by_category.setdefault(key, {})[product.pk] = product
        if product.category_department_slug:
            by_department.setdefault(product.category_department_slug, {})[
                product.pk
            ] = product
    return {
        'by_gender': by_gender,
        'by_category': by_category,
        'by_department': by_department,
    }


def _fashion_leaf_products(spec, gender, index):
    """Возвращает только возможных кандидатов листовой fashion-категории."""
    source_keys = spec.get('source_keys', ())
    departments = spec.get('departments', ())
    candidates = {}
    if source_keys:
        for key in source_keys:
            candidates.update(index['by_category'].get(key, {}))
    if departments:
        for department in departments:
            candidates.update(index['by_department'].get(department, {}))
    if not source_keys and not departments:
        candidates = index['by_gender'].get(gender, {})
    return candidates.values()


def _prepare_fashion_node(
    spec, product_list, gender, selected_category, product_index=None
):
    """Строит fashion-ветку без повторного обхода потомков для каждого товара."""
    if product_index is None:
        product_index = _fashion_product_index(product_list)
    children = []
    child_families = set()
    for child_spec in spec.get('children', ()):
        child_node, families = _prepare_fashion_node(
            child_spec, product_list, gender, selected_category, product_index
        )
        if child_node:
            children.append(child_node)
            child_families.update(families)

    if spec.get('all_gender'):
        families = {
            _family_key(product)
            for product in product_index['by_gender'].get(gender, {}).values()
        }
    elif spec.get('children'):
        # Группирующие узлы объединяют листья; вычисляем их без повторного обхода товаров.
        families = child_families
    else:
        families = {
            _family_key(product)
            for product in _fashion_leaf_products(spec, gender, product_index)
            if _matches_fashion_spec(product, spec, gender)
        }
    if not families:
        return None, families
    return {
        'key': spec['key'],
        'name': spec['name'],
        'count': len(families),
        'is_open': (
            selected_category == spec['key']
            or selected_category.startswith(f"{spec['key']}/")
        ),
        'children': children,
    }, families


def _category_lookup(product_list):
    """Собирает названия ключей без дорогого расчёта счётчиков дерева."""
    lookup = {
        ACCESSORIES_CATEGORY_KEY: 'Аксессуары',
        'bags': 'Сумки',
    }

    def add_fashion(spec):
        lookup[spec['key']] = spec['name']
        for child in spec.get('children', ()):
            add_fashion(child)

    for spec in FASHION_NAVIGATION.values():
        add_fashion(spec)
    for product in product_list:
        department_key = product.category_department_slug
        if department_key:
            lookup[department_key] = product.category_department_name or department_key
        for item in product.category_path or ():
            if item.get('key') and item.get('name'):
                lookup[item['key']] = item['name']
    return lookup


def _matches_category(product, category, grouped_fashion_navigation=True):
    if grouped_fashion_navigation and category == ACCESSORIES_CATEGORY_KEY:
        keys = set(product.category_keys or [])
        return any('/accessories' in key for key in keys) or any(
            _matches_fashion_spec(product, spec, gender)
            for gender, spec in _gender_accessories_specs()
        )
    if category == 'bags':
        return any('/bags' in key for key in (product.category_keys or []))
    spec = _fashion_spec(category) if grouped_fashion_navigation else None
    if spec:
        gender = category.split('/', 1)[0]
        return _matches_fashion_spec(product, spec, gender)
    return (
        product.category_department_slug == category
        or category in (product.category_keys or [])
    )


def _build_category_tree(
    product_list, selected_category='', selected_gender='',
    grouped_fashion_navigation=True,
):
    departments = {}
    nodes = {}
    lookup = {}
    lookup[ACCESSORIES_CATEGORY_KEY] = 'Аксессуары'
    fashion_index = _fashion_product_index(product_list)
    for product in product_list:
        department_key = product.category_department_slug
        if not department_key:
            continue
        family = _family_key(product)
        department = departments.setdefault(department_key, {
            'key': department_key,
            'name': product.category_department_name or department_key,
            'children_map': {},
            'families': set(),
        })
        department['families'].add(family)
        lookup[department_key] = department['name']
        # Для женского и мужского разделов ниже всегда строится специальная
        # fashion-навигация. Создавать параллельно неиспользуемое сырое дерево
        # категорий для этих же товаров не требуется.
        if grouped_fashion_navigation and department_key in FASHION_NAVIGATION:
            continue
        for item in product.category_path or []:
            key = item.get('key')
            parent_key = item.get('parent_key') or department_key
            if not key or not item.get('name'):
                continue
            node = nodes.setdefault(key, {
                'key': key,
                'name': item['name'],
                'children_map': {},
                'families': set(),
            })
            node['families'].add(family)
            lookup[key] = node['name']
            parent = department if parent_key == department_key else nodes.get(parent_key)
            if parent is not None:
                parent['children_map'][key] = node

    def prepare(item):
        return {
            'key': item['key'],
            'name': item['name'],
            'count': len(item['families']),
            'is_open': (
                selected_category == item['key']
                or selected_category.startswith(f"{item['key']}/")
            ),
            'children': [
                prepare(child)
                for child in sorted(item['children_map'].values(), key=lambda value: value['name'])
            ],
        }

    for spec in FASHION_NAVIGATION.values() if grouped_fashion_navigation else ():
        def add_lookup(node_spec):
            lookup[node_spec['key']] = node_spec['name']
            for child_spec in node_spec.get('children', ()):
                add_lookup(child_spec)

        add_lookup(spec)

    if (
        grouped_fashion_navigation
        and selected_category == ACCESSORIES_CATEGORY_KEY
    ):
        tree = []
        root_names = {
            'women': 'Женские аксессуары',
            'men': 'Мужские аксессуары',
        }
        for gender, spec in _gender_accessories_specs():
            accessory_node, _ = _prepare_fashion_node(
                spec, product_list, gender, selected_category, fashion_index,
            )
            if accessory_node:
                accessory_node['name'] = root_names[gender]
                accessory_node['is_open'] = True
                tree.append(accessory_node)
        return tree, lookup

    if grouped_fashion_navigation and selected_gender in FASHION_NAVIGATION:
        fashion_node, _ = _prepare_fashion_node(
            FASHION_NAVIGATION[selected_gender], product_list,
            selected_gender, selected_category, fashion_index,
        )
        if fashion_node:
            fashion_node['is_open'] = True
        return ([fashion_node] if fashion_node else []), lookup

    tree = []
    for department in sorted(
        departments.values(),
        key=lambda value: (
            CATEGORY_DEPARTMENT_ORDER.get(value['key'], 100),
            value['name'],
        ),
    ):
        gender = department['key']
        if grouped_fashion_navigation and gender in FASHION_NAVIGATION:
            fashion_node, _ = _prepare_fashion_node(
                FASHION_NAVIGATION[gender], product_list,
                gender, selected_category, fashion_index,
            )
            if fashion_node:
                tree.append(fashion_node)
        else:
            tree.append(prepare(department))
    return tree, lookup


def _product_category_crumbs(product):
    crumbs = []
    if product.category_department_slug and product.category_department_name:
        crumbs.append({
            'key': product.category_department_slug,
            'name': product.category_department_name,
        })
    nodes = {item.get('key'): item for item in product.category_path or []}
    target = next((
        item for item in reversed(product.category_path or [])
        if item.get('name') == product.category_name
    ), None)
    if target is None and product.category_path:
        target = product.category_path[-1]
    chain = []
    while target and target.get('key'):
        chain.append({'key': target['key'], 'name': target.get('name')})
        target = nodes.get(target.get('parent_key'))
    crumbs.extend(reversed(chain))
    return crumbs


def _attach_card_colors(product_list, all_products):
    family_members = {}
    for product in all_products:
        family_members.setdefault(_family_key(product), []).append(product)
    for product in product_list:
        colors = []
        seen = set()
        for member in family_members.get(_family_key(product), [product]):
            slug = member.color_group
            if not slug or slug in seen:
                continue
            seen.add(slug)
            color = COLOR_GROUP_BY_SLUG.get(slug)
            if color:
                colors.append(color)
        product.catalog_colors = colors[:4]
        product.catalog_extra_colors = max(0, len(colors) - 4)


def catalog(
    request, *, brand_slug=None, brand_name='',
    catalog_template=CATALOG_TEMPLATE, catalog_url_name='shop:catalog',
    brand_location='', brand_logo='', brand_logo_url='', page_class='catalog-page',
    grouped_fashion_navigation=True, flatten_single_department=False,
    catalog_scope=Product.CatalogScope.STORE, catalog_heading_override='',
    catalog_eyebrow='', catalog_description='', is_finder_collection=False,
):
    # До пагинации используем короткий снимок облегчённых объектов без больших
    # raw_data/i18n и изображений. Полные карточки загружаются только для 9 позиций.
    all_products_list = list(catalog_metadata_snapshot(brand_slug, catalog_scope))
    category = request.GET.get('category', '').strip()
    color = request.GET.get('color', '').strip()
    material = request.GET.get('material', '').strip()
    gender = request.GET.get('gender', '').strip()
    collection = request.GET.get('collection', '').strip()
    selected_brand = request.GET.get('brand', '').strip() if not brand_slug else brand_slug
    query = request.GET.get('q', '').strip()

    normalized_gender = ''
    if gender:
        normalized_gender = {
            '女士': 'women', 'женщины': 'women',
            '男士': 'men', 'мужчины': 'men',
        }.get(gender.lower(), gender.lower())
    price_min = price_max = None
    for key in ('price_min', 'price_max'):
        value = request.GET.get(key, '').strip()
        if value:
            try:
                parsed = Decimal(value.replace(',', '.'))
                if key == 'price_min':
                    price_min = parsed
                else:
                    price_max = parsed
            except InvalidOperation:
                pass

    ordering = request.GET.get('ordering', 'popular')
    if collection == 'new':
        ordering = 'new'
    # Фасеты, ценовые фильтры и сортировка обращаются к цене несколько раз.
    # В рамках одного ответа формула и курсы неизменны, поэтому считаем её один раз.
    for item in all_products_list:
        item.__dict__.pop('_request_display_price', None)
        item._request_display_price = item.display_price
    brand_records = list(CatalogBrand.objects.filter(
        is_active=True,
        slug__in={item.brand for item in all_products_list},
    ))
    brand_names = {brand.slug: brand.name for brand in brand_records}
    # Названия категорий берём из полного каталога, счётчики — с учётом фасетов.
    category_lookup = _category_lookup(all_products_list)
    # Фильтруем уже загруженный снимок: повторный тяжёлый SELECT по всему каталогу
    # здесь не нужен. Исходный порядок снимка соответствует popular.
    product_list = all_products_list
    if query:
        folded_query = query.casefold()
        product_list = [
            item for item in product_list
            if folded_query in item.title.casefold() or folded_query in item.sku.casefold()
        ]
    if color:
        product_list = [
            item for item in product_list
            if (
                item.color_group == color if color in COLOR_GROUP_BY_SLUG
                else item.current_color == color
            )
        ]
    if material:
        product_list = [
            item for item in product_list if _material_matches(item.material, material)
        ]
    if collection in {'fall-winter', 'spring-summer'}:
        product_list = [item for item in product_list if item.collection == collection]
    if normalized_gender:
        product_list = [
            item for item in product_list
            if (item.gender or [None])[0] == normalized_gender
        ]
    if ordering == 'new':
        product_list = sorted(product_list, key=lambda item: item.created_at, reverse=True)
        product_list = sorted(
            product_list,
            key=lambda item: (item.fetched_at is not None, item.fetched_at or 0),
            reverse=True,
        )
    elif ordering == 'title':
        product_list = sorted(product_list, key=lambda item: item.title)
    if price_min is not None:
        product_list = [
            item for item in product_list
            if item.display_price is not None and item.display_price >= price_min
        ]
    if price_max is not None:
        product_list = [
            item for item in product_list
            if item.display_price is not None and item.display_price <= price_max
        ]
    def filter_category(items):
        if not category:
            return items
        if category in category_lookup:
            return [
                item for item in items
                if _matches_category(item, category, grouped_fashion_navigation)
            ]
        # Совместимость со старыми ссылками ?category=Название.
        return [item for item in items if item.category_name == category]

    brand_context_products = filter_category(product_list)
    brand_families = {}
    for item in brand_context_products:
        brand_families.setdefault(item.brand, set()).add(_family_key(item))
    brands = [
        {
            'slug': brand.slug,
            'name': brand.name,
            'count': len(brand_families[brand.slug]),
        }
        for brand in brand_records if brand_families.get(brand.slug)
    ] if not brand_slug else []

    if selected_brand and not brand_slug:
        product_list = [item for item in product_list if item.brand == selected_brand]
    category_tree, _ = _build_category_tree(
        product_list, category, selected_gender=normalized_gender,
        grouped_fashion_navigation=grouped_fashion_navigation,
    )
    if flatten_single_department and len(category_tree) == 1:
        category_tree = category_tree[0].get('children') or category_tree
    product_list = filter_category(product_list)
    if ordering in ('price_asc', 'price_desc'):
        available = [item for item in product_list if item.display_price is not None]
        unavailable = [item for item in product_list if item.display_price is None]
        available.sort(
            key=lambda item: item.display_price,
            reverse=ordering == 'price_desc',
        )
        product_list = available + unavailable
    elif collection in ('', 'new') and ordering in ('popular', 'new'):
        # Актуальная осенне-зимняя коллекция занимает первые страницы общей
        # выдачи и «Новых поступлений», сохраняя выбранный порядок внутри сезона.
        product_list.sort(key=lambda item: item.collection != 'fall-winter')
    product_list = _group_by_family(product_list)
    if not brand_slug:
        product_list = _interleave_brands(
            product_list, [brand.slug for brand in brand_records]
        )
    color_families = {}
    for item in all_products_list:
        if item.color_group:
            color_families.setdefault(item.color_group, set()).add(_family_key(item))
    colors = []
    for slug, name, swatch, aliases in COLOR_GROUPS:
        if color_families.get(slug):
            colors.append({
                'value': slug, 'name': name, 'swatch': swatch,
                'count': len(color_families[slug]),
            })
    page_obj = Paginator(product_list, 18).get_page(request.GET.get('page'))
    page_ids = [product.pk for product in page_obj.object_list]
    page_products = products(
        brand_slug, related=('images',), catalog_scope=catalog_scope,
    ).filter(pk__in=page_ids)
    page_by_id = {product.pk: product for product in page_products}
    page_obj.object_list = [page_by_id[pk] for pk in page_ids if pk in page_by_id]
    for product in page_obj.object_list:
        product.catalog_brand_name = brand_names.get(product.brand, product.brand)
    _attach_card_colors(page_obj.object_list, all_products_list)
    material_families = {}
    for item in all_products_list:
        for value in item.material or []:
            key = _material_key(value)
            if key:
                material_families.setdefault(key, set()).add(_family_key(item))
    materials = [
        {'value': key, 'name': _material_name(key), 'count': len(families)}
        for key, families in sorted(
            material_families.items(),
            key=lambda entry: (-len(entry[1]), entry[0].casefold()),
        )
    ]
    catalog_heading = catalog_heading_override if is_finder_collection else (
        category_lookup.get(category)
        or {
            'fall-winter': 'Осень–зима',
            'spring-summer': 'Весна–лето',
            'new': 'Новые поступления',
        }.get(collection)
        or {'women': 'Женщины', 'men': 'Мужчины'}.get(normalized_gender)
        or catalog_heading_override
        or brand_name
        or 'Каталог'
    )
    catalog_url = (
        reverse(catalog_url_name, kwargs={'brand_slug': brand_slug})
        if catalog_url_name == 'shop:brand_catalog'
        else reverse(catalog_url_name)
    )
    context = {
        'page_obj': page_obj,
        'category_tree': category_tree,
        'selected_category': category,
        'selected_category_name': category_lookup.get(category, category),
        'colors': colors,
        'materials': materials,
        'brands': brands,
        'selected_brand': selected_brand if not brand_slug else '',
        'selected_gender': normalized_gender,
        'ordering': ordering,
        'selected_color': color,
        'selected_material': _material_key(material),
        'selected_collection': collection,
        'catalog_heading': catalog_heading,
        'catalog_title': {
            'fall-winter': 'Осень–зима',
            'spring-summer': 'Весна–лето',
            'new': 'Новые поступления',
        }.get(collection, ''),
        'brand_name': brand_name,
        'brand_location': brand_location,
        'brand_logo': brand_logo,
        'brand_logo_url': brand_logo_url,
        'is_brand_boutique': bool(brand_slug),
        'catalog_url_name': catalog_url_name,
        'catalog_url': catalog_url,
        'page_class': page_class,
        'catalog_eyebrow': catalog_eyebrow,
        'catalog_description': catalog_description,
        'is_finder_collection': is_finder_collection,
    }
    return render(request, catalog_template, context)


def product_detail(
    request, pk, slug, *, brand_slug, brand_name,
    product_template=PRODUCT_TEMPLATE, catalog_url_name='shop:catalog',
    brand_logo='', brand_logo_url='', brand_stylesheet='', page_class='catalog-page',
    catalog_scope=Product.CatalogScope.STORE, is_finder_collection=False,
):
    product = get_object_or_404(
        products(brand_slug, catalog_scope=catalog_scope), pk=pk, slug=slug,
    )
    related_candidates = list(products(
        brand_slug, related=('images',), catalog_scope=catalog_scope,
    ).filter(
        category_name=product.category_name
    ).exclude(family_key=product.family_key).exclude(pk=product.pk)[:24])
    related = _group_by_family(related_candidates)[:4]
    variants = list(product.color_variants.all())
    available_sizes = available_size_variants(product)
    selected_size = order_size_variant(product)
    if available_sizes and selected_size is None:
        selected_size = available_sizes[0]
    variant_skus = [variant.sku.strip() for variant in variants if variant.sku.strip()]
    variant_query = Q(sku__in=variant_skus) | Q(sku_without_size__in=variant_skus)
    for variant_sku in variant_skus:
        variant_query |= Q(sku__endswith=f':{variant_sku}')
    family_query = Q(family_key=product.family_key) if product.family_key else Q(pk=product.pk)
    local_products = products(
        brand_slug, related=('images',), catalog_scope=catalog_scope,
    ).filter(
        family_query | variant_query
    ).distinct()
    catalog_url = (
        reverse(catalog_url_name, kwargs={'brand_slug': brand_slug})
        if catalog_url_name == 'shop:brand_catalog'
        else reverse(catalog_url_name)
    )

    def sku_keys(item):
        values = {item.sku.strip().lower()}
        if item.sku_without_size:
            values.add(item.sku_without_size.strip().lower())
        if ':' in item.sku:
            values.add(item.sku.split(':', 1)[1].strip().lower())
        return values

    local_by_sku = {
        key: item for item in local_products for key in sku_keys(item)
    }
    current_keys = sku_keys(product)
    primary_image = product.primary_image
    color_options = [{
        'color': product.current_color or 'Текущий цвет',
        'color_group': COLOR_GROUP_BY_SLUG.get(product.color_group),
        'thumbnail': primary_image.preview_url if primary_image and primary_image.preview_url else '',
        'url': '',
        'is_current': True,
        'is_available_locally': True,
    }]
    seen_skus = set()
    seen_colors = {(product.current_color or '').strip().casefold()}
    for local_product in local_products:
        if local_product.pk == product.pk:
            continue
        color_key = (local_product.current_color or local_product.sku).strip().casefold()
        if color_key in seen_colors:
            continue
        seen_colors.add(color_key)
        local_image = local_product.primary_image
        color_options.append({
            'color': local_product.current_color or local_product.sku,
            'color_group': COLOR_GROUP_BY_SLUG.get(local_product.color_group),
            'thumbnail': local_image.preview_url if local_image and local_image.preview_url else '',
            'url': local_product.get_absolute_url(),
            'is_current': False,
            'is_available_locally': True,
        })
    for variant in variants:
        variant_key = variant.sku.strip().lower()
        if not variant_key or variant_key in seen_skus:
            continue
        seen_skus.add(variant_key)
        local_product = local_by_sku.get(variant_key)
        if variant_key in current_keys or (local_product and local_product.pk == product.pk):
            if variant.color:
                color_options[0]['color'] = variant.color
            if variant.thumbnail and not color_options[0]['thumbnail']:
                color_options[0]['thumbnail'] = variant.thumbnail
            continue
        color_key = (variant.color or variant.sku).strip().casefold()
        if color_key in seen_colors:
            continue
        seen_colors.add(color_key)
        local_image = local_product.primary_image if local_product else None
        color_options.append({
            'color': variant.color or variant.sku,
            'color_group': None,
            'thumbnail': (
                local_image.preview_url if local_image and local_image.preview_url else variant.thumbnail
            ),
            'url': local_product.get_absolute_url() if local_product else '',
            'is_current': False,
            'is_available_locally': bool(local_product),
        })

    return render(request, product_template, {
        'product': product,
        'related': related,
        'color_options': color_options,
        'available_sizes': available_sizes,
        'selected_size': selected_size,
        'category_crumbs': _product_category_crumbs(product),
        'brand_name': brand_name,
        'brand_logo': brand_logo,
        'brand_logo_url': brand_logo_url,
        'brand_stylesheet': brand_stylesheet,
        'catalog_url_name': catalog_url_name,
        'catalog_url': catalog_url,
        'page_class': page_class,
        'is_finder_collection': is_finder_collection,
    })