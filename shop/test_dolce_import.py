import io
import json
import tempfile
import zipfile
from copy import deepcopy
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from .dolce_import import normalize_card, prepare_archive, translation_audit
from .models import CatalogBrand, Product


class DolceImportTests(SimpleTestCase):
    def setUp(self):
        self.image = 'images/dolcegabbana_TEST/front.jpg'
        self.card = {
            'id': 'dolcegabbana:TEST', 'brand': 'Dolce&Gabbana', 'gender': 'men',
            'title': {'raw': 'Derbies en cuir de veau brossé', 'i18n': {
                'ru': 'Кожаные туфли на каблуке из натуральной кожи теленка'}},
            'description': {'raw': 'Lacets cirés', 'i18n': {
                'ru': 'Ремешки из вощеной кожи'}},
            'category': {'raw_path': ['Mode', 'Homme', 'Chaussures', 'Derbies et Richelieux']},
            'availability': {'status': 'in_stock'},
            'price': {'amount': 695, 'currency': 'EUR'},
            'media': {'images': [{'file': self.image}]},
            'variants': {'colors': [{
                'sku': 'TEST', 'current': True, 'availability': {'status': 'in_stock'},
                'label': {'raw': 'Noir', 'i18n': {'ru': 'Чёрный'}},
            }], 'sizes': [{'value': '42', 'availability': {'status': 'in_stock'}}]},
        }

    def test_preserves_source_and_fixes_clear_mistranslations(self):
        original = deepcopy(self.card)
        product = normalize_card(self.card, {self.image})
        self.assertEqual(self.card, original)
        self.assertEqual(product['brand'], 'Dolce & Gabbana')
        self.assertEqual(product['title']['i18n']['ru'], 'Дерби из кожи')
        self.assertEqual(product['description']['i18n']['ru'], 'Вощёные шнурки')
        self.assertEqual(product['category']['canonical_path']['department'], 'men')
        self.assertEqual(product['category']['canonical_path']['categories'][0]['slug'], 'shoes')
        self.assertEqual(product['category']['canonical_path']['categories'][-1]['slug'], 'loafers_mules')

    def test_reviewed_dolce_copy_changes_only_matching_sku_and_source(self):
        samples = (
            ('F9R82TFU272N0000', 'Veste oversize courte en gabardine de laine',
             'Короткая жилетка оверсайз из шерстяной габардины',
             'Укороченный жакет оверсайз из шерстяной габардины',
             'Veste croisée oversize et courte en gabardine de laine : '
             '• Grands revers crantés • Manches longues',
             'Оверсайз-жилет с крестообразным кроем, короткий, сделанный из шерстяной ткани габардин: '
             '• Большие ребристые отверстия • Длинные рукава',
             'Укороченный двубортный жакет оверсайз из шерстяной габардины',
             'Широкие лацканы с вырезом'),
            ('F6TTYTFURADF0658', 'Robe fourreau mi-longue en satin',
             'Полудлинное платье-сукня из атласа', 'Платье-футляр миди из атласа',
             'Robe fourreau mi-longue en satin stretch',
             'Полудлинное платье-футляр из эластичного атласа', '', ''),
            ('BP3467A085980995', 'Nécessaire essenza en raphia',
             'Необходимый эссенциал в форме рафии', 'Косметичка Essenza из рафии',
             'Nécessaire Essenza en raphia et cuir lisse: '
             '• Personnalisation extérieure avec étiquette à logo',
             'Необходимый аксессуар Essenza из рафии и гладкой кожи: '
             '• Завершение палладием • Внешняя настройка с использованием этикетки с логотипом',
             'Косметичка Essenza из рафии и гладкой кожи', 'Фурнитура с палладиевым покрытием'),
        )
        for sku, raw_title, ru_title, expected_title, raw_desc, ru_desc, desc_piece, detail in samples:
            with self.subTest(sku=sku):
                card = deepcopy(self.card)
                card['id'] = f'dolcegabbana:{sku}'
                card['title'] = {'raw': raw_title, 'i18n': {'ru': ru_title}}
                card['description'] = {'raw': raw_desc, 'i18n': {'ru': ru_desc}}
                normalized = normalize_card(card, {self.image})
                self.assertEqual(normalized['title']['i18n']['ru'], expected_title)
                self.assertEqual(normalized['title']['raw'], raw_title)
                self.assertEqual(normalized['description']['raw'], raw_desc)
                if desc_piece:
                    self.assertIn(desc_piece, normalized['description']['i18n']['ru'])
                    self.assertIn(detail, normalized['description']['i18n']['ru'])
                card['id'] = 'dolcegabbana:DIFFERENT'
                other = normalize_card(card, {self.image})
                self.assertNotEqual(other['title']['i18n']['ru'], expected_title)
                if desc_piece:
                    self.assertNotIn(desc_piece, other['description']['i18n']['ru'])

    def test_unsized_shoes_cannot_be_offered_for_sale(self):
        self.card['variants']['sizes'] = []
        product = normalize_card(self.card, {self.image})
        self.assertEqual(product['availability']['status'], 'unknown')
        self.assertEqual(product['variants']['colors'][0]['availability']['status'], 'unknown')

    def test_corrects_source_verified_color_without_changing_raw(self):
        self.card['variants']['colors'][0]['label'] = {
            'raw': 'Doré', 'i18n': {'ru': 'Доре'},
        }
        product = normalize_card(self.card, {self.image})
        self.assertEqual(product['variants']['colors'][0]['label']['i18n']['ru'],
                         'Золотистый')
        self.assertEqual(product['variants']['colors'][0]['label']['raw'], 'Doré')
        self.card['variants']['colors'][0]['label'] = {
            'raw': 'Imprimé Carretto', 'i18n': {'ru': 'Печать: Carretto'},
        }
        product = normalize_card(self.card, {self.image})
        self.assertEqual(product['variants']['colors'][0]['label']['i18n']['ru'],
                         'Принт Carretto')

    def test_editor_review_repairs_mistranslated_product_types_and_materials(self):
        examples = [
            ('Pull ras de cou en soie jacquard', 'Шарф на шею из шелка',
             'Джемпер с круглым вырезом из шелка'),
            ('Blouson matelassé', 'Блуза со стёганой отделкой',
             'Куртка со стёганой отделкой'),
            ('Cotton terry sweatshirt', 'Термобелье из хлопка',
             'Свитшот из хлопка'),
            ('Chemise en denim', 'Футболка из денима', 'Рубашка из денима'),
            ('Chemise Vanity en soie', 'Футболка-ванити из шелка',
             'Рубашка Vanity из шелка'),
            ('Veste croisée en laine', 'Жилетка из шерсти', 'Жакет из шерсти'),
            ('Sac de voyage en cuir', 'Чемодан из кожи', 'Дорожная сумка из кожи'),
            ('Mules plates en cuir de veau', 'Накладки на каблуки из кожи',
             'Мюли на плоской подошве из кожи'),
            ('Mules en cuir de veau à logo DG',
             'Кожаные сандалии для женщин с логотипом DG',
             'Кожаные мюли с логотипом DG'),
            ('Mules en daim', 'Муллы из замши', 'Мюли из замши'),
            ('Slingbacks en patchwork de denim', 'Кроссовки на ремешках из денима',
             'Туфли на ремешках из денима'),
            ('Slingbacks en dentelle léopard avec strass thermocollants',
             'Кроссовки на тонкой кожаной подошве с узором в стиле леопарда, украшенные термоклеящимися стразами',
             'Туфли с открытой пяткой из кружева с леопардовым узором и термоклеевыми стразами'),
            ('Mules en satin avec pierres brodées', 'Муле из атласа с вышитыми камнями',
             'Мюли из атласа с вышитыми камнями'),
            ('Sandales en tissu et cuir de veau nappa',
             'Сандалии из ткани и кожи нубука',
             'Сандалии из ткани и телячьей кожи наппа'),
            ('Robe de chambre longue en soie', 'Длинная шелковая пижама',
             'Длинный шелковый халат'),
            ('Mocassins en daim', 'Мокасины из дамской кожи', 'Мокасины из замши'),
            ('Sneakers en cuir de veau nappa', 'Кроссовки из нубуковой кожи',
             'Кроссовки из кожи наппа'),
            ('Veste en toile rustique', 'Жакет из кожзаменителя в рустикальном стиле',
             'Жакет из фактурной ткани'),
            ('Bottes en cuir', 'Кожаные ботинки', 'Кожаные сапоги'),
            ('Jupe en sergé', 'Юбка из сатина', 'Юбка из саржи'),
            ('Pantalon de smoking style tailleur en gabardine de laine',
             'Пиджак-брюки в стиле костюма из шерстяной габардины',
             'Брюки в стиле смокинга из шерстяной габардины'),
            ('Sac My Sicily', 'Сак Май Сицилия', 'Сумка Май Сицилия'),
            ('Petit sac Sicily en veau nappa plongé',
             'Маленькая сумочка Sicily из нубука темного цвета',
             'Маленькая сумочка Sicily из кожи наппа'),
            ('Slingback Lollo en cuir nappa de soie',
             'Туфли Lollo с каблуком-ремешком, изготовленные из гладкой мягкой кожи ягненка (это не нубук).',
             'Туфли с открытой пяткой Lollo из кожи наппа'),
        ]
        for raw, before, after in examples:
            with self.subTest(raw=raw):
                card = deepcopy(self.card)
                card['title'] = {'raw': raw, 'i18n': {'ru': before}}
                product = normalize_card(card, {self.image})
                self.assertEqual(product['title']['i18n']['ru'], after)
                self.assertEqual(product['title']['raw'], raw)

    def test_editor_review_repairs_description_only_with_matching_source(self):
        examples = [
            ('Pull ras de cou en soie: • Manches courtes',
             'Шарф на шею из шелка: • Короткие рукава',
             'джемпер с круглым вырезом'),
            ('Blouson en denim: • Poches', 'Блуза из денима: • Карманы', 'куртка'),
            ('Cotton terry sweatshirt: • Black',
             'Термобелье из хлопка: • Чёрный', 'свитшот'),
            ('Slingbacks en denim: • Talon', 'Кроссовки из денима: • Каблук', 'туфли'),
            ('Chemise en denim: • Noir', 'Футболка из денима: • Чёрный', 'рубашка'),
            ('Le mannequin mesure 1,75 m et porte une taille 40 IT',
             'Рост модели – 1,75 м; размер талии – 40 IT', 'размер одежды – 40 it'),
            ('Emballage dans le thème de l’article', 'Упаковка в стиле статьи',
             'упаковка в стиле изделия'),
            ('• Empeigne en cuir de veau avec talonnette contrastante',
             'Каблук из кожи теленка; контрастный каблук', 'верх из телячьей кожи'),
            ('• Calotte et visière noires', 'Черная крышка и козырёк', 'тулья'),
            ('Pantalon de jogging jambe droite', 'Брюки с прямыми ногавками',
             'прямыми штанинами'),
            ('Wool trousers: • Leg opening', 'Брюки: • Ширина ногавки', 'ширина штанины'),
            ('Scuba pants: • Blue', 'Шорты для дайвинга: • Синий', 'брюки scuba'),
            ('Sac en cuir et peau lainée', 'Сумка из шерстяной кожи', 'овчины'),
            ('Chemise en sergé de soie', 'Рубашка из шелкового сатина', 'шёлковой саржи'),
            ('Pantalon en sergé de laine stretch',
             'Брюки из эластичного шерстяного сатина', 'эластичной шерстяной саржи'),
            ('Casquette en sergé de coton',
             'Кепка из хлопкового сатина', 'хлопковой саржи'),
            ('Sac avec poignées en sergé imprimé',
             'Сумка с ручками, покрытыми принтованным сатином',
             'принтованной саржей'),
            ('Empeigne en cuir de veau nappa', 'Кожаная внутренняя часть из нубука',
             'верх из телячьей кожи наппа'),
            ('The model is 175 cm tall and wears a size 40 IT',
             'Рост модели — 175 см, размер обуви — 40 IT', 'размер одежды — 40 it'),
            ('Ceinture nœud en satin de soie : • Fermeture scratch',
             'Пояс с узлом из шёлкового атласа: • Замок Scratch', 'застёжка-липучка'),
            ('Mocassins en daim: • Noir', 'Мокасины из дамской кожи: • Черный', 'замши'),
            ('Jupe: • Taille basse', 'Юбка: • Низкий рост', 'низкая посадка'),
            ('Gilet: • Pièce doublée', 'Жилет: • Двойная стенка', 'на подкладке'),
            ('Jupe: • Ceinture fermée par trois boutons-pression',
             'Юбка: • Пояс закрывается с помощью трех молний', 'трёх кнопок'),
        ]
        for raw, before, expected in examples:
            with self.subTest(raw=raw):
                card = deepcopy(self.card)
                card['description'] = {'raw': raw, 'i18n': {'ru': before}}
                product = normalize_card(card, {self.image})
                self.assertIn(expected, product['description']['i18n']['ru'].casefold())
                self.assertEqual(product['description']['raw'], raw)
        card = deepcopy(self.card)
        card['description'] = {'raw': 'Leather jacket', 'i18n': {'ru': 'Блуза на подкладке'}}
        self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                         'Блуза на подкладке')
        card['description'] = {'raw': 'Le mannequin est photographié en atelier',
                               'i18n': {'ru': 'Рост модели – 1,75 м; размер талии – 40 IT'}}
        self.assertIn('размер талии', normalize_card(card, {self.image})['description']['i18n']['ru'])

    def test_repairs_sweatshirt_and_glasses_only_when_confirmed_by_source(self):
        card = deepcopy(self.card)
        card['title'] = {'raw': 'Sweat-shirt à capuche en coton',
                         'i18n': {'ru': 'Футболка с капюшоном из хлопка'}}
        card['description'] = {'raw': 'Sweat-shirt à capuche en coton',
                               'i18n': {'ru': 'Футболка с капюшоном из хлопка'}}
        product = normalize_card(card, {self.image})
        self.assertEqual(product['title']['i18n']['ru'], 'Худи с капюшоном из хлопка')
        self.assertEqual(product['description']['i18n']['ru'], 'Худи с капюшоном из хлопка')
        card['title'] = {'raw': 'Lunettes de soleil', 'i18n': {'ru': 'Солнцезащитные очки'}}
        card['description'] = {'raw': 'Branches en métal. Verres roses',
                               'i18n': {'ru': 'Ветви из металла. Розовые стаканы'}}
        product = normalize_card(card, {self.image})
        self.assertEqual(product['description']['i18n']['ru'], 'Дужки из металла. Розовые линзы')
        card['description']['raw'] = 'Unrelated source'
        product = normalize_card(card, {self.image})
        self.assertEqual(product['description']['i18n']['ru'], 'Ветви из металла. Розовые стаканы')
        card['description'] = {'raw': 'Verres canon de fusil miroir',
                               'i18n': {'ru': 'Оптические элементы винтовки-зеркала'}}
        product = normalize_card(card, {self.image})
        self.assertEqual(product['description']['i18n']['ru'],
                         'Зеркальные линзы цвета оружейной стали')

    def test_model_size_fix_does_not_change_unrelated_waist_or_tshirt(self):
        card = deepcopy(self.card)
        card['description'] = {
            'raw': ('Tour de taille 70 cm. Longueur sur la taille 40 IT. '
                    'Le mannequin mesure 175 cm et porte une taille 40 IT'),
            'i18n': {'ru': ('Обхват талии 70 см. Длина изделия 60 см; размер талии – 40 IT. '
                            'Рост модели – 175 см; размер талии – 40 IT')},
        }
        ru = normalize_card(card, {self.image})['description']['i18n']['ru']
        self.assertIn('Обхват талии 70 см', ru)
        self.assertEqual(ru.count('размер одежды – 40 IT'), 2)
        card['description'] = {'raw': 'T-shirt à manches courtes. Col chemise',
                               'i18n': {'ru': 'Футболка с короткими рукавами. Воротник рубашки'}}
        self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                         'Футболка с короткими рукавами. Воротник рубашки')
        card['description'] = {'raw': 'Satin de soie et sergé',
                               'i18n': {'ru': 'Шёлковый сатин и саржа'}}
        self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                         'Шёлковый сатин и саржа')
        card['description'] = {'raw': 'Nappa et nubuck',
                               'i18n': {'ru': 'Кожа наппа и нубук'}}
        self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                         'Кожа наппа и нубук')
        card['description'] = {'raw': 'Shoes size 40 IT',
                               'i18n': {'ru': 'Размер обуви — 40 IT'}}
        self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                         'Размер обуви — 40 IT')
        card['description'] = {'raw': 'Fermeture zippée',
                               'i18n': {'ru': 'Замок Scratch'}}
        self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                         'Замок Scratch')

    def test_robe_nuisette_is_a_dress_only_in_dress_category(self):
        card = deepcopy(self.card)
        card['category']['raw_path'] = ['Mode', 'Femme', 'Vêtements', 'Robes']
        card['title'] = {'raw': 'Robe nuisette en satin de soie',
                         'i18n': {'ru': 'Ночная рубашка из шёлкового атласа'}}
        card['description'] = {'raw': 'Robe nuisette en satin de soie: • Coupe fluide',
                               'i18n': {'ru': 'Ночная рубашка из шёлкового атласа: • Свободный крой'}}
        product = normalize_card(card, {self.image})
        self.assertEqual(product['title']['i18n']['ru'], 'Платье-комбинация из шёлкового атласа')
        self.assertTrue(product['description']['i18n']['ru'].startswith('Платье-комбинация'))
        self.assertEqual(product['title']['raw'], card['title']['raw'])
        card['category']['raw_path'] = ['Mode', 'Femme', 'Vêtements', 'Lingerie']
        self.assertEqual(normalize_card(card, {self.image})['title']['i18n']['ru'],
                         'Ночная рубашка из шёлкового атласа')

    def test_reviewed_mule_descriptions_require_exact_source_and_missing_translation(self):
        from shop.dolce_import import REVIEWED_MULE_DESCRIPTIONS

        for source, expected in REVIEWED_MULE_DESCRIPTIONS.items():
            with self.subTest(source=source):
                card = deepcopy(self.card)
                card['description'] = {
                    'raw': source.replace(' • ', '<br>• '),
                    'i18n': {'ru': source.replace(' • ', '<br/>• ')},
                }
                product = normalize_card(card, {self.image})
                self.assertEqual(product['description']['i18n']['ru'], expected)
                self.assertEqual(product['description']['raw'], card['description']['raw'])
                card['description']['raw'] += '<br>• Modèle spécial'
                self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                                 card['description']['i18n']['ru'])
                card['description']['raw'] = source
                card['description']['i18n']['ru'] = 'Проверенный редактором перевод'
                self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                                 'Проверенный редактором перевод')

    def test_reviewed_shoe_variants_preserve_material_color_and_heel(self):
        from shop.dolce_import import REVIEWED_SHOE_DESCRIPTIONS

        cases = (
            ('Escarpins en cuir de chevreau : • Rose • Talon de 75 mm',
             ('Туфли из козьей кожи', 'Розовый', '75 мм', 'кожи ягнёнка')),
            ('Slingbacks en cuir de chevreau : • Blanc • Talon de 75 mm',
             ('Туфли с открытой пяткой', 'Белый', '75 мм', 'кожи ягнёнка')),
            ('Slingbacks en cuir de chevreau : • Rose • Talon de 105 mm',
             ('Туфли с открытой пяткой', 'Розовый', '105 мм', 'кожи ягнёнка')),
            ('• Empeigne en résille habillée de dentelle lurex • Talon de 90 mm',
             ('Верх из сетки', '90 мм', 'козьей кожи', 'Тематическая упаковка')),
            ('Mules Bianca en cuir de veau à logo DG : • Bordeaux',
             ('Мюли Bianca', 'Бордовый', 'козьей кожи', 'Защитный чехол')),
            ('Mules en cuir de veau: • Talon de 5 mm recouvert de cuir',
             ('Мюли из телячьей кожи', '5 мм', 'биркой с логотипом', 'Тематическая упаковка')),
            ('Mules en cuir de veau: • Talon de 40 mm recouvert de cuir',
             ('Мюли из телячьей кожи', '40 мм', 'фирменной биркой', 'Тематическая упаковка')),
            ('Mules Keira en satin avec pierres brodées : • Rouge',
             ('Мюли Keira', 'Красный', 'Фирменная стелька из атласа', 'Защитный чехол')),
            ('Slingbacks DG en dentelle léopard : • Noir • Talon de 60 mm',
             ('Туфли с открытой пяткой DG', '60 мм', 'кожи ягнёнка')),
            ('Slingbacks DG en dentelle léopard avec strass : • Noir • Talon de 105 mm',
             ('Туфли с открытой пяткой DG', '105 мм', 'стразами')),
            ('Escarpins Lollo en cuir de veau miroir : • Or • Talon de 105 mm',
             ('Туфли Lollo', 'Золотой', '105 мм', 'эффектом ламе')),
            ('• Hauteur du talon 145 mm • Hauteur de la plateforme 40 mm',
             ('145 мм', '40 мм', 'козьей кожи', 'Тематическая упаковка')),
            ('Mules en cuir de veau à logo DG : • Rose • Talon de 40 mm',
             ('Мюли из телячьей кожи', 'Розовый', '40 мм', 'козьей кожи')),
            ('Slingbacks DG en velours avec boucle bijou : • Noir • Talon de 60 mm',
             ('Туфли с открытой пяткой', 'бархата', '60 мм', 'кожи ягнёнка')),
            ('Mules de plage en caoutchouc avec logo DG ajouré : • Vert',
             ('Пляжные мюли', 'Зелёный', 'каучука', 'Сделано в Италии')),
            ('Mules de plage en caoutchouc avec logo DG ajouré : • Bleu',
             ('Пляжные мюли', 'Синий', 'каучука', 'Сделано в Италии')),
            ('Dolce&Gabbana x Havaianas Tongs Leopard: • Multicolore',
             ('Шлёпанцы', 'Разноцветные', 'леопардовым принтом', 'Сделано в Бразилии')),
            ('• Talon de 105 mm • Bride de cheville ajustable',
             ('105 мм', 'Регулируемый ремешок', 'зеркальным эффектом')),
            ('• Empeigne en cuir verni à broderie • Bride avec fermoir bijou',
             ('лакированной кожи', 'декоративной застёжкой', 'козьей кожи')),
            ('• Talon de 105 mm • Première en cuir de chevreau imprimé avec logo',
             ('105 мм', 'АБС-пластика', 'палладиевым покрытием', 'Резиновая подошва')),
            ('Mules espadrilles en cuir nappa à logo DG : • Empeigne en cuir nappa',
             ('Мюли-эспадрильи', 'кожи наппа', 'плетёной верёвки', 'Резиновая подошва')),
            ('• Empeigne en crystal mesh • Talon de 85 mm',
             ('с кристаллами', '85 мм', 'термоклеевыми стразами', 'козьей кожи')),
            ('Sandales en python lamé à logo DG : • Or • Talon de 105 mm',
             ('кожи питона', 'Золотой', '105 мм', 'эффектом ламе')),
            ('Sandales à plateforme en velours avec boucle bijou : • Noir',
             ('бархата', 'декоративной пряжкой', '135 мм', 'кожи ягнёнка')),
            ('Slingbacks en cuir de veau effet miroir : • Or • Talon de 30 mm',
             ('открытой пяткой', 'Золотой', '30 мм', 'эффектом ламе')),
            ('Sandales Bianca en satin avec pierres brodées : • Noir',
             ('Сандалии Bianca', 'декоративной пряжкой', 'Стелька из атласа')),
            ('Sandales Keira en satin avec pierres brodées : • Noir',
             ('Сандалии Keira', '60 мм', 'декоративной пряжкой')),
            ('Sandales Keira en satin avec rose brodée : • Noir',
             ('Сандалии Keira', '105 мм', 'атласный ремешок')),
            ('Slingbacks en cuir de veau miroir : • Or • Talon de 90 mm',
             ('открытой пяткой', 'Золотой', '90 мм', 'логотипом DG')),
            ('Slingbacks en cuir de veau effet miroir : • Argent • Talon de 60 mm',
             ('открытой пяткой', 'Серебряный', '60 мм', 'логотипом DG')),
            ('Ballerines en cuir nappa mordoré : • Or',
             ('Балетки', 'золотистым отливом', 'козьей кожи')),
            ('Mules en brocart à imprimé fleurs et pierres brodées : • Multicolore',
             ('Мюли из парчи', 'Разноцветные', '60 мм')),
            ('Mules Hawaii en velours imprimé léopard : • Imprimé léopard',
             ('Мюли Hawaii', 'Леопардовый принт', 'ТПУ')),
            ('Slingbacks Lollo en satin avec imprimé Léopard et strass thermocollants : • Imprimé animalier • Talon de 60 mm',
             ('Туфли Lollo', 'леопардовым принтом', '60 мм', 'козьей кожи')),
            ('Slingbacks Lollo en satin avec imprimé Léopard et strass thermocollants : • Imprimé animalier • Talon de 105 mm',
             ('Туфли Lollo', 'леопардовым принтом', '105 мм', 'козьей кожи')),
            ('Mules en cuir de veau lisse : • Violet • Base Bianca',
             ('Мюли из гладкой телячьей кожи', 'Фиолетовый', 'Основа Bianca', 'хлопковый мешочек')),
            ('Mules en caoutchouc : • Bordeaux • Semelle en EVA et TPU',
             ('Мюли из резины', 'Бордовый', 'ЭВА и ТПУ', '10 мм')),
            ('Ballerines Vally en velours à imprimé léopard et logo DG :',
             ('Балетки Vally', 'Леопардовый принт', 'репсовой ленты', 'кожи ягнёнка')),
            ('Sandales Keira en satin à imprimé léopard et pierres brodées :',
             ('Сандалии Keira', 'Леопардовый принт', '105 мм', 'Стелька из атласа')),
        )
        for prefix, fragments in cases:
            with self.subTest(prefix=prefix):
                source = next(text for text in REVIEWED_SHOE_DESCRIPTIONS if text.startswith(prefix))
                card = deepcopy(self.card)
                card['description'] = {'raw': source.replace(' • ', '<br>• '),
                                       'i18n': {'ru': source.replace(' • ', '<br/>• ')}}
                product = normalize_card(card, {self.image})
                self.assertEqual(product['description']['i18n']['ru'],
                                 REVIEWED_SHOE_DESCRIPTIONS[source])
                self.assertEqual(product['description']['raw'], card['description']['raw'])
                for fragment in fragments:
                    self.assertIn(fragment, product['description']['i18n']['ru'])
                card['description']['raw'] += '<br>• Détail supplémentaire'
                self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                                 card['description']['i18n']['ru'])

    def test_every_reviewed_shoe_description_requires_full_source_and_missing_ru(self):
        from shop.dolce_import import REVIEWED_SHOE_DESCRIPTIONS

        for source, expected in REVIEWED_SHOE_DESCRIPTIONS.items():
            with self.subTest(source=source):
                card = deepcopy(self.card)
                card['description'] = {'raw': source.replace(' • ', '<br>• '),
                                       'i18n': {'ru': source.replace(' • ', '<br/>• ')}}
                product = normalize_card(card, {self.image})
                self.assertEqual(product['description']['i18n']['ru'], expected)
                self.assertEqual(product['description']['raw'], card['description']['raw'])

                card['description']['raw'] += '<br>• Autre détail'
                self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                                 card['description']['i18n']['ru'])

                card['description']['raw'] = source
                card['description']['i18n']['ru'] = 'Уже вычитанный перевод'
                self.assertEqual(normalize_card(card, {self.image})['description']['i18n']['ru'],
                                 'Уже вычитанный перевод')

    def test_reviewed_derbies_and_slingbacks_correct_titles_only_with_full_source(self):
        from shop.dolce_import import REVIEWED_SHOE_DESCRIPTIONS

        cases = (
            ('Derbys à lacets en cuir de veau lavé',
             'Кожаные туфли на шнурках из выделанной кожи телятины',
             'Дерби на шнуровке из обработанной телячьей кожи',
             'Derbys à lacets en cuir de veau lavé : • Marron'),
            ('Slingbacks en cuir de veau brillant',
             'Кожаные туфли на шпильке с глянцевой кожи коровьей',
             'Туфли с открытой пяткой из глянцевой телячьей кожи',
             'Slingbacks en cuir de veau brillant : • Noir'),
        )
        for raw_title, old_title, new_title, prefix in cases:
            source = next(text for text in REVIEWED_SHOE_DESCRIPTIONS
                          if text.startswith(prefix))
            with self.subTest(title=raw_title):
                card = deepcopy(self.card)
                card['title'] = {'raw': raw_title, 'i18n': {'ru': old_title}}
                card['description'] = {'raw': source.replace(' • ', '<br>• '),
                                       'i18n': {'ru': source}}
                self.assertEqual(normalize_card(card, {self.image})['title']['i18n']['ru'],
                                 new_title)
                card['description']['raw'] += '<br>• Autre détail'
                self.assertNotEqual(normalize_card(card, {self.image})['title']['i18n']['ru'],
                                    new_title)

    def test_editorial_title_corrections_require_matching_original(self):
        from shop.dolce_import import REVIEWED_SHOE_DESCRIPTIONS

        cases = (
            ('Slingbacks en satin avec strass thermocollants',
             'Кроссовки на ремешках из атласа с термоклеящимися стразами',
             'Туфли с открытой пяткой из атласа с термоклеевыми стразами', None),
            ('Sandales à plateforme en velours avec boucle bijou',
             'Сандалии на платформе из бархата с украшением в виде кольца',
             'Сандалии на платформе из бархата с декоративной пряжкой', None),
            ('Slingbacks en cuir de veau', 'Кожаные туфли на шпильке',
             'Туфли с открытой пяткой из телячьей кожи',
             next(source for source in REVIEWED_SHOE_DESCRIPTIONS
                  if source.startswith('Slingbacks en cuir de veau effet miroir : • Or • Talon de 30 mm'))),
            ('Slingbacks en cuir de veau', 'Кожаные туфли на шпильке',
             'Туфли с открытой пяткой из телячьей кожи',
             next(source for source in REVIEWED_SHOE_DESCRIPTIONS
                  if source.startswith('Slingbacks en cuir de veau miroir : • Or • Talon de 90 mm'))),
            ('Slingbacks en cuir de veau', 'Кожаные туфли на шпильке',
             'Туфли с открытой пяткой из телячьей кожи',
             next(source for source in REVIEWED_SHOE_DESCRIPTIONS
                  if source.startswith('Slingbacks en cuir de veau effet miroir : • Argent • Talon de 60 mm'))),
            ('Ballerines en cuir nappa mordoré', 'Балерины из кожи наппа мордоре',
             'Балетки из кожи наппа с золотистым отливом', None),
            ('Ballerines en velours imprimé', 'Балерины из бархата с принтом',
             'Балетки из бархата с принтом', None),
        )
        for raw, previous, expected, description in cases:
            with self.subTest(raw=raw):
                card = deepcopy(self.card)
                card['title'] = {'raw': raw, 'i18n': {'ru': previous}}
                if description:
                    card['description'] = {'raw': description, 'i18n': {'ru': description}}
                self.assertEqual(normalize_card(card, {self.image})['title']['i18n']['ru'], expected)
                self.assertEqual(card['title']['i18n']['ru'], previous)
                card['title']['raw'] += ' autre'
                # Общее старое правило «slingbacks → туфли» по-прежнему работает;
                # новый полный вариант перевода для изменённого названия недопустим.
                fallback = ('Туфли на ремешках из атласа с термоклеящимися стразами'
                            if raw == 'Slingbacks en satin avec strass thermocollants'
                            else previous)
                self.assertEqual(normalize_card(card, {self.image})['title']['i18n']['ru'], fallback)
                if description:
                    card['title']['raw'] = raw
                    card['description']['raw'] += ' autre'
                    self.assertEqual(normalize_card(card, {self.image})['title']['i18n']['ru'], previous)

    def test_repairs_pants_type_and_belt_loops(self):
        card = deepcopy(self.card)
        card['title'] = {'raw': 'Pantalon en dentelle', 'i18n': {'ru': 'Шорты из кружева'}}
        card['description'] = {'raw': 'Pantalon en dentelle. Passants sur la taille',
                               'i18n': {'ru': 'Штаны из кружева. Прохожие на талии'}}
        product = normalize_card(card, {self.image})
        self.assertEqual(product['title']['i18n']['ru'], 'Брюки из кружева')
        self.assertEqual(product['description']['i18n']['ru'], 'Брюки из кружева. Шлёвки на поясе')

    def test_translation_audit_covers_rejected_cards_without_publishing_french(self):
        card = deepcopy(self.card)
        card['description']['i18n']['ru'] = 'Lacets cirés'
        audit = translation_audit([card], set())
        self.assertEqual(audit['cards_checked'], 1)
        self.assertEqual(audit['counts']['description_not_russian'], 1)
        self.assertFalse(audit['manual_review'][0]['accepted'])
        self.assertEqual(audit['manual_review'][0]['raw_description'], 'Lacets cirés')
        self.assertTrue(any(item['field'] == 'title' for item in audit['corrections']))

    def test_preflight_blocks_literal_translation_in_otherwise_valid_card(self):
        card = deepcopy(self.card)
        card['description']['raw'] = 'Bas de jambe stretch'
        card['description']['i18n']['ru'] = 'Растягивающие штаны для ног'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('catalog.json', json.dumps([card]))
            archive.writestr(self.image, b'placeholder')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            accepted, rejected, _, source_audit = prepare_archive(archive)
        self.assertFalse(accepted)
        self.assertIn('suspicious_literal_translation', rejected[0]['reason'])
        self.assertFalse(source_audit['translation']['manual_review'][0]['accepted'])

    def test_rejects_missing_taxonomy_and_missing_images(self):
        self.card['category']['raw_path'] = []
        with self.assertRaisesRegex(ValueError, 'категории'):
            normalize_card(self.card, {self.image})
        self.card['category']['raw_path'] = ['Mode', 'Homme', 'Chaussures', 'Derbies et Richelieux']
        with self.assertRaisesRegex(ValueError, 'фото'):
            normalize_card(self.card, set())

    def test_preflight_rejects_duplicates(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('catalog.json', json.dumps([self.card, self.card]))
            archive.writestr(self.image, b'not-a-real-jpeg')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            with self.assertRaisesRegex(ValueError, 'Повторяющийся SKU'):
                prepare_archive(archive)

    def test_preflight_excludes_french_text_in_russian_description(self):
        card = deepcopy(self.card)
        card['description']['i18n']['ru'] = (
            'Derbies en cuir de veau brossé avec lacets cirés en cuir'
        )
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('catalog.json', json.dumps([card]))
            archive.writestr(self.image, b'placeholder')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            accepted, rejected, _, _ = prepare_archive(archive)
        self.assertFalse(accepted)
        self.assertIn('untranslated_description', rejected[0]['reason'])

    def test_preflight_rejects_duplicate_zip_member_names(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('catalog.json', json.dumps([self.card]))
            archive.writestr(self.image, b'one')
            archive.writestr(self.image, b'two')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            with self.assertRaisesRegex(ValueError, 'Повторяющиеся имена'):
                prepare_archive(archive)

    def test_command_preflight_checks_actual_jpeg_and_reports_rejections(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = f'{directory}/catalog.zip'
            report_path = f'{directory}/report.json'
            image_bytes = io.BytesIO()
            Image.new('RGB', (400, 500), 'white').save(image_bytes, 'JPEG')
            rejected = deepcopy(self.card)
            rejected['id'] = 'dolcegabbana:UNKNOWN'
            rejected['category']['raw_path'] = []
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('catalog.json', json.dumps([self.card, rejected]))
                archive.writestr(self.image, image_bytes.getvalue())
            output = io.StringIO()
            call_command('replace_dolce_catalog', archive_path, dry_run=True,
                         report=report_path, stdout=output)
            with open(report_path, encoding='utf-8') as saved_report:
                report = json.load(saved_report)
            self.assertEqual(report['accepted'], 1)
            self.assertEqual(report['rejected'], 1)
            self.assertEqual(report['images_expected'], 1)
            self.assertFalse(report['invalid_images'])
            self.assertIn('Preflight: 1', output.getvalue())


class DolceImportIntegrationTests(TestCase):
    @staticmethod
    def _owner_mode_fixture(directory):
        path = f'{directory}/catalog.zip'
        image_path = 'images/dolcegabbana_TEST/front.jpg'
        picture = io.BytesIO()
        Image.new('RGB', (400, 500), 'white').save(picture, 'JPEG')
        card = {
            'id': 'dolcegabbana:TEST', 'brand': 'Dolce & Gabbana', 'gender': 'men',
            'title': {'raw': 'Derbies en cuir de veau', 'i18n': {'ru': 'Дерби из кожи'}},
            'description': {'raw': 'Leather shoes', 'i18n': {'ru': 'Кожаные туфли.'}},
            'category': {'raw_path': ['Mode', 'Homme', 'Chaussures', 'Derbies et Richelieux']},
            'availability': {'status': 'in_stock'},
            'price': {'amount': 695, 'currency': 'EUR'},
            'media': {'images': [{'file': image_path}]},
            'variants': {'colors': [{'sku': 'TEST', 'current': True,
                                     'availability': {'status': 'in_stock'},
                                     'label': {'raw': 'Noir', 'i18n': {'ru': 'Чёрный'}}}],
                         'sizes': [{'value': '42', 'sku': 'SIZE-42',
                                    'availability': {'status': 'in_stock'}}]},
        }
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('catalog.json', json.dumps([card]))
            archive.writestr(image_path, picture.getvalue())
        return path

    def test_import_requires_editorial_review_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            path = f'{directory}/catalog.zip'
            image_path = 'images/dolcegabbana_TEST/front.jpg'
            picture = io.BytesIO()
            Image.new('RGB', (400, 500), 'white').save(picture, 'JPEG')
            card = {
                'id': 'dolcegabbana:TEST', 'brand': 'Dolce & Gabbana', 'gender': 'men',
                'title': {'raw': 'Derbies en cuir de veau', 'i18n': {'ru': 'Дерби из кожи'}},
                'description': {'raw': 'Leather shoes', 'i18n': {'ru': 'Кожаные туфли.'}},
                'category': {'raw_path': ['Mode', 'Homme', 'Chaussures', 'Derbies et Richelieux']},
                'availability': {'status': 'in_stock'},
                'price': {'amount': 695, 'currency': 'EUR'},
                'media': {'images': [{'file': image_path}]},
                'variants': {'colors': [{
                    'sku': 'TEST', 'current': True, 'availability': {'status': 'in_stock'},
                    'label': {'raw': 'Noir', 'i18n': {'ru': 'Чёрный'}},
                }], 'sizes': [{'value': '42', 'sku': 'SIZE-42',
                              'availability': {'status': 'in_stock'}}]},
            }
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('catalog.json', json.dumps([card]))
                archive.writestr(image_path, picture.getvalue())
            with self.assertRaisesRegex(CommandError, 'вычитки переводов'):
                call_command('replace_dolce_catalog', path, backup_confirmed=True,
                             stdout=io.StringIO())
            self.assertFalse(Product.objects.filter(sku=card['id']).exists())
            with override_settings(DEBUG=False):
                with self.assertRaisesRegex(CommandError, 'только на локальной SQLite'):
                    call_command('replace_dolce_catalog', path, backup_confirmed=True,
                                 local_owner_approved_publication=True, stdout=io.StringIO())
            self.assertFalse(Product.objects.filter(sku=card['id']).exists())
            output = io.StringIO()
            with override_settings(DEBUG=True):
                call_command('replace_dolce_catalog', path, backup_confirmed=True,
                             local_owner_approved_publication=True, stdout=output)
            self.assertTrue(Product.objects.filter(sku=card['id']).exists())
            self.assertIn('полная редакторская вычитка НЕ подтверждена', output.getvalue())

    def test_production_owner_mode_rejects_local_database(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory, DEBUG=False):
            path = self._owner_mode_fixture(directory)
            with patch.dict(settings.DATABASES['default'], ENGINE='django.db.backends.sqlite3'):
                with self.assertRaisesRegex(CommandError, 'только в production PostgreSQL'):
                    call_command('replace_dolce_catalog', path, backup_confirmed=True,
                                 production_owner_approved_publication=True,
                                 stdout=io.StringIO())

    def test_production_owner_mode_rejects_unreviewed_archive(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory, DEBUG=False):
            path = self._owner_mode_fixture(directory)
            with patch.dict(settings.DATABASES['default'], ENGINE='django.db.backends.postgresql'):
                with self.assertRaisesRegex(CommandError, 'отличается от проверенного набора'):
                    call_command('replace_dolce_catalog', path, backup_confirmed=True,
                                 production_owner_approved_publication=True,
                                 stdout=io.StringIO())

    def test_imports_product_size_color_and_local_image(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            path = f'{directory}/catalog.zip'
            image_path = 'images/dolcegabbana_TEST/front.jpg'
            picture = io.BytesIO()
            Image.new('RGB', (400, 500), 'white').save(picture, 'JPEG')
            card = {
                'id': 'dolcegabbana:TEST', 'brand': 'Dolce & Gabbana', 'gender': 'men',
                'title': {'raw': 'Derbies en cuir de veau', 'i18n': {'ru': 'Дерби из кожи'}},
                'description': {'raw': 'Leather shoes', 'i18n': {'ru': 'Кожаные туфли.'}},
                'category': {'raw_path': ['Mode', 'Homme', 'Chaussures', 'Derbies et Richelieux']},
                'availability': {'status': 'in_stock'},
                'price': {'amount': 695, 'currency': 'EUR'},
                'media': {'images': [{'file': image_path}]},
                'variants': {'colors': [{
                    'sku': 'TEST', 'current': True, 'availability': {'status': 'in_stock'},
                    'label': {'raw': 'Noir', 'i18n': {'ru': 'Чёрный'}},
                }], 'sizes': [{'value': '42', 'sku': 'SIZE-42',
                             'availability': {'status': 'in_stock'}}]},
            }
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('catalog.json', json.dumps([card]))
                archive.writestr(image_path, picture.getvalue())
            call_command('replace_dolce_catalog', path, backup_confirmed=True,
                         editorial_review_confirmed=True,
                         stdout=io.StringIO())
            product = Product.objects.get(sku='dolcegabbana:TEST')
            self.assertEqual(product.brand, 'dolce-gabbana')
            self.assertEqual(product.current_color, 'Чёрный')
            self.assertEqual(product.category_path_key, 'men/shoes/loafers_mules')
            self.assertTrue(product.images.first().local_image.storage.exists(
                product.images.first().local_image.name
            ))
            self.assertTrue(product.images.first().local_image.name.endswith('.webp'))
            self.assertFalse(product.images.first().preview_image)
            self.assertFalse(product.images.first().gallery_image)
            self.assertEqual(product.images.first().card_url,
                             product.images.first().local_image.url)
            self.assertEqual(product.size_variants.get().size, '42')
            self.assertTrue(product.size_variants.get().in_stock_ecom)

    def test_local_publication_skips_rejected_sku(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory, DEBUG=True):
            path = f'{directory}/catalog.zip'
            picture = io.BytesIO()
            Image.new('RGB', (400, 500), 'white').save(picture, 'JPEG')
            valid = {
                'id': 'dolcegabbana:VALID', 'brand': 'Dolce & Gabbana', 'gender': 'men',
                'title': {'raw': 'Derbies en cuir', 'i18n': {'ru': 'Дерби из кожи'}},
                'description': {'raw': 'Leather shoes', 'i18n': {'ru': 'Кожаные туфли'}},
                'category': {'raw_path': ['Mode', 'Homme', 'Chaussures', 'Derbies et Richelieux']},
                'availability': {'status': 'in_stock'},
                'price': {'amount': 695, 'currency': 'EUR'},
                'media': {'images': [{'file': 'images/dolcegabbana_VALID/front.jpg'}]},
                'variants': {'colors': [{'sku': 'VALID', 'current': True,
                                         'availability': {'status': 'in_stock'},
                                         'label': {'raw': 'Noir', 'i18n': {'ru': 'Чёрный'}}}],
                             'sizes': [{'value': '42', 'sku': 'VALID-42',
                                        'availability': {'status': 'in_stock'}}]},
            }
            rejected = deepcopy(valid)
            rejected['id'] = 'dolcegabbana:REJECTED'
            rejected['category']['raw_path'] = []
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('catalog.json', json.dumps([valid, rejected]))
                archive.writestr('images/dolcegabbana_VALID/front.jpg', picture.getvalue())
            output = io.StringIO()
            call_command('replace_dolce_catalog', path, backup_confirmed=True,
                         local_owner_approved_publication=True, local_batch_size=1,
                         stdout=output)
            self.assertTrue(Product.objects.filter(sku=valid['id']).exists())
            self.assertFalse(Product.objects.filter(sku=rejected['id']).exists())
            self.assertTrue(CatalogBrand.objects.get(slug='dolce-gabbana').is_active)
            self.assertIn('отклонено 1', output.getvalue())