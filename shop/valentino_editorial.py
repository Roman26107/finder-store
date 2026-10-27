"""Редакторская нормализация русских текстов Valentino Garavani."""

from __future__ import annotations

import html
import re
from copy import deepcopy


TITLE_PREFIXES = (
    ('Borsa A Mano Media ', 'Сумка с ручками среднего размера'),
    ('Borsa A Mano Piccola ', 'Маленькая сумка с ручками'),
    ('Borsa A Mano Mini ', 'Мини-сумка с ручкой'),
    ('Borsa Mini A Mano ', 'Мини-сумка с ручкой'),
    ('Borsa A Spalla Media ', 'Сумка через плечо среднего размера'),
    ('Borsa Media A Spalla ', 'Сумка через плечо среднего размера'),
    ('Borsa A Spalla Piccola ', 'Маленькая сумка через плечо'),
    ('Borsa Piccola A Spalla ', 'Маленькая сумка через плечо'),
    ('Borsa A Tracolla Piccola ', 'Маленькая сумка через плечо'),
    ('Borsa Piccola A Tracolla ', 'Маленькая сумка через плечо'),
    ('Borsa A Spalla ', 'Сумка через плечо'),
    ('Borsa A Tracolla ', 'Сумка через плечо'),
    ('Borsa Grande Shopping Morbida ', 'Большая мягкая сумка-тоут'),
    ('Borsa Piccola Shopping Morbida ', 'Маленькая мягкая сумка-тоут'),
    ('Borsa Shopping Grande ', 'Большая сумка-тоут'),
    ('Borsa Shopping Media ', 'Сумка-тоут среднего размера'),
    ('Borsa Media Shopping ', 'Сумка-тоут среднего размера'),
    ('Borsa Shopping Piccola ', 'Маленькая сумка-тоут'),
    ('Borsa Piccola Shopping ', 'Маленькая сумка-тоут'),
    ('Borsa Shopping ', 'Сумка-тоут'),
    ('Borsa Media Con Catena ', 'Сумка на цепочке среднего размера'),
    ('Borsa Media ', 'Сумка среднего размера'),
    ('Borsa Piccola A Secchiello ', 'Маленькая сумка-ведро'),
    ('Borsa Piccola ', 'Маленькая сумка'),
    ('Mini Borsa A Secchiello ', 'Мини-сумка-ведро'),
    ('Mini Borsa A Spalla ', 'Мини-сумка через плечо'),
    ('Mini Borsa A Tracolla ', 'Мини-сумка через плечо'),
    ('Mini Borsa Shopping ', 'Мини-сумка-тоут'),
    ('Mini Borsa Hobo ', 'Мини-сумка-хобо'),
    ('Mini Borsa ', 'Мини-сумка'),
    ('Mini Vanity Bag ', 'Мини-сумка vanity'),
    ('Mini Bauletto ', 'Мини-сумка-боулинг'),
    ('Bauletto ', 'Сумка-боулинг'),
    ('Clutch Piccola ', 'Маленький клатч'),
    ('Minaudière ', 'Клатч-минодьер'),
    ('Minaudiere ', 'Клатч-минодьер'),
    ('Pochette ', 'Клатч'),
    ('Portafoglio Con Catena ', 'Кошелёк на цепочке'),
    ('Pouch ', 'Клатч'),
)


TITLE_PHRASES = (
    (' In Vitello Effetto Cavallino Animalier', ' из телячьей кожи с ворсистой отделкой и анималистичным принтом'),
    (' In Vitello Granato Laminato', ' из ламинированной зернистой телячьей кожи'),
    (' In Vitello Granato', ' из зернистой телячьей кожи'),
    (' In Vitello Laminato', ' из ламинированной телячьей кожи'),
    (' In Vitello Lucido', ' из глянцевой телячьей кожи'),
    (' In Vitello', ' из телячьей кожи'),
    (' In Nappa Laminata', ' из ламинированной кожи наппа'),
    (' In Nappa Lucida', ' из глянцевой кожи наппа'),
    (' In Nappa Ricamata', ' из кожи наппа с вышивкой'),
    (' In Nappa', ' из кожи наппа'),
    (' In Suede E Nappa', ' из замши и кожи наппа'),
    (' In Pitone E Nappa', ' из кожи питона и кожи наппа'),
    (' In Pitone E Suede', ' из кожи питона и замши'),
    (' In Struzzo E Nappa', ' из кожи страуса и кожи наппа'),
    (' In Pelli Esotiche', ' из экзотической кожи'),
    (' In Pelliccia Di Agnello', ' из овчины'),
    (' In Pitone', ' из кожи питона'),
    (' In Struzzo', ' из кожи страуса'),
    (' In Capretto', ' из козьей кожи'),
    (' In Suede', ' из замши'),
    (' In Pelle', ' из кожи'),
    (' In Rafia A Righe', ' из рафии в полоску'),
    (' In Rafia', ' из рафии'),
    (' In Lino', ' из льна'),
    (' In Denim', ' из денима'),
    (' In Canvas Vichy', ' из канваса в клетку виши'),
    (' In Canvas', ' из канваса'),
    (' In Tessuto Jacquard Cloqué Lurex', ' из жаккарда клоке с люрексом'),
    (' In Tessuto Jacquard Cloqué', ' из жаккарда клоке'),
    (' In Tessuto Jacquard Lurex', ' из жаккарда с люрексом'),
    (' In Tessuto Jacquard Papier Floral', ' из жаккарда Papier Floral'),
    (" In Tessuto Jacquard Apres L'Hiver", " из жаккарда Après L’Hiver"),
    (' In Tessuto Intrecciato', ' из плетёного текстиля'),
    (' In Tessuto Crochet', ' из вязаного полотна кроше'),
    (' In Tessuto Moiré', ' из ткани муар'),
    (' In Metallo', ' из металла'),
    (' Con Tracolla Nastro Ricamato', ' с вышитым плечевым ремнём'),
    (' Con Pietre Cabochon', ' с кабошонами'),
    (' Con Perle, Strass E Logo Gioiello', ' с жемчугом, стразами и ювелирным логотипом'),
    (' Con Perle E Strass', ' с жемчугом и стразами'),
    (' Con Strass Motivo Flowerism E Logo Gioiello', ' со стразами Flowerism и ювелирным логотипом'),
    (' Con Strass Motivo Flowerism', ' со стразами Flowerism'),
    (' Con Strass E Logo Gioiello', ' со стразами и ювелирным логотипом'),
    (' Con Cristalli Swarovski® E Custodia In Raso', ' с кристаллами Swarovski® и атласным футляром'),
    (' Con Logo Gioiello', ' с ювелирным логотипом'),
    (' Con Ricamo Floreale', ' с цветочной вышивкой'),
    (' con Ricamo Farfalla', ' с вышивкой в виде бабочки'),
    (' Con Ricamo Sparkling', ' со сверкающей вышивкой'),
    (' Con Ricamo', ' с вышивкой'),
    (' Con Frange', ' с бахромой'),
    (' Con Motivo A Onde', ' с волнистым узором'),
    (' Motivo Chevron', ' с шевронным узором'),
    (' Patchwork', ' в технике пэчворк'),
    (' Ricamata Animalier', ' с анималистичной вышивкой'),
    (' Ricamata', ' с вышивкой'),
    (' Con Strass', ' со стразами'),
)


DESCRIPTION_REPLACEMENTS = (
    ('съемн', 'съёмн'),
    ('Съемн', 'Съёмн'),
    ('кошелек', 'кошелёк'),
    ('Кошелек', 'Кошелёк'),
    ('теленка', 'телёнка'),
    ('ягненка', 'ягнёнка'),
    ('плетен', 'плетён'),
    ('кожи цвета граната', 'зернистой телячьей кожи'),
    ('кожи цвета гранат', 'зернистой телячьей кожи'),
    ('кожи гранатового цвета', 'зернистой телячьей кожи'),
    ('кожи красного цвета', 'зернистой телячьей кожи'),
    ('гранатовой кожи', 'зернистой телячьей кожи'),
    ('кожи телятины цвета граната', 'зернистой телячьей кожи'),
    ('кожи телёнка цвета гранат', 'зернистой телячьей кожи'),
    ('кожи граната', 'зернистой телячьей кожи'),
    ('кожи телятины', 'телячьей кожи'),
    ('кожи теленка', 'телячьей кожи'),
    ('кожи телёнка', 'телячьей кожи'),
    ('из нубука', 'из кожи наппа'),
    ('из наппы', 'из кожи наппа'),
    ('из овечьей кожи', 'из кожи ягнёнка'),
    ('из мягкой кожи ягнёнка', 'из мягкой кожи ягнёнка'),
    ('кожа под кожу кабана', 'телячья кожа с ворсистой отделкой'),
    ('кожи под кожу кабана', 'телячьей кожи с ворсистой отделкой'),
    ('сумка-шоппер', 'сумка через плечо'),
    ('Сумка-шоппер', 'Сумка через плечо'),
    ('сумка-шоппинг', 'сумка-тоут'),
    ('Сумка-шоппинг', 'Сумка-тоут'),
    ('сумка для шоппинга', 'сумка-тоут'),
    ('Сумка для шоппинга', 'Сумка-тоут'),
    ('сумка-перекинутая через плечо', 'сумка через плечо'),
    ('Сумка-перекинутая через плечо', 'Сумка через плечо'),
    ('сумка-перекинутая', 'сумка через плечо'),
    ('Сумка-перекинутая', 'Сумка через плечо'),
    ('перекинутой через тело', 'через плечо'),
    ('перекинутая через тело', 'через плечо'),
    ('в кросс-боди-формате', 'через плечо'),
    ('как кросс-боди', 'через плечо'),
    ('кросс-боди', 'через плечо'),
    ('в виде тканевого узора', 'стёганую отделку'),
    ('узор в виде стежков', 'стёганую отделку'),
    ('узор в виде вставок', 'стёганую отделку'),
    ('шевроновом узоре', 'шевронным узором'),
    ('узором в виде шевронов', 'шевронным узором'),
    ('узором в виде шеврона', 'шевронным узором'),
    ('узор в виде шеврона', 'шевронный узор'),
    ('узор в форме ромбов', 'стёганый ромбовидный узор'),
    ('логотипом-ювелирным украшением', 'ювелирным логотипом'),
    ('логотипом в виде украшения', 'ювелирным логотипом'),
    ('элементом VLogo Signature в виде украшения', 'ювелирным элементом VLogo Signature'),
    ('замок в виде логотипа', 'застёжка VLogo Signature'),
    ('Замок в виде логотипа', 'Застёжка VLogo Signature'),
    ('замок-магнит', 'магнитная застёжка'),
    ('Замок-магнит', 'Магнитная застёжка'),
    ('внутренняя обивка', 'подкладка'),
    ('Иконическая', 'Знаковая'),
    ('иконическая', 'знаковая'),
    ('застежка-карман переработана таким образом, чтобы можно было закрепить молнию',
     'застёжка-крючок переосмыслена как фиксатор бегунка молнии'),
    ('застежка-карман переработана таким образом, чтобы фиксировать молнию',
     'застёжка-крючок переосмыслена как фиксатор бегунка молнии'),
    ('застёжка-карман переработана таким образом, чтобы можно было закрепить молнию',
     'застёжка-крючок переосмыслена как фиксатор бегунка молнии'),
    ('застёжка-карман переработана таким образом, чтобы фиксировать молнию',
     'застёжка-крючок переосмыслена как фиксатор бегунка молнии'),
    ('застежка', 'застёжка'),
    ('Застежка', 'Застёжка'),
    ('более большую сумку', 'сумку большего размера'),
    ('имеется детальное узорное орнаментирование из кожи', 'дополнена кожаной окантовкой'),
    ('декоративные элементы из кожи по краям', 'кожаную окантовку'),
    ('декорирована кожаными элементами по краям', 'дополнена кожаной окантовкой'),
    ('украшена фигурками кошек', 'украшена эмалированными головами кошек'),
    ('покрытыми эмалями', 'покрытыми эмалью'),
    ('цветочным вышивкой', 'цветочной вышивкой'),
    ('с стразами', 'со стразами'),
    ('с съем', 'со съём'),
    ('съёмному цепочке', 'съёмной цепочке'),
    ('съёмного цепочке', 'съёмной цепочке'),
    ('пристежки', 'заклёпки'),
    ('Пристежки', 'Заклёпки'),
    ('заклепки', 'заклёпки'),
    ('Заклепки', 'Заклёпки'),
    ('сумка с камнями-студами', 'клатч Rockstud'),
    ('Сумка с камнями-студами', 'Клатч Rockstud'),
    ('имеет детальный', 'имеет выразительный'),
    ('придает', 'придаёт'),
    ('Ее ', 'Её '),
    ('Это небольшая ', 'Небольшая '),
    ('Это маленькая ', 'Маленькая '),
    ('Это большая ', 'Большая '),
    ('Это сумка ', 'Сумка '),
    ('шоппинг-сумка', 'сумка-тоут'),
    ('Шоппинг-сумка', 'Сумка-тоут'),
    ('Шоппинг-маленькая сумка', 'Маленькая сумка-тоут'),
    ('ремню-перекину', 'плечевому ремню'),
    ('ремнем-перекином', 'плечевым ремнём'),
    ('кожиового цвета', 'кожи'),
    ('кожиового вина', 'кожи'),
    ('сумка медианного размера', 'сумка среднего размера'),
    ('Сумка медианного размера', 'Сумка среднего размера'),
    ('сумка через плечо маленького размера', 'маленькая сумка через плечо'),
    ('Сумка через плечо маленького размера', 'Маленькая сумка через плечо'),
    ('сумка среднего размера на плечо', 'сумка через плечо среднего размера'),
    ('Сумка среднего размера на плечо', 'Сумка через плечо среднего размера'),
    ('через тело', 'через плечо'),
    ('структуру стёганую отделку', 'стёганую отделку'),
    ('структуру в виде ворса', 'стёганую отделку'),
    ('структуру в виде стежков', 'стёганую отделку'),
    ('узором из стежков', 'стёганой отделкой'),
    ('узором в виде зигзагов', 'шевронным узором'),
    ('узор в виде зигзагов', 'шевронный узор'),
    ('в шевронным узором', 'с шевронным узором'),
    ('в виде заклёпками', 'в виде заклёпок'),
    ('головок кошек', 'эмалированных голов кошек'),
    ('глазами кошек', 'эмалированными головами кошек'),
    ('фигурки кошек, покрытые эмалями', 'эмалированные головы кошек'),
    ('эмалированными головами кошек, покрытыми эмалью и украшенными',
     'эмалированными головами кошек, украшенными'),
    ('эмалированными головами кошек, покрытыми эмалью, а также',
     'эмалированными головами кошек и'),
    ('материалов питон и замши', 'кожи питона и замши'),
    ('материал – питон и наппа', 'материалов — кожи питона и кожи наппа'),
    ('изображения страуса, тегуа и питона', 'кожу страуса, тегу и питона'),
    ('VLogo Signature VLogo Signature', 'VLogo Signature'),
    ('Минадьер', 'Клатч-минодьер'),
    ('Минадиер', 'Клатч-минодьер'),
    ('Minaudière', 'Клатч-минодьер'),
)


ISSUE_CHECKS = (
    ('wrong_granata', r'кож\w* (?:цвета )?гранат|гранатов\w+ кож'),
    ('wrong_nappa', r'\bнубук|из наппы|наппа д[’\'`]аньелло'),
    ('wrong_borchie', r'камн\w*-студ|присте[жж]к|шип\w*'),
    ('wrong_cavallino', r'кож\w+ (?:под кожу )?кабана'),
    ('literal_shopping_bag', r'сумк\w+[- ](?:шопп|покуп)|для шоппинга'),
    ('literal_hand_bag', r'сумк\w+ в рук|ручной носки'),
    ('bad_removable_spelling', r'\bсъем'),
    ('bad_agreement', r'с страз|цветочным вышивк|съёмному цепочк|ламинированный$'),
    ('literal_crossbody', r'перекинут\w+|кросс[- ]боди'),
    ('bad_calfskin', r'кож\w+ телятины|кож\w+ теленка'),
    ('machine_borsa', r'\bборса\b'),
    ('machine_fastening', r'застежка-карман|закрепить молнию|фиксировать молнию'),
    ('machine_word', r'орнаментирование|инвелло|камнями-студами'),
    ('broken_style', r'медиан|кожиов|ремн\w*-перекин|структур\w+ стёган|в шевронным'),
    ('bad_chevron', r'узор\w* в виде зигзаг'),
    ('bad_carry_phrase', r'через плечо или через плечо|через тело'),
    ('bad_stud_spelling', r'заклеп'),
)


def _clean_spaces(value):
    return re.sub(r'\s+', ' ', html.unescape(str(value or ''))).strip()


def editorial_title(raw_title):
    """Строит русское название из итальянского оригинала."""
    raw_title = _clean_spaces(raw_title)
    prefix_ru = ''
    remainder = raw_title
    for prefix, translated in TITLE_PREFIXES:
        if raw_title.casefold().startswith(prefix.casefold()):
            prefix_ru = translated
            remainder = raw_title[len(prefix):].strip()
            break
    if not prefix_ru:
        return ''
    for source, translated in TITLE_PHRASES:
        remainder = re.sub(re.escape(source), translated, remainder, flags=re.IGNORECASE)
    remainder = re.sub(r'\s+,', ',', remainder)
    remainder = re.sub(r' с вышивкой с бахромой$', ' с вышивкой и бахромой', remainder)
    remainder = re.sub(r'\bVsling\b', 'VSLING', remainder, flags=re.IGNORECASE)
    remainder = re.sub(r'\bDevain\b', 'DeVain', remainder, flags=re.IGNORECASE)
    remainder = re.sub(r'\s{2,}', ' ', remainder).strip(' .,-')
    return f'{prefix_ru} {remainder}'.strip()


def clean_description(text, *, raw_description=''):
    """Исправляет подтверждённые кальки, орфографию и терминологию Valentino."""
    result = _clean_spaces(text)
    raw = _clean_spaces(raw_description).casefold()
    for old, new in DESCRIPTION_REPLACEMENTS:
        result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE if old[:1].islower() else 0)
    if 'borchi' in raw:
        result = re.sub(r'(?i)шип\w*', 'заклёпками', result)
        result = re.sub(r'(?i)заклеп', 'заклёп', result)
    if 'borchi' in raw:
        result = re.sub(r'(?i)маленькими заклёпками', 'миниатюрными заклёпками', result)
    if 'nappa' in raw:
        result = re.sub(r'(?i)из (?:мягкой )?овечьей кожи', 'из мягкой кожи ягнёнка', result)
        result = re.sub(r'(?i)из (?:мягкой )?кожи ягнёнка', 'из мягкой кожи ягнёнка', result)
    if 'vitello granato' in raw:
        result = re.sub(r'(?i)натуральной кожи красного цвета', 'ламинированной зернистой телячьей кожи', result)
        result = re.sub(r'(?i)натуральной зернистой телячьей кожи', 'зернистой телячьей кожи', result)
    if 'catena' in raw:
        result = re.sub(r'(?i)съёмного ремня(?=[,.;]|$)', 'съёмной цепочки', result)
    result = re.sub(r'(?i)сумк(?:у|а) можно носить как через плечо, так и через плечо', 'сумку можно носить через плечо', result)
    result = re.sub(r'(?i)через плечо или через плечо', 'через плечо', result)
    result = re.sub(r'(?i)на плече или через плечо', 'на плече или через плечо', result)
    result = re.sub(r'(?i)на плечо от ', 'через плечо ', result)
    result = re.sub(r'(?i)сумка маленького размера', 'маленькая сумка', result)
    result = re.sub(r'(?i)сумка среднего размера', 'сумка среднего размера', result)
    result = re.sub(r'(?i)заклёпками и закл[её]пками', 'заклёпками', result)
    result = re.sub(r'(?i)регулируемому и съёмному ремню', 'регулируемому съёмному ремню', result)
    result = re.sub(r'(?i)регулируемого и съёмного ремня', 'регулируемого съёмного ремня', result)
    result = re.sub(r'(?i)имеет дизайн в стиле пэчворк', 'выполнена в технике пэчворк', result)
    result = re.sub(r'(?i)на поверхности сумки присутствуют ', 'модель дополнена ', result)
    result = re.sub(r'(?i)на поверхности сумки имеется ', 'модель дополнена ', result)
    result = re.sub(r'(?i)на сумке имеется ', 'модель дополнена ', result)
    result = re.sub(r'(?i)на сумке имеются ', 'модель дополнена ', result)
    result = re.sub(r'(?i)особенностью этой сумки являются ', 'модель украшена ', result)
    result = re.sub(r'(?i)сумка отличается наличием ', 'сумка украшена ', result)
    result = re.sub(r'(?i)оснащена стёганой отделкой', 'Дополнена стёганой отделкой', result)
    result = re.sub(r'(?i)имеет стёганую отделку', 'Дополнена стёганой отделкой', result)
    result = re.sub(r'(?i)сумочка имеет стёганую отделку', 'Модель дополнена стёганой отделкой', result)
    result = re.sub(r'(?i)края сумки украшены заклёпками и заклёпками', 'Замшевая окантовка украшена сферическими заклёпками', result)
    result = result.replace(
        'вышивкой из стеклярусных элементов и стразов; дополнена кисточками',
        'вышивкой из зеркальных элементов из смолы и стразов; дополнена бахромой',
    )
    result = re.sub(r'(?i)от Valentino Garavani', 'Valentino Garavani', result)
    result = re.sub(r'\s+([,.;:])', r'\1', result)
    result = re.sub(r'([.!?])\s*[-–]\s*', r'\1 ', result)
    result = re.sub(r'\s{2,}', ' ', result).strip()
    result = re.sub(
        r'(?<=[.!?] )([а-яё])', lambda match: match.group(1).upper(), result,
    )
    if result:
        result = result[0].upper() + result[1:]
    return result


def _title_as_sentence(title):
    """Превращает каталожное название в полноценное первое предложение."""
    masculine = title.startswith(('Клатч', 'Кошелёк'))
    participle = 'выполнен' if masculine else 'выполнена'
    if ' из ' in title:
        return title.replace(' из ', f' {participle} из ', 1) + '.'
    return title + '.'


def generated_description(title, raw_description):
    """Собирает чистое русское описание только из фактов итальянского источника."""
    raw_text = _clean_spaces(raw_description)
    raw = raw_text.casefold()
    sentences = [_title_as_sentence(title)]

    if 'lavorazione trapuntata' in raw and 'borchi' in raw:
        sentences.append('Стёганая отделка украшена миниатюрными фирменными заклёпками.')
    elif 'lavorazione trapuntata' in raw:
        sentences.append('Модель дополнена выразительной стёганой отделкой.')
    elif 'maxi borchi' in raw:
        sentences.append('Дизайн дополняют крупные фирменные заклёпки.')
    elif 'borchie a sfera' in raw and 'rivetti' in raw:
        sentences.append('Отделка дополнена сферическими заклёпками и металлическими клёпками.')
    elif 'borchi' in raw:
        sentences.append('Дизайн дополняют фирменные заклёпки.')

    if 'teste di felino' in raw:
        sentences.append(
            'Модель украшена эмалированными головами кошек с кристаллами Swarovski®.'
        )
    if 'specchietti di resina' in raw:
        sentences.append(
            'Вышивка из зеркальных элементов из смолы и стразов дополнена бахромой.'
        )
    elif (
        'ricamo floreale' in raw
        and 'цветочн' not in title.casefold()
        and 'вышивк' not in title.casefold()
    ):
        sentences.append('Модель украшена цветочной вышивкой.')
    if 'dipinti a mano' in raw:
        sentences.append('Детали расписаны вручную.')
    if 'pietre cabochon sintetiche' in raw:
        sentences.append('Отделка дополнена синтетическими кабошонами.')
    if 'chiusura a gancio' in raw:
        sentences.append(
            'Знаковая застёжка-крючок переосмыслена как фиксатор бегунка молнии.'
        )
    elif 'chiusura con bottone magnetico' in raw:
        sentences.append('Предусмотрена магнитная застёжка.')
    elif 'chiusura vlogo signature' in raw and 'VLogo Signature' not in title:
        sentences.append('Дизайн завершает застёжка VLogo Signature.')
    elif 'elemento vlogo signature metallico' in raw and 'VLogo Signature' not in title:
        sentences.append('Модель дополнена металлическим элементом VLogo Signature.')

    carries_hand = bool(re.search(r'(?:portat|indossat)[oa] a mano|a mano grazie', raw))
    carries_shoulder = bool(re.search(r'a spalla|cross[ -]?body', raw))
    if carries_hand and carries_shoulder:
        sentences.append('Модель можно носить в руке, на плече или через плечо.')
    elif carries_shoulder:
        sentences.append('Модель можно носить на плече или через плечо.')
    elif carries_hand:
        sentences.append('Модель предназначена для ношения в руке.')

    if 'tasca interna a parete e tre vani porta carte' in raw:
        sentences.append('Внутри расположены плоский карман и три отделения для карт.')
    if 'fodera in nappa' in raw:
        sentences.append('Подкладка выполнена из кожи наппа.')
    dimensions = re.search(r'dimensioni:\s*w\s*(\d+)\s*x\s*h\s*(\d+)\s*x\s*d\s*(\d+)\s*cm', raw, re.I)
    if dimensions:
        width, height, depth = dimensions.groups()
        sentences.append(f'Размеры: {width} × {height} × {depth} см.')
    if 'made in italy' in raw:
        sentences.append('Изготовлено в Италии.')
    return ' '.join(sentences)


def editorial_issue_kinds(title, description):
    """Возвращает известные дефекты, блокирующие импорт."""
    combined = f'{title} {description}'
    issues = []
    if not title:
        issues.append('unrecognized_title')
    if not description:
        issues.append('empty_description')
    if description and description[0].islower():
        issues.append('description_starts_lowercase')
    if any(
        sentence and sentence[0].islower()
        for sentence in re.split(r'(?<=[.!?])\s+', description)
    ):
        issues.append('sentence_starts_lowercase')
    for kind, pattern in ISSUE_CHECKS:
        if re.search(pattern, combined, re.IGNORECASE):
            issues.append(kind)
    if re.search(r'\b(?:Borsa|Bauletto|Portafoglio|Vitello|Granato|Ricamata|Suede)\b', title, re.I):
        issues.append('italian_product_term_in_title')
    return issues


def editorialize_valentino_card(raw_card):
    """Возвращает карточку с синхронно отредактированными русскими полями."""
    card = deepcopy(raw_card)
    title_data = card.setdefault('title', {})
    description_data = card.setdefault('description', {})
    raw_title = _clean_spaces(title_data.get('raw'))
    raw_description = _clean_spaces(description_data.get('raw'))
    title = editorial_title(raw_title)
    description = generated_description(title, raw_description)
    title_data['raw'] = raw_title
    title_data['lang'] = 'it'
    title_data.setdefault('i18n', {})['ru'] = title
    description_data['raw'] = raw_description
    description_data['lang'] = 'it'
    description_data.setdefault('i18n', {})['ru'] = description
    issues = editorial_issue_kinds(title, description)
    return card, {'title': title, 'description': description, 'issues': issues}
