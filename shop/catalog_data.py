from collections import defaultdict


COLOR_GROUPS = (
    ('black', 'Чёрный', '#171717', ('чёрн', 'черн', 'black', 'noir', 'ebony')),
    ('white', 'Белый', '#f3f1ec', ('бел', 'white', 'blanc', 'ivory', 'слоновая кость', 'экрю', 'кремов', 'ваниль', 'опал')),
    ('gray', 'Серый', '#918d87', ('сер', 'grey', 'gray', 'пепел', 'slate', 'нейтральн', 'гематит', 'тальков', 'тундр', 'антрацит')),
    ('beige', 'Бежевый', '#c7ae88', ('беж', 'beige', 'пес', 'натурал', 'льнян', 'tan', 'верёвоч', 'известняк', 'кунжут', 'коноплян', 'сен', 'сафари', 'камея')),
    ('brown', 'Коричневый', '#76523c', ('корич', 'brown', 'шоколад', 'карамел', 'мед', 'коньяч', 'кофейн', 'табачн', 'дубов', 'бренди', 'эбенов', 'сиена', 'орехов', 'каштан', 'виски', 'ячмен', 'камыш', 'бронз', 'тамаринд', 'верблюж', 'топаз')),
    ('blue', 'Синий', '#315276', ('син', 'голуб', 'blue', 'navy', 'индиго', 'aqua', 'сапфир', 'барвинк', 'васильк')),
    ('green', 'Зелёный', '#65735b', ('зел', 'green', 'хаки', 'khaki', 'олив', 'киви', 'амазони')),
    ('red', 'Красный', '#9b3434', ('крас', 'red', 'rouge', 'бордо', 'гранат', 'vermilion', 'алый', 'кармин', 'рубинов', 'смородин')),
    ('pink', 'Розовый', '#d5a5a5', ('роз', 'pink', 'rose', 'мальва', 'пион', 'азали')),
    ('orange', 'Оранжевый', '#dc702e', ('оранж', 'orange', 'тыкв', 'персик', 'абрикос')),
    ('yellow', 'Жёлтый', '#d4af45', ('жёл', 'желт', 'yellow', 'охра', 'солом', 'горчич', 'мангов', 'ромаш', 'примул')),
    ('purple', 'Фиолетовый', '#806487', ('фиолет', 'purple', 'violet', 'лилов', 'сиренев', 'глицини')),
    ('gold', 'Золотой', '#b08a58', ('золот', 'gold', 'шампан')),
    ('silver', 'Серебристый', '#b9b9b7', ('сереб', 'silver', 'паллад', 'платинов')),
    ('multicolor', 'Разноцветный', 'conic-gradient(#bd5b55,#d4af45,#65735b,#315276,#bd5b55)', ('разноцвет', 'многоцвет', 'assorti', 'ассорти', 'multicolor')),
    ('other', 'Другой', '#d8d2ca', ()),
)

COLOR_GROUP_BY_SLUG = {
    slug: {'slug': slug, 'name': name, 'swatch': swatch}
    for slug, name, swatch, aliases in COLOR_GROUPS
}

CATEGORY_NODE_RULES = {
    'HOME_BLANKETS_PILLOWS': (
        ('pillows', ('подуш', 'pillow', 'cushion')),
        ('blankets', ()),
    ),
    'HOME_CASES_BATH_LINEN': (
        ('bath_linen', ('полотен', 'towel', 'банн', 'bath')),
        ('storage_cases', ()),
    ),
    'HOME_DECORATIVE_OBJECTS_GAMES': (
        ('games', (
            'игров', 'game', 'шахмат', 'chess', 'маджонг', 'mahjong',
            'домино', 'domino', 'покер', 'poker', 'карточн', 'bridge',
            'мостовой', 'мостик',
        )),
        ('decorative_objects', ()),
    ),
    'HOME_FURNITURE_LIGHTING_RUGS': (
        ('lighting', ('ламп', 'светиль', 'торшер', 'люстр', 'lamp', 'light')),
        ('rugs', ('ковёр', 'ковер', 'ковр', 'rug', 'carpet')),
        ('furniture', ()),
    ),
    'HOME_OFFICE_WRITING': (
        ('writing', ('карандаш', 'ручк', 'pen', 'pencil')),
        ('office', ()),
    ),
    'LUGGAGES_SUITCASES_TRAVEL_BAGS': (
        ('luggage', ('чемод', 'багаж', 'посадочный кейс', 'suitcase', 'luggage')),
        ('travel_bags', ()),
    ),
    'MEN_ACCESSORIES': (
        ('gloves', ('перчат', 'glove')),
        ('hats', ()),
    ),
    'WOMEN_ACCESSORIES': (
        ('gloves', ('перчат', 'glove')),
        ('hats', ()),
    ),
    'MEN_BAGS_AND_CLUTCHES': (
        ('clutches', ('клатч', 'clutch', 'сцеплен')),
        ('bags', ()),
    ),
    'WOMEN_BAGS_AND_CLUTCHES': (
        ('clutches', ('клатч', 'clutch', 'сцеплен')),
        ('bags', ()),
    ),
    'MEN_RTW_T_SHIRTS_SWEATSHIRTS': (
        ('sweatshirts', ('толстов', 'свитшот', 'худи', 'sweatshirt', 'hoodie')),
        ('tshirts', ()),
    ),
    'MEN_SCARVES': (
        ('scarves', ('платок', 'платки', 'gavroche')),
        ('mufflers', ()),
    ),
    'MEN_TIES_BOW_TIES': (
        ('bow_ties', ('бабоч', 'bow tie')),
        ('pocket_squares', ('паше', 'карман', 'pocket square')),
        ('ties', ()),
    ),
    'SMALL_LEATHER_GOODS_POUCHES_CASES': (
        ('storage_cases', ('футляр', 'чехол', 'хранен', 'case', 'holder')),
        ('pouches', ()),
    ),
    'WOMEN_CASHMERE_SHAWLS_STOLES': (
        ('stoles', ('палантин', 'накидк', 'stole')),
        ('cashmere_scarves', ()),
    ),
    'WOMEN_SILK_SCARVES_SILK_ACCESS': (
        ('scarf_accessories', (
            'пряжка шарфа', 'кольцо для шарфа', 'зажим для шарфа',
            'подвешивания шелкового шарфа', 'кожаная кисточка',
            'scarf ring', 'scarf buckle',
        )),
        ('scarves', ()),
    ),
    'WOMEN_TWILYS_OTHER_SMALL_FORMATS': (
        ('twilly', ('twilly', 'саржевая лента')),
        ('other_small_formats', ()),
    ),
}

CATEGORY_NAME_OVERRIDES = {
    'petit_h': 'Petit h — дизайнерские объекты',
}

SEASON_CATEGORY_CODES = {
    'PRE_COLLECTION_FW', 'COLLECTION_FW', 'PRE_COLLECTION_PE', 'COLLECTION_PE',
}

SEASONAL_CLOTHING_RULES = (
    ('outerwear', 'Верхняя одежда', (
        'пальто', 'куртк', 'пухов', 'ветров', 'плащ', 'пончо', 'жилет',
        'бомбер', 'парка', 'тренч', 'coat', 'jacket',
    )),
    ('sweatshirts', 'Толстовки', (
        'толстов', 'свитшот', 'худи', 'sweatshirt', 'hoodie',
    )),
    ('tshirts_tops', 'Футболки и топы', (
        'футбол', 'тройник', 'топ', 'второе тело', 't-shirt', 'tee',
    )),
    ('shirts_blouses', 'Рубашки и блузы', (
        'рубаш', 'блуз', 'polo', 'поло', 'shirt', 'blouse',
    )),
    ('knitwear', 'Трикотаж', (
        'свитер', 'кардиган', 'трикотаж', 'пуловер', 'джемпер', 'водолаз',
        'knit', 'sweater', 'cardigan',
    )),
    ('trousers_shorts', 'Брюки и шорты', (
        'брюк', 'шорт', 'джинс', 'леггинс', 'trouser', 'pants', 'shorts',
    )),
    ('dresses_skirts', 'Платья и юбки', (
        'плать', 'юбк', 'dress', 'skirt',
    )),
    ('swim_beachwear', 'Купальники и пляжная одежда', (
        'купаль', 'пляж', 'swim', 'beach',
    )),
    ('jumpsuits', 'Комбинезоны', ('комбинез', 'jumpsuit', 'overall')),
)


def _localized_value(value):
    if not isinstance(value, dict):
        return str(value or '').strip()
    localized = (value.get('i18n') or {}).get('ru')
    return str(localized or value.get('raw') or '').strip()


def _sku_key(value):
    return str(value or '').split(':', 1)[-1].strip().casefold()


def _common_prefix_length(first, second):
    length = 0
    for first_char, second_char in zip(first, second):
        if first_char != second_char:
            break
        length += 1
    return length


def _category_search_text(raw_data, category):
    """Собирает локализованные признаки для выбора составной категории."""
    product_i18n = (raw_data.get('i18n') or {}).get('ru') or {}
    values = [
        _localized_value(raw_data.get('title')),
        str(product_i18n.get('title') or '') if isinstance(product_i18n, dict) else '',
    ]
    translated_path = (category.get('i18n') or {}).get('ru') or []
    if isinstance(translated_path, list) and translated_path:
        values.append(str(translated_path[-1]))
    return ' '.join(value for value in values if value).casefold()


def _select_same_level_node(code, candidates, search_text):
    rules = CATEGORY_NODE_RULES.get(code.upper())
    if not rules:
        return candidates[0]
    by_slug = {
        str(node.get('slug') or '').strip().lower(): node
        for node in candidates
    }
    for slug, keywords in rules:
        if slug not in by_slug:
            continue
        if not keywords or any(keyword in search_text for keyword in keywords):
            return by_slug[slug]
    return candidates[0]


def _catalog_category_nodes(raw_data, category, canonical):
    """Убирает ложное множественное назначение составных рубрик."""
    source_nodes = [
        node for node in (canonical.get('categories') or [])
        if isinstance(node, dict)
    ]
    grouped = defaultdict(list)
    order = []
    for index, node in enumerate(source_nodes):
        try:
            level = int(node.get('level') or 2)
        except (TypeError, ValueError):
            level = 2
        code = str(node.get('code') or '').strip().upper()
        key = (code or f'__node_{index}', level)
        if key not in grouped:
            order.append(key)
        grouped[key].append(node)

    search_text = _category_search_text(raw_data, category)
    selected = []
    for key in order:
        candidates = grouped[key]
        selected.append(
            _select_same_level_node(key[0], candidates, search_text)
            if len(candidates) > 1 else candidates[0]
        )
    return selected


def _seasonal_clothing_nodes(department_slug, search_text):
    if department_slug not in {'women', 'men'}:
        return []
    child_slug, child_name = 'other_clothing', 'Прочая одежда'
    for slug, name, keywords in SEASONAL_CLOTHING_RULES:
        if any(keyword in search_text for keyword in keywords):
            child_slug, child_name = slug, name
            break
    return [
        {
            'slug': 'clothing', 'name': 'Одежда', 'level': 2,
            'key': f'{department_slug}/clothing',
            'parent_key': department_slug,
        },
        {
            'slug': child_slug, 'name': child_name, 'level': 3,
            'key': f'{department_slug}/clothing/{child_slug}',
            'parent_key': f'{department_slug}/clothing',
        },
    ]


def normalize_color_group(value):
    normalized = str(value or '').strip().casefold()
    if not normalized:
        return ''
    for slug, name, swatch, aliases in COLOR_GROUPS:
        if normalized == slug or normalized == name.casefold():
            return slug
    # Сначала проверяем разноцветность: составные названия часто содержат
    # несколько базовых цветов через «/».
    if '/' in normalized or ',' in normalized:
        return 'multicolor'
    for slug, name, swatch, aliases in COLOR_GROUPS:
        if any(alias in normalized for alias in aliases):
            return slug
    return ''


def product_catalog_metadata(
    raw_data, *, fallback_category='', fallback_breadcrumbs=None, fallback_color=''
):
    raw_data = raw_data if isinstance(raw_data, dict) else {}
    category = raw_data.get('category') or {}
    canonical = category.get('canonical_path') or {} if isinstance(category, dict) else {}
    department_slug = str(canonical.get('department') or '').strip()
    department_name = str(canonical.get('department_ru') or '').strip()
    source_codes = [
        str(node.get('code') or '').strip().upper()
        for node in canonical.get('categories') or []
        if isinstance(node, dict)
    ]
    nodes = []
    current_parent_key = department_slug
    for node in _catalog_category_nodes(raw_data, category, canonical):
        slug = str(node.get('slug') or node.get('code') or '').strip().lower()
        name = str(node.get('ru') or node.get('name') or '').strip()
        name = CATEGORY_NAME_OVERRIDES.get(slug, name)
        if slug == 'season_collection':
            continue
        if slug and name:
            try:
                level = int(node.get('level') or 2)
            except (TypeError, ValueError):
                level = 2
            if level <= 2:
                parent_key = department_slug
                key = '/'.join(part for part in (department_slug, slug) if part)
                current_parent_key = key
                level = 2
            else:
                parent_key = current_parent_key or department_slug
                key = '/'.join(part for part in (parent_key, slug) if part)
                level = 3
            nodes.append({
                'slug': slug, 'name': name, 'level': level,
                'key': key, 'parent_key': parent_key,
            })

    inferred_clothing = False
    if not nodes and set(source_codes) & SEASON_CATEGORY_CODES:
        nodes = _seasonal_clothing_nodes(
            department_slug, _category_search_text(raw_data, category)
        )
        inferred_clothing = bool(nodes)

    if not department_slug:
        breadcrumbs = fallback_breadcrumbs or raw_data.get('categories_breadcrumbs') or []
        cleaned = [item for item in breadcrumbs if isinstance(item, dict)]
        if cleaned:
            department_slug = str(cleaned[0].get('code') or '').strip().lower()
            department_name = str(cleaned[0].get('value') or '').strip()
            nodes = []
            parent_key = department_slug
            for level, item in enumerate(cleaned[1:], start=2):
                slug = str(item.get('code') or '').strip().lower()
                name = str(item.get('value') or '').strip()
                if not slug or not name:
                    continue
                key = '/'.join(part for part in (parent_key, slug) if part)
                nodes.append({
                    'slug': slug, 'name': name, 'level': level,
                    'key': key, 'parent_key': parent_key,
                })
                parent_key = key

    has_split_category = any(
        source_codes.count(code) > 1 and code in CATEGORY_NODE_RULES
        for code in set(source_codes)
    )
    translated_path = (category.get('i18n') or {}).get('ru') or []
    translated_category = (
        str(translated_path[-2]).strip()
        if isinstance(translated_path, list) and len(translated_path) > 1
        else ''
    )
    category_name = translated_category or str(fallback_category or '').strip()
    has_name_override = bool(nodes and nodes[-1]['slug'] in CATEGORY_NAME_OVERRIDES)
    if nodes and (
        has_split_category or has_name_override or inferred_clothing or not category_name
    ):
        category_name = nodes[-1]['name']

    category_codes = {
        code for code in source_codes if code
    }
    if category_codes & {'PRE_COLLECTION_FW', 'COLLECTION_FW'}:
        collection = 'fall-winter'
    elif category_codes & {'PRE_COLLECTION_PE', 'COLLECTION_PE'}:
        collection = 'spring-summer'
    else:
        collection = ''

    own_sku = _sku_key(raw_data.get('id') or raw_data.get('sku'))
    variants = ((raw_data.get('variants') or {}).get('colors') or [])
    selected = None
    for variant in variants:
        if isinstance(variant, dict) and _sku_key(variant.get('sku')) == own_sku:
            selected = variant
            break
    if selected is None and own_sku:
        candidates = [variant for variant in variants if isinstance(variant, dict)]
        if candidates:
            closest = max(
                candidates,
                key=lambda variant: _common_prefix_length(
                    own_sku, _sku_key(variant.get('sku'))
                ),
            )
            if _common_prefix_length(own_sku, _sku_key(closest.get('sku'))) >= 6:
                selected = closest
    if selected is None and fallback_color:
        selected = next((
            variant for variant in variants
            if isinstance(variant, dict)
            and _localized_value(variant.get('label')).casefold() == fallback_color.casefold()
        ), None)
    if selected is None:
        selected = next((v for v in variants if isinstance(v, dict)), None)

    exact_color = _localized_value(selected.get('label')) if selected else fallback_color
    base_color = str(selected.get('base_color') or '').strip() if selected else ''
    color_group = normalize_color_group(base_color) or normalize_color_group(exact_color)
    if exact_color and not color_group:
        color_group = 'other'

    family_candidates = [own_sku]
    family_candidates.extend(
        _sku_key(variant.get('sku')) for variant in variants if isinstance(variant, dict)
    )
    family_candidates = [item for item in family_candidates if item]
    source_prefix = str(raw_data.get('id') or raw_data.get('sku') or '').partition(':')[0]
    family_key = min(family_candidates) if family_candidates else own_sku
    if ':' in str(raw_data.get('id') or raw_data.get('sku') or ''):
        family_key = f'{source_prefix}:{family_key}'

    return {
        'category_department_slug': department_slug,
        'category_department_name': department_name,
        'category_path': nodes,
        'category_path_key': nodes[-1]['key'] if nodes else department_slug,
        'category_keys': [node['key'] for node in nodes],
        'category_name': category_name,
        'collection': collection,
        'current_color': exact_color,
        'color_group': color_group,
        'family_key': family_key,
    }


def rebuild_product_catalog_metadata(queryset):
    """Пересобирает таксономию и связанные семейства локальных SKU."""
    products = list(queryset)
    available_fields = {
        field.name for field in queryset.model._meta.get_fields()
        if getattr(field, 'concrete', False)
    }
    by_sku = {_sku_key(product.sku): product for product in products}
    parent = {key: key for key in by_sku}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(first, second):
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    for product in products:
        key = _sku_key(product.sku)
        variants = ((product.raw_data or {}).get('variants') or {}).get('colors') or []
        for variant in variants:
            variant_key = _sku_key(variant.get('sku')) if isinstance(variant, dict) else ''
            if variant_key in by_sku:
                union(key, variant_key)

    members = defaultdict(list)
    for key in by_sku:
        members[find(key)].append(key)
    family_keys = {
        key: min(by_sku[item].sku for item in component)
        for component in members.values()
        for key in component
    }

    for product in products:
        metadata = product_catalog_metadata(
            product.raw_data,
            fallback_category=product.category_name,
            fallback_breadcrumbs=product.categories_breadcrumbs,
            fallback_color=product.current_color,
        )
        for field, value in metadata.items():
            if field in available_fields:
                setattr(product, field, value)
        if 'family_key' in available_fields:
            product.family_key = family_keys[_sku_key(product.sku)]

    if products:
        update_fields = tuple(field for field in (
            'category_department_slug', 'category_department_name',
            'category_path', 'category_path_key', 'category_keys', 'category_name',
            'collection', 'current_color', 'color_group', 'family_key',
        ) if field in available_fields)
        queryset.model.objects.bulk_update(products, update_fields, batch_size=500)
    return len(products)