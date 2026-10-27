from django import template

from shop.statuses import status_icon as get_status_icon

register = template.Library()


@register.filter
def money(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return f'{number:,.0f}'.replace(',', ' ')


@register.filter
def currency_symbol(code):
    return {'CNY': '¥', 'RUB': '₽', 'EUR': '€', 'USD': '$'}.get(code, code)


@register.filter
def status_icon(value):
    return get_status_icon(value)


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ''):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()