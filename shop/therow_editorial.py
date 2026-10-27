"""Редакторская нормализация русских названий и описаний The Row."""

from __future__ import annotations

import re
from copy import deepcopy


TITLE_TYPES = (
    (' Sunglasses Case', 'Футляр для очков'),
    (' Flat Loafer', 'Лоферы'),
    (' Flat Sandal', 'Сандалии'),
    (' Shoe Boot', 'Ботинки'),
    (' Scarf Hat', 'Головной убор'),
    (' Duffle Bag', 'Дорожная сумка'),
    (' Polo Top', 'Поло'),
    (' Crossbody Bag Leather', 'Сумка через плечо'),
    (' Crossbody Bag', 'Сумка через плечо'),
    (' Shoulder Bag', 'Сумка через плечо'),
    (' Top Handle Bag', 'Сумка с ручками'),
    (' Bowling Bag', 'Сумка'),
    (' Clutch Bag', 'Клатч'),
    (' Clutch', 'Клатч'),
    (' Tote Bag', 'Сумка-тоут'),
    (' Park Tote', 'Сумка-тоут'),
    (' Tote', 'Сумка-тоут'),
    (' Baguette', 'Сумка-багет'),
    (' Minaudiere', 'Клатч-минодьер'),
    (' Wristlet', 'Клатч с ремешком'),
    (' Shopper', 'Сумка-шопер'),
    (' Duffle', 'Дорожная сумка'),
    (' Backpack', 'Рюкзак'),
    (' Pouch', 'Клатч'),
    (' Bag', 'Сумка'),
    (' Chelsea Boot', 'Ботинки челси'),
    (' Zipped Boot I', 'Ботинки на молнии Zipped I'),
    (' Bootie', 'Ботильоны'),
    (' Boot', 'Ботинки'),
    (' D’Orsay', 'Туфли'),
    (" D'Orsay", 'Туфли'),
    (' Kitten Heel', 'Туфли на каблуке kitten heel'),
    (' Pump Three', 'Туфли'),
    (' Pump Two', 'Туфли'),
    (' Pump', 'Туфли-лодочки'),
    (' Kitten Mule', 'Мюли'),
    (' Mule', 'Мюли'),
    (' Slip On', 'Лоферы'),
    (' Moccasin', 'Мокасины'),
    (' Loafer', 'Лоферы'),
    (' Slipper', 'Лоферы'),
    (' Ballet Flat', 'Балетки'),
    (' Flat', 'Балетки'),
    (' Flip Flop', 'Сандалии'),
    (' Slide', 'Сандалии'),
    (' Sandal', 'Сандалии'),
    (' Sneakers', 'Кроссовки'),
    (' Sneaker', 'Кроссовки'),
    (' Shoe', 'Туфли'),
    (' Heel', 'Туфли'),
    (' Turtleneck', 'Водолазка'),
    (' Crew Neck Sweater', 'Свитер'),
    (' Sweater', 'Свитер'),
    (' Cardigan', 'Кардиган'),
    (' Sweatshirt', 'Свитшот'),
    (' T-Shirt', 'Футболка'),
    (' Polo', 'Поло'),
    (' Shirt', 'Рубашка'),
    (' Top', 'Топ'),
    (' Jean', 'Джинсы'),
    (' Pants', 'Брюки'),
    (' Pant', 'Брюки'),
    (' Short', 'Шорты'),
    (' Leggings', 'Леггинсы'),
    (' Tights', 'Колготки'),
    (' Jumpsuit', 'Комбинезон'),
    (' Dress', 'Платье'),
    (' Skirt', 'Юбка'),
    (' Vest', 'Жилет'),
    (' Jacket', 'Куртка'),
    (' Coat', 'Пальто'),
    (' Scarf', 'Шарф'),
    (' Gloves', 'Перчатки'),
    (' Beanie', 'Шапка бини'),
    (' Bonnet', 'Шапка'),
    (' Hat', 'Головной убор'),
    (' Belt', 'Ремень'),
    (' Passport Holder', 'Обложка для паспорта'),
    (' Card Holder', 'Картхолдер'),
    (' Carabiner Clip', 'Карабин для аксессуаров'),
    (' Case', 'Футляр'),
    (' Wallet', 'Кошелёк'),
    (' Towel', 'Полотенце'),
    (' Blanket', 'Плед'),
)

ARCHIVE_TITLE_FIXES = {
    'Handmade Lace Lamé Tunic, 1920': 'Кружевная туника ручной работы из ламе, 1920 год',
    'Hammered Metal Coat, 1920': 'Пальто из чеканного металла, 1920 год',
    'Antique Cotton Caftan, 1920': 'Винтажный хлопковый кафтан, 1920 год',
    'Flocked Velvet Shawl, 1900': 'Бархатная шаль с флоковым узором, 1900 год',
    'Silk Shell Coat, 1980': 'Шёлковое пальто, 1980 год',
    'Belted Dress, 2000': 'Платье с поясом, 2000 год',
    'Quilted Silk Velvet Coat, 1900s': 'Стёганое пальто из шёлкового бархата, 1900-е годы',
    'Silk Chiffon Caftan, 1930': 'Кафтан из шёлкового шифона, 1930 год',
    'Silk Gown with Murano Glass Bead, 1900s': 'Шёлковое платье с бусинами из муранского стекла, 1900-е годы',
    'Delphos Silk Gown with Murano Glass Beading, 1930': 'Шёлковое платье Delphos с бусинами из муранского стекла, 1930 год',
}

EXACT_TITLE_FIXES = {
    "90's Bag": 'Сумка 90-х',
    "90's Shoulder Bag": 'Сумка через плечо 90-х',
    'The Essential': 'Кошелёк The Essential',
    'Passport Holder': 'Обложка для паспорта',
    'Liisa Kitten D’Orsay': 'Туфли Liisa Kitten D’Orsay',
    "Liisa Kitten D'Orsay": 'Туфли Liisa Kitten D’Orsay',
    'Liisa Pump Three': 'Туфли Liisa Pump Three',
    'Hudson Two Slip On': 'Лоферы Hudson Two',
    'Carabiner Clip': 'Карабин для аксессуаров',
    'Beach Flip Flop Sport 2': 'Сандалии Beach Sport 2',
    'Liisa Pump Two Heel': 'Туфли Liisa Two',
    'Liisa Pump Three': 'Туфли Liisa Three',
}

DESCRIPTION_DEFECTS = (
    (r'с прямого кроя|с узким прямого кроя|с широкими прямого кроя', 8),
    (r'с непринужденн\w* прямого кроя', 8),
    (r'сшит\w* на заказ|приспособленн\w*', 7),
    (r'лацкан с уступом|воротник с вырезом', 5),
    (r'двубортный пальто|однобортный пальто|длинный пальто', 8),
    (r'минимальн\w+ средней посадки', 7),
    (r'шерстяного костюма|хлопчатобумажного денима', 3),
    (r'отстрочк\w+ сверху|конструкци\w+ (?:из|с) 5 карман', 4),
    (r'детского кашемира|с галькой|заниженной посадкой', 5),
    (r'насос|кот[её]нок|флип-флоп', 8),
    (r'Плиссированный перед|Заутюженные складки', 4),
    (r'(?:утилитарный|классический|минимальный|обтекаемый|натягивающийся) ботильоны', 8),
    (r'толстым подошва|платформа ботильоны|на каблуках ботильоны', 8),
    (r'шерстяной твила|кашемировой габардины|кожи теленка', 5),
    (r'пальто-воронка|автомобильное пальто|открытая передняя часть', 4),
    (r'сумка-тотализатор|простые штаны|расслабленные штаны', 7),
    (r'натуральн\w+ шерст\w+|изготовленн\w+ на заказ', 4),
    (r',\s*,|\s{2,}', 2),
)

# Полные редакторские версии для формул, которые нельзя безопасно исправить
# заменой отдельных слов. Ключом служит английский оригинал: одна редактура
# автоматически применяется ко всем цветам и только пока источник не изменён.
DESCRIPTION_EXACT_FIXES = {
    'Relaxed long-sleeved top in textured cotton jersey with ribbed crewneck and signature center back detail. Spun from pure cotton slub yarn, the fabric has a distinctive uneven texture with a soft, organic hand and rich, tactile finish.': (
        'Топ свободного кроя с длинными рукавами из фактурного хлопкового '
        'джерси с круглым вырезом в рубчик и фирменной деталью по центру '
        'спинки. Чистая хлопковая пряжа с утолщениями придаёт ткани '
        'характерную неровную фактуру, мягкость и выразительную текстуру.'
    ),
    'Softly structured tote bag in matte grained calfskin leather with tubular handles and gusseted side panels. Tumbled for softness, the textured leather offers a supple yet structured hand, along with exceptional durability.': (
        'Сумка-тоут мягкой конструкции из матовой зернистой телячьей кожи '
        'с округлыми ручками и боковыми складками. Барабанная обработка '
        'делает фактурную кожу мягкой и эластичной, сохраняя её форму '
        'и исключительную прочность.'
    ),
    'Refined car shoe in soft calfskin suede with gently squared toe, soft shearling lining, and flexible rubber sole.': (
        'Лоферы для вождения из мягкой телячьей замши со слегка квадратным '
        'носком, мягкой подкладкой из овчины и гибкой резиновой подошвой.'
    ),
    'Long-sleeved sweater in smooth cotton and cashmere with slim, ribbed turtleneck.': (
        'Свитер с длинными рукавами из гладкого хлопка и кашемира '
        'с узким воротником-стойкой в рубчик.'
    ),
    'Tailored single-breasted coat in double-faced virgin wool with notch lapel, three front flap pockets, and concealed front placket with button closure.': (
        'Однобортное пальто строгого кроя из двусторонней первичной шерсти '
        'с лацканами с вырезом, тремя передними карманами с клапанами '
        'и потайной планкой на пуговицах.'
    ),
    'Tailored single-breasted coat in double-faced virgin wool with raglan construction, notch lapel, side seam pockets, and front button closure.': (
        'Однобортное пальто строгого кроя из двусторонней первичной шерсти '
        'с рукавами реглан, лацканами с вырезом, карманами в боковых швах '
        'и застёжкой на пуговицы.'
    ),
    'Tailored single-breasted jacket in tuxedo wool with front besom and welt pockets, satin peak lapel with hand-stitched detailing, and single fabric-covered button closure.': (
        'Однобортный пиджак строгого кроя из смокинговой шерсти с передними '
        'прорезными карманами, атласными остроконечными лацканами с ручной '
        'отделкой и застёжкой на одну обтянутую тканью пуговицу.'
    ),
    'Tailored single-breasted jacket in lightweight wool twill with notch lapel, front flap pockets, double back hem vents, and front button closure.': (
        'Однобортный пиджак строгого кроя из лёгкой шерстяной саржи '
        'с лацканами с вырезом, передними карманами с клапанами, двумя '
        'шлицами сзади и застёжкой на пуговицы.'
    ),
    'Tailored single-breasted jacket in mélange virgin wool with notch lapel, front flap pockets, double back hem vents, and front button closure.': (
        'Однобортный пиджак строгого кроя из меланжевой первичной шерсти '
        'с лацканами с вырезом, передними карманами с клапанами, двумя '
        'шлицами сзади и застёжкой на пуговицы.'
    ),
    'Tailored single-breasted jacket in lightweight mouline wool with notch lapel, front flap pockets, double back hem vents, and front button closure.': (
        'Однобортный пиджак строгого кроя из лёгкой шерсти мулине '
        'с лацканами с вырезом, передними карманами с клапанами, двумя '
        'шлицами сзади и застёжкой на пуговицы.'
    ),
    'Tailored single-breasted jacket in lightweight cashmere twill with notch lapel, front flap pockets, double back hem vents, and front button closure.': (
        'Однобортный пиджак строгого кроя из лёгкой кашемировой саржи '
        'с лацканами с вырезом, передними карманами с клапанами, двумя '
        'шлицами сзади и застёжкой на пуговицы.'
    ),
    'Tailored single-breasted jacket in pinstriped wool with notch lapel, front flap pockets, double back hem vents, and front button closure.': (
        'Однобортный пиджак строгого кроя из шерсти в тонкую полоску '
        'с лацканами с вырезом, передними карманами с клапанами, двумя '
        'шлицами сзади и застёжкой на пуговицы.'
    ),
    'Tailored double-breasted jacket in lightweight mouline wool with notch lapel, front besom pockets, and front button closure.': (
        'Двубортный пиджак строгого кроя из лёгкой шерсти мулине '
        'с лацканами с вырезом, передними прорезными карманами '
        'и застёжкой на пуговицы.'
    ),
    'Tailored double-breasted jacket in lightweight cashmere twill with notch lapel, front besom pockets, and front button closure.': (
        'Двубортный пиджак строгого кроя из лёгкой кашемировой саржи '
        'с лацканами с вырезом, передними прорезными карманами '
        'и застёжкой на пуговицы.'
    ),
    'Casual mid-rise pant in fluid Japanese wool gabardine with wide leg, side seam pockets, and drawstring waist.': (
        'Повседневные брюки средней посадки из струящегося японского '
        'шерстяного габардина с широкими штанинами, карманами в боковых '
        'швах и поясом на завязках.'
    ),
    'Casual mid-rise pant in smooth midweight wool gabardine with wide leg, side seam pockets, and drawstring waist.': (
        'Повседневные брюки средней посадки из гладкого шерстяного габардина '
        'средней плотности с широкими штанинами, карманами в боковых швах '
        'и поясом на завязках.'
    ),
    'Casual mid-rise pant in fluid Japanese wool with wide leg, side seam pockets, and drawstring waist.': (
        'Повседневные брюки средней посадки из струящейся японской шерсти '
        'с широкими штанинами, карманами в боковых швах и поясом на завязках.'
    ),
    'Lightweight wool twill bias-cut shorts with an elongated straight-leg silhouette, side seam pockets, and an elastic waistband.': (
        'Лёгкие шорты косого кроя из шерстяной саржи с удлинённым прямым '
        'силуэтом, карманами в боковых швах и эластичным поясом.'
    ),
    'Long short in soft double-faced cotton jersey with relaxed fit, elasticated waistband, and side seam pockets.': (
        'Удлинённые шорты свободного кроя из мягкого двустороннего '
        'хлопкового джерси с эластичным поясом и карманами в боковых швах.'
    ),
    'Mid-rise shorts in wool twill with a calf-length wide leg, front and back pleats, side seam pockets, and concealed back zip closure.': (
        'Шорты средней посадки из шерстяной саржи длиной до середины икры '
        'с широкими штанинами, складками спереди и сзади, карманами '
        'в боковых швах и потайной молнией сзади.'
    ),
    'Relaxed mid-rise short in crisp cotton with wide leg, double front pleats, seamless side construction, and side welt pockets. Finely woven and brushed, the fabric offers a soft hand and rich character that develops over time.': (
        'Шорты свободного кроя средней посадки из плотного хлопка '
        'с широкими штанинами, двойными складками спереди, бесшовной '
        'конструкцией по бокам и боковыми прорезными карманами. Тонко '
        'сотканная ворсованная ткань отличается мягкостью и со временем '
        'приобретает более выразительную фактуру.'
    ),
    'Minimal tailored short in soft Japanese wool gabardine with wide leg, seamless side construction, and side welt pockets.': (
        'Лаконичные шорты строгого кроя из мягкого японского шерстяного '
        'габардина с широкими штанинами, бесшовной конструкцией по бокам '
        'и боковыми прорезными карманами.'
    ),
    'Single-breasted car coat in washed heavyweight cotton poplin with relaxed mid-thigh length fit, slightly cropped raglan sleeves, slant went pockets, and exposed front button closure.': (
        'Однобортное пальто свободного кроя из плотного хлопкового поплина '
        'с эффектом стирки, длиной до середины бедра, слегка укороченными '
        'рукавами реглан, косыми прорезными карманами и открытой застёжкой '
        'на пуговицы.'
    ),
    'Mid-rise pants in lightweight mouline wool with pleated front, creased front and back leg, side slash pockets, and buttoned back welt pockets.': (
        'Брюки средней посадки из лёгкой шерсти мулине со складками спереди, '
        'заутюженными стрелками, боковыми карманами и задними прорезными '
        'карманами на пуговицах.'
    ),
    'Mid-rise pants in lightweight mouline wool with front double pleats, cuffed leg with creased front and back, side slash pockets, and back besom pockets.': (
        'Брюки средней посадки из лёгкой шерсти мулине с двойными складками '
        'спереди, штанинами с манжетами и заутюженными стрелками, боковыми '
        'и задними прорезными карманами.'
    ),
    'Mid-rise tailored trouser in lightweight mouline wool suiting with straight cuffed leg, double front pleats, and side seam pockets.': (
        'Брюки строгого кроя средней посадки из лёгкой костюмной шерсти '
        'мулине с прямыми штанинами и манжетами, двойными складками '
        'спереди и карманами в боковых швах.'
    ),
    'Tailored single-breasted jacket in soft Japanese wool gabardine with relaxed fit, notch lapel, front flap pockets, and open front.': (
        'Однобортный пиджак строгого кроя из мягкого японского шерстяного '
        'габардина свободного силуэта с лацканами с вырезом, передними '
        'карманами с клапанами и открытым передом.'
    ),
    'Mid-rise tailored pant in lightweight wool suiting with straight cuffed leg, pressed front and back creases, and slanted side pockets. Crafted from virgin wool, the refined fabric offers both timeless appeal and modern function.': (
        'Брюки строгого кроя средней посадки из лёгкой костюмной шерсти '
        'с прямыми штанинами и манжетами, заутюженными стрелками спереди '
        'и сзади и косыми боковыми карманами. Первичная шерсть сочетает '
        'вневременную элегантность с современными функциональными свойствами.'
    ),
    'Mid-rise pants in pinstriped wool with front double pleats, cuffed leg with creased front and back, side slash pockets, and back besom pockets.': (
        'Брюки средней посадки из шерсти в тонкую полоску с двойными '
        'складками спереди, штанинами с манжетами и заутюженными стрелками, '
        'боковыми и задними прорезными карманами.'
    ),
    'Collarless car coat in double-faced baby cashmere with relaxed fit, side seam pockets, and front button closure.': (
        'Пальто без воротника свободного кроя из двустороннего кашемира '
        'baby cashmere с карманами в боковых швах и застёжкой на пуговицы.'
    ),
    'Long-sleeved crewneck sweater in baby cashmere with relaxed fit, seamless construction, and ribbed cuffs and hem.': (
        'Свитер свободного кроя с длинными рукавами и круглым вырезом '
        'из кашемира baby cashmere, выполненный без швов и дополненный '
        'манжетами и низом в рубчик.'
    ),
    'Long-sleeve v-neck sweater in baby cashmere with seamless construction and a minimal silhouette.': (
        'Лаконичный свитер с длинными рукавами и V-образным вырезом '
        'из кашемира baby cashmere, выполненный без швов.'
    ),
    'Long-sleeved turtleneck sweater in baby cashmere with seamless construction and ribbed neck, cuffs, and hem.': (
        'Свитер-водолазка с длинными рукавами из кашемира baby cashmere, '
        'выполненный без швов и дополненный воротником, манжетами и низом '
        'в рубчик.'
    ),
}


def _clean_model(value):
    value = value.strip(' ,-')
    value = value.replace("D'Orsay", 'D’Orsay')
    value = re.sub(r"\b90's\b", '90-х', value)
    return value


def editorial_title(raw_title, *, raw_description='', category_name=''):
    """Строит русское название, сохраняя фирменное имя модели без перевода."""
    raw_title = ' '.join(str(raw_title or '').split())
    if not raw_title:
        return ''
    if raw_title in ARCHIVE_TITLE_FIXES:
        return ARCHIVE_TITLE_FIXES[raw_title]
    if raw_title in EXACT_TITLE_FIXES:
        return EXACT_TITLE_FIXES[raw_title]

    # Названия очков A, B, C и похожие коды не содержат товарного суффикса.
    if category_name == 'Очки':
        model = re.sub(r'\bSunglasses\b', '', raw_title, flags=re.IGNORECASE)
        return f'Солнцезащитные очки {_clean_model(model)}'.strip()

    for suffix, product_type in TITLE_TYPES:
        if raw_title.casefold().endswith(suffix.casefold()):
            model = _clean_model(raw_title[:-len(suffix)])
            if suffix == ' Jacket' and re.search(r'\btailored\b', raw_description, re.I):
                product_type = 'Пиджак'
            if suffix == ' Case' and 'phone' in raw_title.casefold():
                product_type = 'Чехол для телефона'
            return f'{product_type} {model}'.strip()

    # Номер/часть линейки может стоять после товарного типа: Pump Three, Boot I.
    composite = (
        (r'^(.*?)\s+Pump\s+(Two|Three)$', 'Туфли'),
        (r'^(.*?)\s+Boot\s+([IVX]+)$', 'Ботинки'),
        (r'^(.*?)\s+Kitten\s+(Two|Three)$', 'Туфли'),
        (r'^(.*?)\s+Moccasin\s+(Two|Three)$', 'Мокасины'),
        (r'^(.*?)\s+Loafer\s+(Two|Three)$', 'Лоферы'),
        (r'^(.*?)\s+Sandal\s+(Two|Three)$', 'Сандалии'),
        (r'^(.*?)\s+Flat\s+(Two|Three)$', 'Балетки'),
    )
    for pattern, product_type in composite:
        match = re.match(pattern, raw_title, re.I)
        if match:
            return f'{product_type} {_clean_model(" ".join(match.groups()))}'

    # Для редких архивных позиций лучше сохранить оригинальное имя, чем
    # превратить его в ошибочную машинную кальку.
    return raw_title.replace("D'Orsay", 'D’Orsay')


def description_defect_score(text):
    text = ' '.join(str(text or '').split())
    lowercase_penalty = 4 if text and text[0].islower() else 0
    return lowercase_penalty + sum(
        weight * len(re.findall(pattern, text, re.I))
        for pattern, weight in DESCRIPTION_DEFECTS
    )


def _replace_case_insensitive(text, old, new):
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


def clean_description(text, *, raw_description=''):
    """Исправляет подтверждённые кальки и грамматику, опираясь на оригинал."""
    text = ' '.join(str(text or '').replace('\u00a0', ' ').split())
    raw_source = ' '.join(str(raw_description or '').split())
    raw = raw_source.casefold()
    if not text:
        return ''
    if raw_source in DESCRIPTION_EXACT_FIXES:
        return DESCRIPTION_EXACT_FIXES[raw_source]

    replacements = (
        ('с непринужденными прямого кроя', 'со свободными прямыми штанинами'),
        ('с непринужденной прямого кроя', 'со свободными прямыми штанинами'),
        ('с широкими прямого кроя', 'с широкими прямыми штанинами'),
        ('с узким прямого кроя', 'с узкими прямыми штанинами'),
        ('с прямого кроя', 'с прямыми штанинами'),
        ('шерстяного костюма', 'костюмной шерсти'),
        ('хлопчатобумажного денима', 'хлопкового денима'),
        ('детского кашемира', 'мягкого кашемира'),
        ('отстрочкой сверху', 'декоративной отстрочкой'),
        ('конструкцией из 5 карманов', 'классическим дизайном с пятью карманами'),
        ('конструкцией с 5 карманами', 'классическим дизайном с пятью карманами'),
        ('конструкцией из пяти карманов', 'классическим дизайном с пятью карманами'),
        ('лацкан с уступом', 'лацканами с вырезом'),
        ('лацкан с острым углом', 'остроконечными лацканами'),
        ('воротник с вырезом', 'лацканы с вырезом'),
        ('двойные вентиляционные отверстия сзади', 'двумя шлицами сзади'),
        ('вентиляционные отверстия сзади', 'шлица сзади'),
        ('прорезные карманы спереди, и', 'прорезные карманы спереди и'),
        ('с галькой', 'с зернистой фактурой'),
        ('кожи теленка', 'телячьей кожи'),
        ('кожа теленка', 'телячья кожа'),
        ('овечьей кожи', 'кожи ягнёнка'),
        ('шерстяной твила', 'шерстяной саржи'),
        ('шерстяной и кашемировой габардины', 'габардина из шерсти и кашемира'),
        ('хлопковой и кашемировой габардины', 'габардина из хлопка и кашемира'),
        ('кашемировой габардины', 'кашемирового габардина'),
        ('слегка заниженной посадкой', 'слегка опущенным основанием'),
        ('маслянистой мягкостью', 'особой мягкостью'),
        ('плиссированный перед', 'складками спереди'),
        ('шелка', 'шёлка'),
        ('легкого', 'лёгкого'),
        ('съемн', 'съёмн'),
        ('застеж', 'застёж'),
        ('платформа ботильоны', 'Ботильоны на платформе'),
        ('утилитарный ботильоны', 'Практичные ботильоны'),
        ('классический ботильоны', 'Классические ботильоны'),
        ('минимальный ботильоны', 'Лаконичные ботильоны'),
        ('обтекаемый ботильоны', 'Лаконичные ботильоны'),
        ('натягивающийся ботильоны', 'Ботильоны без застёжки'),
        ('на каблуках ботильоны', 'Ботильоны на каблуке'),
        ('толстым подошва', 'толстой подошвой'),
        ('подошва из креп-резины', 'подошвой из креповой резины'),
        ('из креп-резины', 'из креповой резины'),
        ('пальто-воронка', 'пальто с воротником-стойкой'),
        ('автомобильное пальто', 'короткое пальто'),
        ('открытая передняя часть', 'открытый перед'),
        ('закрытая пуговица', 'обтяжная пуговица'),
        ('резинка с рюшами', 'присборенная эластичная лента'),
        ('бумажник в сложенном виде', 'Складной кошелёк'),
        ('сумка-тотализатор', 'сумка-тоут'),
        ('сумка-тот', 'сумка-тоут'),
        ('простые штаны', 'Повседневные брюки'),
        ('расслабленные штаны', 'Свободные брюки'),
        ('натуральной шерсти', 'первичной шерсти'),
        ('натуральная шерсть', 'первичная шерсть'),
        ('изготовленными на заказ двухцветными металлическими пряжками', 'фирменными двухцветными металлическими пряжками'),
        ('изготовленной на заказ двухцветной металлической пряжкой', 'фирменной двухцветной металлической пряжкой'),
        ('натуральной шерстяной фланели', 'фланели из первичной шерсти'),
        ('сшитый по индивидуальным меркам', 'строгого кроя'),
        ('на заказ, сделанные', 'строгого кроя из'),
        ('вырезанные лацканы', 'лацканы с вырезом'),
        ('штаны', 'брюки'),
        ('разгрузочные брюки', 'джоггеры'),
        ('автомобильная обувь', 'лоферы для вождения'),
        ('разносторонний ботильоны', 'универсальные ботильоны'),
        ('расслабленный брюки', 'свободные брюки'),
        ('расслабленные брюки', 'свободные брюки'),
        ('облегающие брюки свободного кроя', 'свободные брюки'),
        ('с широкого кроя', 'с широкими штанинами'),
        ('широкими прямого кроя', 'широкими прямыми штанинами'),
        ('прямого кроя', 'прямого силуэта'),
        ('классическая классическим дизайном', 'классическим дизайном'),
        ('хлопковой габардины', 'хлопкового габардина'),
        ('ворсованной хлопковой габардины', 'ворсованного хлопкового габардина'),
        ('кашемира и хлопковой габардины', 'габардина из кашемира и хлопка'),
        ('с прямыми ногами', 'с прямыми штанинами'),
        ('прямые ноги', 'прямые штанины'),
        ('косыми карманами', 'косыми боковыми карманами'),
        ('замши телячьей кожи', 'телячьей замши'),
        ('замши ягненка', 'замши из кожи ягнёнка'),
        ('формой носка в виде миндаля', 'миндалевидным носком'),
        ('клатч в сложенном виде', 'Складной клатч'),
        ('мягко опущенной основой', 'слегка опущенным основанием'),
        ('боковыми вставками с ластовицами', 'боковыми складками'),
        ('трубчатыми ручками', 'округлыми ручками'),
        ('плоским основанием', 'плоским дном'),
        ('плоское основание', 'плоское дно'),
        ('на ткани французского терри', 'из французского махрового трикотажа'),
        ('хлопкового французского терри', 'французского хлопкового махрового трикотажа'),
        ('шерстяного французского терри', 'французского шерстяного махрового трикотажа'),
        ('с имитацией шеи', 'с воротником-стойкой'),
        ('с имитацией горловины', 'с воротником-стойкой'),
        ('из кедровой ткани', 'из вельвета'),
        ('кедровой ткани', 'вельвета'),
        ('брошенного кашемира', 'ворсованного кашемира'),
        ('брошенный кашемир', 'ворсованный кашемир'),
        ('пряжи slub из чистого хлопка', 'фактурной пряжи из чистого хлопка'),
        ('шёлка habotai', 'шёлка хаботай'),
        ('бархата devore', 'бархата деворе'),
        ('пуговицами corozo', 'пуговицами из корозо'),
        ('хлопка и кашемира French terry', 'французского махрового трикотажа из хлопка и кашемира'),
        ('в стиле relaxed', 'свободного кроя'),
        ('окрашенного методом garment-dyeing', 'окрашенного в готовом виде'),
        ('процедуре стонуолшинга', 'обработке stone wash'),
        ('органичную для рук и приятную на ощупь отделку', 'естественную мягкую фактуру'),
        ('исключительно мягкое прикосновение к рукам', 'исключительно мягкую фактуру'),
        ('исключительно мягкое прикосновение к руке', 'исключительно мягкую фактуру'),
        ('исключительно мягкое прикосновение', 'исключительно мягкую фактуру'),
        ('делая руку мягкой, как перчатка', 'и отличается мягкостью перчаточной кожи'),
        ('проверенный временем внешний вид', 'благородный внешний вид'),
        ('проверенный временем вид', 'благородный внешний вид'),
        ('предлагает новые пропорции и изысканные детали', 'получила обновлённые пропорции и изысканные детали'),
        ('безшовн', 'бесшовн'),
        ('с складками', 'со складками'),
        ('кожаным пряжкой', 'кожаной пряжкой'),
        ('двойной проймой', 'двубортным кроем'),
        ('двойная пройма', 'двубортный крой'),
        ('пиджак с двойной проймой', 'двубортный пиджак'),
        ('пальто с двойной проймой', 'двубортное пальто'),
        ('из из габардина из хлопка и кашемира', 'из хлопково-кашемирового габардина'),
        ('из габардина из хлопка и кашемира', 'из хлопково-кашемирового габардина'),
        ('из из мягкой', 'из мягкой'),
        ('шерстяного тканя', 'костюмной шерсти'),
        ('прямыми манжетами на ногах', 'прямыми штанинами с манжетами'),
        ('прямые манжеты на ногах', 'прямые штанины с манжетами'),
        ('манжетами на ногах', 'штанинами с манжетами'),
        ('мягкости в руках', 'мягкости'),
        ('плавным движением рук', 'пластичностью'),
        ('текстурированность для рук', 'выразительную фактуру'),
        ('автомобильные туфли', 'лоферы для вождения'),
    )
    for old, new in replacements:
        text = _replace_case_insensitive(text, old, new)

    # Tailored означает конструктивный строгий крой, а не индивидуальный пошив.
    if 'tailored' in raw:
        tailored_replacements = (
            (r'(?i)^средней посадки сшитые на заказ брюки', 'Брюки строгого кроя средней посадки'),
            (r'(?i)^завышенной посадки сшитые на заказ брюки', 'Брюки строгого кроя с высокой посадкой'),
            (r'(?i)^заниженной посадки сшитые на заказ брюки', 'Брюки строгого кроя с низкой посадкой'),
            (r'(?i)^приспособленный однобортный (?:жакет|куртка|пиджак)', 'Однобортный пиджак строгого кроя'),
            (r'(?i)^приспособленный двубортный (?:жакет|куртка|пиджак)', 'Двубортный пиджак строгого кроя'),
            (r'(?i)^приспособленный однобортный пальто', 'Однобортное пальто строгого кроя'),
            (r'(?i)^приспособленный двубортный пальто', 'Двубортное пальто строгого кроя'),
            (r'(?i)^сшит(?:ое|ый|ая) на заказ пальто', 'Пальто строгого кроя'),
            (r'(?i)^сшит(?:ый|ая|ое) на заказ (?:жакет|пиджак|куртка)', 'Пиджак строгого кроя'),
            (r'(?i)сшит(?:ые|ый|ая|ое) на заказ', 'строгого кроя'),
            (r'(?i)приспособленн\w*', 'строгого кроя'),
        )
        for pattern, replacement in tailored_replacements:
            text = re.sub(pattern, replacement, text)

    if 'kitten heel' in raw:
        text = re.sub(r'(?i)туфли на высоком каблуке', 'Туфли на невысоком каблуке kitten heel', text)
        text = re.sub(r'(?i)каблук-китен', 'невысокий каблук kitten heel', text)

    grammar = (
        ('двубортный пальто', 'двубортное пальто'),
        ('однобортный пальто', 'однобортное пальто'),
        ('длинный пальто', 'длинное пальто'),
        ('минимальный средней посадки брюки', 'Лаконичные брюки средней посадки'),
        ('завышенной посадки джинсы', 'Джинсы с высокой посадкой'),
        ('средней посадки джинсы', 'Джинсы средней посадки'),
        ('заниженной посадки джинсы', 'Джинсы с низкой посадкой'),
        ('средней посадки брюки', 'Брюки средней посадки'),
        ('завышенной посадки брюки', 'Брюки с высокой посадкой'),
        ('заниженной посадки брюки', 'Брюки с низкой посадкой'),
    )
    for old, new in grammar:
        text = _replace_case_insensitive(text, old, new)

    # Финальные согласования выполняются после замен посадки: именно на этом
    # этапе промежуточное «Расслабленный средней посадки брюки» превращается
    # в конструкцию с существительным в начале.
    final_replacements = (
        ('Расслабленный Брюки', 'Брюки свободного кроя'),
        ('Расслабленные брюки', 'Брюки свободного кроя'),
        ('классическая классическим дизайном', 'классическим дизайном'),
        ('классической классическим дизайном', 'классическим дизайном'),
        ('широкого кроя', 'с широкими штанинами'),
    )
    for old, new in final_replacements:
        text = _replace_case_insensitive(text, old, new)

    text = re.sub(r'\s+([,.;:])', r'\1', text)
    text = re.sub(r',\s*,+', ',', text)
    text = re.sub(r'(?<=[а-яё]),?\s+С (?=[а-яё])', ', с ', text)
    text = re.sub(
        r'(?i)с (прямыми|широкими|зауженными) штанинами,?\s+'
        r'(двойные складки|складки|карманы)',
        lambda match: f'с {match.group(1)} штанинами, {match.group(2).lower()}',
        text,
    )
    text = re.sub(
        r'(?i)(застёжка-молния [^,.]+) и (кожаная|резиновая) подошва',
        lambda match: f'{match.group(1)} и {match.group(2).lower()} подошва',
        text,
    )
    text = re.sub(
        r'(?i),?\s*застёжка-молния (сбоку|с внутренней стороны) и '
        r'(кожаная|резиновая) подошва',
        lambda match: (
            f', с застёжкой-молнией {match.group(1)} и '
            f'{"кожаной" if match.group(2).lower().startswith("кож") else "резиновой"} подошвой'
        ),
        text,
    )
    text = re.sub(
        r'(?i)^Брюки([^.;]*);\s*брюки имеют\s*',
        r'Брюки\1; модель дополнена ',
        text,
    )
    text = re.sub(
        r'(?i)\.\s*Изготовлен из первичной шерсти\s*[–-]\s*',
        '. Первичная шерсть ',
        text,
    )
    text = re.sub(
        r'(?i)\.\s*Изготовлен[аыо]? из первичной шерсти;\s*'
        r'этот изысканный материал обеспечивает как вечную элегантность,? '
        r'так и современные функциональные свойства',
        '. Первичная шерсть сочетает вневременную элегантность '
        'с современными функциональными свойствами',
        text,
    )
    text = re.sub(r'(?i)Кожа, обладающая ([^,.]+), обладает ', r'Кожа отличается \1 и ', text)
    text = re.sub(
        r'(?i)Кожа отличается особой мягкостью и тонким блеском и '
        r'эластичной текстурой и изысканной отделкой',
        'Кожа отличается особой мягкостью, тонким блеском, '
        'эластичной текстурой и изысканной отделкой',
        text,
    )
    text = re.sub(
        r'(?i)Первичная шерсть этот изысканный материал обеспечивает как '
        r'вечную элегантность,? так и современные функциональные свойства',
        'Первичная шерсть сочетает вневременную элегантность '
        'с современными функциональными свойствами',
        text,
    )
    text = re.sub(
        r'(?i)модель дополнена сужающиеся к низу штанины, двойные складки '
        r'спереди и карманы сбоку',
        'модель дополнена зауженными штанинами, двойными складками '
        'спереди и боковыми карманами',
        text,
    )
    text = re.sub(
        r'(?i)с открытым разрезом сзади, разрезом по центру подола сзади '
        r'и потайной застёжкой на молнию сзади',
        'с открытой спиной, разрезом по центру подола и потайной застёжкой-молнией',
        text,
    )
    text = re.sub(
        r'(?i)с двумя накладными карманами спереди, угловыми швами спереди '
        r'и планкой на пуговицах спереди',
        'с двумя передними накладными карманами, угловыми швами и планкой на пуговицах',
        text,
    )
    text = re.sub(
        r'(?i)из мягкой замши с мягкой ручкой сверху с мягкой текстурой',
        'из мягкой замши с верхней ручкой и бархатистой фактурой',
        text,
    )
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


def editorial_description(
    new_text, *, raw_description='', legacy_text='', prefer_legacy=False,
):
    """Выбирает более чистый русский вариант и выполняет итоговую редактуру."""
    candidates = [('new', new_text)]
    if legacy_text:
        candidates.append(('legacy', legacy_text))
    preferred_basis = 'legacy' if prefer_legacy else 'new'
    basis, selected = min(
        candidates,
        key=lambda item: (
            description_defect_score(item[1]), item[0] != preferred_basis,
        ),
    )
    cleaned = clean_description(selected, raw_description=raw_description)
    return cleaned, basis


def editorial_issue_kinds(title, description):
    """Возвращает оставшиеся формализованные редакторские дефекты."""
    issues = []
    checks = (
        ('bad_straight_leg', r'с (?:узким |широкими |непринужденн\w* )?прямого кроя'),
        ('tailored_calque', r'сшит\w* на заказ|приспособленн\w*'),
        ('bad_lapel', r'лацкан с уступом|воротник с вырезом'),
        ('bad_gender', r'двубортный пальто|однобортный пальто|длинный пальто'),
        ('machine_title', r'кот[её]нок|насос|флип-флоп|\b(?:Жан|Джин)\b'),
        ('bad_boot_agreement', r'(?:утилитарный|классический|минимальный|обтекаемый|натягивающийся) ботильоны|на каблуках ботильоны'),
        ('bad_sole_agreement', r'толстым подошва|\bподошва из креп-резины'),
        ('literal_tote', r'сумка-тотализатор'),
        ('literal_pants', r'простые штаны|расслабленные штаны'),
        ('material_calque', r'натуральн\w+ шерст\w+|шерстяной твила|кожи теленка'),
        ('custom_calque', r'изготовленн\w+ на заказ|сшит\w+ по индивидуальным меркам'),
        ('informal_pants', r'\bштаны\b|разгрузочные брюки'),
        ('driving_shoe_calque', r'автомобильная обувь'),
        ('bad_relaxed_agreement', r'расслабленн\w+ брюки'),
        ('bad_wide_leg', r'с широкого кроя|широкими прямого кроя'),
        ('duplicate_construction', r'классическая классическим дизайном'),
        ('bad_gabardine_case', r'хлопковой габардины'),
        ('bad_boot_listing', r'застёжка-молния (?:сбоку|с внутренней стороны) и (?:кожаная|резиновая) подошва'),
        ('bad_suede_phrase', r'замши телячьей кожи|замши ягненка'),
        ('literal_toe_shape', r'формой носка в виде миндаля'),
        ('repeated_product_noun', r'^Брюки[^.;]*;\s*брюки имеют'),
        ('wrong_product_gender', r'\.\s*Изготовлен из первичной шерсти'),
        ('mock_neck_calque', r'имитаци\w+ (?:шеи|горловины)'),
        ('corduroy_calque', r'кедров\w+ ткан'),
        ('brushed_calque', r'брошенн\w+ кашемир'),
        ('soft_hand_calque', r'мягк\w+ прикосновен|органичн\w+ для рук|делая руку мягкой'),
        ('untranslated_textile_term', r'\b(?:slub|habotai|devore|corozo|French terry|garment-dyeing|relaxed)\b'),
        ('broken_double_and', r'отличается особой мягкостью и тонким блеском и'),
        ('broken_model_details', r'модель дополнена сужающиеся'),
        ('double_preposition', r'из из (?:габардина|мягкой)'),
        ('bad_wool_suiting', r'шерстяного тканя'),
        ('literal_cuffs', r'манжетами? на ногах'),
        ('literal_hand', r'мягкости в руках|движением рук|текстурированность для рук'),
        ('literal_driving_shoes', r'автомобильн\w+ (?:туфли|обувь)'),
        ('english_90s', r"90's"),
    )
    combined = f'{title} {description}'
    if description and description[0].islower():
        issues.append('description_starts_lowercase')
    for kind, pattern in checks:
        if re.search(pattern, combined, re.I):
            issues.append(kind)
    if 'из Ламе' in combined:
        issues.append('capitalized_material')
    title_without_models = title.replace('The Essential', '').replace('D’Orsay', '')
    # Jean — фирменное имя модели сумки; kitten heel — принятый обувной термин.
    title_without_models = title_without_models.replace('Jean', '').replace('kitten heel', '')
    if re.search(
        r'\b(?:Bag|Pant|Pants|Sweater|Top|T-Shirt|Jacket|Coat|Belt|Shirt|Slipper|'
        r'Loafer|Dress|Shoe|Slide|Turtleneck|Boot|Sandal|Cardigan|Scarf|Polo|Skirt|'
        r'Sweatshirt|Wallet|Heel|Pouch|Towel|Short|Flip|Flop|Moccasin|Flat|Pump|'
        r'Holder|Shopper|Duffle|Blanket|Jumpsuit|Sunglasses|Clutch)\b',
        title_without_models,
        re.I,
    ):
        issues.append('untranslated_product_type_in_title')
    return issues


def editorialize_therow_card(
    raw_card, *, legacy_description='', prefer_legacy=False,
):
    """Возвращает карточку с синхронными редакторскими RU-полями."""
    card = deepcopy(raw_card)
    title_data = card.setdefault('title', {})
    description_data = card.setdefault('description', {})
    raw_title = str(title_data.get('raw') or '').strip()
    raw_description = str(description_data.get('raw') or '').strip()
    category = card.get('category') or {}
    canonical = category.get('canonical_path') or {}
    categories = canonical.get('categories') or []
    category_name = str(categories[-1].get('name') or '') if categories else ''
    if not category_name:
        translated_path = (category.get('i18n') or {}).get('ru') or []
        if len(translated_path) >= 2:
            category_name = str(translated_path[-2] or '')

    title = editorial_title(
        raw_title,
        raw_description=raw_description,
        category_name=category_name,
    )
    current_description = str(
        (description_data.get('i18n') or {}).get('ru') or ''
    )
    description, basis = editorial_description(
        current_description,
        raw_description=raw_description,
        legacy_text=legacy_description,
        prefer_legacy=prefer_legacy,
    )
    issues = editorial_issue_kinds(title, description)
    title_data.setdefault('i18n', {})['ru'] = title
    description_data.setdefault('i18n', {})['ru'] = description
    category_i18n = category.setdefault('i18n', {})
    category_path = list(category_i18n.get('ru') or [])
    if category_path:
        category_path[-1] = title
        category_i18n['ru'] = category_path
    return card, {
        'title': title,
        'description': description,
        'description_basis': basis,
        'issues': issues,
    }