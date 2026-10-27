from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from time import monotonic
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from django.db import transaction


CBR_DAILY_RATES_URL = 'https://www.cbr.ru/scripts/XML_daily.asp'
PRICING_CACHE_TTL_SECONDS = 60
SUPPORTED_CURRENCIES = ('CNY', 'USD', 'EUR', 'RUB')
CURRENCY_ALIASES = {
    '¥': 'CNY',
    '￥': 'CNY',
    'RMB': 'CNY',
    'YUAN': 'CNY',
    '$': 'USD',
    'US$': 'USD',
    '€': 'EUR',
    '₽': 'RUB',
    'RUR': 'RUB',
}


@dataclass(frozen=True)
class PriceStep:
    name: str
    adjustment_type: str
    value: Decimal
    calculate_from_initial: bool
    amount: Decimal
    result: Decimal


@dataclass(frozen=True)
class PriceCalculation:
    source_amount: Decimal
    source_currency: str
    rate: Decimal
    base_rubles: Decimal
    steps: tuple[PriceStep, ...]
    total: Decimal


def normalize_currency(value):
    currency = str(value or '').strip().upper()
    currency = CURRENCY_ALIASES.get(currency, currency)
    return currency if currency in SUPPORTED_CURRENCIES else currency


def clear_pricing_cache():
    _pricing_snapshot.cache_clear()


@lru_cache(maxsize=2)
def _pricing_snapshot(cache_bucket):
    from .models import ExchangeRate, PricingRule

    rates = {
        item.currency: item.rubles_per_unit
        for item in ExchangeRate.objects.all()
    }
    rates['RUB'] = Decimal('1')
    rules = tuple(
        (item.name, item.adjustment_type, item.value, item.calculate_from_initial)
        for item in PricingRule.objects.filter(is_active=True).order_by('position', 'id')
    )
    return rates, rules


def calculate_price(amount, currency):
    try:
        source_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return None
    source_currency = normalize_currency(currency)
    cache_bucket = int(monotonic() // PRICING_CACHE_TTL_SECONDS)
    rates, rules = _pricing_snapshot(cache_bucket)
    rate = rates.get(source_currency)
    if rate is None:
        return None

    current = source_amount * rate
    base_rubles = current
    steps = []
    for name, adjustment_type, value, calculate_from_initial in rules:
        if adjustment_type == 'percent':
            percentage_base = base_rubles if calculate_from_initial else current
            addition = percentage_base * value / Decimal('100')
        else:
            addition = value
        current += addition
        steps.append(PriceStep(
            name, adjustment_type, value, calculate_from_initial, addition, current,
        ))

    total = current.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return PriceCalculation(
        source_amount=source_amount,
        source_currency=source_currency,
        rate=rate,
        base_rubles=base_rubles,
        steps=tuple(steps),
        total=total,
    )


def price_calculation_snapshot(calculation, pricing_mode='calculated'):
    """Готовит JSON-снимок формулы без потери точности Decimal."""
    def money_string(value):
        return format(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f')

    if calculation is None:
        return {'version': 1, 'pricing_mode': pricing_mode, 'steps': []}
    return {
        'version': 1,
        'pricing_mode': pricing_mode,
        'source_amount': money_string(calculation.source_amount),
        'source_currency': calculation.source_currency,
        'rate': str(calculation.rate),
        'base_rubles': money_string(calculation.base_rubles),
        'total': money_string(calculation.total),
        'steps': [
            {
                'name': step.name,
                'adjustment_type': step.adjustment_type,
                'value': str(step.value),
                'calculate_from_initial': step.calculate_from_initial,
                'amount': money_string(step.amount),
                'result': money_string(step.result),
            }
            for step in calculation.steps
        ],
    }


def fetch_cbr_rates(timeout=15):
    request = Request(CBR_DAILY_RATES_URL, headers={'User-Agent': 'AURENE/1.0'})
    with urlopen(request, timeout=timeout) as response:
        content = response.read()
    root = ElementTree.fromstring(content)
    rate_date = datetime.strptime(root.attrib['Date'], '%d.%m.%Y').date()
    rates = {'RUB': Decimal('1')}
    for node in root.findall('Valute'):
        currency = node.findtext('CharCode')
        if currency not in {'CNY', 'USD', 'EUR'}:
            continue
        nominal = Decimal(node.findtext('Nominal'))
        value = Decimal(node.findtext('Value').replace(',', '.'))
        rates[currency] = value / nominal
    missing = {'CNY', 'USD', 'EUR'} - rates.keys()
    if missing:
        raise ValueError(f'В ответе Банка России отсутствуют валюты: {", ".join(sorted(missing))}.')
    return rate_date, rates


@transaction.atomic
def update_cbr_rates(timeout=15):
    from .models import ExchangeRate

    rate_date, rates = fetch_cbr_rates(timeout=timeout)
    for currency, value in rates.items():
        ExchangeRate.objects.update_or_create(
            currency=currency,
            defaults={
                'rubles_per_unit': value,
                'rate_date': rate_date,
                'source': 'Банк России',
            },
        )
    clear_pricing_cache()
    return rate_date, rates