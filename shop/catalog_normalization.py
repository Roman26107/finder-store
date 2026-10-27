"""Нормализация внешних canonical-каталогов перед импортом в FINDER."""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import PurePosixPath


CYRILLIC_RE = re.compile(r'[А-Яа-яЁё]')
HTML_ENTITY_RE = re.compile(r'&(?:#\d+|#x[\da-f]+|\w+);', re.IGNORECASE)


# Категории Miu Miu сведены к тем же ключам, которые используются витриной FINDER.
MIUMIU_CATEGORY_MAP = {
    ('Prêt-à-porter', "Vêtements d'extérieur"): ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    ('Prêt-à-porter', 'Vestes et manteaux'): ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    ('Prêt-à-porter', 'Tricots'): ('clothing', 'Одежда', 'knitwear', 'Трикотаж'),
    ('Prêt-à-porter', 'Jupes'): ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    ('Prêt-à-porter', 'Robes'): ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    ('Prêt-à-porter', 'Pantalons et shorts'): ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    ('Prêt-à-porter', 'Chemises et hauts'): ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы'),
    ('Prêt-à-porter', 'Sous-vêtements et vêtements de plage'): ('clothing', 'Одежда', 'swim_beachwear', 'Бельё и пляжная одежда'),
    ('Sacs', 'Sacs portés main'): ('bags', 'Сумки', 'handbags', 'Сумки с ручками'),
    ('Sacs', 'Mini sacs'): ('bags', 'Сумки', 'mini_bags', 'Мини-сумки'),
    ('Sacs', 'Sacs hobo'): ('bags', 'Сумки', 'hobo_bags', 'Сумки-хобо'),
    ('Sacs', 'Cabas'): ('bags', 'Сумки', 'tote_bags', 'Сумки-тоут'),
    ('Sacs', 'Sacs à bandoulière'): ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'),
    ('Sacs', 'Sacs à dos'): ('bags', 'Сумки', 'backpacks', 'Рюкзаки'),
    ('Chaussures', 'Baskets'): ('shoes', 'Обувь', 'sneakers', 'Кроссовки'),
    ('Chaussures', 'Ballerines'): ('shoes', 'Обувь', 'ballet_flats', 'Балетки'),
    ('Chaussures', 'Mocassins et chaussures à lacets'): ('shoes', 'Обувь', 'loafers_mules', 'Лоферы и обувь на шнуровке'),
    ('Chaussures', 'Sandales'): ('shoes', 'Обувь', 'sandals', 'Сандалии'),
    ('Chaussures', 'Bottes et bottines'): ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны'),
    ('Chaussures', 'Escarpins'): ('shoes', 'Обувь', 'heels', 'Туфли-лодочки'),
    ('Accessoires', 'Lunettes'): ('accessories', 'Аксессуары', 'eyewear', 'Очки'),
    ('Accessoires', 'Accessoires de sac et porte-clés'): ('accessories', 'Аксессуары', 'bag_accessories', 'Аксессуары для сумок и брелоки'),
    ('Accessoires', 'Chapeaux'): ('accessories', 'Аксессуары', 'hats', 'Головные уборы'),
    ('Accessoires', 'Ceintures'): ('accessories', 'Аксессуары', 'belts', 'Ремни'),
    ('Accessoires', 'Accessoires de tête'): ('accessories', 'Аксессуары', 'hair_accessories', 'Аксессуары для волос'),
    ('Petite maroquinerie', 'Petits portefeuilles'): ('accessories', 'Аксессуары', 'wallets', 'Кошельки'),
    ('Petite maroquinerie', 'Porte-cartes'): ('accessories', 'Аксессуары', 'card_holders', 'Картхолдеры'),
    ('Petite maroquinerie', 'Grands portefeuilles'): ('accessories', 'Аксессуары', 'wallets', 'Кошельки'),
    ('Bijoux fantaisie', "Boucles d'oreilles"): ('accessories', 'Аксессуары', 'earrings', 'Серьги'),
    ('Bijoux fantaisie', 'Colliers'): ('accessories', 'Аксессуары', 'necklaces', 'Ожерелья'),
    ('Bijoux fantaisie', 'Bracelets'): ('accessories', 'Аксессуары', 'bracelets', 'Браслеты'),
    ('Bijoux fantaisie', 'Bagues et broches'): ('accessories', 'Аксессуары', 'rings_brooches', 'Кольца и броши'),
}


MIUMIU_TITLE_FIXES = {
    'miumiu:5D743E_3KY8_F0038_F_055': "Кожаные мокасины Deco'",
    'miumiu:5E165E_D7R_F0044_F_BD05': 'Кроссовки 530 SL New Balance x Miu Miu из технической ткани и замши',
    'miumiu:5E333E_Z5O_F0379_F_005': 'Мюли New Balance x Miu Miu 530 SL',
    'miumiu:5HC511_2G8G_F0379': 'Панама из ткани дрилл',
    'miumiu:5HC511_ADXI_F011P': 'Панама из ткани дрилл',
    'miumiu:5HC511_ADXI_F022X': 'Панама из ткани дрилл',
    'miumiu:5HC511_ADXI_F0964': 'Панама из ткани дрилл',
    'miumiu:MJT694_1464_F0161_S_OOO': 'Топ из окрашенного в готовом виде трикотажа в рубчик',
    'miumiu:MJT696_15NM_F0031_S_OOO': 'Топ из окрашенного в готовом виде трикотажа в рубчик',
    'miumiu:MJT747_6726_F0009_S_OOO': 'Топ из эластичного технического джерси',
    'miumiu:MPT296_17QK_F0JRS_S_OOO': 'Топ-бандана из замши',
    'miumiu:MPT308_178A_F0343_S_OOO': 'Жилет из овчины',
    'miumiu:MPV940_17PW_F0401_S_OOO': 'Куртка-бомбер из замши',
    'miumiu:MPV949_17QK_F0JRS_S_OOO': 'Куртка-бомбер из замши',
    'miumiu:MPV955_172R_F0594_S_OOO': 'Куртка-бомбер из овчины',
    'miumiu:MPV985_178A_F098L_S_OOO': 'Куртка-бомбер из овчины',
    'miumiu:MPW058_6441_F0032_S_OOO': 'Куртка-бомбер из овчины',
    'miumiu:MPW058_6441_F0038_S_OOO': 'Куртка-бомбер из овчины',
    'miumiu:5R517E_054_F0393_F_005': 'Мюли Deck из замши',
    'miumiu:5R604E_054_F0324_F_005': 'Мюли Deck из замши',
    'miumiu:5BB172_2AMP_F0009_V_OLO': 'Сумка Beau из кожи айерс',
    'miumiu:5BB172_2BN6_F0005_V_OLO': 'Сумка Beau из кожи айерс с принтом под питона',
    'miumiu:5BB173_2CRI_F0046_V_OWO': 'Сумка Beau из кожи в технике пэчворк',
    'miumiu:5BB173_2CRW_F0002_V_OTM': 'Сумка Beau из кожи',
    'miumiu:5BB173_2CRW_F0036_V_OTM': 'Сумка Beau из кожи',
    'miumiu:5BB173_2CRW_F0046_V_OTM': 'Сумка Beau из кожи',
    'miumiu:5BB173_2CRW_F0K74_V_OTM': 'Сумка Beau из кожи',
    'miumiu:5BB173_2CRW_F0NBL_V_OTM': 'Сумка Beau из кожи',
    'miumiu:5HC179_ADXI_F011P': 'Бейсболка из ткани дрилл',
    'miumiu:5HC370_2G9V_F05QB': 'Бейсболка из кожи наппа и ткани дрилл',
    'miumiu:MJLA38_14RW_F0008_S_OOO': 'Толстовка из хлопкового пике',
    'miumiu:MH2071_6472_F0D65_S_OOO': 'Куртка из эластичного поплина с эффектом выцветания',
    'miumiu:ML1395_18JJ_F0GBN_S_OOO': 'Куртка в клетку',
    'miumiu:ML1398_181N_F0XX7_S_OOO': 'Куртка в клетку',
    'miumiu:MPT333_19QL_F0046_S_OOO': 'Топ из вощёной замши',
}


SUSPICIOUS_RUSSIAN_PHRASES = (
    'в швеции', 'из швеции', 'ткань дрель', 'тканью дрели',
    'верхняя часть бака', 'задворки', 'шерстяной кожи',
    'ногавк', 'брюшеная', 'брюшеной', 'кардур', 'карманы-щелевки',
    'сумка-тото', 'теленочья', 'теленочьей', 'стоунуォッシュ',
    'с свободным', 'с съемн', 'юбка-скерт', 'длинный пальто',
    'с одной проймой', 'низкого кроя', 'высокого кроя',
    'защитные крышки', 'ткань деним цвета изделия',
    'углубленные лацканы', 'ремень со съёмным ремнём', 'пиджак-брюки',
)

MIUMIU_DESCRIPTION_REPLACEMENTS = {
    'тканью дрели': 'тканью дрилл',
    'шерстяной кожи': 'овчины',
    'из швеции': 'из замши',
}

MIUMIU_DESCRIPTION_FIXES = {
    'miumiu:MJLA31_1841_F0BD9_S_OOO': (
        'Непринуждённый характер футболки дополнен знаковым логотипом Miu Miu. '
        'Контрастное цветовое решение подчёркивает современный повседневный стиль модели.'
    ),
    'miumiu:MJNA15_1841_F0Y1C_S_OOO': (
        'Непринуждённый характер футболки дополнен знаковым логотипом Miu Miu. '
        'Контрастное цветовое решение подчёркивает современный повседневный стиль модели.'
    ),
    'miumiu:MJT696_15NM_F0031_S_OOO': (
        'Универсальный топ из трикотажа в рубчик окрашен в готовом виде и дополнен '
        'знаковым логотипом Miu Miu.'
    ),
    'miumiu:MK1659_19U3_F057W_S_OOO': (
        'Эта рубашка отражает эксперименты Miu Miu с элементами мужского гардероба. '
        'Винтажная клетка, свободный крой и необработанный край создают непринуждённый выразительный образ.'
    ),
    'miumiu:MK1659_19U3_F0A8Z_S_OOO': (
        'Эта рубашка отражает эксперименты Miu Miu с элементами мужского гардероба. '
        'Винтажная клетка, свободный крой и необработанный край создают непринуждённый выразительный образ.'
    ),
    'miumiu:MK2019_19EX_F0013_S_OOO': (
        'Цветочный мотив из архивной коллекции 1996 года передаёт интерес Miu Miu к винтажной эстетике. '
        'Эластичная прочная ткань, прилегающий крой и спортивные линии напоминают о женском бельевом стиле.'
    ),
    'miumiu:MMF01I_1847_F0018_S_OOO': (
        'Особая техника вязания хлопкового кардигана воспроизводит эффект тонкого кружева ручной работы, '
        'создавая выразительную объёмную фактуру и подчёркивая спортивные линии модели.'
    ),
    'miumiu:MMG588_1847_F0008_S_OOO': (
        'Особая техника вязания хлопковой мини-юбки воспроизводит эффект тонкого кружева ручной работы. '
        'Объёмная фактура подчёркивает спортивные линии и узнаваемые детали Miu Miu.'
    ),
    'miumiu:MML02L_18TW_F0008_S_OOO': (
        'Тонкая шерстяная пряжа подчёркивает рельефный узор свитера, который плавно расходится '
        'от центра к боковым линиям и демонстрирует мастерство исполнения Miu Miu.'
    ),
    'miumiu:MML03L_1847_F0008_S_OOO': (
        'Особая техника вязания хлопкового поло воспроизводит эффект тонкого кружева ручной работы. '
        'Объёмная фактура подчёркивает спортивные линии и характерные детали Miu Miu.'
    ),
    'miumiu:MML07A_13S1_F0028_S_OOO': (
        'Естественная эстетика Miu Miu сочетается в этом лаконичном свитере с мягкостью кашемира. '
        'Классические мотивы придают модели выразительность, сохраняя баланс элегантности и универсальности.'
    ),
    'miumiu:MML09A_60BG_F0011_S_OOO': (
        'Мягкий кашемир дополнен контрастными деталями, создающими характерный для Miu Miu эффект многослойности. '
        'Сочетание цветов подчёркивает современный непринуждённый характер модели.'
    ),
    'miumiu:MMLB63_174B_F0008_S_OOO': (
        'Узор аргайл добавляет игривую выразительность этому мягкому кашемировому свитеру, '
        'переосмысляя классическую модель в современной эстетике Miu Miu.'
    ),
    'miumiu:MMLB65_174B_F0040_S_OOO': (
        'Узор аргайл добавляет игривую выразительность этому мягкому кашемировому поло, '
        'переосмысляя классическую модель в современной эстетике Miu Miu.'
    ),
    'miumiu:MP2051_6701_F0031_S_OOO': (
        'Специальная обработка и двухслойное переплетение придают шерстяным брюкам слегка рельефную '
        'естественно неоднородную фактуру. Зауженный крой без подкладки сочетает мягкость трикотажа '
        'с линиями, вдохновлёнными лыжными брюками.'
    ),
    'miumiu:MPV949_17QK_F0JRS_S_OOO': (
        'Мягкая фактура замши по-новому раскрывает силуэт куртки-бомбера. Кожаные детали в тон '
        'подчёркивают смелую женственность Miu Miu и внимание бренда к материалам.'
    ),
    'miumiu:MPV960_172P_F0003_S_OOO': (
        'Куртка-бомбер из овчины, вдохновлённая авиаторским стилем, отличается винтажной фактурой '
        'и контрастным воротником. Детали отражают узнаваемую эстетику Miu Miu и эксперименты '
        'бренда с элементами мужского гардероба.'
    ),
    'miumiu:MT2194_19EX_F0089_S_OOO': (
        'Цветочный мотив из архивной коллекции 1996 года передаёт интерес Miu Miu к винтажной эстетике. '
        'Эластичная прочная ткань, прилегающий крой и спортивные линии напоминают о женском бельевом стиле.'
    ),
    'miumiu:SMUB11_E14L_F04I0_C_U055': (
        'Солнцезащитные очки из ацетата отличаются утончённым характером. Слегка изогнутая оправа формы '
        '«кошачий глаз» дополнена плоскими дужками с вертикальным логотипом Miu Miu.'
    ),
    'miumiu:SMUB11_E16K_FE80Q_C_U055': (
        'Солнцезащитные очки из ацетата отличаются утончённым характером. Слегка изогнутая оправа формы '
        '«кошачий глаз» дополнена плоскими дужками с вертикальным логотипом Miu Miu.'
    ),
    'miumiu:SMUB11_E20M_FE20I_C_U055': (
        'Солнцезащитные очки из ацетата отличаются утончённым характером. Слегка изогнутая оправа формы '
        '«кошачий глаз» дополнена плоскими дужками с вертикальным логотипом Miu Miu.'
    ),
    'miumiu:SMUB11_E21I_FE10Y_C_U055': (
        'Солнцезащитные очки из ацетата отличаются утончённым характером. Слегка изогнутая оправа формы '
        '«кошачий глаз» дополнена плоскими дужками с вертикальным логотипом Miu Miu.'
    ),
    'miumiu:SP9490_19EX_F0013_S_OOO': (
        'Цветочный мотив из архивной коллекции 1996 года передаёт интерес Miu Miu к винтажной эстетике. '
        'Эластичная устойчивая к затяжкам ткань, прилегающий крой и спортивные линии напоминают '
        'о женском бельевом стиле.'
    ),
}

MIUMIU_MATERIAL_ALIASES = (
    ('хлопок', ('coton',)),
    ('кожа ягнёнка', ("cuir d'agneau", 'cuir d’agneau')),
    ('телячья кожа', ('cuir de veau',)),
    ('козья кожа', ('cuir de chèvre',)),
    ('кожа буйвола', ('buffle',)),
    ('кожа водяной змеи', ('serpent',)),
    ('кожа', ('cuir',)),
    ('шерсть', ('laine',)),
    ('кашемир', ('cachemire',)),
    ('шёлк', ('soie',)),
    ('мохер', ('mohair',)),
    ('лама', ('lama',)),
    ('верблюжья шерсть', ('poil de chameau',)),
    ('лён', ('lin',)),
    ('вискоза', ('viscose',)),
    ('лиоцелл', ('lyocell',)),
    ('полиэстер', ('polyester',)),
    ('полиамид', ('polyamide',)),
    ('эластан', ('élasthanne', 'elasthanne')),
    ('акрил', ('acrylique',)),
    ('ацетат', ('acétate', 'acetate')),
    ('латунь', ('laiton',)),
    ('металл', ('métal', 'metal')),
    ('стекло', ('verre',)),
    ('эмаль', ('émail', 'email')),
    ('смола', ('résine', 'resine')),
    ('метакрилат', ('méthacrylate', 'methacrylate')),
    ('термопластичный полиуретан', ('polyuréthane thermoplastique',)),
)

# Названия оттенков в выгрузке приходят на французском даже после общего
# перевода карточки. Словарь сохраняет фирменные имена, но делает фильтр и
# карточку понятными русскоязычному покупателю.
MIUMIU_COLOR_TERMS = {
    'abricot': 'Абрикосовый', 'acajou': 'Красное дерево', 'acier': 'Стальной',
    'agave': 'Агава', 'aigue-marine': 'Аквамарин', 'albinos': 'Белоснежный',
    'begonia': 'Бегониевый',
    'albâtre': 'Алебастровый', 'aluminium': 'Алюминиевый', 'amarante': 'Амарантовый',
    'amaranth': 'Амарантовый', 'amarente': 'Амарантовый', 'ambre': 'Янтарный',
    'anthracite': 'Антрацитовый', 'antique': 'Античный', 'aqua': 'Аква',
    'ardoise': 'Сланцевый', 'argent': 'Серебристый', 'argile': 'Глиняный',
    'astral': 'Астральный', 'beige': 'Бежевый', 'beige chair': 'Телесно-бежевый',
    'beige désert': 'Пустынно-бежевый', 'beige liège': 'Пробково-бежевый',
    'beige malt': 'Солодово-бежевый', 'beige poudré': 'Пудрово-бежевый',
    'beige sable': 'Песочно-бежевый', 'bijou': 'Драгоценный', 'billard': 'Бильярдный зелёный',
    'blanc': 'Белый', 'blanc albino': 'Белоснежный', 'blanc albinos': 'Белоснежный',
    'blanc cire': 'Восково-белый', 'blanc craie': 'Мелово-белый', 'blanc cru': 'Натуральный белый',
    'bleu': 'Синий', 'bleu armée de l’air': 'Военно-воздушный синий',
    'bleu astral': 'Астральный синий', 'bleu aviateur': 'Авиационный синий',
    'bleu aviation': 'Авиационный синий', 'bleu baltique': 'Балтийский синий',
    'bleu barbeau': 'Васильковый', 'bleu ciel': 'Небесно-голубой',
    'bleu clair': 'Светло-голубой', 'bleu cobalt': 'Кобальтовый синий',
    'bleu encre': 'Чернильно-синий', 'bleu indigo': 'Индиго', 'bleu lac': 'Озёрно-синий',
    'bleu layette': 'Нежно-голубой', 'bleu marine': 'Тёмно-синий', 'bleu mer': 'Морской синий',
    'bleu pervenche': 'Барвинковый', 'bleu poudre': 'Пудрово-голубой',
    'bleu roi': 'Королевский синий', 'bleu royal': 'Королевский синий',
    'bleu saphir': 'Сапфировый', 'bleu vif': 'Ярко-синий', 'bleuet': 'Васильковый',
    'blu ray': 'Сине-голубой', 'blue astral': 'Астральный синий', 'blush rosé': 'Розовый румянец',
    'bois de bruyère': 'Вересковое дерево', 'bordeaux': 'Бордовый', 'boue': 'Серо-коричневый',
    'bouteille': 'Бутылочно-зелёный', 'brandy': 'Бренди', 'bronze': 'Бронзовый',
    'brun foncé': 'Тёмно-коричневый', 'bruyère': 'Вересковый', 'bégonia': 'Бегониевый',
    'bégonia rose': 'Розовая бегония', 'cacao': 'Какао', 'cacao brown': 'Коричневый какао',
    'café': 'Кофейный', 'calcaire': 'Известняковый', 'camel': 'Верблюжий',
    'camel lenses': 'Линзы верблюжьего оттенка', 'camée': 'Камея', 'cannelle': 'Коричный',
    'caramel': 'Карамельный', 'carmin': 'Карминовый', 'cerise': 'Вишнёвый',
    'chanvre': 'Конопляный', 'chêne': 'Дубовый', 'cocoa brown': 'Коричневый какао',
    'cognac': 'Коньячный', 'colombe': 'Голубиный серый', 'corail': 'Коралловый',
    'cord': 'Верёвочный', 'corde': 'Верёвочный', 'cornflower blue': 'Васильковый',
    'craie': 'Меловой', 'cristal': 'Хрустальный', 'crème': 'Кремовый',
    'cuir': 'Кожаный', 'cèdre': 'Кедровый', 'céleste': 'Небесный',
    'ebony': 'Эбеновый', 'ebène': 'Эбеновый', 'ébène': 'Эбеновый', 'ecru': 'Экрю',
    'écru': 'Экрю', 'encre': 'Чернильный', 'fauve': 'Рыжевато-коричневый',
    'fauve clair': 'Светлый рыжевато-коричневый', 'fer': 'Железный',
    'fougère': 'Папоротниковый', 'framboise': 'Малиновый', 'fuchsia': 'Фуксия',
    'fumée': 'Дымчатый', 'gerbera': 'Герберовый', 'glycine': 'Глициниевый',
    'grenat': 'Гранатовый', 'grey': 'Серый', 'gris': 'Серый',
    'gris acier': 'Стальной серый', 'gris anthracite': 'Антрацитовый серый',
    'gris ardoise': 'Сланцево-серый', 'gris argile': 'Глиняно-серый',
    'gris calcaire': 'Известняково-серый', 'gris colombe': 'Голубиный серый',
    'gris fer': 'Железно-серый', 'gris foncé': 'Тёмно-серый', 'gris fumé': 'Дымчато-серый',
    'gris granite': 'Гранитно-серый', 'gris marbre': 'Мраморно-серый',
    'gris nuage': 'Облачно-серый', 'gris perle': 'Жемчужно-серый', 'gris plomb': 'Свинцово-серый',
    'groseille': 'Смородиновый', 'hématite': 'Гематитовый', 'indigo': 'Индиго',
    'iris': 'Ирисовый', 'ivoire': 'Слоновая кость', 'ivory': 'Слоновая кость',
    'jade': 'Нефритовый', 'jaune': 'Жёлтый', 'jaune beurre': 'Масляно-жёлтый',
    'jaune citron': 'Лимонно-жёлтый', 'jaune ensoleillé': 'Солнечно-жёлтый',
    'jaune pollen': 'Пыльцево-жёлтый', 'jaune soleil': 'Солнечно-жёлтый', 'kaki': 'Хаки',
    'lac': 'Озёрный', 'laiton': 'Латунный', 'laque': 'Лаковый', 'lime': 'Лаймовый',
    'liège': 'Пробковый', 'loupe de bois': 'Кап дерева', 'mangue': 'Манговый',
    'marbre': 'Мраморный', 'marron': 'Коричневый', 'marron cacao': 'Коричневый какао',
    'marron camel': 'Верблюжий коричневый', 'marron châtaigne': 'Каштановый',
    'marron cigare': 'Сигарно-коричневый', 'marron foncé': 'Тёмно-коричневый',
    'marron marbre': 'Мраморно-коричневый', 'marron tourbe': 'Торфяно-коричневый',
    'mauve': 'Сиреневый', 'miel': 'Медовый', 'moutarde': 'Горчичный',
    'multicolore': 'Разноцветный', 'naturel': 'Натуральный', 'neutre': 'Нейтральный',
    'noir': 'Чёрный', 'noisette': 'Ореховый', 'noisette marron': 'Орехово-коричневый',
    'nuage': 'Облачный', 'nénuphar': 'Кувшинковый', 'ocre': 'Охристый',
    'olive': 'Оливковый', 'opale': 'Опаловый', 'opaline': 'Опалиновый',
    'or': 'Золотой', 'or vintage': 'Винтажное золото', 'orange': 'Оранжевый',
    'orge': 'Ячменный', 'palissandre': 'Палисандровый', 'peach': 'Персиковый',
    'pervenche': 'Барвинковый', 'pierre calcaire': 'Известняковый',
    'pierre ponce': 'Пемзовый', 'platine': 'Платиновый', 'plomb': 'Свинцовый',
    'pyrite': 'Пиритовый', 'pêche': 'Персиковый', 'rose': 'Розовый',
    'rose albâtre': 'Алебастрово-розовый', 'rose begonia': 'Бегониево-розовый',
    'rose bégonia': 'Бегониево-розовый', 'rose géranium': 'Гераниево-розовый',
    'rose orchidée': 'Орхидейно-розовый', 'rose poudré': 'Пудрово-розовый',
    'rose pâle': 'Бледно-розовый', 'rose pétale': 'Лепестково-розовый',
    'rouge': 'Красный', 'rouge amarante': 'Амарантово-красный', 'rouge rubis': 'Рубиново-красный',
    'rouille': 'Ржаво-коричневый', 'rubis': 'Рубиновый', 'rush': 'Камышовый',
    'sable': 'Песочный', 'safari': 'Сафари', 'sésame': 'Кунжутный', 'tabac': 'Табачный',
    'talc': 'Тальковый', 'tamarin': 'Тамариндовый', 'terre cuite': 'Терракотовый',
    'terre de sienne': 'Сиена', 'topaz': 'Топазовый', 'topaze': 'Топазовый',
    'toundra': 'Тундровый', 'tundra': 'Тундровый', 'travertin': 'Травертиновый',
    'turquoise': 'Бирюзовый', 'vanille': 'Ванильный', 'vert': 'Зелёный',
    'vert billard': 'Бильярдный зелёный', 'vert bouteille': 'Бутылочно-зелёный',
    'vert citron': 'Лимонно-зелёный', 'vert de soja': 'Соево-зелёный',
    'vert forêt': 'Лесной зелёный', 'vert fougère': 'Папоротниково-зелёный',
    'vert kiwi': 'Киви', 'vert laurier': 'Лаврово-зелёный', 'vert laurier-rose': 'Олеандрово-зелёный',
    'vert lierre': 'Плющево-зелёный', 'vert loden': 'Лоденово-зелёный',
    'vert olive': 'Оливково-зелёный', 'vert pistache': 'Фисташково-зелёный',
    'vert sauge': 'Шалфейно-зелёный', 'vert selva': 'Зелёный сельва',
    'vert soja': 'Соево-зелёный', 'violet': 'Фиолетовый', 'whisky': 'Виски',
    'white': 'Белый', 'écarlate': 'Алый', 'émeraude': 'Изумрудный',
}

MIUMIU_EYEWEAR_COLOR_LABELS = {
    'verre transparent blue ray': 'Прозрачные сине-голубые линзы',
    'verres amazonia': 'Линзы цвета Амазонии',
    'verres anthracite bio': 'Антрацитовые биолинзы',
    'verres beige camel': 'Бежево-верблюжьи линзы',
    'verres bio abricot': 'Абрикосовые биолинзы',
    'verres bio dégradé de foin': 'Биолинзы с градиентом оттенка сена',
    'verres bio dégradées pivoine': 'Биолинзы с пионовым градиентом',
    'verres bio dégradées tamarind': 'Биолинзы с тамариндовым градиентом',
    'verres bio foin': 'Биолинзы оттенка сена',
    'verres bio gris basalte': 'Базальтово-серые биолинзы',
    'verres bio rouge marsala': 'Биолинзы цвета красной марсалы',
    'verres bleus': 'Синие линзы', 'verres bronze': 'Бронзовые линзы',
    'verres camomille': 'Линзы ромашкового оттенка',
    'verres dégradés pervenche': 'Линзы с барвинковым градиентом',
    'verres dégradés roses': 'Линзы с розовым градиентом',
    'verres en cristal dégradé bleu ciel clair': 'Хрустальные линзы со светло-голубым градиентом',
    'verres gris': 'Серые линзы', 'verres gris ardoise': 'Сланцево-серые линзы',
    'verres gris clair': 'Светло-серые линзы', 'verres gris foncé': 'Тёмно-серые линзы',
    'verres gris-vert': 'Серо-зелёные линзы', 'verres indigo': 'Линзы цвета индиго',
    'verres lilas': 'Сиреневые линзы', 'verres marron chocolat': 'Шоколадно-коричневые линзы',
    'verres mauves': 'Сиреневые линзы',
    'verres miroitées dégradées azalée': 'Зеркальные линзы с градиентом азалии',
    'verres ombrés gris fumé': 'Дымчато-серые градиентные линзы',
    'verres ombrés rose bégonia': 'Бегониево-розовые градиентные линзы',
    'verres or rose': 'Линзы цвета розового золота', 'verres pervenche': 'Барвинковые линзы',
    'verres primevère': 'Линзы оттенка примулы', 'verres rose clair': 'Светло-розовые линзы',
    'verres roses': 'Розовые линзы', 'verres rubis': 'Рубиновые линзы',
    'verres tamarind': 'Тамариндовые линзы',
    'lentilles bio forêt': 'Лесные зелёные биолинзы',
}


def clean_text(value):
    """Убирает транспортные HTML-сущности и неразрывные пробелы из коротких полей."""
    if not isinstance(value, str):
        return value
    return ' '.join(html.unescape(value).replace('\u00a0', ' ').split())


def _localized_title(product):
    title = product.get('title') or {}
    return clean_text((title.get('i18n') or {}).get('ru') or title.get('raw') or '')


def _clothing_leaf(title):
    value = title.casefold()
    if any(word in value for word in ('пальто', 'куртк', 'пиджак', 'бомбер', 'пухов', 'ветров', 'жилет')):
        return 'outerwear', 'Верхняя одежда'
    if any(word in value for word in ('плать', 'юбк')):
        return 'dresses_skirts', 'Платья и юбки'
    if any(word in value for word in ('брюк', 'шорт', 'джинс')):
        return 'trousers_shorts', 'Брюки и шорты'
    if any(word in value for word in ('рубаш', 'блуз')):
        return 'shirts_blouses', 'Рубашки и блузы'
    if any(word in value for word in ('свитшот', 'толстов', 'флис', 'худи')):
        return 'sweatshirts', 'Толстовки'
    return 'tshirts_tops', 'Футболки и топы'


def _miumiu_taxonomy(product):
    category = product.get('category') or {}
    raw_path = [clean_text(item) for item in category.get('raw_path') or []]
    branch = raw_path[1] if len(raw_path) > 1 else ''
    leaf = raw_path[2] if len(raw_path) > 2 else ''
    title = _localized_title(product)

    if branch == 'Parfums':
        return ('beauty', 'Красота и ароматы', 'fragrances', 'Парфюмерия')
    if branch == 'Prêt-à-porter' and (
        leaf in {'T-shirts et sweatshirts', 'Cuir', 'Denim', ''}
        or (len(raw_path) == 3 and (branch, leaf) not in MIUMIU_CATEGORY_MAP)
    ):
        leaf_slug, leaf_name = _clothing_leaf(title)
        return ('clothing', 'Одежда', leaf_slug, leaf_name)
    if branch == 'Accessoires' and leaf == 'Écharpes et gants':
        if any(word in title.casefold() for word in ('перчат', 'вареж')):
            return ('accessories', 'Аксессуары', 'gloves', 'Перчатки')
        return ('scarves', 'Шарфы и платки', 'scarves', 'Шарфы и платки')
    if branch == 'Petite maroquinerie' and not leaf:
        return ('accessories', 'Аксессуары', 'wallets', 'Кошельки')
    if branch == 'Petite maroquinerie' and len(raw_path) == 3:
        return ('accessories', 'Аксессуары', 'wallets', 'Кошельки')
    if branch == 'Bijoux fantaisie' and not leaf:
        return ('accessories', 'Аксессуары', 'bracelets', 'Браслеты')
    if branch == 'Bijoux fantaisie' and len(raw_path) == 3:
        return ('accessories', 'Аксессуары', 'bracelets', 'Браслеты')
    if not branch:
        return ('accessories', 'Аксессуары', 'tech_accessories', 'Чехлы для устройств')
    return MIUMIU_CATEGORY_MAP.get((branch, leaf))


def _canonical_category(code, slug, name, level):
    return {'code': code, 'slug': slug, 'ru': name, 'level': level}


def _normalize_miumiu_materials(attributes):
    existing = attributes.get('material_normalized') or []
    raw = (attributes.get('material') or {}).get('raw')
    source = clean_text(raw).casefold() if isinstance(raw, str) else ''
    result = [clean_text(value) for value in existing if clean_text(value)]
    for normalized, aliases in MIUMIU_MATERIAL_ALIASES:
        if any(alias in source for alias in aliases) and normalized not in result:
            result.append(normalized)
    # «Parfums» в исходном поле обозначает категорию, а не материал.
    attributes['material_normalized'] = result


def _normalize_miumiu_color_label(label):
    if isinstance(label, dict):
        raw = clean_text(label.get('raw'))
        value = clean_text((label.get('i18n') or {}).get('ru') or raw)
    else:
        raw = clean_text(label)
        value = raw
    if not value:
        return
    exact = MIUMIU_EYEWEAR_COLOR_LABELS.get(value.casefold())
    if exact:
        translated = exact
    else:
        translated = ' / '.join(
            MIUMIU_COLOR_TERMS.get(part.casefold(), part)
            for part in re.split(r'\s*(?:/|\+)\s*', value)
            if part
        )
    if isinstance(label, dict):
        label['raw'] = raw
        label.setdefault('i18n', {})['ru'] = translated


def _normalize_miumiu_colors(product):
    attributes = product.get('attributes') or {}
    _normalize_miumiu_color_label(attributes.get('color'))
    for variant in (product.get('variants') or {}).get('colors') or []:
        if isinstance(variant, dict):
            _normalize_miumiu_color_label(variant.get('label'))


def normalize_miumiu_product(raw_product):
    """Возвращает копию карточки Miu Miu с канонической русской таксономией."""
    product = deepcopy(raw_product)
    product_id = str(product.get('id') or '')
    if not product_id.startswith('miumiu:') and clean_text(product.get('brand')).casefold() != 'miu miu':
        raise ValueError(f'Карточка {product_id or "без id"} не относится к Miu Miu')

    for field in ('title',):
        value = product.get(field)
        if isinstance(value, dict):
            value['raw'] = clean_text(value.get('raw'))
            translations = value.setdefault('i18n', {})
            if translations.get('ru'):
                translations['ru'] = clean_text(translations['ru'])
    if product_id in MIUMIU_TITLE_FIXES:
        product.setdefault('title', {}).setdefault('i18n', {})['ru'] = MIUMIU_TITLE_FIXES[product_id]

    attributes = product.get('attributes')
    if not isinstance(attributes, dict):
        attributes = {}
        product['attributes'] = attributes
    _normalize_miumiu_materials(attributes)
    _normalize_miumiu_colors(product)

    description = product.get('description')
    if isinstance(description, dict):
        # В описании сохраняем HTML, исправляя только неразрывные пробелы.
        for key in ('raw',):
            if isinstance(description.get(key), str):
                description[key] = description[key].replace('\u00a0', ' ')
        for key, value in (description.get('i18n') or {}).items():
            if isinstance(value, str):
                value = value.replace('\u00a0', ' ')
                if key == 'ru':
                    for incorrect, replacement in MIUMIU_DESCRIPTION_REPLACEMENTS.items():
                        value = re.sub(re.escape(incorrect), replacement, value, flags=re.IGNORECASE)
                description['i18n'][key] = value
        if product_id in MIUMIU_DESCRIPTION_FIXES:
            description.setdefault('i18n', {})['ru'] = MIUMIU_DESCRIPTION_FIXES[product_id]

    taxonomy = _miumiu_taxonomy(product)
    if taxonomy is None:
        category = product.get('category') or {}
        raise ValueError(f'{product_id}: неизвестная категория {category.get("raw_path")}')
    parent_slug, parent_name, leaf_slug, leaf_name = taxonomy
    source_code = f'MIUMIU_{parent_slug}_{leaf_slug}'.upper()
    canonical_nodes = [_canonical_category(source_code, parent_slug, parent_name, 2)]
    if leaf_slug != parent_slug:
        canonical_nodes.append(_canonical_category(source_code, leaf_slug, leaf_name, 3))

    category = product.get('category')
    if not isinstance(category, dict):
        category = {}
        product['category'] = category
    raw_path = [clean_text(item) for item in category.get('raw_path') or []]
    category['raw_path'] = raw_path
    if not isinstance(category.get('i18n'), dict):
        category['i18n'] = {}
    category['i18n']['ru'] = [
        'Женщины', parent_name, leaf_name, _localized_title(product),
    ]
    category['canonical_path'] = {
        'department': 'women',
        'department_ru': 'Женщины',
        'categories': canonical_nodes,
    }
    return product


def normalize_miumiu_catalog(products):
    normalized = []
    errors = []
    for index, product in enumerate(products, start=1):
        try:
            normalized.append(normalize_miumiu_product(product))
        except (TypeError, ValueError) as exc:
            errors.append({'index': index, 'id': product.get('id') if isinstance(product, dict) else None, 'error': str(exc)})
    return normalized, errors


# The Row использует короткую англоязычную таксономию Shopify. Здесь она
# приводится к тем же ключам, что уже используются общим каталогом FINDER.
THEROW_CATEGORY_MAP = {
    'PANTS': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'SWEATERS': ('clothing', 'Одежда', 'knitwear', 'Трикотаж'),
    'TSHIRTS': ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы'),
    'TOPS': ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы'),
    'JACKETS': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'OUTERWEAR': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'DRESSES': ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    'SKIRTS': ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    'SWEATSHIRTS': ('clothing', 'Одежда', 'sweatshirts', 'Толстовки'),
    'UNDERPINNINGS': ('clothing', 'Одежда', 'swim_beachwear', 'Бельё и пляжная одежда'),
    'SANDALS': ('shoes', 'Обувь', 'sandals', 'Сандалии'),
    'SNEAKERS': ('shoes', 'Обувь', 'sneakers', 'Кроссовки'),
    'BOOTS': ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны'),
    'PUMPS': ('shoes', 'Обувь', 'heels', 'Туфли на каблуке'),
    'SHOULDER BAGS': ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'),
    'CROSSBODY BAGS': ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'),
    'SMALL BAGS': ('bags', 'Сумки', 'mini_bags', 'Мини-сумки'),
    'TOP HANDLE BAGS': ('bags', 'Сумки', 'handbags', 'Сумки с ручками'),
    'TOTES': ('bags', 'Сумки', 'tote_bags', 'Сумки-тоут'),
    'SMALL LEATHER GOODS': ('accessories', 'Аксессуары', 'wallets', 'Кошельки'),
}

THEROW_MATERIAL_ALIASES = (
    ('верблюжья шерсть', (r'\bcamel hair\b',)),
    ('кожа ягнёнка', (r'\blambskin\b', r'\blamb leather\b')),
    ('телячья кожа', (r'\bcalfskin\b', r'\bcalf leather\b', r'\bcalfskin leather\b')),
    ('козья кожа', (r'\bgoatskin\b', r'\bgoat leather\b')),
    ('замша', (r'\bsuede\b',)),
    ('кашемир', (r'\bcashmere\b',)),
    ('мохер', (r'\bmohair\b',)),
    ('шерсть', (r'\bwool\b',)),
    ('шёлк', (r'\bsilk\b',)),
    ('хлопок', (r'\bcotton\b', r'\bdenim\b')),
    ('лён', (r'\blinen\b',)),
    ('вискоза', (r'\bviscose\b',)),
    ('лиоцелл', (r'\blyocell\b',)),
    ('полиэстер', (r'\bpolyester\b',)),
    ('полиамид', (r'\bpolyamide\b', r'\bnylon\b')),
    ('эластан', (r'\belastane\b', r'\bspandex\b')),
    ('акрил', (r'\bacrylic\b',)),
    ('ацетат', (r'\bacetate\b',)),
    ('латунь', (r'\bbrass\b',)),
    ('металл', (r'\bmetal\b', r'\bmetallic\b')),
    ('стекло', (r'\bglass\b',)),
    ('смола', (r'\bresin\b',)),
    ('резина', (r'\brubber\b',)),
    ('холст', (r'\bcanvas\b',)),
    ('текстиль', (r'\btextile\b', r'\blace\b')),
    ('рафия', (r'\braffia\b',)),
)

# Исправления опираются на исходное английское название оттенка, а не на
# машинный русский перевод. Остальные фирменные оттенки сохраняются как есть.
THEROW_COLOR_FIXES = {
    'leather': 'Коричневый',
    'cuir': 'Коричневый',
    'fabric': 'Бежевый',
    'toile': 'Бежевый',
    'multi': 'Многоцветный',
    'multi stripe': 'Многоцветная полоска',
    'driftwood': 'Серо-коричневый',
    'dovetail': 'Серо-бежевый',
    'bruciato': 'Тёмно-коричневый',
    'shell': 'Светло-бежевый',
    'military': 'Оливковый',
    'militare': 'Оливковый',
    'sauvage': 'Тёмно-коричневый',
    'muschio': 'Мохово-зелёный',
    'inglese': 'Английский зелёный',
    'leaf': 'Зелёный лист',
    'mud': 'Серо-коричневый',
    'anchor': 'Тёмно-синий',
    'faded': 'Выцветший серый',
    'wither': 'Приглушённый коричневый',
}

THEROW_TITLE_FIXES = {
    'therow:adalet-top-light-blue-stripe': 'Топ Adalet в светло-голубую полоску',
    'therow:ama-slide-toile': 'Сандалии Ama Slide',
    'therow:parla-scarf-almond-cream': 'Шарф Parla',
    'therow:marlo-12-muschio-ans': 'Сумка Marlo 12',
    'therow:nobilis-chelsea-boot-black': 'Ботинки челси Nobilis',
    'therow:nobilis-chelsea-boot-lacquer-brown': 'Ботинки челси Nobilis',
}

THEROW_TITLE_SERIES = (
    ('Ama Slide', 'Сандалии Ama Slide'),
    ('Marlo 12', 'Сумка Marlo 12'),
    ('Large N/S Park Tote', 'Большая сумка-тоут N/S Park'),
    ('Small Terrasse', 'Маленькая сумка Terrasse'),
    ('Astra Bag', 'Сумка Astra'),
    ('Mistral Top', 'Топ Mistral'),
    ('Philo Shirt', 'Рубашка Philo'),
    ('Medium Ornamental Towel', 'Среднее декоративное полотенце'),
    ('Medium Flower Towel', 'Среднее полотенце с цветочным узором'),
)

THEROW_TITLE_SUFFIXES = (
    (' Baguette', 'Сумка-багет'),
    (' Sneakers', 'Кроссовки'),
    (' Sneaker', 'Кроссовки'),
    (' Pants', 'Брюки'),
    (' Pant', 'Брюки'),
    (' Shirt', 'Рубашка'),
    (' Scarf', 'Шарф'),
    (' Belt', 'Ремень'),
    (' Dress', 'Платье'),
    (' Skirt', 'Юбка'),
    (' Coat', 'Пальто'),
    (' Bag', 'Сумка'),
)

THEROW_AWKWARD_TITLE_RE = re.compile(
    r'\b(?:баг|белт|скарф|ширт|пантс?|сникерс?|коут|дресс|скерт)\b',
    re.IGNORECASE,
)

THEROW_DESCRIPTION_REPLACEMENTS = (
    ('ногавками', 'штанинами'),
    ('ногавках', 'штанинах'),
    ('ногавицы', 'штанины'),
    ('ногавки', 'штанины'),
    ('ногавок', 'штанин'),
    ('бархатного кардура', 'вельвета'),
    ('кардуровым', 'вельветовым'),
    ('кашемирового кардуроя', 'кашемирового вельвета'),
    ('ткани кардурой', 'вельвета'),
    ('кардура', 'вельвета'),
    ('брюшеной', 'ворсованной'),
    ('карманами-щелевками', 'прорезными карманами'),
    ('юбка-скерт', 'юбка'),
    ('сумка-тото', 'сумка-тоут'),
    ('теленочьей кожи', 'телячьей кожи'),
    ('с эффектом «стоунуォッシュ»', 'с эффектом обработки stone wash'),
    ('методом «стоунуォッシュ»', 'методом stone wash'),
    ('с свободным', 'со свободным'),
    ('с съемным пряжкой', 'со съёмной пряжкой'),
    ('ремень со съёмным ремнём и пряжкой', 'ремень со съёмной пряжкой'),
    ('с съемн', 'со съёмн'),
    (' съемн', ' съёмн'),
    ('одной широкой ремешком', 'одним широким ремешком'),
    ('скрытым застежкой', 'скрытой застёжкой'),
    ('длинный пальто', 'длинное пальто'),
    ('застегивается спереди на ползамок', 'застёгивается спереди на короткую молнию'),
    ('застежку-полузамок', 'короткую застёжку-молнию'),
    ('ползамок', 'короткая молния'),
)

THEROW_WASHED_REPLACEMENTS = (
    ('из отбеленного хлопкового денима', 'из хлопкового денима с эффектом стирки'),
    ('из отбеленного двустороннего кашемира', 'из двустороннего кашемира с эффектом стирки'),
    ('из отбеленного хлопкового полотна', 'из хлопкового полотна с эффектом стирки'),
    ('из отбеленного хлопка', 'из хлопка с эффектом стирки'),
    ('из отбеленного тафты', 'из тафты с эффектом стирки'),
    ('из отбеленного шелка', 'из шёлка с эффектом стирки'),
)

THEROW_RINSED_REPLACEMENTS = (
    ('из отбеленного индиго-цветного хлопкового денима', 'из промытого хлопкового денима цвета индиго'),
)

THEROW_CONTEXT_DESCRIPTION_REPLACEMENTS = {
    'mid-rise': (
        ('средней длины', 'средней посадки'),
        ('среднего кроя', 'средней посадки'),
    ),
    'low-rise': (
        ('низкого кроя', 'низкой посадки'),
    ),
    'high-rise': (
        ('высокого кроя', 'высокой посадки'),
    ),
    'single-breasted': (
        ('Пиджак с одной проймой', 'Однобортный пиджак'),
        ('Плащ с одной проймой', 'Однобортный плащ'),
        ('пиджак с одной проймой', 'однобортный пиджак'),
        ('с одной проймой', 'однобортного кроя'),
    ),
    'notch lapel': (
        ('воротник с вырезом', 'лацканы с вырезом'),
        ('углубленные лацканы', 'лацканы с вырезом'),
    ),
    'storm flap': (
        ('защитные крышки', 'штормовые клапаны'),
    ),
    'garment-dyed': (
        ('ткани деним цвета изделия', 'денима, окрашенного в готовом виде'),
    ),
}

THEROW_BASE_COLOR_ALIASES = (
    ('multicolor', ('multicolor', 'multi stripe', 'multi')),
    ('black', ('black', 'ebony')),
    ('white', ('white', 'ivory', 'off white', 'milk', 'porcelain', 'chalk')),
    ('gray', ('grey', 'gray', 'charcoal', 'anthracite', 'smoke', 'fog', 'pebble', 'cement', 'concrete', 'stone', 'pepper', 'fume', 'rhino')),
    ('beige', ('beige', 'sand', 'natural', 'ecru', 'cream', 'vanilla', 'shell', 'bone', 'bisque', 'biscuit', 'desert', 'dune', 'toile', 'dovetail', 'papyrus', 'macadamia', 'thé')),
    ('brown', ('brown', 'leather', 'cuir', 'taupe', 'sepia', 'walnut', 'coffee', 'moka', 'mocha', 'chocolate', 'chestnut', 'tobacco', 'cognac', 'camel', 'honey', 'mud', 'driftwood', 'wither', 'bruciato', 'sauvage', 'oak', 'rust', 'russet', 'cigare', 'nuez', 'kentucky', 'brasil', 'basalt beach', 'cinnamon', 'fudge', 'espresso')),
    ('blue', ('blue', 'navy', 'indigo', 'cobalt', 'midnight', 'twilight', 'anchor', 'sapphire', 'zaffiro')),
    ('green', ('green', 'olive', 'moss', 'pine', 'spruce', 'sage', 'leaf', 'khaki', 'military', 'muschio', 'amazzonia', 'inglese', 'emerald', 'lichen')),
    ('red', ('red', 'burgundy', 'bordeaux', 'borgogna', 'scarlet', 'scarlatto', 'carmine', 'carmin', 'amaranth', 'amaranto', 'lacquer red')),
    ('pink', ('pink', 'rose', 'blush')),
    ('orange', ('orange', 'copper')),
    ('yellow', ('yellow', 'mustard', 'straw', 'butter')),
    ('purple', ('purple', 'plum', 'aubergine')),
    ('gold', ('gold',)),
    ('silver', ('silver',)),
)


def _therow_base_color(raw_color):
    parts = [part.strip().casefold() for part in re.split(r'\s*/\s*', raw_color) if part.strip()]
    groups = []
    for part in parts:
        group = next((
            slug for slug, aliases in THEROW_BASE_COLOR_ALIASES
            if any(alias in part for alias in aliases)
        ), '')
        if group and group not in groups:
            groups.append(group)
    if len(groups) > 1:
        return 'multicolor'
    return groups[0] if groups else ''


def _normalize_therow_title(product):
    title = product.get('title') or {}
    raw_title = clean_text(title.get('raw'))
    translated = clean_text((title.get('i18n') or {}).get('ru'))
    if not raw_title or not THEROW_AWKWARD_TITLE_RE.search(translated):
        return
    for suffix, product_type in THEROW_TITLE_SUFFIXES:
        if raw_title.casefold().endswith(suffix.casefold()):
            model = raw_title[:-len(suffix)].strip(' ,-')
            if model:
                title.setdefault('i18n', {})['ru'] = f'{product_type} {model}'
            return


def _normalize_therow_description(product):
    description = product.get('description') or {}
    raw = clean_text(description.get('raw')).casefold()
    translated = (description.get('i18n') or {}).get('ru')
    if not isinstance(translated, str):
        return
    for incorrect, replacement in THEROW_DESCRIPTION_REPLACEMENTS:
        translated = re.sub(
            re.escape(incorrect), replacement, translated, flags=re.IGNORECASE,
        )
    if 'washed' in raw:
        for incorrect, replacement in THEROW_WASHED_REPLACEMENTS:
            translated = re.sub(
                re.escape(incorrect), replacement, translated, flags=re.IGNORECASE,
            )
    if 'rinsed' in raw:
        for incorrect, replacement in THEROW_RINSED_REPLACEMENTS:
            translated = re.sub(
                re.escape(incorrect), replacement, translated, flags=re.IGNORECASE,
            )
    for source_term, replacements in THEROW_CONTEXT_DESCRIPTION_REPLACEMENTS.items():
        if source_term not in raw:
            continue
        for incorrect, replacement in replacements:
            translated = re.sub(
                re.escape(incorrect), replacement, translated, flags=re.IGNORECASE,
            )
    if 'trouser' in raw:
        translated = re.sub(r'^Пиджак(?:-брюки)?', 'Брюки', translated)
    description.setdefault('i18n', {})['ru'] = translated


def _therow_taxonomy(product):
    category = product.get('category') or {}
    raw_path = [clean_text(item).upper() for item in category.get('raw_path') or []]
    leaf = raw_path[-1] if raw_path else ''
    title = _localized_title(product).casefold()
    raw_title = clean_text((product.get('title') or {}).get('raw')).casefold()

    if leaf == 'FLATS':
        if any(word in title for word in ('балет', 'mary jane', 'мэри джейн')):
            return ('shoes', 'Обувь', 'ballet_flats', 'Балетки')
        return ('shoes', 'Обувь', 'loafers_mules', 'Лоферы и мюли')
    if leaf in {'ACCESSORIES', 'M ACCESSORIES', 'ACCESSORIES-APAC'}:
        if 'полотен' in title:
            return ('home', 'Для дома', 'bath_linen', 'Принадлежности для ванной')
        if (
            'плед' in title or 'одеял' in title or 'бланкет' in title
            or 'blanket' in raw_title
        ):
            return ('home', 'Для дома', 'blankets', 'Пледы')
        if 'очк' in title or 'солнцезащитн' in title:
            return ('accessories', 'Аксессуары', 'eyewear', 'Очки')
        if 'перчат' in title:
            return ('accessories', 'Аксессуары', 'gloves', 'Перчатки')
        if any(word in title for word in ('шапк', 'шляп', 'бини', 'боннет')):
            return ('accessories', 'Аксессуары', 'hats', 'Головные уборы')
        if 'шарф' in title or 'платок' in title or 'скарф' in title:
            return ('scarves', 'Шарфы и платки', 'scarves', 'Шарфы и платки')
        if 'ремень' in title or 'пояс' in title or 'belt' in raw_title:
            return ('accessories', 'Аксессуары', 'belts', 'Ремни')
        if 'карт' in title:
            return ('accessories', 'Аксессуары', 'card_holders', 'Картхолдеры')
        if 'кошел' in title or 'паспорт' in title:
            return ('accessories', 'Аксессуары', 'wallets', 'Кошельки')
    if leaf == 'SMALL LEATHER GOODS' and 'карт' in title:
        return ('accessories', 'Аксессуары', 'card_holders', 'Картхолдеры')
    if leaf == 'GALERIE':
        for slug, name, keywords in (
            ('outerwear', 'Верхняя одежда', ('пальто', 'куртк', 'пуховик', 'кафтан', 'шаль')),
            ('dresses_skirts', 'Платья и юбки', ('плать', 'туник')),
        ):
            if any(word in title for word in keywords):
                return ('clothing', 'Одежда', slug, name)
        return ('clothing', 'Одежда', 'other_clothing', 'Прочая одежда')
    return THEROW_CATEGORY_MAP.get(leaf)


def _normalize_therow_materials(product):
    attributes = product.setdefault('attributes', {})
    title = clean_text((product.get('title') or {}).get('raw'))
    description = clean_text((product.get('description') or {}).get('raw'))
    source = f'{title} {description}'.casefold()
    materials = []
    for normalized, patterns in THEROW_MATERIAL_ALIASES:
        if any(re.search(pattern, source) for pattern in patterns):
            materials.append(normalized)
    # Если указан конкретный тип кожи, обобщённое значение только дублирует фильтр.
    specific_leather = {'кожа ягнёнка', 'телячья кожа', 'козья кожа', 'замша'}
    if any(item in materials for item in specific_leather):
        materials = [item for item in materials if item != 'кожа']
    elif re.search(r'\bleather\b', source):
        materials.append('кожа')
    attributes['material_normalized'] = list(dict.fromkeys(materials))


def _normalize_therow_colors(product):
    for variant in (product.get('variants') or {}).get('colors') or []:
        if not isinstance(variant, dict) or not isinstance(variant.get('label'), dict):
            continue
        label = variant['label']
        raw = clean_text(label.get('raw'))
        base_color = _therow_base_color(raw)
        if base_color:
            variant['base_color'] = base_color
        parts = [part for part in re.split(r'\s*/\s*', raw) if part]
        translated = [THEROW_COLOR_FIXES.get(part.casefold()) for part in parts]
        if parts and all(translated):
            label.setdefault('i18n', {})['ru'] = ' / '.join(translated)


def normalize_therow_product(raw_product):
    """Возвращает The Row-карточку с общей таксономией и чистыми справочниками."""
    product = deepcopy(raw_product)
    product_id = str(product.get('id') or '')
    if not product_id.startswith('therow:') and clean_text(product.get('brand')).casefold() != 'the row':
        raise ValueError(f'Карточка {product_id or "без id"} не относится к The Row')

    for field in ('title', 'description'):
        value = product.get(field)
        if isinstance(value, dict):
            value['raw'] = clean_text(value.get('raw'))
            translations = value.setdefault('i18n', {})
            if translations.get('ru'):
                translations['ru'] = clean_text(translations['ru'])
    if product_id in THEROW_TITLE_FIXES:
        product.setdefault('title', {}).setdefault('i18n', {})['ru'] = THEROW_TITLE_FIXES[product_id]
    else:
        raw_title = clean_text((product.get('title') or {}).get('raw'))
        for source_title, translated_title in THEROW_TITLE_SERIES:
            if raw_title.casefold().startswith(source_title.casefold()):
                product.setdefault('title', {}).setdefault('i18n', {})['ru'] = translated_title
                break
    _normalize_therow_title(product)
    _normalize_therow_description(product)

    taxonomy = _therow_taxonomy(product)
    if taxonomy is None:
        category = product.get('category') or {}
        raise ValueError(f'{product_id}: неизвестная категория {category.get("raw_path")}')
    parent_slug, parent_name, leaf_slug, leaf_name = taxonomy
    source_gender = clean_text(product.get('gender')).casefold()
    if parent_slug == 'home':
        department, department_name = 'home', 'Для дома'
        product['gender'] = ['women', 'men']
    else:
        department = source_gender if source_gender in {'women', 'men'} else 'women'
        department_name = 'Женщины' if department == 'women' else 'Мужчины'
        if source_gender not in {'women', 'men'}:
            product['gender'] = ['women', 'men']

    source_code = f'THEROW_{parent_slug}_{leaf_slug}'.upper()
    if parent_slug == 'home':
        # Для самостоятельного департамента не создаём home/home/...
        nodes = [_canonical_category(source_code, leaf_slug, leaf_name, 2)]
    else:
        nodes = [_canonical_category(source_code, parent_slug, parent_name, 2)]
        if leaf_slug != parent_slug:
            nodes.append(_canonical_category(source_code, leaf_slug, leaf_name, 3))
    category = product.setdefault('category', {})
    category['raw_path'] = [clean_text(item) for item in category.get('raw_path') or []]
    category.setdefault('i18n', {})['ru'] = [
        department_name, parent_name, leaf_name, _localized_title(product),
    ]
    category['canonical_path'] = {
        'department': department,
        'department_ru': department_name,
        'categories': nodes,
    }
    _normalize_therow_materials(product)
    _normalize_therow_colors(product)
    return product


def normalize_therow_catalog(products):
    normalized = []
    errors = []
    for index, product in enumerate(products, start=1):
        try:
            normalized.append(normalize_therow_product(product))
        except (TypeError, ValueError) as exc:
            errors.append({
                'index': index,
                'id': product.get('id') if isinstance(product, dict) else None,
                'error': str(exc),
            })
    families = defaultdict(list)
    for product in normalized:
        canonical = (product.get('category') or {}).get('canonical_path') or {}
        categories = canonical.get('categories') or []
        leaf = categories[-1].get('slug') if categories else ''
        raw_title = clean_text((product.get('title') or {}).get('raw')).casefold()
        families[(canonical.get('department'), leaf, raw_title)].append(product)
    for family in families.values():
        colors = []
        seen_skus = set()
        for product in family:
            for variant in (product.get('variants') or {}).get('colors') or []:
                sku = clean_text(variant.get('sku')) if isinstance(variant, dict) else ''
                if sku and sku not in seen_skus:
                    colors.append(deepcopy(variant))
                    seen_skus.add(sku)
        for product in family:
            product.setdefault('variants', {})['colors'] = deepcopy(colors)
    return normalized, errors


def catalog_quality_report(products, image_paths=()):
    """Формирует машинно-читаемый отчёт качества после нормализации."""
    counters = Counter()
    issues = []
    known_images = {str(PurePosixPath(path)) for path in image_paths}
    referenced_images = set()
    issue_keys = set()

    def issue(kind, product_id, value=''):
        key = (kind, product_id)
        if key in issue_keys:
            return
        issue_keys.add(key)
        counters[kind] += 1
        issues.append({'type': kind, 'product_id': product_id, 'value': str(value)[:500]})

    for product in products:
        product_id = str(product.get('id') or '')
        title = _localized_title(product)
        title_data = product.get('title') or {}
        description_data = product.get('description') or {}
        raw_title = clean_text(title_data.get('raw'))
        raw_description = clean_text(description_data.get('raw'))
        description = (description_data.get('i18n') or {}).get('ru') or ''
        category = product.get('category') or {}
        canonical = category.get('canonical_path') or {}
        if not title or not CYRILLIC_RE.search(title):
            issue('untranslated_title', product_id, title)
        if HTML_ENTITY_RE.search(title):
            issue('title_html_entity', product_id, title)
        if not description or not CYRILLIC_RE.search(description):
            issue('untranslated_description', product_id)
        if raw_title and title.casefold() == raw_title.casefold() and re.search(r'[A-Za-z]', raw_title):
            issue('untranslated_title', product_id, title)
        if (
            raw_description and description.casefold() == raw_description.casefold()
            and re.search(r'[A-Za-z]', raw_description)
        ):
            issue('untranslated_description', product_id)
        if raw_description and len(raw_description) >= 20 and description:
            length_ratio = len(description) / len(raw_description)
            if length_ratio < .35 or length_ratio > 4:
                issue('suspicious_translation_length', product_id, round(length_ratio, 2))
        if product_id.startswith('therow:') and THEROW_AWKWARD_TITLE_RE.search(title):
            issue('suspicious_title_translation', product_id, title)
        english_description_words = re.findall(r'[A-Za-z]{3,}', description)
        description_letters = re.findall(r'[A-Za-zА-Яа-яЁё]', description)
        if (
            len(english_description_words) >= 4 and description_letters
            and sum(letter.isascii() for letter in description_letters) / len(description_letters) > .3
        ):
            issue('partially_untranslated_description', product_id, description)
        suspicious = [phrase for phrase in SUSPICIOUS_RUSSIAN_PHRASES if phrase in f'{title} {description}'.casefold()]
        if suspicious:
            issue('suspicious_translation', product_id, ', '.join(suspicious))
        if not canonical.get('department') or not canonical.get('categories'):
            issue('missing_canonical_taxonomy', product_id)
        attributes = product.get('attributes') or {}
        raw_material = clean_text((attributes.get('material') or {}).get('raw'))
        if raw_material and raw_material.casefold() != 'parfums' and not attributes.get('material_normalized'):
            issue('unnormalized_material', product_id, raw_material)
        for variant in (product.get('variants') or {}).get('colors') or []:
            color = (variant.get('label') or {}) if isinstance(variant, dict) else {}
            value = clean_text((color.get('i18n') or {}).get('ru')) if isinstance(color, dict) else ''
            if value and not CYRILLIC_RE.search(value):
                issue('untranslated_color', product_id, value)
            elif value and re.search(r'[A-Za-zÀ-ÿ]', value):
                issue('partially_untranslated_color', product_id, value)
        local_product_images = 0
        remote_product_images = 0
        for image in (product.get('media') or {}).get('images') or []:
            path = image.get('file')
            urls = image.get('urls') or {}
            if urls.get('original') or urls.get('large') or urls.get('medium') or urls.get('thumbnail'):
                remote_product_images += 1
            if not path:
                if not remote_product_images:
                    issue('missing_image_source', product_id, image.get('role'))
            else:
                local_product_images += 1
                normalized_path = str(PurePosixPath(path))
                referenced_images.add(normalized_path)
                if known_images and normalized_path not in known_images:
                    issue('image_not_found', product_id, normalized_path)
        if not local_product_images and remote_product_images:
            issue('remote_images_only', product_id, remote_product_images)

    return {
        'products': len(products),
        'images_on_disk': len(known_images),
        'referenced_images': len(referenced_images),
        'orphan_images': len(known_images - referenced_images) if known_images else 0,
        'issue_counts': dict(sorted(counters.items())),
        'issues': issues,
    }