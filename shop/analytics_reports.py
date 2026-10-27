"""Расчёты коммерческой аналитики для backoffice dashboard."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from .models import AnalyticsEvent, MarketingCampaignSpend, Order, OrderItem, ProductRequest


# События серверной выдачи страницы (cart_view и подобные) не подтверждают
# запуск браузерного счётчика: поисковые роботы тоже вызывают эти views.
CONFIRMED_VISIT_EVENTS = (
    'page_view', 'catalog_view', 'brand_view', 'category_view', 'product_view',
    'internal_search', 'filter_use', 'sort_use', 'product_click',
    'product_photo_view', 'size_select', 'color_select', 'scroll_25',
    'scroll_50', 'scroll_75', 'scroll_100', 'session_engaged',
    'personal_order_start', 'checkout_delivery', 'checkout_contacts',
)


def qualified_analytics_sessions(queryset):
    """Не включаем сессии сканеров без клиентских событий или конверсий."""
    tracked_events = AnalyticsEvent.objects.filter(
        session_id=OuterRef('pk'), event_type__in=CONFIRMED_VISIT_EVENTS,
        path__gt='',
    )
    session_leads = ProductRequest.objects.filter(analytics_session_id=OuterRef('pk'))
    session_orders = Order.objects.filter(analytics_session_id=OuterRef('pk'))
    return queryset.annotate(
        has_tracked_event=Exists(tracked_events),
        has_lead=Exists(session_leads),
        has_order=Exists(session_orders),
    ).filter(Q(has_tracked_event=True) | Q(has_lead=True) | Q(has_order=True))


def percent(value, total):
    return round(value * 100 / total, 1) if total else 0


def comparison(current, previous):
    if not previous:
        return None if not current else 100
    return round((current - previous) * 100 / previous, 1)


def identity_count(sessions):
    """Считает аккаунт один раз, а незалогиненного посетителя — по first-party cookie."""
    identities = set()
    for user_id, visitor_user_id, visitor_id in sessions.values_list(
        'user_id', 'visitor__user_id', 'visitor_id'
    ):
        account_id = user_id or visitor_user_id
        identities.add(('user', account_id) if account_id else ('visitor', visitor_id))
    return len(identities)


def report_period(params, now=None):
    """Возвращает единый полуоткрытый диапазон для preset или произвольных дат."""
    now = now or timezone.localtime()
    try:
        days = int(params.get('days', 30))
    except (TypeError, ValueError):
        days = 30
    days = days if days in (1, 7, 30, 90, 365) else 30
    try:
        start_date = date.fromisoformat(params.get('date_from', ''))
        end_date = date.fromisoformat(params.get('date_to', ''))
        if end_date < start_date or (end_date - start_date).days > 730:
            raise ValueError
        since = timezone.make_aware(datetime.combine(start_date, time.min))
        until = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        custom = True
    except (TypeError, ValueError):
        end_date = now.date()
        start_date = end_date - timedelta(days=days - 1)
        since = timezone.make_aware(datetime.combine(start_date, time.min))
        until = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        custom = False
    duration = max(until - since, timedelta(seconds=1))
    return {
        'days': days, 'since': since, 'until': until,
        'previous_since': since - duration, 'previous_until': since,
        'start_date': start_date, 'end_date': end_date, 'custom_period': custom,
        'period_label': (
            f'{start_date:%d.%m.%Y}–{end_date:%d.%m.%Y}' if custom
            else 'Сегодня' if days == 1 else f'{days} дней'
        ),
    }


def _allocated_amount(spend, start_date, end_date):
    overlap_start = max(spend.period_start, start_date)
    overlap_end = min(spend.period_end, end_date)
    overlap_days = max(0, (overlap_end - overlap_start).days + 1)
    campaign_days = max(1, (spend.period_end - spend.period_start).days + 1)
    return (spend.amount * overlap_days / campaign_days).quantize(Decimal('0.01'))


def _period_spends(start_date, end_date):
    spends = list(MarketingCampaignSpend.objects.filter(
        period_end__gte=start_date, period_start__lte=end_date,
    ))
    for spend in spends:
        spend.allocated_amount = _allocated_amount(spend, start_date, end_date)
    return spends


def economics_breakdown(
    revenue, cost_of_goods, total_spend, net_profit, costs_complete,
    pricing_components=None,
):
    """Делит выручку (либо расходы при убытке) на взаимоисключающие части."""
    empty = {
        'economics_chart_ready': False,
        'economics_chart_total': revenue,
        'economics_chart_label': 'Выручка',
        'economics_loss': None,
        'economics_breakdown': [],
        'economics_chart': {'labels': [], 'values': [], 'colors': []},
    }
    if not costs_complete or cost_of_goods is None or net_profit is None:
        return empty

    pricing_components = pricing_components or []
    palette = ('#866f62', '#c39b77', '#7f8c8d', '#8b7891', '#9b805d', '#65828a')
    formula_components = [
        (item['label'], item['amount'], palette[index % len(palette)])
        for index, item in enumerate(pricing_components)
    ]
    expense_components = [
        ('Себестоимость товаров', cost_of_goods, '#181715'),
        *formula_components,
        ('Реклама', total_spend, '#b08c83'),
    ]
    loss = max(-net_profit, Decimal('0'))
    if loss:
        total = sum((amount for _, amount, _ in expense_components), Decimal('0'))
        label = 'Все расходы'
        components = expense_components
    else:
        total = revenue
        label = 'Выручка'
        components = [
            *expense_components,
            ('Чистая прибыль', net_profit, '#49694c'),
        ]
    rows = [
        {
            'label': component_label,
            'amount': amount,
            'percent': percent(amount, total),
            'color': color,
        }
        for component_label, amount, color in components
    ]
    return {
        'economics_chart_ready': total > 0,
        'economics_chart_total': total,
        'economics_chart_label': label,
        'economics_loss': loss or None,
        'economics_breakdown': rows,
        # Canvas должен получать числа, иначе Decimal сериализуется строкой.
        'economics_chart': {
            'labels': [row['label'] for row in rows],
            'values': [float(row['amount']) for row in rows],
            'colors': [row['color'] for row in rows],
        },
    }


def _order_pricing_components(paid_orders):
    """Суммирует строки сохранённых формул, не применяя текущие правила к истории."""
    order_ids = list(paid_orders.values_list('id', flat=True))
    if not order_ids:
        return [], True
    items = list(OrderItem.objects.filter(order_id__in=order_ids).only(
        'order_id', 'quantity', 'price_breakdown',
    ))
    covered_orders = set()
    amounts = {}
    for item in items:
        snapshot = item.price_breakdown
        if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
            return [], False
        covered_orders.add(item.order_id)
        steps = snapshot.get('steps', [])
        if not isinstance(steps, list):
            return [], False
        for step in steps:
            try:
                name = str(step['name']).strip()
                amount = Decimal(str(step['amount'])) * item.quantity
            except (InvalidOperation, KeyError, TypeError, ValueError):
                return [], False
            if not name or not amount.is_finite() or amount < 0:
                return [], False
            amounts[name] = amounts.get(name, Decimal('0')) + amount
    if covered_orders != set(order_ids):
        return [], False
    return [
        {'label': name, 'amount': amount}
        for name, amount in amounts.items()
    ], True


def commercial_context(
    period, sessions, leads, events=None, previous_sessions=None, previous_leads=None,
):
    """Фактическая экономика по оплатам и last-touch рекламная атрибуция."""
    since, until = period['since'], period['until']
    previous_since, previous_until = period['previous_since'], period['previous_until']
    paid_orders = Order.objects.filter(paid_at__gte=since, paid_at__lt=until)
    previous_orders = Order.objects.filter(
        paid_at__gte=previous_since, paid_at__lt=previous_until,
    )
    spends = _period_spends(period['start_date'], period['end_date'])
    total_spend = sum((spend.allocated_amount for spend in spends), Decimal('0'))

    previous_start_date = timezone.localtime(previous_since).date()
    previous_end_date = timezone.localtime(
        previous_until - timedelta(microseconds=1),
    ).date()
    previous_spends = _period_spends(previous_start_date, previous_end_date)
    previous_spend = sum(
        (spend.allocated_amount for spend in previous_spends), Decimal('0'),
    )

    order_count = paid_orders.count()
    revenue = paid_orders.aggregate(total=Sum('actual_revenue'))['total'] or Decimal('0')
    previous_revenue = (
        previous_orders.aggregate(total=Sum('actual_revenue'))['total'] or Decimal('0')
    )
    previous_order_count = previous_orders.count()
    costs_complete = order_count > 0 and not paid_orders.filter(
        Q(actual_revenue__isnull=True) | Q(cost_of_goods__isnull=True),
    ).exists()
    cost_of_goods = (
        paid_orders.aggregate(total=Sum('cost_of_goods'))['total'] or Decimal('0')
        if costs_complete else None
    )
    pricing_components, pricing_details_complete = _order_pricing_components(paid_orders)
    pricing_expenses = (
        sum((item['amount'] for item in pricing_components), Decimal('0'))
        if pricing_details_complete else None
    )
    gross_profit = revenue - cost_of_goods if cost_of_goods is not None else None
    profit_after_ads = gross_profit - total_spend if gross_profit is not None else None
    net_profit = (
        profit_after_ads - pricing_expenses
        if profit_after_ads is not None and pricing_expenses is not None else None
    )
    previous_costs_complete = previous_order_count > 0 and not previous_orders.filter(
        Q(actual_revenue__isnull=True) | Q(cost_of_goods__isnull=True),
    ).exists()
    previous_cost = (
        previous_orders.aggregate(total=Sum('cost_of_goods'))['total'] or Decimal('0')
        if previous_costs_complete else None
    )
    previous_profit = (
        previous_revenue - previous_cost if previous_cost is not None else None
    )
    visitors = identity_count(sessions)
    lead_count = leads.count()
    paying_customers = len({
        ('user', order.user_id) if order.user_id else (
            'email', order.customer_email.casefold()
        )
        for order in paid_orders
    })

    context = {
        'paid_orders': paid_orders, 'order_count': order_count,
        'revenue': revenue, 'average_order': revenue / order_count if order_count else None,
        'cost_of_goods': cost_of_goods, 'costs_complete': costs_complete,
        'gross_profit': gross_profit, 'profit_after_ads': profit_after_ads,
        'net_profit': net_profit, 'pricing_components': pricing_components,
        'pricing_expenses': pricing_expenses,
        'pricing_details_complete': pricing_details_complete,
        'total_spend': total_spend, 'spends': spends[:20],
        'lead_cr': percent(lead_count, visitors),
        'order_cr': percent(order_count, visitors),
        'cpl': total_spend / lead_count if lead_count else None,
        'cac': total_spend / paying_customers if paying_customers else None,
        'roas': revenue / total_spend if total_spend else None,
        'paying_customers': paying_customers,
        'commercial_comparison': {
            'orders': comparison(order_count, previous_order_count),
            'revenue': comparison(revenue, previous_revenue),
            'average_order': comparison(
                revenue / order_count if order_count else 0,
                previous_revenue / previous_order_count if previous_order_count else 0,
            ),
            'gross_profit': (
                comparison(gross_profit, previous_profit)
                if gross_profit is not None and previous_profit is not None else None
            ),
            'roas': comparison(
                revenue / total_spend if total_spend else 0,
                previous_revenue / previous_spend if previous_spend else 0,
            ),
        },
    }
    context.update(economics_breakdown(
        revenue, cost_of_goods, total_spend, net_profit,
        costs_complete and pricing_details_complete,
        pricing_components=pricing_components,
    ))
    if previous_sessions is not None and previous_leads is not None:
        previous_visitors = identity_count(previous_sessions)
        context['commercial_comparison'].update({
            'lead_cr': comparison(
                percent(lead_count, visitors),
                percent(previous_leads.count(), previous_visitors),
            ),
            'order_cr': comparison(
                percent(order_count, visitors),
                percent(previous_order_count, previous_visitors),
            ),
        })
    context.update(_campaign_report(sessions, leads, paid_orders, spends))
    context.update(_crm_report(leads, since, until))
    if events is not None:
        context.update(_catalog_report(events, leads, paid_orders))
        context.update(_search_report(events, paid_orders))
    return context


def _touch_key(source='', campaign='', content='', ad_id=''):
    return (source or 'direct', campaign or '', content or '', ad_id or '')


def _campaign_report(sessions, leads, paid_orders, spends):
    rows = defaultdict(lambda: {
        'sessions': 0, 'visitor_ids': set(), 'leads': 0, 'sales': 0,
        'customer_ids': set(), 'revenue': Decimal('0'),
        'gross_profit': Decimal('0'), 'costs_complete': True,
        'spend': Decimal('0'),
    })
    for item in sessions.values(
        'source', 'campaign', 'content', 'ad_id', 'visitor_id',
    ):
        row = rows[_touch_key(
            item['source'], item['campaign'], item['content'], item['ad_id'],
        )]
        row['sessions'] += 1
        row['visitor_ids'].add(item['visitor_id'])
    for item in leads.values('source', 'utm_campaign', 'utm_content', 'ad_id'):
        rows[_touch_key(
            item['source'], item['utm_campaign'], item['utm_content'], item['ad_id'],
        )]['leads'] += 1
    for order in paid_orders:
        row = rows[_touch_key(
            order.source, order.utm_campaign, order.utm_content, order.ad_id,
        )]
        row['sales'] += 1
        row['customer_ids'].add(order.user_id or order.customer_email.casefold())
        row['revenue'] += order.actual_revenue or Decimal('0')
        if order.gross_profit is None:
            row['costs_complete'] = False
        else:
            row['gross_profit'] += order.gross_profit
    for spend in spends:
        rows[_touch_key(
            spend.source, spend.campaign, spend.content, spend.ad_id,
        )]['spend'] += spend.allocated_amount

    campaigns = []
    for key, values in rows.items():
        visitors = len(values.pop('visitor_ids'))
        customers = len(values.pop('customer_ids'))
        row = dict(zip(('source', 'campaign', 'content', 'ad_id'), key), **values)
        profit = row['gross_profit'] - row['spend'] if row['costs_complete'] else None
        row.update({
            'visitors': visitors, 'customers': customers,
            'cr': percent(row['leads'], visitors),
            'cpl': row['spend'] / row['leads'] if row['leads'] else None,
            'cac': row['spend'] / customers if customers else None,
            'roas': row['revenue'] / row['spend'] if row['spend'] else None,
            'profit': profit,
        })
        campaigns.append(row)
    campaigns.sort(
        key=lambda row: (row['revenue'], row['leads'], row['sessions']), reverse=True,
    )

    first_rows = defaultdict(lambda: {
        'leads': 0, 'sales': 0, 'revenue': Decimal('0'),
    })
    for item in leads.values(
        'first_source', 'first_utm_campaign', 'first_utm_content', 'first_ad_id',
    ):
        first_rows[_touch_key(
            item['first_source'], item['first_utm_campaign'],
            item['first_utm_content'], item['first_ad_id'],
        )]['leads'] += 1
    for order in paid_orders:
        row = first_rows[_touch_key(
            order.first_source, order.first_utm_campaign,
            order.first_utm_content, order.first_ad_id,
        )]
        row['sales'] += 1
        row['revenue'] += order.actual_revenue or Decimal('0')
    first_touch = [
        dict(zip(('source', 'campaign', 'content', 'ad_id'), key), **values)
        for key, values in first_rows.items()
    ]
    first_touch.sort(key=lambda row: (row['revenue'], row['leads']), reverse=True)
    return {'campaign_report': campaigns[:30], 'first_touch_report': first_touch[:20]}


def _crm_report(leads, since, until):
    lead_count = leads.count()
    funnel_values = [
        ('Новые лиды', lead_count),
        ('Связались', leads.filter(contacted_at__isnull=False).count()),
        ('Квалифицировали', leads.filter(qualified_at__isnull=False).count()),
        ('Сделали предложение', leads.filter(offered_at__isnull=False).count()),
        ('Создали заказ', leads.filter(ordered_at__isnull=False).count()),
        ('Оплатили', leads.filter(converted_order__paid_at__isnull=False).count()),
    ]
    crm_funnel = [
        {'label': label, 'value': value, 'percent': percent(value, lead_count)}
        for label, value in funnel_values
    ]
    loss_reasons = list(leads.exclude(loss_reason='').values(
        'loss_reason',
    ).annotate(total=Count('id')).order_by('-total'))
    labels = dict(ProductRequest.LossReason.choices)
    for row in loss_reasons:
        row['label'] = labels.get(row['loss_reason'], row['loss_reason'])
        row['percent'] = percent(row['total'], lead_count)
    return {'crm_funnel': crm_funnel, 'loss_reasons': loss_reasons}


def _order_item_revenue(item, order_totals):
    total = order_totals.get(item.order_id, Decimal('0'))
    if not total:
        return Decimal('0')
    return (item.order.actual_revenue or Decimal('0')) * item.subtotal / total


def _order_item_profit(item, order_totals):
    total = order_totals.get(item.order_id, Decimal('0'))
    if not total or item.order.gross_profit is None:
        return None
    return item.order.gross_profit * item.subtotal / total


def _catalog_report(events, leads, paid_orders):
    product_rows = {
        row['product_id']: {
            **row, 'cart_adds': 0, 'orders': 0, 'revenue': Decimal('0'),
            'profit': Decimal('0'), 'profit_complete': True,
        }
        for row in events.filter(
            event_type='product_view', product__isnull=False,
        ).values(
            'product_id', 'product__title', 'product__sku_without_size',
            'product__brand',
        ).annotate(
            views=Count('id'), visitors=Count('visitor_id', distinct=True),
        )
    }
    for row in events.filter(
        event_type='cart_add', product_id__in=product_rows,
    ).values('product_id').annotate(total=Count('id')):
        product_rows[row['product_id']]['cart_adds'] = row['total']

    items = list(OrderItem.objects.filter(
        order__in=paid_orders,
    ).select_related('order'))
    order_totals = defaultdict(Decimal)
    for item in items:
        order_totals[item.order_id] += item.subtotal
    for item in items:
        if not item.product_id:
            continue
        if item.product_id not in product_rows:
            product_rows[item.product_id] = {
                'product_id': item.product_id,
                'product__title': item.product_title,
                'product__sku_without_size': item.sku,
                'product__brand': item.brand,
                'views': 0, 'visitors': 0, 'cart_adds': 0,
                'orders': 0, 'revenue': Decimal('0'),
                'profit': Decimal('0'), 'profit_complete': True,
            }
        row = product_rows[item.product_id]
        row['orders'] += 1
        row['revenue'] += _order_item_revenue(item, order_totals)
        item_profit = _order_item_profit(item, order_totals)
        if item_profit is None:
            row['profit_complete'] = False
        else:
            row['profit'] += item_profit
    products = list(product_rows.values())
    for row in products:
        row['cr'] = percent(row['orders'], row['visitors'])
        if not row.pop('profit_complete'):
            row['profit'] = None
    products.sort(key=lambda row: (row['revenue'], row['views']), reverse=True)

    brands = defaultdict(lambda: {
        'views': 0, 'visitor_ids': set(), 'leads': 0,
        'orders': set(), 'revenue': Decimal('0'),
        'profit': Decimal('0'), 'profit_complete': True,
    })
    for event in events.filter(
        event_type__in=('brand_view', 'product_view'),
    ).exclude(brand='').values('brand', 'visitor_id'):
        row = brands[event['brand']]
        row['views'] += 1
        row['visitor_ids'].add(event['visitor_id'])
    for lead in leads.prefetch_related('items'):
        for brand in {item.brand for item in lead.items.all() if item.brand}:
            brands[brand]['leads'] += 1
    for item in items:
        brand = item.brand or 'Без бренда'
        brands[brand]['orders'].add(item.order_id)
        brands[brand]['revenue'] += _order_item_revenue(item, order_totals)
        item_profit = _order_item_profit(item, order_totals)
        if item_profit is None:
            brands[brand]['profit_complete'] = False
        else:
            brands[brand]['profit'] += item_profit
    brand_report = []
    for brand, values in brands.items():
        visitors = len(values.pop('visitor_ids'))
        orders = len(values.pop('orders'))
        if not values.pop('profit_complete'):
            values['profit'] = None
        brand_report.append({
            'brand': brand, 'visitors': visitors, 'orders': orders,
            'cr': percent(orders, visitors), **values,
        })
    brand_report.sort(key=lambda row: (row['revenue'], row['views']), reverse=True)
    return {'product_report': products[:30], 'brand_report': brand_report[:30]}


def _search_report(events, paid_orders):
    rows = list(events.filter(event_type='internal_search').values(
        'metadata__query', 'metadata__result_count',
    ).annotate(
        total=Count('id'),
        searchers=Count('visitor_id', distinct=True),
    ).order_by('-total')[:30])
    clicks_by_session = defaultdict(list)
    for session_id, created_at in events.filter(
        event_type='product_click',
    ).values_list('session_id', 'created_at'):
        clicks_by_session[session_id].append(created_at)
    paid_by_visitor = defaultdict(list)
    for visitor_id, paid_at in paid_orders.exclude(
        analytics_visitor_id=None,
    ).values_list('analytics_visitor_id', 'paid_at'):
        paid_by_visitor[visitor_id].append(paid_at)
    searches = events.filter(event_type='internal_search').values(
        'metadata__query', 'metadata__result_count', 'session_id', 'visitor_id',
        'created_at',
    )
    grouped = defaultdict(lambda: {'clicked': set(), 'converted': set()})
    for event in searches:
        key = (event['metadata__query'], event['metadata__result_count'])
        if any(
            clicked_at >= event['created_at']
            for clicked_at in clicks_by_session.get(event['session_id'], ())
        ):
            grouped[key]['clicked'].add(event['session_id'])
        if any(
            paid_at >= event['created_at']
            for paid_at in paid_by_visitor.get(event['visitor_id'], ())
        ):
            grouped[key]['converted'].add(event['visitor_id'])
    for row in rows:
        values = grouped[(row['metadata__query'], row['metadata__result_count'])]
        row['clicks'] = len(values['clicked'])
        row['sales'] = len(values['converted'])
        row['click_rate'] = percent(row['clicks'], row['total'])
        row['conversion_rate'] = percent(row['sales'], row['searchers'])
    return {
        'search_report': rows,
        'zero_search_report': [row for row in rows if not row['metadata__result_count']],
    }