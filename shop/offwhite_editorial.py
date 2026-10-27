"""Проверенные редакторские исправления перевода Off-White без изменения оригинала."""

import re


def _garment_replacement(match, forms):
    word = forms[match.group(1)]
    return word.capitalize() if match.group(0)[0].isupper() else word


def improve_offwhite_copy(card):
    """Исправляет только ошибки, подтверждённые английским текстом этой карточки.

    Функция идемпотентна; цвет, размер, наличие и остальные поля не затрагиваются.
    """
    title_data = card.get('title') or {}
    description_data = card.get('description') or {}
    if not isinstance(title_data, dict) or not isinstance(description_data, dict):
        return card
    title_i18n = title_data.get('i18n') or {}
    description_i18n = description_data.get('i18n') or {}
    title = title_i18n.get('ru') or ''
    text = description_i18n.get('ru') or ''
    raw_title = str(title_data.get('raw') or '')
    raw_description = str(description_data.get('raw') or '')

    # This exact English description names a sweatshirt and a ribbed crew neckline.
    # A general noun swap alone produces the ungrammatical «спортивной свитшотом».
    if (raw_title == 'Sporty Over Crew Logo Sweatshirt'
            and raw_description == (
                '<p>Elevate your urban wardrobe with the Sporty Over Crew Logo Sweatshirt '
                'showcasing a refined blend of casual elegance and distinctive branding.</p>'
                '<ul type="circle"><li>Ribbed crew neckline with contrasting trim</li>'
                '<li>Long sleeves with ribbed cuffs</li><li>Signature Off-White logo at front</li>'
                '<li>Embroidered crest detail</li><li>Ribbed hemline with striped detailing</li>'
                '<li>Relaxed silhouette for contemporary fit</li></ul>'
            )):
        text = text.replace(
            'Улучшите свой городской гардероб спортивной футболкой Sporty Over Crew Logo, '
            'сочетающей в себе элегантность повседневной одежды и узнаваемый брендовый дизайн.',
            'Свитшот Sporty Over Crew Logo сочетает повседневный стиль с фирменными деталями.',
        ).replace(
            'Улучшите свой городской гардероб спортивной свитшотом Sporty Over Crew Logo, '
            'сочетающей в себе элегантность повседневной одежды и узнаваемый брендовый дизайн.',
            'Свитшот Sporty Over Crew Logo сочетает повседневный стиль с фирменными деталями.',
        ).replace(
            'Воротник-стойка с контрастной отделкой',
            'Круглый вырез с отделкой в рубчик и контрастным кантом',
        )

    if raw_title.upper() == 'OW BE RIGHT BACK' and title == 'Кроссовки OW':
        title = 'Кроссовки OW Be Right Back'
    if raw_title.upper() == 'OFF RIB BRAZILIAN PANTIES' and (
        title.startswith('Носки ') or title.startswith('Трусы ')
    ):
        title = 'Трусы OFF RIB Brazilian'

    if re.search(r'\bbowling shirt\b', raw_title, re.I) and title.startswith('Рубашка '):
        text = re.sub(r'\b[Фф]утболка для боулинга\b',
                      lambda match: 'Рубашка для боулинга' if match.group(0)[0].isupper()
                      else 'рубашка для боулинга', text)
        text = re.sub(r'\b[Фф]утболк(а|и|у|е|ой)\b',
                      lambda match: _garment_replacement(match, {
                          'а': 'рубашка', 'и': 'рубашки', 'у': 'рубашку',
                          'е': 'рубашке', 'ой': 'рубашкой',
                      }), text)
    elif re.search(r'\bsweatshirts?\b', raw_description, re.I) and title.startswith('Свитшот '):
        text = re.sub(r'\b[Фф]утболк(а|и|у|е|ой)\b',
                      lambda match: _garment_replacement(match, {
                          'а': 'свитшот', 'и': 'свитшота', 'у': 'свитшот',
                          'е': 'свитшоте', 'ой': 'свитшотом',
                      }), text)
        text = text.replace('Эта Свитшот', 'Этот свитшот').replace('эта свитшот', 'этот свитшот')
        text = text.replace('Этот Свитшот', 'Этот свитшот')
        text = text.replace('Эта свитшот', 'Этот свитшот')
        text = text.replace('У нее укороченный крой', 'У модели укороченный крой')
        text = text.replace('У неё укороченный крой', 'У модели укороченный крой')

    if re.search(r'\bcleats\b', raw_description, re.I):
        text = re.sub(r'сменные наколенники', 'сменные шипы с надписью «OFF»', text, flags=re.I)
    if re.search(r'\bUnisex fit\b', raw_description, re.I):
        text = text.replace('Универсальный размер', 'Крой унисекс')
    if re.search(r'\bHalf Arrow\b', raw_description, re.I) and not re.search(r'\bhalf.moon\b', raw_description, re.I):
        text = text.replace('полумесяца', 'половины стрелы')
    if 'button closure' in raw_description.lower() and not re.search(r'\bsnap(?:s)?\b|poppers?', raw_description, re.I):
        text = re.sub(r'заст[её]жка на кнопки',
                      lambda match: 'Застёжка на пуговицы' if match.group(0)[0].isupper()
                      else 'застёжка на пуговицы', text, flags=re.I)

    # Замены проверенных машинных калек: не меняем HTML-разметку и имена линий.
    text = text.replace('Эта худи украшена', 'Худи украшено')
    text = re.sub(r'\bЭта худи\b', 'Это худи', text)
    text = re.sub(r'\bэта худи\b', 'эта модель', text)
    if raw_description.lower().find('hoodie') >= 0:
        text = text.replace('; она имеет', '; модель имеет')
    text = re.sub(r'\b[Кк]оллар-поло\b', 'Воротник поло', text)
    text = re.sub(r'\b[Кк]оллар с пуговицами\b', 'Отложной воротник на пуговицах', text)
    text = re.sub(r'\b[Кк]оллар рубашки\b', 'Рубашечный воротник', text)
    text = re.sub(r'\b[Кк]оллар куртки\b', 'Рубашечный воротник', text)
    text = re.sub(r'\b[Кк]оллар свитера\b', 'Рубашечный воротник', text)
    text = re.sub(r'\b[Кк]оллар расклешенный\b', 'Широкий отложной воротник', text)
    text = re.sub(r'\b[Кк]оллар в стиле бомбера\b', 'Воротник бомбера', text)
    text = text.replace('Коллар в стиле бомбер', 'Воротник бомбера')
    text = re.sub(r'\b[Рр]ебристый коллар\b', 'Ребристый воротник', text)
    text = re.sub(r'\b[Кк]оллар свободного кроя\b', 'Свободный отложной воротник', text)
    text = re.sub(r'\b[Кк]оллар на пуговицах\b', 'Отложной воротник на пуговицах', text)
    text = re.sub(r'\b[Кк]оллар и манжеты\b', 'Воротник и манжеты', text)
    text = re.sub(r'\b[Фф]ит «регулярный»', 'Стандартный крой', text)
    text = re.sub(r'\b[Фф]ит — стандартный', 'Стандартный крой', text)
    text = re.sub(r'\b[Фф]ит — облегающий', 'Облегающий крой', text)
    text = re.sub(r'\b[Фф]ит — плотный', 'Прилегающий крой', text)
    text = re.sub(r'\b[Фф]ит сужающийся', 'Зауженный крой', text)
    text = re.sub(r'\b[Фф]ит Slim Relax', 'Умеренно приталенный крой', text)
    text = re.sub(r'\b[Фф]ит бразильский', 'Бразильский крой', text)
    text = text.replace('изысканным урбанистическим луксузным дизайном',
                        'изысканным городским дизайном')
    text = text.replace('изысканным изысканным городским дизайном',
                        'изысканным городским дизайном')
    text = text.replace('урбанистическим луксузным дизайном', 'городским дизайном')
    text = text.replace('присутствуют множество', 'присутствует множество')
    text = text.replace('Замок-фиксатор в форме лобстера', 'Карабин')
    text = text.replace('замок-фиксатор в форме лобстера', 'карабин')
    text = text.replace('зип-листов', 'фирменной пластиковой стяжки Zip Tie')
    text = text.replace('крупный изображение', 'крупное изображение')
    if ('Lightweight cushioned midsole' in raw_description
            and '</li>Легкая амортизирующая средняя подошва<li>' in text):
        text = text.replace('</li>Легкая амортизирующая средняя подошва<li>',
                            '</li><li>Лёгкая амортизирующая промежуточная подошва</li><li>')
    if 'li>Rivets' in raw_description and 'Гвоздики' in text:
        text = text.replace('Гвоздики', 'Заклёпки')
    if re.search(r'\b(?:hoodie|hooded)\b', raw_title + ' ' + raw_description, re.I):
        text = text.replace('Эта футболка с капюшоном', 'Это худи')
        if raw_title == 'Ink Arrow Regular Logo Hoodie':
            text = text.replace('Она изготовлена из качественного хлопка',
                                'Модель изготовлена из качественного хлопка')
    if re.search(r'\bovershirt\b', raw_title, re.I):
        text = text.replace('На свитере Dream Arrow', 'На рубашке Dream Arrow')
    if re.search(r'\bQuote Knit Hoodie\b', raw_title, re.I):
        text = text.replace('Эта свитшот-худи', 'Это худи')
    if re.search(r'\bcuffed sweatpants\b', raw_title, re.I):
        text = text.replace('Эти спортивные брюки с рукавами', 'Эти спортивные брюки с манжетами')
        text = text.replace('Дизайн с рукавами', 'Манжеты на штанинах')
    if 'ribbed halterneck' in raw_description.lower():
        text = text.replace('Эта майка с ребрами на груди', 'Этот топ в рубчик с бретелью вокруг шеи')
        text = text.replace('шеи изготовлена', 'шеи изготовлен')
        text = text.replace('Произведена в Италии', 'Произведён в Италии')
    if re.search(r'\bribbed rowing top\b', raw_description, re.I):
        text = text.replace('Эта майка с ребрами', 'Этот топ в рубчик')
        text = text.replace('хлопка с ребрами', 'хлопка в рубчик')
        text = text.replace('и сделана из хлопка', 'и изготовлен из хлопка')
        text = text.replace('Фасон — обрезной', 'Укороченный крой')
    if re.search(r'\bskate fit\b', raw_description, re.I):
        text = re.sub(r'Свитшот имеет форму, подходящую для катания на скейтборде\.', 'Крой Skate.', text)
        text = re.sub(r'свитшот имеет форму, подходящую для катания на скейтборде\.', 'крой Skate.', text)
        text = re.sub(r'Подходит для (?:катания на скейтборде|скейтбординга)\.', 'Крой Skate.', text)
        text = re.sub(r'Фасон, подходящий для скейтбордист(?:ов|а)\.', 'Крой Skate.', text)
        text = re.sub(r'Фасон, подходящий для скейтбординга\.', 'Крой Skate.', text)
        text = re.sub(r'Футболка имеет форму, подходящую для катания на скейтборде\.', 'Крой Skate.', text)
        text = re.sub(r'Фасон, подходящий для катания на скейтборде\.', 'Крой Skate.', text)
    if card.get('id') == 'offwhite:44MGE04XZ26F005100':
        text = text.replace('принт с изображением скейтера из серии Dream Arrow Skate',
                            'принт Dream Arrow Skate')
    if card.get('id') == 'offwhite:2AA13FS26JER001W102':
        text = text.replace('Универсальный размер', 'Крой унисекс')
    # Прицельная проверка предупреждений preflight по английским описаниям.
    if card.get('id') == 'offwhite:2AD282S26JER001W960' and 'Double Top' in raw_title:
        text = text.replace('Футболка Light Jersey', 'Топ Light Jersey')
    if card.get('id') == 'offwhite:2BA079S26FLE001W102' and 'Crop Crewneck' in raw_title:
        text = text.replace('Футболка Embroidered', 'Свитшот Embroidered')
        text = text.replace('Короткий крой в стиле crewneck', 'Укороченный крой и круглый вырез')
    if card.get('id') == 'offwhite:2BB058S26FLE002W663' and 'Hoodie' in raw_title:
        text = text.replace('Свитер с принтом Spray Off Stamp Over Hoodie',
                            'Худи Spray Off Stamp Over')
    if card.get('id') == 'offwhite:44MBD05LS26F001001' and 'Tracktop' in raw_title:
        text = text.replace('Трекпант Marker Nylon Relaxed',
                            'Спортивная куртка Marker Nylon Relaxed')
    if card.get('id') == 'offwhite:44MYA18JZ26D001963' and 'Moneyworld artwork' in raw_description:
        text = text.replace('Эти джинсы Slim Relax Jeans, разработанные компанией Moneyworld,',
                            'Джинсы Slim Relax с принтом Moneyworld')
        text = text.replace('Эти джинсы Slim Relax Jeans, с принтом Moneyworld,',
                            'Джинсы Slim Relax с принтом Moneyworld')
        text = text.replace('Джинсы Slim Relax с принтом Moneyworld, имеют',
                            'Джинсы Slim Relax с принтом Moneyworld имеют')
        text = text.replace('Графика от Moneyworld', 'Принт Moneyworld')
    if card.get('id') == 'offwhite:44XCE04JS26F003001' and 'Button and zip closure' in raw_description:
        text = text.replace('Застёжка на кнопки и молнию',
                            'Застёжка на пуговицу и молнию')
    if card.get('id') == 'offwhite:44MAA027Z26J002001' and 'Ribbed crewneck' in raw_description:
        text = text.replace('Воротник-стойка с ребрами', 'Круглый вырез с отделкой в рубчик')
    if card.get('id') == 'offwhite:OWBB035F25FLE0024C19' and 'Ink Arrow Regular Logo Hoodie' in raw_title:
        text = text.replace('Она изготовлена', 'Модель изготовлена')
    if card.get('id') == 'offwhite:OWBB058S25FLE0010510' and 'Hoodie' in raw_title:
        text = text.replace('У нее свободный крой', 'Свободный крой')
    if re.search(r'\bHalf Arrow Low Waist Denim Skirt\b', raw_title, re.I):
        text = re.sub(r'<p>.*?</p>',
                      '<p>Джинсовая юбка Half Arrow с низкой посадкой дополнена '
                      'потёртым поясом и фирменным мотивом Arrow на задних карманах.</p>',
                      text, count=1, flags=re.S)
        text = re.sub(r'<li[^>]*>Удлиненная длина ног</li>', '', text, flags=re.I)

    if card.get('id') == 'offwhite:OWIA288F25LEA0010140' and 'Round toe design' in raw_description and '<ul' not in text:
        text = ('<p>' + text + '</p><ul><li>Закруглённый носок</li>'
                '<li>Шнуровка с надписью «Shoelaces»</li><li>Фирменный мотив Arrow по бокам</li>'
                '<li>Логотип на язычке и заднике</li><li>Контрастная стяжка Zip Tie</li>'
                '<li>Рельефная резиновая подошва</li><li>Кожаный верх</li>'
                '<li>Фирменные элементы Off-White™</li></ul>')

    # Further source-checked copy review: apply only when the English phrase
    # proves the intended meaning (hook-and-loop straps are NOT a Zip Tie).
    if re.search(r'\bzip[ -]?tie\b', raw_description, re.I):
        text = re.sub(r'элементом в виде заст[её]жки-липучки',
                      'элементом в виде пластиковой стяжки Zip Tie', text, flags=re.I)
        text = re.sub(r'элемент в виде заст[её]жки-липучки',
                      'элемент в виде пластиковой стяжки Zip Tie', text, flags=re.I)
        text = re.sub(r'характерную этикетку с заст[её]жкой-липучкой',
                      'фирменную пластиковую стяжку Zip Tie', text, flags=re.I)
        text = re.sub(r'Декоративная этикетка с заст[её]жкой-липучкой',
                      'Фирменная пластиковая стяжка Zip Tie', text, flags=re.I)
        text = re.sub(r'(?:бирк[а-яё]*|этикетк[а-яё]*|элемент[а-яё]*) с заст[её]жкой-липучкой',
                      'пластиковой стяжкой Zip Tie', text, flags=re.I)
        text = re.sub(r'замок-липучка|заст[её]жк[аиы]-липучк[аииы]|бирка-липучка|этикетка-липучка',
                      lambda m: 'пластиковая стяжка Zip Tie' if m.group(0)[0].islower()
                      else 'Пластиковая стяжка Zip Tie', text, flags=re.I)
        text = text.replace('В комплекте идет узнаваемый пластиковая стяжка Zip Tie',
                            'В комплекте идёт фирменная пластиковая стяжка Zip Tie')
        text = text.replace('узнаваемый пластиковая стяжка Zip Tie',
                            'фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Узнаваемый пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('узнаваемая пластиковая стяжка Zip Tie',
                            'фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Узнаваемая пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Характерные пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Уникальные пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Отличительная пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('знакомая пластиковая стяжка Zip Tie',
                            'фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Характерная пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Декоративная пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Уникальная пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Привязана знакомая пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        text = text.replace('Привязана фирменная пластиковая стяжка Zip Tie',
                            'Фирменная пластиковая стяжка Zip Tie')
        if re.search(r'\bquoted?\s+["“]?shoe[ _-]*laces', raw_description, re.I):
            text = re.sub(r'Элементы(?: дизайна)?,? (?:имитирующие|напоминающие) шнурки|'
                          r'Узор(?:ы)?,? имитирующ(?:ий|ие) шнурки',
                          'Надпись «Shoe Laces» на шнурках', text, flags=re.I)
    if re.search(r'\bskate-inspired\b', raw_description, re.I):
        text = re.sub(r'вдохновленн(?:ые|ый|ая|ое) коньками',
                      'вдохновлённые скейтбордингом', text, flags=re.I)
    if re.search(r'\bcalf socks\b', raw_description, re.I):
        text = text.replace('Чулки с ребрами из смесового хлопка',
                            'Носки до середины икры из хлопкового трикотажа в рубчик')
        text = text.replace('в рубчик, с логотипом', 'в рубчик с логотипом')
        text = text.replace('Структура ткани с ребрами', 'Трикотаж в рубчик')
    if re.search(r'\bbookish logo\b', raw_description, re.I):
        text = text.replace('логотипа-цитата', 'фирменного текстового логотипа')
    if re.search(r'\bcolor blocks?\b|\bcolor block paneling\b', raw_description, re.I):
        text = text.replace('контрастными контрастными цветовыми вставками',
                            'контрастными цветовыми вставками')
        text = text.replace('Контрастные контрастными цветовыми вставками',
                            'Контрастные цветовые вставки')
        text = text.replace('Дизайн с контрастными контрастными цветовыми вставками',
                            'Дизайн с контрастными цветовыми вставками')
    if card.get('id') == 'offwhite:29B00273TW354' and 'sequin embellishments' in raw_description:
        text = text.replace('Украшения из кристаллов и страз',
                            'Украшения из кристаллов и пайеток')
    if (card.get('id') == 'offwhite:44XYA18JS26D003020'
            and raw_title == 'Diagonal Embroidery Slim Relaxed Jeans'):
        text = text.replace('The Diagonal Embroidery Slim Relaxed Jeans сочетают в себе вечную конструкцию из денима,',
                            'Джинсы Diagonal Embroidery Slim Relaxed сочетают классический деним,')
        text = text.replace('современный дизайн и изысканные логотипы бренда.',
                            'современный дизайн и фирменную графику.')
        text = text.replace('Слимный крой', 'Зауженный крой')
        text = text.replace('Кожаный элемент на спинке', 'Кожаная нашивка с надписью сзади')
        text = text.replace('Узор в диагональном стиле на задних частях брюк',
                            'Фирменная диагональная вышивка на задней стороне штанин')
        text = text.replace('Металлическая пуговица спереди и молния для застёгивания',
                            'Застёжка на металлическую пуговицу и молнию')
    if (card.get('id') == 'offwhite:44MBB085Z26F002001'
            and raw_title == 'Swarovski Double Eagle Skate Hoodie'
            and 'Hood with drawstring' in raw_description):
        text = text.replace('Дизайн этой футболки Swarovski Double Eagle Skate',
                            'Дизайн худи Swarovski Double Eagle Skate')

    # A case/holder with card slots is a card holder, not a phone case or wallet.
    # Restrict this to the individually reviewed source-title + Russian-type pairs.
    if (title.startswith('Картхолдер ') and re.search(r'\bcard (?:case|holder)\b', raw_title, re.I)
            and ('card' in raw_description.lower())):
        text = re.sub(r'\bКошелек-картхолдер\b', 'Картхолдер', text)
        text = re.sub(r'\bКошелек для карток\b', 'Картхолдер', text)
        text = re.sub(r'\bКошелек для карт\b', 'Картхолдер', text)
        text = re.sub(r'\bКошелек\b', 'Картхолдер', text)
        text = re.sub(r'\bкошелек для карток\b', 'картхолдер', text)
        text = re.sub(r'\bЧехол для карточек\b|\bЧехол для карт\b|\bЧехол\b',
                      'Картхолдер', text)
        text = re.sub(r'\bэтот чехол\b', 'этот картхолдер', text)
        text = re.sub(r'\bчехла\b', 'картхолдера', text)
        if raw_title == 'Out Of Office Sneaker Card Case':
            text = text.replace('карток', 'карт')
        if raw_title == 'Jitney Iridescent Card Case':
            text = text.replace('карманов для карт для оптимального хранения',
                                'отделений для удобного хранения карт')

    # The name "top" alone is inconclusive (Who Cares 90S Fit explicitly says
    # t-shirt in English). These source descriptions confirm a sleeveless top,
    # a halter top, or simply a top instead of the Russian word "футболка".
    reviewed_top_titles = {
        'Off Stamp Ribbed Rowing Top', 'Sleek Meteor Long-Sleeved Top',
        'Race Logo Arrow Seamless Top', 'Tech Jer Serena Long-Sleeve Top',
        'Off Stamp Rib Kat Top', 'Black Logo Band Zip Crop Top',
        'Black Stamp Seamless Crop Top', 'Denim Doll Top',
    }
    if title.startswith('Топ ') and raw_title in reviewed_top_titles and raw_description:
        if not re.search(r'\bt-?shirt\b|\bfootball jersey\b', raw_description, re.I):
            text = re.sub(r'\b[Фф]утболк(а|и|у|ой|е)\b',
                          lambda m: _garment_replacement(m, {
                              'а': 'топ', 'и': 'топа', 'у': 'топ', 'ой': 'топом', 'е': 'топе',
                          }), text)
            text = text.replace('Эта топ', 'Этот топ').replace('эта топ', 'этот топ')
            text = text.replace('Этой топ', 'Этому топу')
            text = text.replace('Эта футболка', 'Этот топ')
            if raw_title == 'Race Logo Arrow Seamless Top':
                text = text.replace('у нее вырез-халтер', 'у него вырез с бретелью вокруг шеи')
                text = text.replace('Изготовлена из эластичной ткани', 'Изготовлен из эластичной ткани')
                text = text.replace('Произведена в Италии', 'Произведён в Италии')
            if raw_title == 'Tech Jer Serena Long-Sleeve Top':
                text = text.replace('Ознакомьтесь с современным дизайном с помощью топа Tech Jer Serena с длинными рукавами, изготовленной из высокопроизводительной технической ткани, отражающей динамичный городской стиль.',
                                    'Топ Tech Jer Serena с длинными рукавами изготовлен из технического трикотажа и дополнен контрастными деталями.')
                text = text.replace('Ткань из высокопроизводительной технической майки',
                                    'Технический трикотаж')
                text = text.replace('Контрастными цветовыми вставками подчеркивают структуру изделия',
                                    'Контрастные цветовые вставки подчёркивают конструкцию изделия')
            if raw_title == 'Off Stamp Rib Kat Top':
                text = text.replace('имеет безрукавный дизайн', 'имеет крой без рукавов')
            if raw_title == 'Black Stamp Seamless Crop Top':
                text = text.replace('безразрезная', 'бесшовная')
                text = text.replace('Эта бесшовная топ', 'Этот бесшовный топ')
                text = text.replace('Сделано в Италии', 'Произведён в Италии')
            if raw_title == 'Denim Doll Top':
                text = text.replace('из денима на 100% хлопка', 'из 100% хлопкового денима')
                text = text.replace('обрывистыми краями', 'потёртыми краями')
                text = text.replace('Произведена в Италии', 'Произведён в Италии')

    if (raw_title == 'Scribble Arrow Skate Logo T-Shirt'
            and 'relaxed fit' in raw_description):
        text = text.replace(
            'Применяется терминология: «свободного кроя» = свободного/непринуждённого кроя '
            '(имеется в виду форма/силуэт одежды; это НЕ «разглаженный/выглаженный», здесь речь не идёт о утюге). ',
            '',
        )
        text = text.replace('Воротник с ребрами', 'Круглый вырез с отделкой в рубчик')
    if raw_title == 'Mix Logos Knit Beanie' and 'ribbed knit fabric' in raw_description:
        text = text.replace('Тщательно сделанный шарф-берет из ткани с ребрами, украшенный разнообразными логотипами.',
                            'Вязаная шапка в рубчик украшена нашивками с логотипами.')
        text = text.replace('Структура ткани с ребрами', 'Трикотаж в рубчик')
        text = text.replace('Манжета, которая можно сложить', 'Отворот')
        text = text.replace('Произведено в Италии', 'Произведена в Италии')
    if (raw_title == 'Switch Off Crop Crewneck Sweatshirt'
            and 'Long sleeves with ribbed cuffs' in raw_description):
        text = text.replace('Свитшот с коротким рукавом', 'Укороченный свитшот с длинными рукавами')
        text = text.replace('Вырез с ребрами жесткости', 'Круглый вырез с отделкой в рубчик')
        text = text.replace('Длинные рукава с ребрами жесткости на манжетах',
                            'Длинные рукава с манжетами в рубчик')
    if raw_title == 'Off Stamp Rib Tank Dress' and 'Scoop neckline' in raw_description:
        text = text.replace('Платье с ребрами на талии', 'Платье-майка в рубчик')
        text = text.replace('Воротник-чашечка', 'Округлый вырез')
        text = text.replace('Мягкая текстура с ребрами', 'Мягкий трикотаж в рубчик')
    if raw_title == '3.0 Off Court Sneakers' and 'Signature leather zip-tie tag at the laces' in raw_description:
        text = text.replace('Характерными элементами линейки Off Court являются логотипы Zip Tie, Arrow и надпись',
                            'Характерные детали линейки Off Court — Zip Tie, Arrow и надпись')
        text = text.replace('Технический сетчатый браслет для лодыжек',
                            'Сетчатая отделка вокруг щиколотки')
        text = text.replace('Знак из кожи с застежкой-молнией на шнурках',
                            'Фирменная кожаная стяжка Zip Tie на шнурках')
        text = text.replace('Резиновая подошва с ребрами', 'Рельефная резиновая подошва')
    if raw_title in ('Scribble Diag Boxy T-Shirt', 'Ink Arrow Boxy T-Shirt') and 'Ribbed crew neckline' in raw_description:
        text = text.replace('Воротник-стойка с ребрами жесткости',
                            'Круглый вырез с отделкой в рубчик')
    if raw_title == 'Scribble Diag Boxy T-Shirt' and 'scribble diagonal' in raw_description.lower():
        text = text.replace('узор в стиле «скраббл»', 'узор Scribble')
        text = text.replace('Силуэт футболки — прямоугольный, что обеспечивает свободную посадку.',
                            'Прямой свободный крой.')
    if raw_title == 'Arrow Scribble Mid Socks' and 'Ribbed knit construction' in raw_description:
        text = text.replace('Структура ткани с ребрами обеспечивает дополнительный комфорт',
                            'Трикотаж в рубчик обеспечивает комфорт')

    if title != title_i18n.get('ru'):
        title_data.setdefault('i18n', {})['ru'] = title
        path = ((card.get('category') or {}).get('i18n') or {}).get('ru')
        if isinstance(path, list) and path:
            path[-1] = title
    if text != description_i18n.get('ru'):
        description_data.setdefault('i18n', {})['ru'] = text
    return card