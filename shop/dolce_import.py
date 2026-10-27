"""Консервативная адаптация выгрузки Dolce & Gabbana к категориям FINDER."""

from copy import deepcopy
from collections import Counter, defaultdict
import html
from pathlib import PurePosixPath
import re

from .catalog_normalization import _canonical_category, catalog_quality_report, clean_text
from .services import _validate_zip_entries, parse_json_bytes


BRAND = 'dolce-gabbana'
# Лимиты относятся только к этой проверенной по структуре команде; общие лимиты
# пользовательского ZIP-импорта остаются прежними.
MAX_FILES = 40000
MAX_BYTES = 5 * 1024 ** 3
LITERAL_TRANSLATION_RE = re.compile(
    r'растягивающие штаны для ног|оптические элементы винтовки|низкий рост кейт', re.I
)

COLOR_FIXES = {
    'Noir': 'Чёрный', 'Black': 'Чёрный', 'Doré': 'Золотистый',
    'Gold': 'Золотистый', 'Imprimé': 'С принтом', 'Print': 'С принтом',
    'Multicolore': 'Разноцветный', 'Multicolor': 'Разноцветный',
    'Imprimé Animalier': 'Анималистический принт',
    'Azure': 'Лазурный', 'Rose Pâle': 'Бледно-розовый',
    'Brown': 'Коричневый', 'Lilas': 'Сиреневый',
    'Imprimé Carretto': 'Принт Carretto',
}

LEAVES = {
    'Pantalons et Shorts': ('clothing', 'Одежда', 'trousers_shorts', 'Брюки и шорты'),
    'Robes': ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    'Jupes': ('clothing', 'Одежда', 'dresses_skirts', 'Платья и юбки'),
    'Pulls': ('clothing', 'Одежда', 'knitwear', 'Трикотаж'),
    'Tops et Chemisiers': ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы'),
    'Manteaux et Blousons': ('clothing', 'Одежда', 'outerwear', 'Верхняя одежда'),
    'Vestes': ('clothing', 'Одежда', 'outerwear', 'Жакеты и куртки'),
    'Chemises': ('clothing', 'Одежда', 'shirts_blouses', 'Рубашки и блузы'),
    'Costumes et Blazers': ('clothing', 'Одежда', 'outerwear', 'Пиджаки и костюмы'),
    'Denim': ('clothing', 'Одежда', 'trousers_shorts', 'Джинсы'),
    'T-Shirts et Polos': ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и поло'),
    'T-Shirts et Sweatshirts': ('clothing', 'Одежда', 'tshirts_tops', 'Футболки и топы'),
    'Sweatshirts': ('clothing', 'Одежда', 'sweatshirts', 'Толстовки'),
    'Maillots de bain': ('clothing', 'Одежда', 'swim_beachwear', 'Купальники и пляжная одежда'),
    'Lingerie': ('clothing', 'Одежда', 'swim_beachwear', 'Бельё'),
    'Sous-vêtements et Loungewear': ('clothing', 'Одежда', 'swim_beachwear', 'Бельё и домашняя одежда'),
    'Bijoux fantaisie': ('accessories', 'Аксессуары', 'fashion_jewelry', 'Украшения'),
    'Portefeuilles et Petite Maroquinerie': ('accessories', 'Аксессуары', 'wallets', 'Кошельки'),
    'Ceintures': ('accessories', 'Аксессуары', 'belts', 'Ремни'),
    'Chaussettes': ('accessories', 'Аксессуары', 'socks', 'Носки'),
    'Écharpes et Foulards': ('accessories', 'Аксессуары', 'scarves', 'Шарфы и платки'),
    'Echarpes et Foulards': ('accessories', 'Аксессуары', 'scarves', 'Шарфы и платки'),
    'Chapeaux et Gants': ('accessories', 'Аксессуары', 'hats', 'Головные уборы'),
    'Bonnets et Gants': ('accessories', 'Аксессуары', 'hats', 'Головные уборы'),
    'Cravates et Pochettes': ('accessories', 'Аксессуары', 'ties', 'Галстуки и платки'),
    'Coques de telephone': ('accessories', 'Аксессуары', 'tech_accessories', 'Чехлы для устройств'),
    'Lifestyle et Autres accessoires': ('accessories', 'Аксессуары', 'other_accessories', 'Прочие аксессуары'),
    'Accessoires de sac et bandoulières': ('accessories', 'Аксессуары', 'bag_accessories', 'Аксессуары для сумок'),
    'Sacs à Main': ('bags', 'Сумки', 'handbags', 'Сумки с ручками'),
    'Sacs à Épaule et à Bandoulière': ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'),
    'Sac à Bandoulière': ('bags', 'Сумки', 'shoulder_bags', 'Сумки через плечо'),
    'Cabas': ('bags', 'Сумки', 'tote_bags', 'Сумки-тоут'),
    'Sacs Cabas': ('bags', 'Сумки', 'tote_bags', 'Сумки-тоут'),
    'Petits sacs et pochettes': ('bags', 'Сумки', 'mini_bags', 'Мини-сумки'),
    'Sacs à Dos et Sacs Banane': ('bags', 'Сумки', 'backpacks', 'Рюкзаки'),
    'Attachés-cases et pochettes': ('bags', 'Сумки', 'travel_bags', 'Портфели и клатчи'),
    'Sacs de voyage': ('bags', 'Сумки', 'travel_bags', 'Дорожные сумки'),
    'Sneakers': ('shoes', 'Обувь', 'sneakers', 'Кроссовки'),
    'Baskets': ('shoes', 'Обувь', 'sneakers', 'Кроссовки'),
    'Mules et Sabots': ('shoes', 'Обувь', 'loafers_mules', 'Мюли и сабо'),
    'Sandales et Mules': ('shoes', 'Обувь', 'sandals', 'Сандалии и мюли'),
    'Sandales et Compensées': ('shoes', 'Обувь', 'sandals', 'Сандалии'),
    'Escarpins et Slingbacks': ('shoes', 'Обувь', 'heels', 'Туфли'),
    'Drivers et mocassins': ('shoes', 'Обувь', 'loafers_mules', 'Мокасины и лоферы'),
    'Derbies et Richelieux': ('shoes', 'Обувь', 'loafers_mules', 'Дерби и оксфорды'),
    'Bottes et Bottines': ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны'),
    'Bottines et Bottes': ('shoes', 'Обувь', 'boots', 'Сапоги и ботильоны'),
    'Chaussures plates': ('shoes', 'Обувь', 'ballet_flats', 'Обувь на плоской подошве'),
    'Lunettes de Soleil': ('accessories', 'Аксессуары', 'eyewear', 'Солнцезащитные очки'),
}


def _taxonomy(card):
    path = (card.get('category') or {}).get('raw_path') or []
    if not path:
        return None  # Без исходной рубрики нельзя надёжно угадать вид товара.
    if path[0] != 'Mode' or card.get('gender') not in ('women', 'men'):
        return None
    branch = path[2] if len(path) > 2 else ''
    leaf = path[-1] if len(path) > 3 else ''
    if branch == 'Lunettes de Soleil':
        leaf = branch
    if leaf not in LEAVES:
        return None
    parent, parent_ru, child, child_ru = LEAVES[leaf]
    if branch == 'Chaussures' and parent != 'shoes' or branch == 'Sacs' and parent != 'bags':
        return None
    if branch == 'Vêtements' and parent != 'clothing':
        return None
    if branch == 'Accessoires' and parent != 'accessories':
        return None
    if branch == 'Lunettes de Soleil' and child != 'eyewear':
        return None
    if branch not in ('Chaussures', 'Sacs', 'Vêtements', 'Accessoires', 'Lunettes de Soleil'):
        return None
    return parent, parent_ru, child, child_ru


def _replace_preserving_case(pattern, replacement, text):
    return re.sub(pattern, lambda match: replacement.capitalize() if match.group()[0].isupper()
                  else replacement, text, flags=re.I)


LEG_FORMS = {
    'а': 'штанина', 'и': 'штанины', 'у': 'штанину', 'ой': 'штаниной',
    'е': 'штанине', 'ами': 'штанинами', 'ах': 'штанинах',
}


REVIEWED_MULE_DESCRIPTIONS = {
    ('Mules en cuir de veau à logo DG : • Empeigne en cuir de veau '
     '• Première en cuir de chevreau avec logo gaufré • Semelle extérieure en cuir à logo '
     '• Made in Italy'): (
        'Мюли из телячьей кожи с логотипом DG:<br>• Верх из телячьей кожи'
        '<br>• Стелька из козьей кожи с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Сделано в Италии'
    ),
    ('• Empeigne en cuir de veau • Première en cuir de chevreau avec logo gaufré '
     '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
     '• Made in Italy'): (
        '• Верх из телячьей кожи<br>• Стелька из козьей кожи с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
        '<br>• Сделано в Италии'
    ),
    ('• Empeigne en cuir de veau • Première en cuir de chevreau avec logo gaufré '
     '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
     '• Fabrication italienne'): (
        '• Верх из телячьей кожи<br>• Стелька из козьей кожи с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
        '<br>• Произведено в Италии'
    ),
    ('• Empeigne en denim • Première en cuir de chevreau avec logo gaufré '
     '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
     '• Fabrication italienne'): (
        '• Верх из денима<br>• Стелька из козьей кожи с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
        '<br>• Произведено в Италии'
    ),
    ('• Empeigne en cuir de veau • Talon de 40 mm '
     '• Première en cuir de chevreau avec logo gaufré • Semelle extérieure en cuir à logo '
     '• Emballage dans le thème de l’article • Made in Italy'): (
        '• Верх из телячьей кожи<br>• Каблук высотой 40 мм'
        '<br>• Стелька из козьей кожи с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
        '<br>• Сделано в Италии'
    ),
}

# Полностью сверенные варианты из исходного ZIP; каждый ключ — всё описание,
# а не совпадение по модели/цвету/отдельной фразе.
REVIEWED_SHOE_DESCRIPTIONS = {
    **REVIEWED_MULE_DESCRIPTIONS,
    **{
        ('Derbys à lacets en cuir de veau lavé : '
         f'• {color} • Base Marsala • Semelle en cuir et TPU antidérapant '
         '• Semelle intérieure en cuir et feutre '
         '• Première intégrale en cuir griffée avec étiquette '
         '• Accessoire DG croisé • Pièces en métal galvanisé ruthénium '
         '• Article présenté dans un sachet en coton et un emballage gaufré noir '
         '• Hauteur du talon 15 mm • Made in Italy'): (
            'Дерби на шнуровке из обработанной телячьей кожи:'
            f'<br>• {color_ru}<br>• Основа Marsala'
            '<br>• Подошва из кожи и нескользящего ТПУ'
            '<br>• Внутренняя подошва из кожи и войлока'
            '<br>• Полная кожаная стелька с фирменной биркой'
            '<br>• Декоративный элемент в виде перекрещенных букв DG'
            '<br>• Металлические детали с рутениевым покрытием'
            '<br>• Хлопковый мешочек и чёрная тиснёная упаковка'
            '<br>• Высота каблука 15 мм<br>• Сделано в Италии'
        )
        for color, color_ru in (('Marron', 'Коричневый'), ('Noir', 'Чёрный'))
    },
    ('Slingbacks en cuir de veau brillant : • Noir • Base Jackie New '
     '• Semelle en cuir avec logo DG en métal '
     '• Première intégrale en cuir griffée avec étiquette '
     '• Griffe avec bouton strass • Pièces en métal galvanisé palladium '
     '• Article présenté dans un sachet en coton noir et un emballage gaufré noir '
     '• Hauteur du talon 60 mm • Made in Italy'): (
        'Туфли с открытой пяткой из глянцевой телячьей кожи:'
        '<br>• Чёрный<br>• Основа Jackie New'
        '<br>• Кожаная подошва с металлическим логотипом DG'
        '<br>• Полная кожаная стелька с фирменной биркой'
        '<br>• Фирменная отделка с пуговицей со стразами'
        '<br>• Металлические детали с палладиевым покрытием'
        '<br>• Чёрный хлопковый мешочек и чёрная тиснёная упаковка'
        '<br>• Высота каблука 60 мм<br>• Сделано в Италии'
    ),
    ('Ballerines Vally en velours à imprimé léopard et logo DG : '
     '• Imprimé léopard • Bride ajustable en gros-grain avec boucle à logo '
     '• Première en cuir d’agneau imprimé avec étiquette à logo '
     '• Semelle en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Балетки Vally из бархата с леопардовым принтом и логотипом DG:'
        '<br>• Леопардовый принт'
        '<br>• Регулируемый ремешок из репсовой ленты с пряжкой с логотипом'
        '<br>• Стелька из кожи ягнёнка с принтом и биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Sandales Keira en satin à imprimé léopard et pierres brodées : '
     '• Imprimé léopard • Talon de 105 mm • Bride ajustable avec boucle bijou '
     '• Première en satin avec étiquette à logo • Semelle en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Сандалии Keira из атласа с леопардовым принтом и вышивкой камнями:'
        '<br>• Леопардовый принт<br>• Каблук высотой 105 мм'
        '<br>• Регулируемый ремешок с декоративной пряжкой'
        '<br>• Стелька из атласа с биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    **{
        (f'Slingbacks Lollo en satin avec imprimé Léopard et strass thermocollants : '
         f'• Imprimé animalier • Talon de {heel} mm '
         '• Première en cuir de chevreau imprimé avec étiquette à logo '
         '• Semelle extérieure en cuir imprimé à logo '
         '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
            'Туфли Lollo с открытой пяткой из атласа с леопардовым принтом '
            'и термоклеевыми стразами:<br>• Анималистический принт'
            f'<br>• Каблук высотой {heel} мм'
            '<br>• Стелька из козьей кожи с принтом и биркой с логотипом'
            '<br>• Подошва из кожи с принтом и логотипом'
            '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
        )
        for heel in (60, 105)
    },
    ('Mules en cuir de veau lisse : • Violet • Base Bianca '
     '• Semelle en cuir avec logo DG marqué à chaud '
     '• Première en cuir avec griffe marquée à chaud '
     '• Article présenté dans un sachet en coton noir et un emballage gaufré noir '
     '• Made in Italy'): (
        'Мюли из гладкой телячьей кожи:<br>• Фиолетовый<br>• Основа Bianca'
        '<br>• Кожаная подошва с логотипом DG, нанесённым горячим тиснением'
        '<br>• Кожаная стелька с фирменным горячим тиснением'
        '<br>• Чёрный хлопковый мешочек и чёрная тиснёная упаковка'
        '<br>• Сделано в Италии'
    ),
    ('Mules en caoutchouc : • Bordeaux • Semelle en EVA et TPU '
     '• Empeigne en TPU • Première avec logo DG gaufré '
     '• Article présenté dans un sachet en coton noir et un emballage '
     'en fibre de cellulose issue de papier recyclé • Hauteur de la semelle 10 mm '
     '• Made in Italy'): (
        'Мюли из резины:<br>• Бордовый<br>• Подошва из ЭВА и ТПУ'
        '<br>• Верх из ТПУ<br>• Стелька с тиснёным логотипом DG'
        '<br>• Чёрный хлопковый мешочек и упаковка из целлюлозного волокна '
        'из переработанной бумаги<br>• Высота подошвы 10 мм'
        '<br>• Сделано в Италии'
    ),
    ('Slingbacks en cuir de veau miroir : • Or • Talon de 90 mm avec logo DG '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir lamé à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли с открытой пяткой из телячьей кожи с зеркальным эффектом:'
        '<br>• Золотой<br>• Каблук высотой 90 мм с логотипом DG'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Подошва из кожи с эффектом ламе и логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Slingbacks en cuir de veau effet miroir : • Argent • Talon de 60 mm avec logo DG '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir lamé à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли с открытой пяткой из телячьей кожи с зеркальным эффектом:'
        '<br>• Серебряный<br>• Каблук высотой 60 мм с логотипом DG'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Подошва из кожи с эффектом ламе и логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Ballerines en cuir nappa mordoré : • Or '
     '• Bride en cuir nappa mordoré à logo DG '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Балетки из кожи наппа с золотистым отливом:'
        '<br>• Золотой<br>• Ремешок из кожи наппа с золотистым отливом и логотипом DG'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Mules en brocart à imprimé fleurs et pierres brodées : • Multicolore '
     '• Talon de 60 mm • Première en cuir de chevreau imprimé avec étiquette à logo '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Мюли из парчи с цветочным принтом и вышивкой камнями:'
        '<br>• Разноцветные<br>• Каблук высотой 60 мм'
        '<br>• Стелька из козьей кожи с принтом и биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Mules Hawaii en velours imprimé léopard : • Imprimé léopard '
     '• Brides ajustables avec boucle griffée du logo '
     '• Première en velours imprimé avec étiquette à logo • Semelle en TPU à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Мюли Hawaii из бархата с леопардовым принтом:'
        '<br>• Леопардовый принт'
        '<br>• Регулируемые ремешки с пряжкой с логотипом'
        '<br>• Стелька из бархата с принтом и биркой с логотипом'
        '<br>• Подошва из ТПУ с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Sandales à plateforme en velours avec boucle bijou : • Noir '
     '• Talon de 135 mm • Première en cuir d’agneau avec logo gaufré '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Сандалии на платформе из бархата с декоративной пряжкой:'
        '<br>• Чёрный<br>• Каблук высотой 135 мм'
        '<br>• Стелька из кожи ягнёнка с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Slingbacks en cuir de veau effet miroir : • Or • Talon de 30 mm '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir lamé à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли с открытой пяткой из телячьей кожи с зеркальным эффектом:'
        '<br>• Золотой<br>• Каблук высотой 30 мм'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Подошва из кожи с эффектом ламе и логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Sandales Bianca en satin avec pierres brodées : • Noir '
     '• Bride ajustable avec boucle bijou '
     '• Première en satin avec étiquette à logo • Semelle en cuir griffée du logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Сандалии Bianca из атласа с вышитыми камнями:'
        '<br>• Чёрный<br>• Регулируемый ремешок с декоративной пряжкой'
        '<br>• Стелька из атласа с биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Sandales Keira en satin avec pierres brodées : • Noir • Talon de 60 mm '
     '• Bride ajustable avec boucle bijou • Première en satin avec étiquette à logo '
     '• Semelle en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Сандалии Keira из атласа с вышитыми камнями:'
        '<br>• Чёрный<br>• Каблук высотой 60 мм'
        '<br>• Регулируемый ремешок с декоративной пряжкой'
        '<br>• Стелька из атласа с биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Sandales Keira en satin avec rose brodée : • Noir • Talon de 105 mm '
     '• Bride en satin ajustable avec boucle • Première en satin avec logo gaufré '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Сандалии Keira из атласа с вышитой розой:'
        '<br>• Чёрный<br>• Каблук высотой 105 мм'
        '<br>• Регулируемый атласный ремешок с пряжкой'
        '<br>• Стелька из атласа с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Mules espadrilles en cuir nappa à logo DG : • Empeigne en cuir nappa '
     '• Première en corde avec insert en cuir de chevreau et logo gaufré '
     '• Semelle extérieure en corde tressée • Semelle en caoutchouc à logo '
     '• Made in Italy'): (
        'Мюли-эспадрильи из кожи наппа с логотипом DG:'
        '<br>• Верх из кожи наппа'
        '<br>• Стелька из верёвки со вставкой из козьей кожи и тиснёным логотипом'
        '<br>• Внешняя подошва из плетёной верёвки'
        '<br>• Резиновая подошва с логотипом<br>• Сделано в Италии'
    ),
    ('• Empeigne en crystal mesh • Talon de 85 mm avec strass thermocollants '
     '• Première en cuir de chevreau lamé et étiquette à logo '
     '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
     '• Fabrication italienne'): (
        '• Верх из сетки с кристаллами'
        '<br>• Каблук высотой 85 мм с термоклеевыми стразами'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
        '<br>• Произведено в Италии'
    ),
    ('Sandales en python lamé à logo DG : • Or • Talon de 105 mm '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir lamé à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Сандалии из кожи питона с эффектом ламе и логотипом DG:'
        '<br>• Золотой<br>• Каблук высотой 105 мм'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Подошва из кожи с эффектом ламе и логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    **{
        (f'Mules de plage en caoutchouc avec logo DG ajouré : • {color} '
         '• Première en caoutchouc avec logo gaufré '
         '• Semelle extérieure ultralégère et semelle en caoutchouc à logo '
         '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
            f'Пляжные мюли из каучука с ажурным логотипом DG:<br>• {color_ru}'
            '<br>• Стелька из каучука с тиснёным логотипом'
            '<br>• Ультралёгкая внешняя подошва и каучуковая подошва с логотипом'
            '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
        )
        for color, color_ru in (
            ('Vert', 'Зелёный'), ('Noir', 'Чёрный'),
            ('Bleu', 'Синий'), ('Blanc', 'Белый'),
        )
    },
    ('Dolce&Gabbana x Havaianas Tongs Leopard: • Multicolore '
     '• Première en caoutchouc à imprimé léopard • Semelle en caoutchouc à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Brasil'): (
        'Шлёпанцы Dolce&Gabbana x Havaianas с леопардовым принтом:'
        '<br>• Разноцветные<br>• Каучуковая стелька с леопардовым принтом'
        '<br>• Каучуковая подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Бразилии'
    ),
    ('• Talon de 105 mm • Bride de cheville ajustable '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir effet miroir avec logo '
     '• Emballage sur le thème de l’article • Made in Italy'): (
        '• Каблук высотой 105 мм<br>• Регулируемый ремешок на щиколотке'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Подошва из кожи с зеркальным эффектом и логотипом'
        '<br>• Тематическая упаковка<br>• Сделано в Италии'
    ),
    ('• Empeigne en cuir verni à broderie • Bride avec fermoir bijou '
     '• Première en cuir de chevreau imprimé avec étiquette à logo '
     '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
     '• Made in Italy'): (
        '• Верх из лакированной кожи с вышивкой'
        '<br>• Ремешок с декоративной застёжкой'
        '<br>• Стелька из козьей кожи с принтом и биркой с логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Тематическая упаковка<br>• Сделано в Италии'
    ),
    ('• Talon de 105 mm • Première en cuir de chevreau imprimé avec logo '
     '• Semelle extérieure en abs avec logo DG en métal galvanisé palladium '
     '• Semelle en caoutchouc • Emballage dans le thème de l’article '
     '• Made in Italy'): (
        '• Каблук высотой 105 мм'
        '<br>• Стелька из козьей кожи с принтом и логотипом'
        '<br>• Внешняя подошва из АБС-пластика с логотипом DG из металла'
        ' с палладиевым покрытием'
        '<br>• Резиновая подошва<br>• Тематическая упаковка'
        '<br>• Сделано в Италии'
    ),
    **{
        (f'Mules Bianca en cuir de veau à logo DG : • {color} '
         '• Première en cuir de chevreau avec logo gaufré '
         '• Semelle extérieure en cuir à logo '
         '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
            f'Мюли Bianca из телячьей кожи с логотипом DG:<br>• {color_ru}'
            '<br>• Стелька из козьей кожи с тиснёным логотипом'
            '<br>• Кожаная подошва с логотипом'
            '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
        )
        for color, color_ru in (('Rose', 'Розовый'), ('Bordeaux', 'Бордовый'))
    },
    **{
        (f'Mules en cuir de veau: • Talon de {heel} mm recouvert de cuir '
         f'• Première en cuir de veau avec étiquette {label} '
         '• Semelle en cuir lisse avec logo '
         f'• L’article est livré {packaging} • Made in Italy'): (
            'Мюли из телячьей кожи:'
            f'<br>• Каблук высотой {heel} мм, обтянутый кожей'
            f'<br>• Стелька из телячьей кожи с {label_ru}'
            '<br>• Подошва из гладкой кожи с логотипом'
            '<br>• Тематическая упаковка<br>• Сделано в Италии'
        )
        for heel, label, label_ru, packaging in (
            (5, 'logotée', 'биркой с логотипом', 'dans un packaging thématique'),
            (40, 'griffée', 'фирменной биркой', 'avec un packaging thématique'),
        )
    },
    ('Mules Keira en satin avec pierres brodées : • Rouge '
     '• Première de propreté griffée en satin '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Мюли Keira из атласа с вышитыми камнями:<br>• Красный'
        '<br>• Фирменная стелька из атласа'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Slingbacks DG en dentelle léopard : • Noir • Talon de 60 mm '
     '• Première en cuir d’agneau avec logo gaufré • Semelle en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли с открытой пяткой DG из кружева с леопардовым узором:'
        '<br>• Чёрный<br>• Каблук высотой 60 мм'
        '<br>• Стелька из кожи ягнёнка с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Slingbacks DG en dentelle léopard avec strass : • Noir • Talon de 105 mm '
     '• Première en cuir d’agneau avec logo gaufré • Semelle en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли с открытой пяткой DG из кружева с леопардовым узором и стразами:'
        '<br>• Чёрный<br>• Каблук высотой 105 мм'
        '<br>• Стелька из кожи ягнёнка с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Escarpins Lollo en cuir de veau miroir : • Or • Talon de 105 mm '
     '• Première en cuir de chevreau lamé avec étiquette • Semelle en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли Lollo из телячьей кожи с зеркальным эффектом:'
        '<br>• Золотой<br>• Каблук высотой 105 мм'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('• Hauteur du talon 145 mm • Hauteur de la plateforme 40 mm '
     '• Première en cuir de chevreau lamé avec étiquette à logo '
     '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
     '• Made in Italy'): (
        '• Каблук высотой 145 мм<br>• Платформа высотой 40 мм'
        '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
        '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
        '<br>• Сделано в Италии'
    ),
    ('Mules en cuir de veau à logo DG : • Rose • Talon de 40 mm '
     '• Première en cuir de chevreau avec logo gaufré '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Мюли из телячьей кожи с логотипом DG:'
        '<br>• Розовый<br>• Каблук высотой 40 мм'
        '<br>• Стелька из козьей кожи с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    ('Slingbacks DG en velours avec boucle bijou : • Noir • Talon de 60 mm '
     '• Première en cuir d’agneau avec logo gaufré '
     '• Semelle extérieure en cuir à logo '
     '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
        'Туфли с открытой пяткой DG из бархата с декоративной пряжкой:'
        '<br>• Чёрный<br>• Каблук высотой 60 мм'
        '<br>• Стелька из кожи ягнёнка с тиснёным логотипом'
        '<br>• Кожаная подошва с логотипом'
        '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
    ),
    **{
        (f'{kind} en cuir de chevreau : • {color} • Talon de {heel} mm '
         '• Première en cuir d’agneau avec logo gaufré • Semelle en cuir à logo '
         '• Modèle livré dans une housse de protection à logo • Made in Italy'): (
            f'{name} из козьей кожи:<br>• {color_ru}<br>• Каблук высотой {heel} мм'
            '<br>• Стелька из кожи ягнёнка с тиснёным логотипом'
            '<br>• Кожаная подошва с логотипом'
            '<br>• Защитный чехол с логотипом в комплекте<br>• Сделано в Италии'
        )
        for kind, color, heel, name, color_ru in (
            ('Escarpins', 'Rose', 75, 'Туфли', 'Розовый'),
            ('Sandales', 'Blanc', 75, 'Сандалии', 'Белый'),
            ('Slingbacks', 'Noir', 75, 'Туфли с открытой пяткой', 'Чёрный'),
            ('Slingbacks', 'Blanc', 75, 'Туфли с открытой пяткой', 'Белый'),
            ('Slingbacks', 'Rose', 105, 'Туфли с открытой пяткой', 'Розовый'),
            ('Sandales Devotion', 'Noir', 105, 'Сандалии Devotion', 'Чёрный'),
            ('Sandales Devotion', 'Rouge', 105, 'Сандалии Devotion', 'Красный'),
        )
    },
    **{
        ('• Empeigne en résille habillée de dentelle lurex '
         + (f'• Talon de {heel} mm ' if heel else '')
         + '• Première en cuir de chevreau lamé avec étiquette à logo '
         '• Semelle extérieure en cuir à logo • Emballage dans le thème de l’article '
         '• Fabrication italienne'): (
            '• Верх из сетки, покрытой кружевом с люрексом'
            + (f'<br>• Каблук высотой {heel} мм' if heel else '')
            + '<br>• Стелька из козьей кожи с эффектом ламе и биркой с логотипом'
              '<br>• Кожаная подошва с логотипом<br>• Тематическая упаковка'
              '<br>• Произведено в Италии'
        )
        for heel in (None, 60, 90)
    },
}


def _plain_source(text):
    return clean_text(html.unescape(re.sub(r'<[^>]+>', ' ', text or '')))


def _repair_leg_terms(text):
    return re.sub(
        r'(?i)\bногавк(ами|ах|ой|а|и|у|е)\b',
        lambda match: (LEG_FORMS[match.group(1).lower()].capitalize()
                       if match.group()[0].isupper() else LEG_FORMS[match.group(1).lower()]),
        text,
    )


def _repair_twill_agreement(text):
    """Согласовать только формы, оставшиеся после «сатин» → «саржа»."""
    for before, after in (
        (r'\bэластичного шерстяной саржи\b', 'эластичной шерстяной саржи'),
        (r'\bпринтованного саржи\b', 'принтованной саржи'),
        (r'\bпринтованным саржей\b', 'принтованной саржей'),
        (r'\bхлопкового саржи\b', 'хлопковой саржи'),
        (r'\bэластичного саржи\b', 'эластичной саржи'),
    ):
        text = _replace_preserving_case(before, after, text)
    return text


def _repair_title(card):
    """Исправлять только несомненные подмены типа вещи, не угадывать состав."""
    title = card['title']
    raw = clean_text(title.get('raw')).casefold()
    ru = clean_text((title.get('i18n') or {}).get('ru'))
    if raw == 'slingbacks en satin avec strass thermocollants':
        ru = re.sub(r'(?i)^кроссовки на ремешках из атласа с термоклеящимися стразами$',
                    'Туфли с открытой пяткой из атласа с термоклеевыми стразами', ru)
    if raw == 'ballerines en cuir nappa mordoré':
        ru = re.sub(r'(?i)^балерины из кожи наппа мордоре$',
                    'Балетки из кожи наппа с золотистым отливом', ru)
    if raw == 'ballerines en velours imprimé':
        ru = re.sub(r'(?i)^балерины из бархата с принтом$',
                    'Балетки из бархата с принтом', ru)
    description_source = _plain_source((card.get('description') or {}).get('raw'))
    if (raw == 'derbys à lacets en cuir de veau lavé'
            and ru.casefold() == 'кожаные туфли на шнурках из выделанной кожи телятины'
            and description_source in REVIEWED_SHOE_DESCRIPTIONS
            and description_source.startswith('Derbys à lacets en cuir de veau lavé : ')):
        ru = 'Дерби на шнуровке из обработанной телячьей кожи'
    if (raw == 'slingbacks en cuir de veau brillant'
            and ru.casefold() == 'кожаные туфли на шпильке с глянцевой кожи коровьей'
            and description_source in REVIEWED_SHOE_DESCRIPTIONS
            and description_source.startswith('Slingbacks en cuir de veau brillant : ')):
        ru = 'Туфли с открытой пяткой из глянцевой телячьей кожи'
    if (raw == 'slingbacks en cuir de veau'
            and ru.casefold() == 'кожаные туфли на шпильке'
            and description_source in REVIEWED_SHOE_DESCRIPTIONS
            and description_source.startswith((
                'Slingbacks en cuir de veau miroir : • Or • Talon de 90 mm avec logo DG ',
                'Slingbacks en cuir de veau effet miroir : • Argent • Talon de 60 mm avec logo DG ',
            ))):
        ru = 'Туфли с открытой пяткой из телячьей кожи'
    if raw == 'sandales à plateforme en velours avec boucle bijou':
        ru = re.sub(r'(?i)с украшением в виде кольца$', 'с декоративной пряжкой', ru)
    if (raw == 'slingbacks en cuir de veau'
            and _plain_source((card.get('description') or {}).get('raw')) ==
            'Slingbacks en cuir de veau effet miroir : • Or • Talon de 30 mm '
            '• Première en cuir de chevreau lamé avec étiquette à logo '
            '• Semelle extérieure en cuir lamé à logo '
            '• Modèle livré dans une housse de protection à logo • Made in Italy'):
        ru = re.sub(r'(?i)^кожаные туфли на шпильке$',
                    'Туфли с открытой пяткой из телячьей кожи', ru)
    if re.search(r'\b(?:derb(?:y|ies|ys)|derbies)\b', raw) and not re.search(r'дерби', ru, re.I):
        ru = 'Дерби из кожи' if 'cuir' in raw or 'leather' in raw else 'Дерби'
    elif 'monk strap' in raw and 'монах' in ru.casefold():
        ru = 'Туфли монки из телячьей кожи' if 'cuir de veau' in raw else 'Туфли монки'
    elif re.search(r'\b(?:derb(?:y|ies|ys)|monk strap)\b', raw) and 'каблук' in ru.casefold():
        ru = 'Дерби' if 'derb' in raw else 'Туфли монки'
    if 'suède' in raw and 'швеции' in ru.casefold():
        ru = re.sub('(?i)в швеции', 'из замши', ru)
    if re.search(r'\b(?:sweat.?shirt|sweat en)\b', raw) and re.search(r'^футболка\b', ru, re.I):
        ru = re.sub('(?i)^футболка', 'Худи' if 'capuche' in raw else 'Свитшот', ru, count=1)
    if re.search(r'\bsweatshirt\b', raw) and re.search(r'^термобелье\b', ru, re.I):
        ru = re.sub(r'(?i)^термобелье\b', 'Свитшот', ru, count=1)
    if re.search(r'\bchemise\b', raw) and not re.search(r'\bt-shirt\b', raw):
        if 'vanity' in raw:
            ru = re.sub(r'(?i)\bфутболка-ванити\b', 'Рубашка Vanity', ru)
        ru = _replace_preserving_case(r'\bфутболка\b', 'рубашка', ru)
    if raw.startswith('robe nuisette ') and 'Robes' in (card.get('category') or {}).get('raw_path', []):
        ru = re.sub(r'(?i)^ночная рубашка\b', 'Платье-комбинация', ru)
    if re.search(r'\bpantalon\b', raw) and not re.search(r'\bshort|bermuda\b', raw):
        ru = re.sub(r'(?i)^пиджак-брюки\b', 'Брюки', ru)
        if raw.startswith('pantalon de smoking'):
            ru = re.sub(r'(?i)^брюки в стиле костюма\b', 'Брюки в стиле смокинга', ru)
        ru = re.sub('(?i)^шорты(?=\b|[- ])', 'Брюки', ru)
        ru = re.sub('(?i)^юбка(?=\b|[- ])', 'Брюки', ru)
        ru = re.sub('(?i)^штаны(?=\b|[- ])', 'Брюки', ru)
        ru = re.sub('(?i)ногавками', 'штанинами', ru)
        if 'jambe' in raw:
            ru = re.sub('(?i)прямыми ногами', 'прямыми штанинами', ru)
    if re.search(r'\bmules?\b', raw):
        ru = re.sub('(?i)^мулетки|^мулы', 'Мюли', ru)
        if raw == 'mules en satin avec pierres brodées':
            ru = re.sub(r'(?i)^муле\b', 'Мюли', ru)
        ru = _replace_preserving_case(r'\bмулл(?:ы|ов)\b|\bмулет(?:ы|ов)\b', 'мюли', ru)
        if re.search(r'\bmules? plates?\b', raw):
            ru = re.sub(r'(?i)^накладки на каблуки|^каблуки|^кожаные подошвы для мюлей|^пластина для мулей|^кожаные подошвы для мулов', 'Мюли на плоской подошве', ru)
        if ru.casefold().startswith('кожаные сандалии для женщин'):
            ru = re.sub('(?i)^кожаные сандалии для женщин', 'Кожаные мюли', ru)
        if re.search(r'^кожаные туфли с кружевом|^розовые туфли на каблуке', ru, re.I):
            ru = re.sub('(?i)^кожаные туфли с кружевом', 'Мюли с кружевом', ru)
            ru = re.sub('(?i)^розовые туфли на каблуке', 'Мюли', ru)
        if 'caïman' in raw:
            ru = re.sub('(?i)^мюли и кайманы', 'Мюли из кожи каймана', ru)
    if re.match(r'\s*(?:mini\s+)?sac\b', raw):
        ru = re.sub(r'(?i)^сак\b', 'Сумка', ru)
        ru = re.sub(r'(?i)^большой сак\b', 'Большая сумка', ru)
        if 'à main' in raw:
            ru = re.sub('(?i)^клатч\b', 'Сумка', ru)
    if 'soutien-gorge de bain' in raw:
        ru = re.sub('(?i)^купальник-трикотаж', 'Лиф купальника', ru)
    if re.search(r'\bpull\b', raw):
        ru = _replace_preserving_case(r'\bшарф на шею\b', 'джемпер с круглым вырезом', ru)
        ru = _replace_preserving_case(r'\bшарф\b', 'джемпер', ru)
        if 'ras de cou' in raw:
            ru = re.sub(r'(?i)\bджемпер из (.*?) для шеи\b',
                        lambda match: 'Джемпер с круглым вырезом из ' + match.group(1), ru)
            ru = _replace_preserving_case(r'\bджемпер-(?:столетник|столбик)\b',
                                          'джемпер с круглым вырезом', ru)
        if re.search(r'^футболка\b', ru, re.I):
            ru = re.sub(r'(?i)^футболка\b', 'Джемпер', ru)
    if re.search(r'\bblouson\b', raw):
        ru = _replace_preserving_case(r'\bблуз(?:а|он)\b', 'куртка', ru)
    if re.search(r'\bveste\b', raw) and not re.search(r'\b(?:gilet|sans manches)\b', raw):
        ru = _replace_preserving_case(r'\bжилетка\b|\bжилет\b', 'жакет', ru)
        ru = re.sub(r'(?i)^оверсайз-жакет\b', 'Жакет оверсайз', ru)
    if re.search(r'\bslingbacks?\b', raw) and not re.search(r'\bsneakers?\b', raw):
        ru = _replace_preserving_case(r'\bкроссовки\b', 'туфли', ru)
        if raw == 'slingbacks en dentelle léopard avec strass thermocollants':
            ru = re.sub(r'(?i)^туфли на тонкой кожаной подошве с узором в стиле леопарда, '
                        r'украшенные термоклеящимися стразами$',
                        'Туфли с открытой пяткой из кружева с леопардовым узором '
                        'и термоклеевыми стразами', ru)
    if 'robe de chambre' in raw and 'пижам' in ru.casefold():
        ru = _replace_preserving_case(r'\bпижама\b', 'халат', ru)
        ru = _replace_preserving_case(r'\bдлинная\b', 'длинный', ru)
        ru = _replace_preserving_case(r'\bшелковая\b', 'шелковый', ru)
    if re.search(r'\bdaim\b', raw) and 'дамской кожи' in ru.casefold():
        ru = re.sub('(?i)дамской кожи', 'замши', ru)
    if re.search(r'\bnappa\b', raw) and 'nubuck' not in raw:
        ru = re.sub('(?i)нубуковой кожи', 'кожи наппа', ru)
        if raw == 'slingback lollo en cuir nappa de soie':
            ru = 'Туфли с открытой пяткой Lollo из кожи наппа'
        if 'calfskin' in raw or 'veau' in raw:
            ru = re.sub(r'(?i)из нубука (?:телячьей кожи|красного быка)|из нубука и кожи теленка',
                        'из телячьей кожи наппа', ru)
        ru = re.sub(r'(?i)\bнубука\b', 'кожи наппа', ru)
        if raw == 'sandales en tissu et cuir de veau nappa':
            ru = re.sub(r'(?i)из ткани и кожи кожи наппа',
                        'из ткани и телячьей кожи наппа', ru)
        if 'nappa plongé' in raw:
            ru = re.sub(r'(?i)\s+темного цвета\b', '', ru)
    if 'toile rustique' in raw:
        ru = re.sub('(?i)из кожзаменителя в рустикальном стиле', 'из фактурной ткани', ru)
    if 'sac de voyage' in raw:
        ru = _replace_preserving_case(r'\bчемодан\b', 'дорожная сумка', ru)
    if re.search(r'\bbottes\b', raw):
        ru = _replace_preserving_case(r'\bботинки\b', 'сапоги', ru)
    if 'sergé' in raw and 'satin' not in raw:
        ru = _replace_preserving_case(r'\bсатина\b', 'саржи', ru)
        ru = _replace_preserving_case(r'\bсатин\b', 'саржа', ru)
        ru = re.sub(r'(?i)\bшелкового саржи\b', 'шёлковой саржи', ru)
        ru = re.sub(r'(?i)\bшерстяного саржи\b', 'шерстяной саржи', ru)
        ru = _repair_twill_agreement(ru)
    # Применять после общих правил (в частности «жилетка» → «жакет»).
    reviewed_titles = {
        'dolcegabbana:F9R82TFU272N0000': (
            'veste oversize courte en gabardine de laine',
            'короткая жакет оверсайз из шерстяной габардины',
            'Укороченный жакет оверсайз из шерстяной габардины',
        ),
        'dolcegabbana:F6TTYTFURADF0658': (
            'robe fourreau mi-longue en satin',
            'полудлинное платье-сукня из атласа',
            'Платье-футляр миди из атласа',
        ),
        'dolcegabbana:BP3467A085980995': (
            'nécessaire essenza en raphia',
            'необходимый эссенциал в форме рафии',
            'Косметичка Essenza из рафии',
        ),
    }
    reviewed = reviewed_titles.get(card.get('id'))
    if reviewed and raw == reviewed[0] and ru.casefold() == reviewed[1]:
        ru = reviewed[2]
    title.setdefault('i18n', {})['ru'] = ru


def _repair_description(card):
    description = card.get('description') or {}
    raw = clean_text(description.get('raw')).casefold()
    ru = (description.get('i18n') or {}).get('ru') or ''
    if (card.get('id') == 'dolcegabbana:F9R82TFU272N0000'
            and raw.startswith('veste croisée oversize et courte en gabardine de laine :')
            and 'grands revers crantés' in raw):
        ru = ru.replace(
            'Оверсайз-жилет с крестообразным кроем, короткий, сделанный из шерстяной ткани габардин',
            'Укороченный двубортный жакет оверсайз из шерстяной габардины',
        ).replace('Большие ребристые отверстия', 'Широкие лацканы с вырезом')
    if (card.get('id') == 'dolcegabbana:BP3467A085980995'
            and raw.startswith('nécessaire essenza en raphia et cuir lisse:')
            and 'personnalisation extérieure avec étiquette à logo' in raw):
        ru = ru.replace('Необходимый аксессуар Essenza из рафии и гладкой кожи',
                        'Косметичка Essenza из рафии и гладкой кожи')
        ru = ru.replace('Завершение палладием', 'Фурнитура с палладиевым покрытием')
        ru = ru.replace('Внешняя настройка с использованием этикетки с логотипом',
                        'Снаружи — бирка с логотипом')
        ru = ru.replace('Статья поставляется в футляре с логотипом и в уже собранной коробке',
                        'Изделие поставляется с фирменным мешочком и предварительно собранной коробкой')
    # Только полностью сверенные исходные тексты; не заменять уже переведённую версию.
    if not re.search(r'[А-Яа-яЁё]', ru):
        reviewed = REVIEWED_SHOE_DESCRIPTIONS.get(_plain_source(description.get('raw')))
        if reviewed:
            description.setdefault('i18n', {})['ru'] = reviewed
            return
    # Применять только когда конкретная фраза присутствует и в оригинале.
    if 'lacets cirés' in raw:
        ru = ru.replace('Ремешки из вощеной кожи', 'Вощёные шнурки')
    if 'empeigne' in raw:
        ru = ru.replace('Часть обуви из кожи теленка с брошеной текстурой',
                        'Верх из полированной телячьей кожи')
    if 'première en cuir de veau' in raw:
        ru = ru.replace('Внутренняя часть обуви из кожи теленка',
                        'Стелька из телячьей кожи')
    if 'semelle extérieure en cuir' in raw:
        ru = ru.replace('Внешняя подошва из кожи', 'Кожаная подошва')
    if 'semelle' in raw:
        ru = re.sub('(?i)внешняя подошва', 'Подошва', ru)
        ru = re.sub('(?i)внутренняя подошва', 'Стелька', ru)
    if 'empeigne' in raw:
        ru = re.sub('(?i)корпус обуви', 'Верх обуви', ru)
    if 'peau lainée' in raw:
        ru = re.sub(r'(?i)шерстяной кожи', 'овчины', ru)
    if raw.startswith('• empeigne en cuir de veau avec talonnette contrastante'):
        ru = re.sub(r'(?i)^каблук из кожи теленка', 'Верх из телячьей кожи', ru)
    if 'lunettes' in clean_text(card.get('title', {}).get('raw')).casefold():
        if 'branches' in raw:
            ru = re.sub(r'(?i)ветви\b', 'Дужки', ru)
        if 'verres' in raw:
            ru = re.sub('(?i)стаканы', 'линзы', ru)
            ru = re.sub('(?i)(?<=• )черные очки', 'Чёрные линзы', ru)
            if 'verres canon de fusil miroir' in raw:
                ru = re.sub(r'(?i)оптические элементы винтовки-зеркала',
                            'Зеркальные линзы цвета оружейной стали', ru)
    if 'passants sur la taille' in raw:
        ru = re.sub('(?i)прохожие на талии', 'Шлёвки на поясе', ru)
    if 'piping' in raw:
        ru = re.sub('(?i)трубопроводы', 'Канты', ru)
        ru = re.sub('(?i)трубопровод', 'кант', ru)
    if re.search(r'\bderb(?:y|ies|ys)\b', clean_text(card.get('title', {}).get('raw')).casefold()) and not re.search(r'\b(?:sneakers?|slingbacks?)\b', raw):
        ru = re.sub('(?i)кроссовки', 'Дерби', ru)
    if re.search(r'\b(?:sweat.?shirt|sweat en)\b', clean_text(card.get('title', {}).get('raw')).casefold()) and not re.search(r'\bt-shirt\b', raw):
        ru = re.sub(r'(?i)\bфутболка\b', 'Худи' if 'capuche' in raw else 'Свитшот', ru)
    if re.search(r'\bchemise\b', raw) and not re.search(r'\bt-shirt\b', raw):
        if 'vanity' in raw:
            ru = re.sub(r'(?i)\bфутболка-ванити\b', 'Рубашка Vanity', ru)
        ru = _replace_preserving_case(r'\bфутболка\b', 'рубашка', ru)
    if raw.startswith('robe nuisette ') and 'Robes' in (card.get('category') or {}).get('raw_path', []):
        ru = re.sub(r'(?i)^ночная рубашка\b', 'Платье-комбинация', ru)
    if raw.startswith('robe ') and 'Robes' in (card.get('category') or {}).get('raw_path', []):
        ru = re.sub(r'(?i)^ночная рубашка\b', 'Платье', ru)
    if re.search(r'\b(?:mules?|mulettes?)\b', raw):
        ru = _replace_preserving_case(r'\bмулл(?:ы|ов)\b|\bмулет(?:ы|ов)\b', 'мюли', ru)
    if 'le mannequin' in raw and re.search(r'le mannequin[^•]{0,110}porte une taille', raw):
        ru = re.sub(r'(?i)((?:рост|параметры) модел[ьяи][^.!•]{0,100}?)размер талии\b',
                    r'\1размер одежды', ru)
        ru = re.sub(r'(?i)(\bна ней )размер талии(?=\s*[—–-]\s*\d+\s*IT\b)',
                    r'\1размер одежды', ru)
    if re.search(r'the model is \d+(?:[.,]\d+)?\s*cm tall and wears (?:a )?size\s*\d+', raw):
        ru = re.sub(r'(?i)((?:рост|параметры) модел[ьяи][^.!•]{0,100}?)размер обуви\b',
                    r'\1размер одежды', ru)
    if re.search(r'\b(?:jambe|pantalon|jean|trousers?|pants?|bermuda|shorts?|leg opening)\b', raw):
        ru = _repair_leg_terms(ru)
    if raw.startswith('scuba pants'):
        ru = re.sub(r'(?i)^шорты для дайвинга', 'Брюки Scuba', ru)
    if re.search(r'\bnappa\b', raw) and 'nubuck' not in raw:
        ru = re.sub(r'(?i)\bнубука\b', 'кожи наппа', ru)
        if 'empeigne en cuir de veau nappa' in raw:
            ru = re.sub(r'(?i)кожаная внутренняя часть из кожи наппа',
                        'Верх из телячьей кожи наппа', ru)
    if 'sergé' in raw and 'satin' not in raw:
        ru = re.sub(r'(?i)\bсатина\b', 'саржи', ru)
        ru = re.sub(r'(?i)\bсатином\b', 'саржей', ru)
        ru = re.sub(r'(?i)\bсатине\b', 'сарже', ru)
        ru = re.sub(r'(?i)\bсатин\b', 'саржа', ru)
        ru = re.sub(r'(?i)\bшелкового саржи\b', 'шёлковой саржи', ru)
        ru = re.sub(r'(?i)\bшерстяного саржи\b', 'шерстяной саржи', ru)
        ru = _repair_twill_agreement(ru)
    if re.search(r'\blongueur[^•]{0,100}\b(?:sur|pour) (?:une |la )?taille\s+\d+\s*it\b', raw):
        ru = re.sub(r'(?i)(\bдлина[^.!•]{0,125}?)размер талии(?=\s*[—–-]\s*\d+\s*IT\b)',
                    r'\1размер одежды', ru)
    if 'emballage dans le thème de l’article' in raw:
        ru = re.sub(r'(?i)упаковка в стиле статьи', 'Упаковка в стиле изделия', ru)
    if 'fermeture scratch' in raw:
        ru = re.sub(r'(?i)замок scratch', 'Застёжка-липучка', ru)
    if 'calotte et visière' in raw:
        ru = re.sub(r'(?i)\bкрышка\b', 'тулья', ru)
    if re.search(r'\bsweatshirt\b', raw):
        ru = _replace_preserving_case(r'\bтермобелье\b', 'свитшот', ru)
    if re.search(r'\bpantalon\b', raw) and not re.search(r'\bshort\b', raw):
        ru = re.sub(r'(?i)\bштаны\b', 'Брюки', ru)
    if re.search(r'\bpull\b', raw):
        ru = _replace_preserving_case(r'\bшарф на шею\b', 'джемпер с круглым вырезом', ru)
        ru = _replace_preserving_case(r'\bшарф\b', 'джемпер', ru)
    if re.search(r'\bblouson\b', raw):
        ru = _replace_preserving_case(r'\bблуз(?:а|он)\b', 'куртка', ru)
    if re.search(r'\bslingbacks?\b', raw) and not re.search(r'\bsneakers?\b', raw):
        ru = _replace_preserving_case(r'\bкроссовки\b', 'туфли', ru)
    if 'robe de chambre' in raw and 'пижам' in ru.casefold():
        ru = _replace_preserving_case(r'\bпижама\b', 'халат', ru)
        ru = _replace_preserving_case(r'\bдлинная\b', 'длинный', ru)
        ru = _replace_preserving_case(r'\bшелковая\b', 'шелковый', ru)
    if re.search(r'\bdaim\b', raw):
        ru = re.sub('(?i)дамской кожи', 'замши', ru)
    if 'taille basse' in raw:
        ru = _replace_preserving_case(r'\bнизкий рост\b', 'низкая посадка', ru)
    if re.search(r'\b(?:pièce doublée|doublure|doublé)\b', raw):
        ru = _replace_preserving_case(r'\bдвойная стенка\b', 'на подкладке', ru)
    if 'trois boutons-pression' in raw:
        ru = re.sub(r'(?i)\bтр[её]х молни[йия]\b', 'трёх кнопок', ru)
    if re.search(r'\bnappa\b', raw) and 'nubuck' not in raw:
        ru = re.sub('(?i)нубуковой кожи', 'кожи наппа', ru)
    ru = re.sub(r'(?i)\bс съемн', 'со съёмн', ru)
    ru = re.sub(r'(?i)\bс сьемн', 'со съёмн', ru)
    description.setdefault('i18n', {})['ru'] = ru


def normalize_card(card, available_images):
    if not isinstance(card, dict) or not str(card.get('id') or '').startswith('dolcegabbana:'):
        raise ValueError('Посторонняя карточка в каталоге Dolce & Gabbana')
    card = deepcopy(card)
    taxonomy = _taxonomy(card)
    if not taxonomy:
        raise ValueError('Нет однозначной категории FINDER')
    parent, parent_ru, child, child_ru = taxonomy
    department = card['gender']
    card['brand'] = 'Dolce & Gabbana'
    card['category']['canonical_path'] = {
        'department': department,
        'department_ru': 'Женщины' if department == 'women' else 'Мужчины',
        'categories': [
            _canonical_category('DOLCE_GABBANA_' + parent.upper(), parent, parent_ru, 2),
            _canonical_category('DOLCE_GABBANA_' + child.upper(), child, child_ru, 3),
        ],
    }
    card['category'].setdefault('i18n', {})['ru'] = [
        card['category']['canonical_path']['department_ru'], parent_ru,
        child_ru, clean_text((card['title'].get('i18n') or {}).get('ru')),
    ]
    _repair_title(card)
    card['category']['i18n']['ru'][-1] = card['title']['i18n']['ru']
    _repair_description(card)
    for variant in (card.get('variants') or {}).get('colors') or []:
        label = variant.get('label') or {}
        translated = COLOR_FIXES.get(label.get('raw'))
        if translated:
            label.setdefault('i18n', {})['ru'] = translated
    images = (card.get('media') or {}).get('images') or []
    card['media']['images'] = [
        image for image in images if image.get('file') in available_images
    ]
    if not card['media']['images']:
        raise ValueError('Нет локального фото')
    if not card.get('variants', {}).get('colors'):
        raise ValueError('Нет данных о цвете')
    if not all(clean_text((v.get('label') or {}).get('i18n', {}).get('ru')
                          or (v.get('label') or {}).get('raw'))
               for v in card['variants']['colors']):
        raise ValueError('Нет названия цвета')
    if not (card.get('price') or {}).get('amount'):
        raise ValueError('Нет цены')
    if not clean_text((card.get('title') or {}).get('i18n', {}).get('ru')):
        raise ValueError('Нет названия на русском')
    if not clean_text((card.get('description') or {}).get('i18n', {}).get('ru')):
        raise ValueError('Нет описания на русском')
    if not any(variant.get('current') for variant in card['variants']['colors']):
        raise ValueError('Нет подтверждённого текущего цвета')
    if card['availability']['status'] == 'in_stock' and not card['variants'].get('sizes') and parent in ('shoes', 'clothing'):
        card['availability']['status'] = 'unknown'
        for variant in card['variants']['colors']:
            if variant.get('current'):
                variant['availability'] = {'status': 'unknown', 'raw': None}
    return card


def translation_audit(raw_cards, accepted_ids):
    """Проверяет тексты ВСЕХ SKU, включая исключённые из импорта.

    Правки сравниваются с оригиналом по отдельности от проверки таксономии/фото.
    Этот отчёт не доказывает литературное качество перевода: непроверенные
    описания перечислены для ручной редакторской работы.
    """
    counts = Counter()
    review = []
    corrections = []
    for original in raw_cards:
        if not isinstance(original, dict):
            continue
        card = deepcopy(original)
        sku = str(card.get('id') or '')
        title = (card.get('title') or {}).get('i18n') or {}
        description = (card.get('description') or {}).get('i18n') or {}
        before_title = title.get('ru') or ''
        before_description = description.get('ru') or ''
        if isinstance(card.get('title'), dict):
            _repair_title(card)
        if isinstance(card.get('description'), dict):
            _repair_description(card)
        after_title = (card.get('title') or {}).get('i18n', {}).get('ru') or ''
        after_description = (card.get('description') or {}).get('i18n', {}).get('ru') or ''
        if after_title != before_title:
            counts['titles_corrected'] += 1
            corrections.append({'id': sku, 'field': 'title',
                                'raw': (card.get('title') or {}).get('raw'),
                                'before': before_title, 'after': after_title})
        if after_description != before_description:
            counts['descriptions_corrected'] += 1
            corrections.append({'id': sku, 'field': 'description',
                                'raw': (card.get('description') or {}).get('raw'),
                                'before': before_description, 'after': after_description})
        for variant in (card.get('variants') or {}).get('colors') or []:
            label = variant.get('label') or {}
            if COLOR_FIXES.get(label.get('raw')) and COLOR_FIXES[label['raw']] != (label.get('i18n') or {}).get('ru'):
                counts['colors_corrected'] += 1
        reasons = []
        if not re.search(r'[А-Яа-яЁё]', after_title):
            reasons.append('title_not_russian')
        if not re.search(r'[А-Яа-яЁё]', after_description):
            reasons.append('description_not_russian')
        if LITERAL_TRANSLATION_RE.search(after_description):
            reasons.append('suspicious_literal_translation')
        raw_title = clean_text((card.get('title') or {}).get('raw')).casefold()
        if re.search(r'\bpantalon\b', raw_title) and re.search(r'(?i)^\s*(?:шорты|юбка)\b', after_title):
            reasons.append('title_type_mismatch')
        if re.search(r'\b(?:derb(?:y|ies|ys))\b', raw_title) and re.search(r'(?i)^\s*(?:кроссовки|сапоги|сумки)\b', after_title):
            reasons.append('title_type_mismatch')
        if reasons:
            for reason in reasons:
                counts[reason] += 1
            review.append({
                'id': sku, 'accepted': sku in accepted_ids, 'reasons': reasons,
                'raw_title': (card.get('title') or {}).get('raw'),
                'ru_title': after_title,
                'raw_description': str((card.get('description') or {}).get('raw') or '')[:500],
                'ru_description': after_description[:500],
            })
    return {'cards_checked': len(raw_cards), 'counts': dict(sorted(counts.items())),
            'manual_review': review, 'corrections': corrections}


def prepare_archive(archive):
    entries = _validate_zip_entries(archive, max_files=MAX_FILES, max_uncompressed_size=MAX_BYTES)
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        raise ValueError('Повторяющиеся имена файлов в ZIP')
    json_files = [entry for name, entry in entries if name == 'catalog.json']
    if len(json_files) != 1 or any(name.endswith('.json') and name != 'catalog.json' for name, _ in entries):
        raise ValueError('Ожидается единственный catalog.json')
    paths = {name for name, _ in entries if PurePosixPath(name).suffix.lower() == '.jpg'}
    raw = parse_json_bytes(archive.read(json_files[0]), 'catalog.json')
    cards, rejected, seen = [], [], set()
    source_audit = {'missing_image_references': [], 'in_stock_without_sizes': []}
    for original in raw:
        sku = str(original.get('id') or '') if isinstance(original, dict) else ''
        if not sku.startswith('dolcegabbana:'):
            raise ValueError(f'Архив содержит карточку другого бренда: {sku}')
        if sku in seen:
            raise ValueError(f'Повторяющийся SKU: {sku}')
        seen.add(sku)
        for image in ((original.get('media') or {}).get('images') or []):
            image_path = image.get('file') if isinstance(image, dict) else None
            if image_path not in paths:
                source_audit['missing_image_references'].append({
                    'id': sku, 'file': image_path,
                })
        if (original.get('availability') or {}).get('status') == 'in_stock' and not (
            (original.get('variants') or {}).get('sizes')
        ):
            source_audit['in_stock_without_sizes'].append(sku)
        try:
            cards.append(normalize_card(original, paths))
        except (ValueError, AttributeError, TypeError) as exc:
            rejected.append({'id': sku, 'reason': str(exc)})
    # The exported ru field can contain French text. Do not publish it as a
    # "translation" until a human has corrected and reviewed the copy.
    quality = catalog_quality_report(cards, paths)
    blocking = {
        'untranslated_title', 'untranslated_description',
        'partially_untranslated_description', 'suspicious_translation',
        'untranslated_color', 'partially_untranslated_color',
    }
    by_id = defaultdict(set)
    for issue in quality['issues']:
        if issue['type'] in blocking:
            by_id[issue['product_id']].add(issue['type'])
    for card in cards:
        if LITERAL_TRANSLATION_RE.search((card.get('description') or {}).get('i18n', {}).get('ru') or ''):
            by_id[card['id']].add('suspicious_literal_translation')
    rejected.extend({
        'id': sku, 'reason': 'Проверка перевода: ' + ', '.join(sorted(reasons))
    } for sku, reasons in by_id.items())
    cards = [card for card in cards if card['id'] not in by_id]
    source_audit['translation'] = translation_audit(raw, {card['id'] for card in cards})
    return cards, rejected, paths, source_audit