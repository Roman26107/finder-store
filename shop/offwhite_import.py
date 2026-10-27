"""Безопасная нормализация и аудит canonical-каталога Off-White."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from pathlib import PurePosixPath
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from .catalog_normalization import _canonical_category, catalog_quality_report, clean_text
from .services import _validate_zip_entries, parse_json_bytes


BRAND_SLUG = 'off-white'
BRAND_NAME = 'Off-White'
MIN_STOREFRONT_IMAGE_EDGE = 300

# Единственная карточка выгрузки без цены проверена по официальной карточке
# Off-White с тем же SKU и цветом. Наличие намеренно не подменяется: оно зависит
# от регионального storefront, поэтому unknown будет импортирован как недоступный.
AUDITED_PRICE_FIXES = {
    'offwhite:OMIA243C99LEA0020110': {
        'amount': 450.0,
        'currency': 'EUR',
        'verified_at': '2026-09-21',
        'source': 'official-off-white-product-page',
    },
}

CRITICAL_QUALITY_ISSUES = {
    'duplicate_id', 'image_not_found', 'invalid_image_file',
    'missing_canonical_taxonomy', 'missing_image_source', 'missing_price',
    'suspicious_translation', 'unclassified_product', 'untranslated_description',
    'untranslated_title',
}


# Все значения приведены к существующим веткам FASHION_NAVIGATION FINDER.
RAW_CATEGORY_MAP = {
    'T-Shirts': ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы'),
    'Sweatshirts': ('clothing', 'Одежда', 'sweatshirts', 'Толстовки'),
    'Shirts': ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы'),
    'Knitwear': ('clothing', 'Одежда', 'knitwear', 'Трикотаж'),
    'Outerwear': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'Formal Jackets': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'Blazers': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'Leather': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'Pants': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'Pants & Denim': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'Denim': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'Shorts': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'Skirts & Shorts': ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    'Dresses': ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    'Underwear': ('clothing', 'Одежда', 'swim_beachwear', 'Бельё и пляжная одежда'),
    'Swimwear': ('clothing', 'Одежда', 'swim_beachwear', 'Купальники и пляжная одежда'),
    'Tops and Bras': ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы'),
    'Leggings': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'Sneakers': ('shoes', 'Обувь', 'sneakers', 'Кроссовки'),
    'Sneakers Archive': ('shoes', 'Обувь', 'sneakers', 'Кроссовки'),
    'Loafers and Flats': ('shoes', 'Обувь', 'loafers_mules', 'Лоферы и мюли'),
    'Slides and Flats': ('shoes', 'Обувь', 'sandals', 'Сандалии'),
    'Slides and Espadrilles': ('shoes', 'Обувь', 'sandals', 'Сандалии'),
    'Formal and Slides': ('shoes', 'Обувь', 'loafers_mules', 'Лоферы и мюли'),
    'Mules and Pumps': ('shoes', 'Обувь', 'heels', 'Туфли на каблуке'),
    'Boots': ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны'),
    'Sunglasses': ('accessories', 'Аксессуары', 'eyewear', 'Очки'),
    'Eyeglasses': ('accessories', 'Аксессуары', 'eyewear', 'Очки'),
    'Eyewear': ('accessories', 'Аксессуары', 'eyewear', 'Очки'),
    'Hats & Caps': ('accessories', 'Аксессуары', 'hats', 'Головные уборы'),
    'Belts': ('accessories', 'Аксессуары', 'belts', 'Ремни'),
    'Socks': ('accessories', 'Аксессуары', 'socks', 'Носки'),
    'Rings': ('accessories', 'Аксессуары', 'rings', 'Кольца'),
    'Bracelets': ('accessories', 'Аксессуары', 'bracelets', 'Браслеты'),
    'Necklaces': ('accessories', 'Аксессуары', 'necklaces', 'Ожерелья'),
    'Earrings': ('accessories', 'Аксессуары', 'earrings', 'Серьги'),
    'Wallets & Cardholders': ('accessories', 'Аксессуары', 'wallets', 'Кошельки'),
    'Shoulder Bags': ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'),
    'Handbags': ('bags', 'Сумки', 'handbags', 'Сумки с ручками'),
    'Tote Bags': ('bags', 'Сумки', 'tote_bags', 'Сумки-тоут'),
    'Backpacks': ('bags', 'Сумки', 'backpacks', 'Рюкзаки'),
    'Belt Bags': ('bags', 'Сумки', 'shoulder_bags', 'Поясные сумки'),
}


URL_CATEGORY_MAP = {
    't-shirts': RAW_CATEGORY_MAP['T-Shirts'],
    'sweatshirts': RAW_CATEGORY_MAP['Sweatshirts'],
    'shirts': RAW_CATEGORY_MAP['Shirts'],
    'knitwear': RAW_CATEGORY_MAP['Knitwear'],
    'outerwear': RAW_CATEGORY_MAP['Outerwear'],
    'pants': RAW_CATEGORY_MAP['Pants'],
    'denim': RAW_CATEGORY_MAP['Denim'],
    'shorts': RAW_CATEGORY_MAP['Shorts'],
    'dresses': RAW_CATEGORY_MAP['Dresses'],
    'underwear': RAW_CATEGORY_MAP['Underwear'],
    'swimwear': RAW_CATEGORY_MAP['Swimwear'],
    'sneakers': RAW_CATEGORY_MAP['Sneakers'],
    'boots': RAW_CATEGORY_MAP['Boots'],
    'sunglasses': RAW_CATEGORY_MAP['Sunglasses'],
    'eyeglasses': RAW_CATEGORY_MAP['Eyeglasses'],
    'hats-caps': RAW_CATEGORY_MAP['Hats & Caps'],
    'belts': RAW_CATEGORY_MAP['Belts'],
    'socks': RAW_CATEGORY_MAP['Socks'],
    'rings': RAW_CATEGORY_MAP['Rings'],
    'bracelets': RAW_CATEGORY_MAP['Bracelets'],
    'necklaces': RAW_CATEGORY_MAP['Necklaces'],
    'earrings': RAW_CATEGORY_MAP['Earrings'],
    'wallets-cardholders': RAW_CATEGORY_MAP['Wallets & Cardholders'],
    'shoulder-bags': RAW_CATEGORY_MAP['Shoulder Bags'],
    'handbags': RAW_CATEGORY_MAP['Handbags'],
    'tote-bags': RAW_CATEGORY_MAP['Tote Bags'],
    'backpacks': RAW_CATEGORY_MAP['Backpacks'],
    'belt-bags': RAW_CATEGORY_MAP['Belt Bags'],
}


# Сначала идут более специфичные конструкции. Английское имя линии сохраняется,
# переводится только товарный тип — это устраняет буквальные машинные кальки.
TITLE_TYPE_RULES = (
    # Dubrae is an ornament threaded onto sneaker laces, not a sneaker.
    (r'^OW_DUBRAE_[A-Z0-9_]+$', 'Украшение для шнурков',
     ('accessories', 'Аксессуары', 'shoe_accessories', 'Аксессуары для обуви')),
    (r'\bkeyrings?\b$', 'Брелок',
     ('accessories', 'Аксессуары', 'keyrings', 'Брелоки')),
    # The upstream Sneakers Accessories branch contains actual replacement laces.
    # Require a terminal product name: "Shoelace Flat Sandal" is still a sandal.
    (r'(?<![a-z])(?:shoe[ _-]*laces?|shoelaces?)(?:_\d+)?$', 'Шнурки',
     ('accessories', 'Аксессуары', 'shoe_accessories', 'Аксессуары для обуви')),
    (r'\b(?:card holders?|card cases?)\b', 'Картхолдер', ('accessories', 'Аксессуары', 'card_holders', 'Картхолдеры')),
    (r'\b(?:top handle bags?)\b', 'Сумка с ручками', ('bags', 'Сумки', 'handbags', 'Сумки с ручками')),
    (r'\bwallets?\b', 'Кошелёк', ('accessories', 'Аксессуары', 'wallets', 'Кошельки')),
    (r'\b(?:sneakers?|trainer)\b', 'Кроссовки', ('shoes', 'Обувь', 'sneakers', 'Кроссовки')),
    (r'\b(?:socks?)\b', 'Носки', ('accessories', 'Аксессуары', 'socks', 'Носки')),
    (r'\b(?:bodysuit|body suit)\b', 'Боди', ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы')),
    (r'\b(?:bra|bralette)\b', 'Бра', ('clothing', 'Одежда', 'swim_beachwear', 'Бельё и пляжная одежда')),
    (r'\b(?:tank top|tank)\b', 'Топ', ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы')),
    (r'\b(?:t-?shirt|tee)\b', 'Футболка', ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы')),
    (r'\b(?:hoodie)\b', 'Худи', ('clothing', 'Одежда', 'sweatshirts', 'Толстовки')),
    (r'\b(?:sweatshirt|crewneck)\b', 'Свитшот', ('clothing', 'Одежда', 'sweatshirts', 'Толстовки')),
    (r'\b(?:cardigan)\b', 'Кардиган', ('clothing', 'Одежда', 'knitwear', 'Трикотаж')),
    (r'\b(?:turtleneck)\b', 'Водолазка', ('clothing', 'Одежда', 'knitwear', 'Трикотаж')),
    (r'\b(?:sweater|knit)\b', 'Свитер', ('clothing', 'Одежда', 'knitwear', 'Трикотаж')),
    (r'\b(?:polo)\b', 'Поло', ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы')),
    (r'\b(?:bowling sh(?:irt)?|pajama (?:ls )?shir(?:t)?|overshir(?:t)?)\b', 'Рубашка', ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы')),
    (r'\b(?:shirt)\b', 'Рубашка', ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы')),
    (r'\b(?:top)\b', 'Топ', ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы')),
    (r'\b(?:bomber)\b', 'Куртка-бомбер', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:varsity jacket|varsity)\b', 'Куртка varsity', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:blazer)\b', 'Блейзер', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:parka)\b', 'Парка', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:coat)\b', 'Пальто', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:jacket)\b', 'Куртка', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:jkt)\b', 'Куртка', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:vest)\b', 'Жилет', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:jumpsuit)\b', 'Комбинезон', ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки')),
    (r'\b(?:dress)\b', 'Платье', ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки')),
    (r'\b(?:skirt)\b', 'Юбка', ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки')),
    (r'\b(?:jeans|denim pants)\b', 'Джинсы', ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты')),
    (r'\b(?:sweatpants?|track ?pants?|pants?|trousers|chino)\b', 'Брюки', ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты')),
    (r'\b(?:sweatshorts?|swimshorts?|shorts?|berm)\b', 'Шорты', ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты')),
    (r'\b(?:leggings|tights)\b', 'Леггинсы', ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты')),
    (r'\b(?:crew)\b', 'Свитшот', ('clothing', 'Одежда', 'sweatshirts', 'Толстовки')),
    (r'\b(?:turtle|turtlen)\b', 'Водолазка', ('clothing', 'Одежда', 'knitwear', 'Трикотаж')),
    (r'\b(?:onesie|body)\b', 'Боди', ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы')),
    (r'\b(?:boxers|briefs|underwear)\b', 'Нижнее бельё', ('clothing', 'Одежда', 'swim_beachwear', 'Бельё и пляжная одежда')),
    (r'\b(?:swimsuit|bikini|swimwear)\b', 'Купальник', ('clothing', 'Одежда', 'swim_beachwear', 'Купальники и пляжная одежда')),
    (r'\b(?:loafers?|slippers?)\b', 'Лоферы', ('shoes', 'Обувь', 'loafers_mules', 'Лоферы и мюли')),
    (r'\b(?:mules?|pumps?)\b', 'Туфли', ('shoes', 'Обувь', 'heels', 'Туфли на каблуке')),
    (r'\b(?:boots?|booties)\b', 'Ботинки', ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны')),
    (r'\b(?:slides?|sandals?|flip flops?|espadrilles?)\b', 'Сандалии', ('shoes', 'Обувь', 'sandals', 'Сандалии')),
    (r'\b(?:sunglasses|eyeglasses|glasses)\b', 'Очки', ('accessories', 'Аксессуары', 'eyewear', 'Очки')),
    (r'\b(?:unglasses|eyewear style|optical style)\b', 'Очки', ('accessories', 'Аксессуары', 'eyewear', 'Очки')),
    (r'\b(?:baseball cap|cap)\b', 'Бейсболка', ('accessories', 'Аксессуары', 'hats', 'Головные уборы')),
    (r'\b(?:beanie|hat)\b', 'Головной убор', ('accessories', 'Аксессуары', 'hats', 'Головные уборы')),
    (r'\b(?:scarf|balaclava)\b', 'Шарф', ('accessories', 'Аксессуары', 'scarves', 'Шарфы и платки')),
    (r'\b(?:gloves?)\b', 'Перчатки', ('accessories', 'Аксессуары', 'gloves', 'Перчатки')),
    (r'\b(?:belt)\b', 'Ремень', ('accessories', 'Аксессуары', 'belts', 'Ремни')),
    (r'\b(?:card holders?|card cases?)\b', 'Картхолдер', ('accessories', 'Аксессуары', 'card_holders', 'Картхолдеры')),
    (r'\b(?:doc holder|key holder)\b', 'Футляр', ('accessories', 'Аксессуары', 'card_holders', 'Картхолдеры')),
    (r'\b(?:wallets?|bifold)\b', 'Кошелёк', ('accessories', 'Аксессуары', 'wallets', 'Кошельки')),
    (r'\b(?:rings?)\b', 'Кольцо', ('accessories', 'Аксессуары', 'rings', 'Кольца')),
    (r'\b(?:bracelets?)\b', 'Браслет', ('accessories', 'Аксессуары', 'bracelets', 'Браслеты')),
    (r'\b(?:necklaces?)\b', 'Ожерелье', ('accessories', 'Аксессуары', 'necklaces', 'Ожерелья')),
    (r'\b(?:wall chain|arrow chain|wizard chain)\b', 'Ожерелье', ('accessories', 'Аксессуары', 'necklaces', 'Ожерелья')),
    (r'\b(?:earrings?)\b', 'Серьги', ('accessories', 'Аксессуары', 'earrings', 'Серьги')),
    (r'\b(?:backpacks?)\b', 'Рюкзак', ('bags', 'Сумки', 'backpacks', 'Рюкзаки')),
    (r'\b(?:tote bags?|tote)\b', 'Сумка-тоут', ('bags', 'Сумки', 'tote_bags', 'Сумки-тоут')),
    (r'\b(?:shoulder bags?|camera bags?|crossbody bags?)\b', 'Сумка через плечо', ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо')),
    (r'\b(?:handbags?|bowling bags?)\b', 'Сумка с ручками', ('bags', 'Сумки', 'handbags', 'Сумки с ручками')),
    (r'\b(?:belt bags?|bum bags?)\b', 'Поясная сумка', ('bags', 'Сумки', 'shoulder_bags', 'Поясные сумки')),
    (r'\b(?:bags?|pouches?|clutch(?:es)?)\b', 'Клатч', ('bags', 'Сумки', 'mini_bags', 'Клатчи и мини-сумки')),
    (r'\b(?:suitcase)\b', 'Чемодан', ('bags', 'Сумки', 'travel_bags', 'Дорожные сумки и чемоданы')),
    (r'\b(?:coin purse)\b', 'Кошелёк для монет', ('accessories', 'Аксессуары', 'wallets', 'Кошельки')),
    (r'\b(?:shoulder)\b', 'Сумка через плечо', ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо')),
    (r'\b(?:jitney|binder soft|hemisphere|arcade jumbo|sandbag)\b', 'Сумка', ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо')),
    (r'\b(?:hair clip)\b', 'Заколка для волос', ('accessories', 'Аксессуары', 'hair_accessories', 'Аксессуары для волос')),
    (r'\b(?:foulard)\b', 'Платок', ('accessories', 'Аксессуары', 'scarves', 'Шарфы и платки')),
    (r'\b(?:derby|espadrillas?|floating arrow|kick off|reboot dollar)\b', 'Обувь', ('shoes', 'Обувь', 'loafers_mules', 'Лоферы и мюли')),
    (r'\b(?:tracktop)\b', 'Спортивная куртка', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:overshirt)\b', 'Куртка-рубашка', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
    (r'\b(?:out of office|be right back|vulcanized|3\.0 off court|odsy|reboot arrow)\b', 'Кроссовки', ('shoes', 'Обувь', 'sneakers', 'Кроссовки')),
    (r'\b(?:sliders?)\b', 'Шлёпанцы', ('shoes', 'Обувь', 'sandals', 'Сандалии')),
    (r'\b(?:cuissard)\b', 'Ботфорты', ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны')),
    (r'\b(?:biker)\b', 'Байкерская куртка', ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда')),
)


TAXONOMY_TITLE_TYPES = {
    'tshirts_tops': 'Топ',
    'sweatshirts': 'Свитшот',
    'shirts_blouses': 'Рубашка',
    'knitwear': 'Трикотажное изделие',
    'outerwear': 'Верхняя одежда',
    'trousers_shorts': 'Брюки',
    'dresses_skirts': 'Платье или юбка',
    'swim_beachwear': 'Пляжная одежда',
    'sneakers': 'Кроссовки',
    'loafers_mules': 'Лоферы или мюли',
    'sandals': 'Сандалии',
    'heels': 'Туфли',
    'boots': 'Ботинки',
    'eyewear': 'Очки',
    'hats': 'Головной убор',
    'scarves': 'Шарф',
    'gloves': 'Перчатки',
    'belts': 'Ремень',
    'socks': 'Носки',
    'card_holders': 'Картхолдер',
    'wallets': 'Кошелёк',
    'rings': 'Кольцо',
    'bracelets': 'Браслет',
    'necklaces': 'Ожерелье',
    'earrings': 'Серьги',
    'backpacks': 'Рюкзак',
    'tote_bags': 'Сумка-тоут',
    'shoulder_bags': 'Сумка через плечо',
    'handbags': 'Сумка с ручками',
}


MATERIAL_PATTERNS = (
    ('телячья кожа', (r'calf(?:skin)?', r'calf leather')),
    ('кожа ягнёнка', (r'lamb(?:skin)?', r'lamb leather')),
    ('кожа', (r'\bleather\b',)),
    ('замша', (r'\bsuede\b',)),
    ('хлопок', (r'\bcotton\b', r'\bdenim\b')),
    ('полиамид', (r'polyamide', r'\bnylon\b')),
    ('полиэстер', (r'polyester',)),
    ('эластан', (r'elastane', r'spandex')),
    ('шерсть', (r'\bwool\b',)),
    ('кашемир', (r'cashmere',)),
    ('шёлк', (r'\bsilk\b',)),
    ('вискоза', (r'viscose', r'\brayon\b')),
    ('ацетат', (r'acetate',)),
    ('акрил', (r'acrylic',)),
    ('лён', (r'\blinen\b',)),
    ('лиоцелл', (r'lyocell',)),
    ('модал', (r'\bmodal\b',)),
    ('мохер', (r'mohair',)),
    ('альпака', (r'alpaca',)),
    ('полиуретан', (r'polyurethane',)),
    ('полиуретан', (r'thermoplastic polyurethan', r'\bpu\b')),
    ('поливинилхлорид', (r'\bpvc\b',)),
    ('ЭВА', (r'\beva\b',)),
    ('купро', (r'\bcupro\b',)),
    ('текстиль', (r'\bmesh\b', r'fabric tape')),
    ('резина', (r'\brubber\b',)),
    ('латунь', (r'\bbrass\b',)),
    ('металл', (r'\bmetal\b', r'stainless steel')),
    ('серебро', (r'\bsilver\b',)),
    ('стекло', (r'\bglass\b', r'crystal')),
    ('смола', (r'\bresin\b',)),
    ('керамика', (r'ceramic',)),
    ('поликарбонат', (r'polycarbonate',)),
)


COLOR_GROUPS = (
    ('black', 'Чёрный', ('black', 'nero', 'anthracite')),
    ('white', 'Белый', ('white', 'ivory', 'cream', 'chalk')),
    ('gray', 'Серый', ('grey', 'gray', 'silver', 'smoke')),
    ('red', 'Красный', ('red', 'cherry', 'burgundy', 'bordeaux', 'scarlet')),
    ('pink', 'Розовый', ('pink', 'rose', 'fuchsia')),
    ('orange', 'Оранжевый', ('orange', 'apricot', 'peach')),
    ('yellow', 'Жёлтый', ('yellow', 'gold', 'mustard')),
    ('green', 'Зелёный', ('green', 'khaki', 'olive', 'mint')),
    ('blue', 'Синий', ('blue', 'navy', 'azure', 'cyan')),
    ('purple', 'Фиолетовый', ('purple', 'violet', 'lilac')),
    ('brown', 'Коричневый', ('brown', 'chocolate', 'cognac', 'camel')),
    ('beige', 'Бежевый', ('beige', 'sand', 'ecru', 'natural', 'nude')),
    ('multicolor', 'Разноцветный', ('multicolor', 'multi', 'rainbow')),
)


DESCRIPTION_REPLACEMENTS = (
    (r'(?i)топ-одежд\w*', 'топ'),
    (r'(?i)высок(?:ий|ого) турнувочн(?:ый|ого) воротник', 'высокий воротник-стойка'),
    (r'(?i)с турнувк\w*', 'с воротником-водолазкой'),
    (r'(?i)турнув\w*', 'воротник-водолазка'),
    (r'(?i)цветов(?:ыми|ые|ых) блок(?:ами|и|ов)', 'контрастными цветовыми вставками'),
    (r'(?i)\bштаны\b', 'брюки'),
    (r'(?i)\bштанов\b', 'брюк'),
    (r'(?i)\bштанами\b', 'брюками'),
    (r'(?i)\bштанах\b', 'брюках'),
    (r'(?i)\bногавками\b', 'штанинами'),
    (r'(?i)\bногавках\b', 'штанинах'),
    (r'(?i)\bногавке\b', 'штанине'),
    (r'(?i)\bногавки\b', 'штанины'),
    (r'(?i)\bногавок\b', 'штанин'),
    (r'(?i)брюки для перевозки грузов', 'брюки карго'),
    (r'(?i)воротник-стойка высокого кроя', 'высокий воротник-стойка'),
    (r'(?i)\bс съемн', 'со съёмн'),
    (r'(?i)\bмуллы\b', 'мюли'),
    (r'(?i)\bслайды\b', 'шлёпанцы'),
    (r'(?i)кошельк(?:и|ов) и карман(?:ы|ов) для карт', 'кошельки и картхолдеры'),
    (r'(?i)внешн(?:яя|ей|юю) одежд(?:а|ы|у)', 'верхняя одежда'),
    (r'(?i)формальн(?:ые|ых) документы', 'классическая обувь'),
    (r'(?i)серебряный пряжка', 'серебряная пряжка'),
    (r'(?i)с небольшим логотипом-цитата', 'с небольшим фирменным текстовым логотипом'),
    (r'(?i)логотипом-цитата', 'фирменным текстовым логотипом'),
    (r'(?i)логотип-цитата', 'фирменный текстовый логотип'),
    (r'(?i)стройная форма', 'зауженный силуэт'),
    (r'(?i)стройную форму', 'зауженный силуэт'),
    (r'(?i)стройной формы', 'зауженного силуэта'),
    (r'(?i)стройная посадка', 'зауженная посадка'),
    (r'(?i)стройную посадку', 'зауженную посадку'),
    (r'(?i)стройной посадки', 'зауженной посадки'),
    (r'(?i)структурированным верхним ручком', 'жёсткой формой и верхней ручкой'),
    (r'(?i)диагональные наклейки', 'диагональные нашивки'),
    (r'(?i)наклейка с логотипом', 'нашивка с логотипом'),
    (r'(?i)наклейку с логотипом', 'нашивку с логотипом'),
    (r'(?i)наклейкой с логотипом', 'нашивкой с логотипом'),
    (r'(?i)из полированного латуни', 'из полированной латуни'),
    (r'(?i)водолазный воротник', 'воротник-водолазка'),
    (r'(?i)иконичный зип-ленточный замок', 'фирменная пластиковая стяжка'),
    (r'(?i)цитата «шнурки для обуви»', 'надпись «Shoe Laces»'),
    (r'(?i)вкладка «логотип» сбоку', 'фирменная нашивка сбоку'),
    (r'(?i)деталь мотива стрелки-подписи', 'фирменный мотив Arrow'),
    (r'(?i)юбка-мини с застежкой', 'мини-юбка в клетку'),
    (r'(?i)кристальными пуговицами', 'пуговицами с кристаллами'),
    (r'(?i)дизайн этой кофты', 'дизайн этого худи'),
    (r'(?i)дизайн в стиле прозрачного черного цвета', 'прозрачно-чёрное исполнение'),
    (r'(?i)визуальный стиль прозрачного черного цвета', 'прозрачно-чёрное исполнение'),
    (r'(?i)двойной кошелек', 'складной кошелёк'),
    (r'(?i)двойная конструкция внутри', 'складная конструкция'),
    (r'(?i)несколько карманов для карт для удобства хранения', 'несколько отделений для карт'),
    (r'(?i)узоры с тиснением внутри', 'тиснёный логотип внутри'),
)


# Эти типы однозначно задаются существительным в названии и могут исправлять
# ошибочную исходную рубрику выгрузки (например, Socks в Underwear или Hats).
TITLE_TAXONOMY_OVERRIDE_SLUGS = {
    'boots', 'card_holders', 'handbags', 'keyrings', 'sandals', 'sneakers', 'socks',
    'shoe_accessories', 'wallets',
}


OFFWHITE_SUSPICIOUS_PHRASES = (
    'топ-одеж', 'турнув', 'выключить', 'функцией отключения',
    'над экипажем', 'штаны для перевозки груза', 'парашютным механизмом раскрытия',
    'усталый диагноз', '5 штук', 'муллы', 'формальные документы',
)


def _title_rule(raw_title):
    for pattern, product_type, taxonomy in TITLE_TYPE_RULES:
        match = re.search(pattern, raw_title, flags=re.IGNORECASE)
        if match:
            return match, product_type, taxonomy
    return None


def editorial_title(raw_title, fallback='', taxonomy=None):
    """Создаёт естественное русское название без перевода имён линий."""
    raw_title = clean_text(raw_title) or ''
    if re.search(r'\bpanties\b', raw_title, re.I):
        model = re.sub(r'\s*panties\b', '', raw_title, flags=re.I).strip()
        return f'Трусы {model}'.strip()
    rule = _title_rule(raw_title)
    if not rule:
        product_type = TAXONOMY_TITLE_TYPES.get(taxonomy[2], '') if taxonomy else ''
        if product_type:
            return f'{product_type} {raw_title}'.strip()
        fallback = clean_text(fallback) or ''
        return fallback if re.search(r'[А-Яа-яЁё]', fallback) else raw_title
    match, product_type, _taxonomy = rule
    if product_type in {'Шнурки', 'Украшение для шнурков'}:
        return f'{product_type} {raw_title}'
    model = clean_text(f'{raw_title[:match.start()]} {raw_title[match.end():]}')
    model = re.sub(r'^[\s,;:/-]+|[\s,;:/-]+$', '', model)
    if not model or model.casefold() in {'off-white', 'off white'}:
        return product_type
    return f'{product_type} {model}'


def _taxonomy(product):
    raw_title = clean_text((product.get('title') or {}).get('raw')) or ''
    if re.search(r'\bpanties\b', raw_title, re.I):
        # Экспорт ошибочно относит эту модель к носкам; явный тип важнее raw_path.
        return RAW_CATEGORY_MAP['Underwear']
    title_rule = _title_rule(raw_title)
    if title_rule and title_rule[2][2] in TITLE_TAXONOMY_OVERRIDE_SLUGS:
        return title_rule[2]
    raw_path = [clean_text(item) for item in (product.get('category') or {}).get('raw_path') or []]
    for item in reversed(raw_path):
        if item in RAW_CATEGORY_MAP:
            taxonomy = RAW_CATEGORY_MAP[item]
            if item == 'Wallets & Cardholders':
                raw_title = clean_text((product.get('title') or {}).get('raw'))
                if re.search(r'(?i)card holder|card case', raw_title):
                    return 'accessories', 'Аксессуары', 'card_holders', 'Картхолдеры'
            return taxonomy
    source = product.get('source') or {}
    source_url = source.get('url') if isinstance(source, dict) else ''
    url_parts = [part.casefold() for part in urlparse(str(source_url or '')).path.split('/')]
    for part in reversed(url_parts[:-1]):
        if part in URL_CATEGORY_MAP:
            taxonomy = URL_CATEGORY_MAP[part]
            if part == 'wallets-cardholders':
                raw_title = clean_text((product.get('title') or {}).get('raw')) or ''
                if re.search(r'(?i)card holder|card case', raw_title):
                    return 'accessories', 'Аксессуары', 'card_holders', 'Картхолдеры'
            return taxonomy
    if title_rule:
        return title_rule[2]
    raw_description = clean_text((product.get('description') or {}).get('raw')) or ''
    first_sentence = re.split(r'[.!?]', raw_description, maxsplit=1)[0][:500]
    description_rule = _title_rule(first_sentence)
    return description_rule[2] if description_rule else None


def _normalize_materials(product):
    attributes = product.get('attributes')
    if not isinstance(attributes, dict):
        attributes = {}
        product['attributes'] = attributes
    material = attributes.get('material') or {}
    raw = (clean_text(material.get('raw') if isinstance(material, dict) else material) or '').casefold()
    normalized = []
    for name, patterns in MATERIAL_PATTERNS:
        if any(re.search(pattern, raw) for pattern in patterns):
            normalized.append(name)
    if any(item in normalized for item in ('телячья кожа', 'кожа ягнёнка', 'замша')):
        normalized = [item for item in normalized if item != 'кожа']
    attributes['material_normalized'] = list(dict.fromkeys(normalized))


def _normalize_colors(product):
    for variant in (product.get('variants') or {}).get('colors') or []:
        if not isinstance(variant, dict):
            continue
        label = variant.get('label')
        if not isinstance(label, dict):
            continue
        raw = (clean_text(label.get('raw')) or '').casefold()
        matches = [
            (slug, name) for slug, name, aliases in COLOR_GROUPS
            if any(alias in raw for alias in aliases)
        ]
        if not matches:
            translated = clean_text((label.get('i18n') or {}).get('ru'))
            if translated and not re.search(r'[A-Za-z]', translated):
                continue
            matches = [('other', 'Другой')]
        unique = list(dict.fromkeys(matches))
        variant['base_color'] = unique[0][0] if len(unique) == 1 else 'multicolor'
        translations = label.get('i18n')
        if not isinstance(translations, dict):
            translations = {}
            label['i18n'] = translations
        translations['ru'] = ' / '.join(name for _slug, name in unique)


def _normalize_description(product, title):
    description = product.get('description')
    if not isinstance(description, dict):
        description = {}
        product['description'] = description
    raw = description.get('raw') or ''
    translations = description.get('i18n')
    if not isinstance(translations, dict):
        translations = {}
        description['i18n'] = translations
    translated = (translations.get('ru') or '').replace('\u00a0', ' ')
    for pattern, replacement in DESCRIPTION_REPLACEMENTS:
        translated = re.sub(pattern, replacement, translated)
    translated = re.sub(r'\s{2,}', ' ', translated).strip()
    translated = re.sub(r'(<p[^>]*>)брюки\b', r'\1Брюки', translated)
    translated = re.sub(
        r'(<li[^>]*>)([а-яё])',
        lambda match: f'{match.group(1)}{match.group(2).upper()}',
        translated,
    )
    if not clean_text(translated):
        material = product.get('attributes', {}).get('material_normalized') or []
        detail = f' Материал: {", ".join(material)}.' if material else ''
        translated = f'<p>{title}.{detail}</p>'
    description['raw'] = raw.replace('\u00a0', ' ') if isinstance(raw, str) else raw
    translations['ru'] = translated


def _normalize_commercial_data(product):
    """Восстанавливает цену/наличие только из текущего варианта этой карточки."""
    variants = [
        item for item in (product.get('variants') or {}).get('colors') or []
        if isinstance(item, dict)
    ]
    current = next((item for item in variants if item.get('current')), None)
    current = current or (variants[0] if variants else {})
    price = product.get('price')
    if not isinstance(price, dict):
        price = {}
        product['price'] = price
    variant_price = current.get('price') or {}
    if price.get('amount') in (None, '') and isinstance(variant_price, dict):
        price['amount'] = variant_price.get('amount')
    if price.get('currency') in (None, '') and isinstance(variant_price, dict):
        price['currency'] = variant_price.get('currency')
    audited_fix = AUDITED_PRICE_FIXES.get(str(product.get('id') or ''))
    if audited_fix and (
        price.get('amount') in (None, '') or price.get('currency') in (None, '')
    ):
        price['amount'] = audited_fix['amount']
        price['currency'] = audited_fix['currency']
        normalized_from = product.get('normalized_from')
        if not isinstance(normalized_from, dict):
            normalized_from = {}
            product['normalized_from'] = normalized_from
        normalized_from['price_audit'] = dict(audited_fix)
    availability = product.get('availability')
    if not isinstance(availability, dict):
        availability = {}
        product['availability'] = availability
    variant_availability = current.get('availability') or {}
    if availability.get('status') in (None, '', 'unknown') and isinstance(
        variant_availability, dict
    ):
        availability['status'] = variant_availability.get('status') or 'unknown'
        availability['raw'] = variant_availability.get('raw')


def normalize_offwhite_product(raw_product):
    product = deepcopy(raw_product)
    product_id = str(product.get('id') or '') if isinstance(product, dict) else ''
    brand = (clean_text(product.get('brand')) or '') if isinstance(product, dict) else ''
    if not product_id.startswith('offwhite:') and brand.casefold() not in {'off white', 'off-white'}:
        raise ValueError(f'Карточка {product_id or "без id"} не относится к Off-White')

    raw_title = clean_text((product.get('title') or {}).get('raw')) or ''
    old_title = clean_text(((product.get('title') or {}).get('i18n') or {}).get('ru'))
    taxonomy = _taxonomy(product)
    if taxonomy is None:
        raise ValueError(f'{product_id}: не удалось определить категорию по названию {raw_title!r}')
    title = editorial_title(raw_title, old_title, taxonomy)
    if not title or not re.search(r'[А-Яа-яЁё]', title):
        raise ValueError(f'{product_id}: не удалось перевести тип товара в названии {raw_title!r}')
    title_data = product.get('title')
    if not isinstance(title_data, dict):
        title_data = {'raw': raw_title}
        product['title'] = title_data
    title_translations = title_data.get('i18n')
    if not isinstance(title_translations, dict):
        title_translations = {}
        title_data['i18n'] = title_translations
    title_translations['ru'] = title
    product['brand'] = BRAND_NAME

    parent_slug, parent_name, leaf_slug, leaf_name = taxonomy
    source_gender_value = product.get('gender')
    if isinstance(source_gender_value, (list, tuple, set)):
        source_genders = {
            (clean_text(item) or '').casefold() for item in source_gender_value
            if isinstance(item, str)
        }
    else:
        source_gender = (clean_text(source_gender_value) or '').casefold()
        source_genders = {source_gender} if source_gender else set()
    if source_genders == {'men'}:
        department, department_name = 'men', 'Мужчины'
        product['gender'] = 'men'
    elif source_genders == {'women'}:
        department, department_name = 'women', 'Женщины'
        product['gender'] = 'women'
    else:
        department, department_name = 'women', 'Женщины'
        product['gender'] = ['women', 'men']

    source_code = f'OFFWHITE_{parent_slug}_{leaf_slug}'.upper()
    nodes = [_canonical_category(source_code, parent_slug, parent_name, 2)]
    if leaf_slug != parent_slug:
        nodes.append(_canonical_category(source_code, leaf_slug, leaf_name, 3))
    category = product.get('category')
    if not isinstance(category, dict):
        category = {}
        product['category'] = category
    category['raw_path'] = [clean_text(item) for item in category.get('raw_path') or []]
    category_translations = category.get('i18n')
    if not isinstance(category_translations, dict):
        category_translations = {}
        category['i18n'] = category_translations
    category_translations['ru'] = [department_name, parent_name, leaf_name, title]
    category['canonical_path'] = {
        'department': department, 'department_ru': department_name,
        'categories': nodes,
    }

    _normalize_materials(product)
    _normalize_colors(product)
    _normalize_description(product, title)
    _normalize_commercial_data(product)
    return product


def normalize_offwhite_catalog(products):
    normalized, errors = [], []
    for index, product in enumerate(products, start=1):
        try:
            normalized.append(normalize_offwhite_product(product))
        except (AttributeError, TypeError, ValueError) as exc:
            errors.append({
                'index': index,
                'id': product.get('id') if isinstance(product, dict) else None,
                'error': str(exc),
            })
    return normalized, errors


def prepare_offwhite_archive(archive):
    entries = _validate_zip_entries(archive)
    json_entries = [(name, item) for name, item in entries if name.lower().endswith('.json')]
    if len(json_entries) != 1:
        raise ValueError(f'в архиве должен быть ровно один JSON-файл, найдено: {len(json_entries)}')
    json_name, json_entry = json_entries[0]
    raw_cards = parse_json_bytes(archive.read(json_entry), json_name)
    normalized, normalization_errors = normalize_offwhite_catalog(raw_cards)
    source_ids = [str(card.get('id') or '') for card in raw_cards if isinstance(card, dict)]
    duplicate_ids = sorted(
        product_id for product_id, count in Counter(source_ids).items()
        if product_id and count > 1
    )
    if duplicate_ids:
        normalization_errors.extend({
            'id': product_id, 'error': 'повторяющийся id в исходном каталоге',
        } for product_id in duplicate_ids)

    image_entries = {
        name: item for name, item in entries
        if PurePosixPath(name.lower()).suffix in {'.jpg', '.jpeg', '.png', '.webp'}
    }
    valid_images, low_resolution, invalid_images = set(), set(), []
    dimensions = {}
    for name, entry in image_entries.items():
        try:
            with archive.open(entry) as source:
                with Image.open(source) as image:
                    width, height = image.size
                    image.verify()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            invalid_images.append({'file': name, 'error': str(exc)[:500]})
            continue
        dimensions[name] = [width, height]
        if min(width, height) < MIN_STOREFRONT_IMAGE_EDGE:
            low_resolution.add(name)
        else:
            valid_images.add(name)

    prepared = []
    for source_card in normalized:
        card = deepcopy(source_card)
        images = []
        for image in (card.setdefault('media', {}).get('images') or []):
            path = str(image.get('file') or '').replace('\\', '/')
            if path in valid_images:
                image['position'] = len(images)
                images.append(image)
        card['media']['images'] = images
        if not images:
            normalization_errors.append({
                'id': card.get('id'), 'error': 'нет полноразмерных локальных изображений',
            })
            continue
        prepared.append(card)

    quality = catalog_quality_report(prepared, valid_images)
    source_field_anomalies = []
    filtered_issues = []
    by_id = {str(card.get('id') or ''): card for card in prepared}
    for issue in quality['issues']:
        if issue.get('type') != 'unnormalized_material':
            filtered_issues.append(issue)
            continue
        product = by_id.get(str(issue.get('product_id') or '')) or {}
        raw_material = clean_text(
            ((product.get('attributes') or {}).get('material') or {}).get('raw')
        ) or ''
        if re.search(r'(?i)\b(?:weight|measurements?|height)\b', raw_material):
            source_field_anomalies.append({
                'product_id': product.get('id'), 'field': 'attributes.material.raw',
                'value': raw_material,
                'reason': 'В поле материала находятся вес или размеры.',
            })
        else:
            filtered_issues.append(issue)
    quality['issues'] = filtered_issues
    for product in prepared:
        text = ' '.join((
            clean_text(((product.get('title') or {}).get('i18n') or {}).get('ru')),
            clean_text(((product.get('description') or {}).get('i18n') or {}).get('ru')),
        )).casefold()
        suspicious = [phrase for phrase in OFFWHITE_SUSPICIOUS_PHRASES if phrase in text]
        if suspicious:
            quality['issues'].append({
                'type': 'suspicious_translation', 'product_id': product.get('id'),
                'value': ', '.join(suspicious),
            })
    quality['issue_counts'] = dict(Counter(
        issue['type'] for issue in quality['issues']
    ))
    if normalization_errors:
        quality['issue_counts']['unclassified_product'] = len(normalization_errors)
        quality['issues'].extend({
            'type': 'unclassified_product',
            'product_id': error.get('id') or '',
            'value': error.get('error') or '',
        } for error in normalization_errors)
    if invalid_images:
        quality['issue_counts']['invalid_image_file'] = len(invalid_images)

    missing_price_ids = [
        str(card.get('id') or '') for card in prepared
        if (card.get('price') or {}).get('amount') in (None, '')
        or (card.get('price') or {}).get('currency') in (None, '')
    ]
    if missing_price_ids:
        quality['issue_counts']['missing_price'] = len(missing_price_ids)
        quality['issues'].extend({
            'type': 'missing_price', 'product_id': product_id,
            'value': 'нет суммы или валюты в карточке и текущем варианте',
        } for product_id in missing_price_ids)
    unknown_availability_ids = [
        str(card.get('id') or '') for card in prepared
        if (card.get('availability') or {}).get('status') in (None, '', 'unknown')
    ]
    if unknown_availability_ids:
        quality['issue_counts']['unknown_availability'] = len(unknown_availability_ids)
        quality['issues'].extend({
            'type': 'unknown_availability', 'product_id': product_id,
            'value': 'наличие не подтверждено; товар будет недоступен для заказа',
        } for product_id in unknown_availability_ids)

    image_references = [
        str(image.get('file') or '').replace('\\', '/')
        for card in prepared
        for image in (card.get('media') or {}).get('images') or []
    ]
    raw_category_counts = Counter(
        ' > '.join(clean_text(item) or '' for item in (card.get('category') or {}).get('raw_path') or [])
        or '(пусто)'
        for card in raw_cards if isinstance(card, dict)
    )
    canonical_category_counts = Counter()
    for card in prepared:
        canonical = (card.get('category') or {}).get('canonical_path') or {}
        nodes = canonical.get('categories') or []
        key = '/'.join([
            str(canonical.get('department') or ''),
            *(str(node.get('slug') or '') for node in nodes),
        ]).strip('/')
        canonical_category_counts[key or '(пусто)'] += 1
    empty_fields = Counter()
    for card in prepared:
        if not clean_text((card.get('title') or {}).get('raw')):
            empty_fields['raw_title'] += 1
        if not clean_text(((card.get('title') or {}).get('i18n') or {}).get('ru')):
            empty_fields['russian_title'] += 1
        if not clean_text((card.get('description') or {}).get('raw')):
            empty_fields['raw_description'] += 1
        if not clean_text(((card.get('description') or {}).get('i18n') or {}).get('ru')):
            empty_fields['russian_description'] += 1
        if not ((card.get('attributes') or {}).get('material') or {}).get('raw'):
            empty_fields['raw_material'] += 1
        if not (card.get('attributes') or {}).get('material_normalized'):
            empty_fields['normalized_material'] += 1
        if not (card.get('source') or {}).get('url'):
            empty_fields['source_url'] += 1
        if not (card.get('media') or {}).get('images'):
            empty_fields['images'] += 1
    quality.update({
        'source_products': len(raw_cards),
        'normalized_products': len(prepared),
        'normalization_errors': normalization_errors,
        'source_images': len(image_entries),
        'eligible_images': len(valid_images),
        'excluded_low_resolution_images': len(low_resolution),
        'invalid_images': invalid_images,
        'source_field_anomalies': source_field_anomalies,
        'image_dimensions': dimensions,
        'image_order': 'source; one image per SKU in this export',
        'crop_policy': 'contain; source composition preserved',
        'statistics': {
            'unique_skus': len(set(source_ids)),
            'duplicate_skus': duplicate_ids,
            'brands': dict(sorted(Counter(
                str(card.get('brand') or '(пусто)')
                for card in raw_cards if isinstance(card, dict)
            ).items())),
            'genders': dict(sorted(Counter(
                str(card.get('gender') or '(пусто)')
                for card in raw_cards if isinstance(card, dict)
            ).items())),
            'source_categories': dict(sorted(raw_category_counts.items())),
            'finder_categories': dict(sorted(canonical_category_counts.items())),
            'currencies': dict(sorted(Counter(
                str((card.get('price') or {}).get('currency') or '(пусто)')
                for card in prepared
            ).items())),
            'availability': dict(sorted(Counter(
                str((card.get('availability') or {}).get('status') or '(пусто)')
                for card in prepared
            ).items())),
            'empty_fields': dict(sorted(empty_fields.items())),
            'image_references': len(image_references),
            'unique_image_references': len(set(image_references)),
            'duplicate_image_references': sorted(
                path for path, count in Counter(image_references).items()
                if path and count > 1
            ),
            'missing_price_skus': missing_price_ids,
            'unknown_availability_skus': unknown_availability_ids,
        },
    })
    quality['critical_issue_counts'] = {
        key: count for key, count in quality['issue_counts'].items()
        if key in CRITICAL_QUALITY_ISSUES and count
    }
    return prepared, quality


def compact_quality_report(report):
    result = dict(report)
    dimensions = result.pop('image_dimensions', {})
    result['image_dimension_variants'] = sorted({
        f'{width}x{height}' for width, height in dimensions.values()
    })
    return result