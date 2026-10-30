"""Собственная рабочая панель FINDER, независимая от шаблонов Django Admin."""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from math import ceil
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .availability import available_products
from .analytics_reports import (
    commercial_context, identity_count, qualified_analytics_sessions, report_period,
)
from .forms import (
    BannerForm,
    BannerImageFormSet,
    BackofficeOrderForm,
    BackofficeProductForm,
    BackofficeProductImageFormSet,
    BackofficeProductRequestForm,
    CollectionBatchForm,
    CollectionDraftFormSet,
    CatalogBrandForm,
    CatalogCategoryForm,
    CatalogColorForm,
    CatalogMaterialForm,
    CatalogSizeForm,
    ExchangeRateFormSet,
    MarketingCampaignSpendForm,
    PricePreviewForm,
    PricingRuleFormSet,
    ProductImportForm,
    ProductZipImportForm,
    SecurityCodeForm,
    SizeProfileForm,
    WeeklyLookForm,
    WeeklyLookItemFormSet,
)
from .models import (
    AnalyticsEvent, AnalyticsSession, ApiRequestLog, Banner, BannerImage, CartItem, CatalogBrand,
    CatalogCategory, CatalogColor, CatalogMaterial, CollectionBatch,
    CustomerProfile, ExchangeRate, Order, PricingRule, Product,
    ProductImage, ProductRequest, ErrorLog, SecurityChallenge, SizeProfile, WeeklyLook, WeeklyLookItem,
)
from .pricing import calculate_price, update_cbr_rates
from .services import import_product, import_zip_archive, parse_json_file
from telegram_bot.forms import ManagerContactFormSet
from telegram_bot.models import ManagerContact, TelegramAccount
from .security import (
    SecurityChallengeDeliveryError, create_security_challenge,
    verify_security_challenge,
)
from .session_security import invalidate_user_sessions


PRODUCT_LIST_DEFERRED_FIELDS = (
    'source_url', 'description', 'dimensions', 'made_in', 'color_label',
    'product_detail_html', 'care_instruction_html', 'story_behind',
    'categories_breadcrumbs', 'category_path', 'category_keys', 'material',
    'i18n', 'raw_data',
)


def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse('shop:login')
            return redirect(f'{login_url}?next={request.get_full_path()}')
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def permission_required(permission):
    def decorator(view):
        @staff_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.has_perm(permission):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def _page(request, queryset, per_page=30):
    return Paginator(queryset, per_page).get_page(request.GET.get('page'))


@permission_required('telegram_bot.view_managercontact')
def manager_contacts(request):
    can_change = request.user.has_perm('telegram_bot.change_managercontact')
    can_add = request.user.has_perm('telegram_bot.add_managercontact')
    can_delete = request.user.has_perm('telegram_bot.delete_managercontact')
    if request.method == 'POST' and not (can_change and can_add and can_delete):
        raise PermissionDenied
    queryset = ManagerContact.objects.order_by('position', 'pk')
    formset = ManagerContactFormSet(request.POST or None, queryset=queryset)
    if request.method == 'POST' and formset.is_valid():
        formset.save()
        messages.success(request, 'Способы связи сохранены.')
        return redirect('shop:control_manager_contacts')
    return render(request, 'shop/backoffice/manager_contacts.html', {
        'active_section': 'manager_contacts',
        'formset': formset,
        'can_change': can_change and can_add and can_delete,
    })


@permission_required('telegram_bot.view_telegramaccount')
def telegram_accounts(request):
    accounts = TelegramAccount.objects.select_related('user').order_by('user__username')
    return render(request, 'shop/backoffice/telegram_accounts.html', {
        'active_section': 'telegram',
        'accounts': accounts,
        'bot_username': settings.TELEGRAM_BOT_USERNAME,
        'can_add': request.user.has_perm('telegram_bot.add_telegramaccount'),
        'technical_add_url': reverse('admin:telegram_bot_telegramaccount_add'),
    })


@staff_required
def admin_grant_start(request, pk, grant_type):
    if not request.user.is_superuser or request.method != 'POST':
        raise PermissionDenied
    if grant_type not in {'web', 'telegram'}:
        raise PermissionDenied
    target = get_object_or_404(get_user_model(), pk=pk, is_active=True)
    if grant_type == 'telegram' and not TelegramAccount.objects.filter(user=target).exists():
        messages.error(request, 'Сначала пользователь должен безопасно привязать Telegram в личном кабинете.')
        return redirect('shop:control_user_detail', pk=target.pk)
    purpose = (
        SecurityChallenge.Purpose.WEB_ADMIN_GRANT
        if grant_type == 'web' else SecurityChallenge.Purpose.TELEGRAM_ADMIN_GRANT
    )
    try:
        challenge = create_security_challenge(
            target_user=request.user,
            requested_by=request.user,
            purpose=purpose,
            context={'grant_user_id': target.pk, 'grant_type': grant_type},
        )
    except (SecurityChallengeDeliveryError, ValueError):
        messages.error(request, 'Не удалось отправить код подтверждения. Проверьте e-mail администратора.')
        return redirect('shop:control_user_detail', pk=target.pk)
    request.session['pending_admin_grant'] = str(challenge.pk)
    return redirect('shop:control_admin_grant_verify')


@staff_required
def admin_grant_verify(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    challenge_id = request.session.get('pending_admin_grant')
    challenge = SecurityChallenge.objects.filter(
        pk=challenge_id, requested_by=request.user, consumed_at__isnull=True,
    ).first()
    if challenge is None:
        messages.warning(request, 'Запрос назначения истёк. Начните операцию заново.')
        return redirect('shop:control_users')
    target = get_object_or_404(get_user_model(), pk=challenge.context.get('grant_user_id'))
    form = SecurityCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        verified, message = verify_security_challenge(
            challenge.pk, form.cleaned_data['code'],
            purpose=challenge.purpose, target_user_id=request.user.pk,
        )
        if verified:
            grant_type = challenge.context.get('grant_type')
            if grant_type == 'web':
                target.is_staff = True
                target.is_superuser = True
                target.save(update_fields=('is_staff', 'is_superuser'))
            elif grant_type == 'telegram':
                TelegramAccount.objects.filter(user=target).update(
                    role=TelegramAccount.Role.ADMINISTRATOR,
                )
            invalidate_user_sessions(target.pk)
            request.session.pop('pending_admin_grant', None)
            messages.success(request, f'Административный доступ для {target.email or target.username} подтверждён.')
            return redirect('shop:control_user_detail', pk=target.pk)
        form.add_error('code', message)
    return render(request, 'shop/backoffice/admin_grant_verify.html', {
        'active_section': 'users', 'form': form, 'target': target,
        'grant_type': challenge.context.get('grant_type'),
    })


@staff_required
def error_logs(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method == 'POST':
        log = get_object_or_404(ErrorLog, pk=request.POST.get('log_id'))
        log.resolved = not log.resolved
        log.save(update_fields=('resolved',))
        messages.success(request, 'Статус ошибки обновлён.')
        return redirect('shop:control_error_logs')
    queryset = ErrorLog.objects.all()
    state = request.GET.get('state', 'open')
    if state == 'open':
        queryset = queryset.filter(resolved=False)
    elif state == 'resolved':
        queryset = queryset.filter(resolved=True)
    return render(request, 'shop/backoffice/error_logs.html', {
        'active_section': 'errors', 'logs': _page(request, queryset, 50), 'state': state,
    })


def _product_reference_context():
    return {
        'brand_options': CatalogBrand.objects.filter(is_active=True),
        'color_options': CatalogColor.objects.filter(is_active=True),
        'category_options': CatalogCategory.objects.filter(is_active=True),
    }


def _product_image_forms(request, product):
    """Показывает существующие фото и только одну форму для нового."""
    return BackofficeProductImageFormSet(
        request.POST or None, request.FILES or None, instance=product, prefix='images'
    )


def _analytics_page_name(value):
    """Человекочитаемое имя URL для отчёта переходов."""
    parsed = urlparse(value or '')
    path = parsed.path or '/'
    query = parse_qs(parsed.query)
    if path == '/':
        return 'Главная'
    if path.startswith('/cart/checkout'):
        return 'Оформление заказа'
    if path.startswith('/cart'):
        return 'Корзина'
    if '/request' in path or '/personal-order' in path:
        return 'Персональный заказ'
    if '/product/' in path:
        return 'Карточка товара'
    if '/catalog' in path:
        details = query.get('brand') or query.get('category') or query.get('q')
        return f'Каталог · {details[0]}' if details else 'Каталог'
    return path.strip('/').replace('-', ' ').capitalize() or 'Главная'


def _analytics_filter_name(filters):
    labels = {
        'gender': ('Пол', {'women': 'Женское', 'men': 'Мужское'}),
        'category': ('Категория', {}), 'brand': ('Бренд', {}),
        'color': ('Цвет', {}), 'material': ('Материал', {}),
        'price_min': ('Цена от', {}), 'price_max': ('Цена до', {}),
        'collection': ('Коллекция', {}),
    }
    parts = []
    for key, value in (filters or {}).items():
        label, choices = labels.get(key, (key, {}))
        display = choices.get(str(value), str(value))
        if key in {'price_min', 'price_max'}:
            display = f'{display} ₽'
        parts.append(f'{label}: {display}')
    return ' + '.join(parts) or 'Без фильтров'


def _qualified_analytics_sessions(queryset):
    """Совместимая точка входа для отчётов и карточки пользователя."""
    return qualified_analytics_sessions(queryset)


def _stored_formset_files(formset, model, field_name):
    """Запоминает старые файлы до удаления или замены объектов формсетом."""
    object_ids = [form.instance.pk for form in formset.forms if form.instance.pk]
    storage = model._meta.get_field(field_name).storage
    names = model.objects.filter(pk__in=object_ids).values_list(field_name, flat=True)
    return tuple((storage, name) for name in names if name)


def _delete_unreferenced_media_files(files, model, field_name):
    """Удаляет файл после commit, только когда живых ссылок на него больше нет."""
    for storage, name in files:
        if name and not model.objects.filter(**{field_name: name}).exists():
            storage.delete(name)


@staff_required
def dashboard(request):
    products = Product.objects.all()
    orders = Order.objects.all()
    requests = ProductRequest.objects.all()
    product_metrics = products.aggregate(
        product_count=Count('id'),
        published_count=Count('id', filter=Q(is_active=True)),
        out_of_stock_count=Count('id', filter=Q(in_stock_ecom=False)),
    )
    order_metrics = orders.aggregate(
        new_order_count=Count('id', filter=Q(status=Order.Status.NEW)),
        open_order_count=Count(
            'id', filter=~Q(status__in=(Order.Status.COMPLETED, Order.Status.CANCELLED)),
        ),
    )
    request_metrics = requests.aggregate(
        new_request_count=Count('id', filter=Q(status=ProductRequest.Status.NEW)),
        open_request_count=Count(
            'id', filter=~Q(
                status__in=(ProductRequest.Status.COMPLETED, ProductRequest.Status.CANCELLED),
            ),
        ),
    )
    context = {
        'active_section': 'dashboard',
        **product_metrics,
        **order_metrics,
        **request_metrics,
        'customer_count': get_user_model().objects.filter(is_staff=False).count(),
        'recent_orders': orders.prefetch_related('items')[:6],
        'recent_requests': requests.prefetch_related('items')[:6],
    }
    return render(request, 'shop/backoffice/dashboard.html', context)


@permission_required('shop.view_product')
def catalog_updates(request):
    """Сводка готовности и активности внешнего обновления цен и наличия."""
    now = timezone.now()
    since = now - timedelta(hours=24)
    batch_size = 100
    products = Product.objects.filter(catalog_scope=Product.CatalogScope.STORE)
    published = products.filter(is_active=True)
    available_by_brand = dict(
        available_products(published).values('brand').annotate(total=Count('id')).values_list(
            'brand', 'total'
        )
    )
    brand_names = dict(CatalogBrand.objects.values_list('slug', 'name'))
    brand_rows = []
    for row in products.values('brand').annotate(
        total=Count('id'),
        published=Count('id', filter=Q(is_active=True)),
        changed_24h=Count('id', filter=Q(updated_at__gte=since)),
        last_changed_at=Max('updated_at'),
    ).order_by('brand'):
        available = available_by_brand.get(row['brand'], 0)
        row.update({
            'name': brand_names.get(row['brand'], row['brand'].replace('-', ' ').title()),
            'available': available,
            'unavailable': max(0, row['published'] - available),
            'batches': ceil(row['published'] / batch_size) if row['published'] else 0,
        })
        brand_rows.append(row)

    parser_config = {}
    api_configuration_valid = True
    try:
        configured_keys = json.loads(settings.AURENE_API_KEYS_JSON or '{}')
        if not isinstance(configured_keys, dict):
            api_configuration_valid = False
            configured_keys = {}
        parser_config = configured_keys.get('parser') or {}
        if not isinstance(parser_config, dict):
            api_configuration_valid = False
            parser_config = {}
    except (TypeError, json.JSONDecodeError):
        api_configuration_valid = False
    raw_parser_scopes = parser_config.get('scopes') or []
    if not isinstance(raw_parser_scopes, list) or not all(
        isinstance(scope, str) for scope in raw_parser_scopes
    ):
        api_configuration_valid = False
        raw_parser_scopes = []
    parser_scopes = set(raw_parser_scopes)
    parser_ready = bool(
        api_configuration_valid
        and isinstance(parser_config.get('secret'), str)
        and len(parser_config['secret'].encode('utf-8')) >= 32
        and {'catalog:read', 'catalog:write'}.issubset(parser_scopes)
    )

    parser_logs = ApiRequestLog.objects.filter(key_id='parser')
    bulk_logs = parser_logs.filter(
        path='/api/v1/catalog/commercial/bulk/', method='POST',
    )
    bulk_24h = bulk_logs.filter(created_at__gte=since)
    api_metrics = bulk_24h.aggregate(
        requests=Count('id'),
        successful=Count('id', filter=Q(status_code__gte=200, status_code__lt=300)),
        failed=Count('id', filter=Q(status_code__gte=400)),
        average_duration_ms=Avg('duration_ms'),
    )
    latest_bulk = bulk_logs.first()
    latest_parser_request = parser_logs.first()
    total_published = published.count()
    total_available = available_products(published).count()
    return render(request, 'shop/backoffice/catalog_updates.html', {
        'active_section': 'updates',
        'batch_size': batch_size,
        'brand_rows': brand_rows,
        'product_count': products.count(),
        'published_count': total_published,
        'available_count': total_available,
        'unavailable_count': max(0, total_published - total_available),
        'changed_24h_count': products.filter(updated_at__gte=since).count(),
        'total_batches': sum(row['batches'] for row in brand_rows),
        'parser_ready': parser_ready,
        'api_configuration_valid': api_configuration_valid,
        'parser_read_scope': 'catalog:read' in parser_scopes,
        'parser_write_scope': 'catalog:write' in parser_scopes,
        'api_requires_https': settings.AURENE_API_REQUIRE_HTTPS,
        'api_metrics': api_metrics,
        'latest_bulk': latest_bulk,
        'latest_parser_request': latest_parser_request,
        'recent_parser_logs': parser_logs[:15],
    })


@staff_required
def analytics_dashboard(request, section='overview'):
    """Разделённая бизнес-аналитика с единым периодом и расчётами."""
    if not request.user.has_perm('shop.view_analyticssession'):
        raise PermissionDenied
    now = timezone.localtime()
    period = report_period(request.GET, now)
    days = period['days']
    since, until = period['since'], period['until']
    previous_since, previous_until = period['previous_since'], period['previous_until']
    sessions = _qualified_analytics_sessions(AnalyticsSession.objects.filter(
        started_at__gte=since, started_at__lt=until,
    ))
    confirmed_session_ids = qualified_analytics_sessions(
        AnalyticsSession.objects.all()
    ).values('pk')
    events = AnalyticsEvent.objects.filter(
        created_at__gte=since, created_at__lt=until,
        session_id__in=confirmed_session_ids,
    )
    leads = ProductRequest.objects.filter(created_at__gte=since, created_at__lt=until)
    previous_sessions = _qualified_analytics_sessions(AnalyticsSession.objects.filter(
        started_at__gte=previous_since, started_at__lt=previous_until,
    ))
    previous_events = AnalyticsEvent.objects.filter(
        created_at__gte=previous_since, created_at__lt=previous_until,
        session_id__in=confirmed_session_ids,
    )
    previous_leads = ProductRequest.objects.filter(
        created_at__gte=previous_since, created_at__lt=previous_until
    )
    spend_form = MarketingCampaignSpendForm(request.POST or None)
    if request.method == 'POST':
        if not request.user.has_perm('shop.add_marketingcampaignspend'):
            raise PermissionDenied
        if spend_form.is_valid():
            spend_form.save()
            messages.success(request, 'Расход рекламной кампании сохранён.')
            query = request.GET.urlencode()
            target = reverse(
                'shop:control_analytics_marketing'
                if section == 'marketing' else 'shop:control_analytics'
            )
            return redirect(f'{target}?{query}' if query else target)
    commercial = commercial_context(
        period, sessions, leads, events, previous_sessions, previous_leads,
    )
    spends = commercial['spends']
    total_spend = commercial['total_spend']
    session_count = sessions.count()
    page_views = events.filter(event_type='page_view').exclude(path='').count()
    engaged_count = sessions.filter(engaged=True).count()

    def percent(value, total):
        return round(value * 100 / total, 1) if total else 0

    event_counts = dict(events.values_list('event_type').annotate(total=Count('id')))
    product_views = event_counts.get('product_view', 0)
    checkout_starts = event_counts.get('checkout_start', 0)
    successes = event_counts.get('checkout_success', 0)
    top_products = events.filter(
        event_type='product_view', product__isnull=False
    ).values('product_id', 'product__title', 'product__sku_without_size').annotate(
        views=Count('id'), visitors=Count('visitor_id', distinct=True),
    ).order_by('-views')[:15]
    top_brands = events.filter(event_type__in=('brand_view', 'product_view')).exclude(
        brand=''
    ).values('brand').annotate(
        views=Count('id'), visitors=Count('visitor_id', distinct=True)
    ).order_by('-views')[:12]
    lost_products = Product.objects.filter(
        analytics_events__event_type='product_view',
        analytics_events__created_at__gte=since,
        analytics_events__created_at__lt=until,
    ).annotate(
        views=Count(
            'analytics_events',
            filter=Q(analytics_events__event_type='product_view',
                     analytics_events__created_at__gte=since,
                     analytics_events__created_at__lt=until),
            distinct=True,
        ),
        orders=Count(
            'order_items', filter=Q(
                order_items__order__paid_at__gte=since,
                order_items__order__paid_at__lt=until,
            ),
            distinct=True,
        ),
    ).filter(orders=0).order_by('-views')[:15]
    lead_status_rows = list(
        leads.values('lead_status').annotate(total=Count('id')).order_by('-total')
    )
    lead_status_labels = dict(ProductRequest.LeadStatus.choices)
    for row in lead_status_rows:
        row['label'] = lead_status_labels.get(row['lead_status'], row['lead_status'])

    def comparison(current, previous):
        if not previous:
            return None if not current else 100
        return round((current - previous) * 100 / previous, 1)

    unique_visitors = identity_count(sessions)
    previous_unique_visitors = identity_count(previous_sessions)
    previous_page_views = previous_events.filter(event_type='page_view').exclude(path='').count()
    previous_lead_count = previous_leads.count()

    hourly = period['start_date'] == period['end_date']
    bucket_function = TruncHour if hourly else TruncDate
    session_series = dict(sessions.annotate(
        bucket=bucket_function('started_at')
    ).values_list('bucket').annotate(total=Count('id')))
    visitor_series = dict(sessions.annotate(
        bucket=bucket_function('started_at')
    ).values_list('bucket').annotate(total=Count('visitor_id', distinct=True)))
    page_series = dict(events.filter(event_type='page_view').exclude(path='').annotate(
        bucket=bucket_function('created_at')
    ).values_list('bucket').annotate(total=Count('id')))
    lead_series = dict(leads.annotate(
        bucket=bucket_function('created_at')
    ).values_list('bucket').annotate(total=Count('id')))
    if hourly:
        last_hour = now.hour if period['end_date'] == now.date() else 23
        timeline_buckets = [since + timedelta(hours=hour) for hour in range(last_hour + 1)]
        timeline_labels = [value.strftime('%H:00') for value in timeline_buckets]
        key = lambda value: timezone.localtime(value).replace(minute=0, second=0, microsecond=0)
    else:
        timeline_buckets = [
            since.date() + timedelta(days=offset)
            for offset in range((period['end_date'] - period['start_date']).days + 1)
        ]
        timeline_labels = [value.strftime('%d.%m') for value in timeline_buckets]
        key = lambda value: timezone.localtime(value).date() if hasattr(value, 'hour') else value
    session_series = {key(bucket): total for bucket, total in session_series.items()}
    visitor_series = {key(bucket): total for bucket, total in visitor_series.items()}
    page_series = {key(bucket): total for bucket, total in page_series.items()}
    lead_series = {key(bucket): total for bucket, total in lead_series.items()}

    device_rows = list(
        sessions.values('device_type').annotate(total=Count('id')).order_by('-total')
    )
    source_rows = list(
        sessions.values('source', 'medium').annotate(
            sessions=Count('id'), visitors=Count('visitor_id', distinct=True)
        ).order_by('-sessions')[:15]
    )
    device_labels = dict(AnalyticsSession.Device.choices)
    for row in device_rows:
        row['label'] = device_labels.get(row['device_type'], row['device_type'])

    def funnel_rows(rows):
        maximum = max((value for _, value in rows), default=0)
        return [
            {'label': label, 'value': value,
             'percent': round(value * 100 / maximum) if maximum else 0}
            for label, value in rows
        ]

    catalog_funnel = [
        ('Просмотры каталога', event_counts.get('catalog_view', 0)
         + event_counts.get('category_view', 0) + event_counts.get('brand_view', 0)),
        ('Карточки товара', product_views),
        ('Добавления в корзину', event_counts.get('cart_add', 0)),
        ('Начало оформления', checkout_starts),
        ('Заказ оформлен', successes),
    ]
    request_funnel = [
        ('Просмотр страницы', event_counts.get('personal_order_view', 0)),
        ('Начало заполнения', event_counts.get('personal_order_start', 0)),
        ('Бренд заполнен', event_counts.get('personal_order_brand', 0)),
        ('Модель заполнена', event_counts.get('personal_order_model', 0)),
        ('Заявка отправлена', event_counts.get('personal_order_submit', 0)),
    ]
    zero_search_count = events.filter(
        event_type='internal_search', metadata__result_count=0,
    ).count()
    attention_items = []
    unanswered = leads.filter(
        lead_status=ProductRequest.LeadStatus.NEW,
        created_at__lt=now - timedelta(minutes=30),
    ).count()
    if unanswered:
        attention_items.append({
            'level': 'danger', 'text': f'{unanswered} новых лидов без ответа более 30 минут',
        })
    if lost_products:
        attention_items.append({
            'level': 'warning', 'text': f'{len(lost_products)} товаров смотрят, но не покупают',
        })
    if zero_search_count:
        attention_items.append({
            'level': 'warning', 'text': f'{zero_search_count} поисковых запросов без результатов',
        })
    if commercial['order_count'] and not commercial['costs_complete']:
        attention_items.append({
            'level': 'warning',
            'text': 'Не у всех оплаченных заказов внесены выручка и себестоимость — прибыль скрыта',
        })
    if commercial['cpl'] is not None and commercial['cpl'] > 700:
        attention_items.append({
            'level': 'danger',
            'text': f'CPL {commercial["cpl"]:.0f} ₽ выше целевого диапазона 500–700 ₽',
        })
    lead_cr_change = commercial['commercial_comparison'].get('lead_cr')
    if lead_cr_change is not None and lead_cr_change < -10:
        attention_items.append({
            'level': 'danger', 'text': f'CR в лид снизился на {abs(lead_cr_change):.1f}%',
        })
    elif lead_cr_change is not None and lead_cr_change > 10:
        attention_items.append({
            'level': 'success', 'text': f'CR в лид вырос на {lead_cr_change:.1f}%',
        })
    stale_popular = Product.objects.filter(
        analytics_events__event_type='product_view',
        analytics_events__created_at__gte=since,
        analytics_events__created_at__lt=until,
    ).filter(
        Q(fetched_at__isnull=True) | Q(fetched_at__lt=int(now.timestamp()) - 86400),
    ).distinct().count()
    if stale_popular:
        attention_items.append({
            'level': 'warning',
            'text': f'{stale_popular} просматриваемых товаров не обновлялись более 24 часов',
        })

    paid_orders = commercial['paid_orders']
    revenue_series = dict(paid_orders.annotate(
        bucket=bucket_function('paid_at')
    ).values_list('bucket').annotate(total=Sum('actual_revenue')))
    revenue_series = {key(bucket): total for bucket, total in revenue_series.items()}
    order_series = dict(paid_orders.annotate(
        bucket=bucket_function('paid_at')
    ).values_list('bucket').annotate(total=Count('id')))
    order_series = {key(bucket): total for bucket, total in order_series.items()}
    profit_series = Counter()
    profit_series_complete = True
    for order in paid_orders.only('paid_at', 'actual_revenue', 'cost_of_goods'):
        if order.gross_profit is None:
            profit_series_complete = False
        else:
            profit_series[key(order.paid_at)] += order.gross_profit

    page_rows = events.filter(event_type='page_view').exclude(path='')
    popular_pages = list(page_rows.values('path', 'page_title').annotate(
        views=Count('id'), visitors=Count('visitor_id', distinct=True),
    ).order_by('-views')[:15])
    landing_pages = list(sessions.values('landing_page').annotate(
        sessions=Count('id'), visitors=Count('visitor_id', distinct=True),
    ).order_by('-sessions')[:15])
    last_pages = {}
    for row in page_rows.order_by('session_id', 'created_at').values(
        'session_id', 'path', 'page_title',
    ):
        last_pages[row['session_id']] = row
    exit_counter = Counter((row['path'], row['page_title']) for row in last_pages.values())
    exit_pages = [
        {'path': path, 'page_title': title, 'exits': total}
        for (path, title), total in exit_counter.most_common(15)
    ]

    catalog_products = Product.objects.all()
    active_products = catalog_products.filter(is_active=True)
    stale_timestamp = int(now.timestamp()) - 86400
    last_sync_timestamp = catalog_products.aggregate(value=Max('fetched_at'))['value']
    last_catalog_sync = (
        timezone.localtime(datetime.fromtimestamp(last_sync_timestamp, tz=datetime_timezone.utc))
        if last_sync_timestamp else None
    )

    lead_paid_count = leads.filter(converted_order__paid_at__isnull=False).count()
    lead_rejected_count = leads.filter(
        Q(lead_status=ProductRequest.LeadStatus.REJECTED)
        | Q(status=ProductRequest.Status.CANCELLED)
    ).count()
    lead_new_count = leads.filter(lead_status=ProductRequest.LeadStatus.NEW).count()
    lead_work_count = leads.exclude(lead_status__in=(
        ProductRequest.LeadStatus.NEW, ProductRequest.LeadStatus.REJECTED,
    )).filter(converted_order__paid_at__isnull=True).count()
    crm_pipeline_values = [
        ('Новый', leads.count()),
        ('Связались', leads.filter(contacted_at__isnull=False).count()),
        ('Ищем товар', leads.filter(qualified_at__isnull=False).count()),
        ('Цена предложена', leads.filter(offered_at__isnull=False).count()),
        ('Согласовано', leads.filter(ordered_at__isnull=False).count()),
        ('Ожидаем оплату', leads.filter(converted_order__isnull=False).count()),
        ('Оплачено', lead_paid_count),
        ('Выкуплено', leads.filter(converted_order__status__in=(
            Order.Status.PURCHASED, Order.Status.SHIPPING, Order.Status.COMPLETED,
        )).count()),
        ('Доставлено', leads.filter(converted_order__status=Order.Status.COMPLETED).count()),
    ]
    crm_pipeline = funnel_rows(crm_pipeline_values)
    margin = (
        round(commercial['net_profit'] * 100 / commercial['revenue'], 1)
        if commercial['net_profit'] is not None and commercial['revenue'] else None
    )
    average_profit = (
        commercial['net_profit'] / commercial['order_count']
        if commercial['net_profit'] is not None and commercial['order_count'] else None
    )

    marketing_groups = defaultdict(lambda: {
        'sessions': 0, 'visitor_ids': set(), 'leads': 0, 'orders': 0,
        'customer_ids': set(), 'spend': Decimal('0'), 'revenue': Decimal('0'),
        'profit': Decimal('0'), 'profit_complete': True, 'details': [],
    })
    for row in sessions.values('source', 'campaign', 'visitor_id'):
        item = marketing_groups[(row['source'] or 'direct', row['campaign'] or '')]
        item['sessions'] += 1
        item['visitor_ids'].add(row['visitor_id'])
    for row in leads.values('source', 'utm_campaign'):
        marketing_groups[(row['source'] or 'direct', row['utm_campaign'] or '')]['leads'] += 1
    for order in paid_orders:
        item = marketing_groups[(order.source or 'direct', order.utm_campaign or '')]
        item['orders'] += 1
        item['customer_ids'].add(('user', order.user_id) if order.user_id else (
            'email', order.customer_email.casefold()
        ))
        item['revenue'] += order.actual_revenue or Decimal('0')
        if order.gross_profit is None:
            item['profit_complete'] = False
        else:
            item['profit'] += order.gross_profit
    for spend in spends:
        marketing_groups[(spend.source or 'direct', spend.campaign or '')][
            'spend'
        ] += spend.allocated_amount
    for detail in commercial['campaign_report']:
        marketing_groups[(detail['source'], detail['campaign'])]['details'].append(detail)
    marketing_report = []
    for (source, campaign), values in marketing_groups.items():
        visitors = len(values.pop('visitor_ids'))
        customers = len(values.pop('customer_ids'))
        gross_profit = values.pop('profit')
        profit_complete = values.pop('profit_complete')
        profit = gross_profit - values['spend'] if profit_complete else None
        marketing_report.append({
            'source': source, 'campaign': campaign, 'visitors': visitors,
            'customers': customers, 'cr': percent(values['leads'], visitors),
            'cpl': values['spend'] / values['leads'] if values['leads'] else None,
            'cac': values['spend'] / customers if customers else None,
            'roas': values['revenue'] / values['spend'] if values['spend'] else None,
            'profit': profit, **values,
        })
    marketing_report.sort(
        key=lambda row: (row['revenue'], row['leads'], row['visitors']), reverse=True,
    )

    order_economics = []
    for order in paid_orders.prefetch_related('items')[:20]:
        components = Counter()
        components_complete = True
        for item in order.items.all():
            snapshot = item.price_breakdown
            if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
                components_complete = False
                continue
            for step in snapshot.get('steps', []):
                try:
                    components[str(step['name'])] += Decimal(str(step['amount'])) * item.quantity
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    components_complete = False
        order_economics.append({
            'order': order,
            'revenue': order.actual_revenue,
            'cost': order.cost_of_goods,
            'gross_profit': order.gross_profit,
            'margin': (
                round(order.gross_profit * 100 / order.actual_revenue, 1)
                if order.gross_profit is not None and order.actual_revenue else None
            ),
            'components': [
                {'label': label, 'amount': amount} for label, amount in components.items()
            ],
            'components_complete': components_complete,
        })

    context = {
        'active_section': 'analytics', 'analytics_section': section,
        'analytics_query': request.GET.urlencode(), 'days': days,
        'custom_period': period['custom_period'],
        'hourly': hourly,
        'date_from': period['start_date'], 'date_to': period['end_date'],
        'period_label': period['period_label'],
        'unique_visitors': unique_visitors,
        'session_count': session_count,
        'new_sessions': sessions.filter(is_new_visitor=True).count(),
        'returning_sessions': sessions.filter(is_new_visitor=False).count(),
        'page_views': page_views,
        'pages_per_session': round(page_views / session_count, 2) if session_count else 0,
        'avg_duration': round(
            sessions.aggregate(value=Avg('duration_seconds'))['value'] or 0
        ),
        'engagement_rate': percent(engaged_count, session_count),
        'bounce_rate': percent(session_count - engaged_count, session_count),
        'sources': source_rows,
        'devices': device_rows,
        'campaigns': sessions.exclude(campaign='').values(
            'source', 'campaign', 'content', 'ad_id'
        ).annotate(sessions=Count('id')).order_by('-sessions')[:20],
        'searches': events.filter(event_type='internal_search').values(
            'metadata__query'
        ).annotate(total=Count('id')).order_by('-total')[:15],
        'top_products': top_products, 'top_brands': top_brands,
        'event_counts': event_counts,
        'catalog_funnel': funnel_rows(catalog_funnel),
        'request_funnel': funnel_rows(request_funnel),
        'leads': leads.prefetch_related('items')[:20],
        'lead_count': leads.count(),
        'total_spend': total_spend,
        'cpl': round(total_spend / leads.count(), 2) if leads.count() else None,
        'spend_form': spend_form,
        'spends': spends[:20],
        'lead_statuses': lead_status_rows,
        'transitions': events.filter(event_type='page_view').exclude(
            referrer_path=''
        ).values('referrer_path', 'path').annotate(total=Count('id')).order_by('-total')[:15],
        'filter_usage': events.filter(event_type='filter_use').values(
            'metadata__filters'
        ).annotate(total=Count('id')).order_by('-total')[:15],
        'sort_usage': events.filter(event_type='sort_use').values(
            'metadata__ordering'
        ).annotate(total=Count('id')).order_by('-total')[:10],
        'scrolls': [
            (mark, event_counts.get(f'scroll_{mark}', 0))
            for mark in (25, 50, 75, 100)
        ],
        'checkout_stages': [
            ('Корзина', event_counts.get('cart_view', 0)),
            ('Начало checkout', checkout_starts),
            ('Контакты', event_counts.get('checkout_contacts', 0)),
            ('Доставка', event_counts.get('checkout_delivery', 0)),
            ('Оплата', event_counts.get('checkout_payment', 0)),
            ('Успешно', successes + event_counts.get('payment_success', 0)),
            ('Ошибка оплаты', event_counts.get('payment_error', 0)),
        ],
        'abandoned_carts': sessions.filter(
            events__event_type='cart_add'
        ).exclude(events__event_type='checkout_success').distinct().count(),
        'lost_products': lost_products,
        'catalog_view_count': catalog_funnel[0][1],
        'cart_add_count': event_counts.get('cart_add', 0),
        'popular_pages': popular_pages,
        'landing_pages': landing_pages,
        'exit_pages': exit_pages,
        'catalog_product_count': catalog_products.count(),
        'catalog_active_count': active_products.count(),
        'catalog_in_stock_count': active_products.filter(in_stock_ecom=True).count(),
        'catalog_out_of_stock_count': active_products.filter(in_stock_ecom=False).count(),
        'catalog_stale_count': active_products.filter(
            Q(fetched_at__isnull=True) | Q(fetched_at__lt=stale_timestamp)
        ).count(),
        'last_catalog_sync': last_catalog_sync,
        'catalog_api_usage': None,
        'lead_new_count': lead_new_count,
        'lead_work_count': lead_work_count,
        'lead_paid_count': lead_paid_count,
        'lead_rejected_count': lead_rejected_count,
        'lead_sale_cr': percent(lead_paid_count, leads.count()),
        'crm_pipeline': crm_pipeline,
        'margin': margin,
        'average_profit': average_profit,
        'marketing_report': marketing_report,
        'order_economics': order_economics,
        'kpi_comparison': {
            'visitors': comparison(unique_visitors, previous_unique_visitors),
            'sessions': comparison(session_count, previous_sessions.count()),
            'page_views': comparison(page_views, previous_page_views),
            'leads': comparison(leads.count(), previous_lead_count),
        },
        'traffic_chart': {
            'labels': timeline_labels,
            'series': [
                {'label': 'Сессии', 'color': '#9b7955',
                 'values': [session_series.get(bucket, 0) for bucket in timeline_buckets]},
                {'label': 'Просмотры', 'color': '#181715',
                 'values': [page_series.get(bucket, 0) for bucket in timeline_buckets]},
                {'label': 'Лиды', 'color': '#49694c',
                 'values': [lead_series.get(bucket, 0) for bucket in timeline_buckets]},
            ],
        },
        'overview_charts': {
            'traffic': {
                'labels': timeline_labels,
                'series': [{'label': 'Посетители', 'color': '#181715',
                            'values': [visitor_series.get(bucket, 0) for bucket in timeline_buckets]}],
            },
            'leads': {
                'labels': timeline_labels,
                'series': [{'label': 'Лиды', 'color': '#49694c',
                            'values': [lead_series.get(bucket, 0) for bucket in timeline_buckets]}],
            },
            'sales': {
                'labels': timeline_labels,
                'series': [{'label': 'Продажи', 'color': '#9b7955',
                            'values': [order_series.get(bucket, 0) for bucket in timeline_buckets]}],
            },
            'revenue': {
                'labels': timeline_labels,
                'series': [{'label': 'Выручка, ₽', 'color': '#647183',
                            'values': [float(revenue_series.get(bucket, 0)) for bucket in timeline_buckets]}],
            },
        },
        'finance_chart': {
            'labels': timeline_labels,
            'series': [
                {'label': 'Выручка', 'color': '#181715',
                 'values': [float(revenue_series.get(bucket, 0)) for bucket in timeline_buckets]},
                {'label': 'Валовая прибыль', 'color': '#49694c',
                 'values': [float(profit_series.get(bucket, 0)) for bucket in timeline_buckets]},
            ],
        },
        'profit_series_complete': profit_series_complete,
        'device_chart': {
            'labels': [row['label'] for row in device_rows],
            'values': [row['total'] for row in device_rows],
            'colors': ['#181715', '#9b7955', '#c9bba9'],
        },
        'source_chart': {
            'labels': [row['source'] for row in source_rows[:6]],
            'values': [row['sessions'] for row in source_rows[:6]],
            'colors': ['#181715', '#9b7955', '#49694c', '#b08c83', '#647183', '#c9bba9'],
        },
    }
    context.update(commercial)
    transition_rows = list(context['transitions'])
    for row in transition_rows:
        row['from_label'] = _analytics_page_name(row['referrer_path'])
        row['to_label'] = _analytics_page_name(row['path'])
    context['transitions'] = transition_rows
    filter_rows = list(context['filter_usage'])
    for row in filter_rows:
        row['label'] = _analytics_filter_name(row['metadata__filters'])
    context['filter_usage'] = filter_rows
    context['attention_items'] = attention_items
    return render(request, 'shop/backoffice/analytics/dashboard.html', context)


@permission_required('shop.view_product')
def product_list(request):
    queryset = Product.objects.defer(
        *PRODUCT_LIST_DEFERRED_FIELDS
    ).filter(
        catalog_scope=Product.CatalogScope.STORE,
    ).prefetch_related('images').order_by('-updated_at')
    query = request.GET.get('q', '').strip()
    visibility = request.GET.get('visibility', '')
    stock = request.GET.get('stock', '')
    collection = request.GET.get('collection', '')
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query) | Q(sku__icontains=query)
            | Q(category_name__icontains=query) | Q(current_color__icontains=query)
        )
    if visibility == 'published':
        queryset = queryset.filter(is_active=True)
    elif visibility == 'hidden':
        queryset = queryset.filter(is_active=False)
    if stock == 'in':
        queryset = queryset.filter(in_stock_ecom=True)
    elif stock == 'out':
        queryset = queryset.filter(in_stock_ecom=False)
    if collection:
        queryset = queryset.filter(collection=collection)

    if request.method == 'POST':
        if not request.user.has_perm('shop.change_product'):
            raise PermissionDenied
        selected = request.POST.getlist('selected')
        action = request.POST.get('action')
        updates = {
            'publish': {'is_active': True},
            'hide': {'is_active': False},
            'feature': {'is_featured': True},
            'unfeature': {'is_featured': False},
            'in_stock': {'in_stock_ecom': True},
            'out_of_stock': {'in_stock_ecom': False},
        }
        if not selected or action not in updates:
            messages.warning(request, 'Выберите товары и действие.')
        else:
            updated = Product.objects.filter(
                pk__in=selected, catalog_scope=Product.CatalogScope.STORE,
            ).update(**updates[action])
            messages.success(request, f'Обновлено товаров: {updated}.')
        return redirect(request.get_full_path())

    page_obj = _page(request, queryset)
    return render(request, 'shop/backoffice/products/list.html', {
        'active_section': 'products',
        'page_obj': page_obj,
        'query': query,
        'visibility': visibility,
        'stock': stock,
        'collection': collection,
        'total_count': page_obj.paginator.count,
    })


@permission_required('shop.view_product')
def product_edit(request, pk):
    product = get_object_or_404(Product.objects.prefetch_related('images'), pk=pk)
    can_change = request.user.has_perm('shop.change_product')
    if request.method == 'POST' and not can_change:
        raise PermissionDenied
    form = BackofficeProductForm(request.POST or None, instance=product)
    images = _product_image_forms(request, product)
    if request.method == 'POST' and form.is_valid() and images.is_valid():
        previous_files = _stored_formset_files(images, ProductImage, 'local_image')
        with transaction.atomic():
            product = form.save()
            images.save()
            if product.catalog_scope == Product.CatalogScope.COLLECTION:
                product.collection_reviewed_at = timezone.now()
                product.save(update_fields=('collection_reviewed_at', 'updated_at'))
                product._prefetched_objects_cache.pop('images', None)
                missing = _collection_readiness(product)
                if product.is_active and missing:
                    product.is_active = False
                    product.save(update_fields=('is_active', 'updated_at'))
                    messages.warning(
                        request,
                        'Карточка сохранена как черновик: ' + ', '.join(missing) + '.',
                    )
            transaction.on_commit(lambda: _delete_unreferenced_media_files(
                previous_files, ProductImage, 'local_image'
            ))
        messages.success(request, 'Карточка товара сохранена.')
        if product.collection_batch_id:
            next_product = product.collection_batch.products.filter(
                catalog_scope=Product.CatalogScope.COLLECTION,
                collection_reviewed_at__isnull=True,
            ).exclude(pk=product.pk).order_by('pk').first()
            if next_product:
                messages.info(request, 'Открыта следующая карточка партии.')
                return redirect('shop:control_product_edit', pk=next_product.pk)
            return redirect(
                'shop:control_collection_batch', pk=product.collection_batch_id,
            )
        return redirect('shop:control_product_edit', pk=product.pk)
    is_collection = product.catalog_scope == Product.CatalogScope.COLLECTION
    return render(request, 'shop/backoffice/products/edit.html', {
        'active_section': 'collection' if is_collection else 'products',
        'product': product, 'form': form,
        'image_formset': images, 'image_limit': max(10, product.images.count()),
        'can_change': can_change,
        'is_collection': is_collection,
        'catalog_back_url': reverse(
            'shop:control_collection' if is_collection else 'shop:control_products'
        ),
        'catalog_back_label': 'FINDER COLLECTION' if is_collection else 'Основной каталог',
        **_product_reference_context(),
    })


@permission_required('shop.add_product')
def product_create(request):
    product = Product()
    form = BackofficeProductForm(request.POST or None, instance=product)
    images = _product_image_forms(request, product)
    if request.method == 'POST' and form.is_valid() and images.is_valid():
        with transaction.atomic():
            product = form.save()
            images.instance = product
            images.save()
        messages.success(request, 'Новая карточка товара создана.')
        return redirect('shop:control_product_edit', pk=product.pk)
    return render(request, 'shop/backoffice/products/edit.html', {
        'active_section': 'products', 'product': product, 'form': form,
        'image_formset': images, 'image_limit': 10,
        'can_change': True, 'is_create': True,
        'catalog_back_url': reverse('shop:control_products'),
        'catalog_back_label': 'Основной каталог',
        **_product_reference_context(),
    })


def _collection_readiness(product):
    """Возвращает причины, по которым черновик COLLECTION нельзя публиковать."""
    missing = []
    if not product.collection_reviewed_at:
        missing.append('не проверена в полном редакторе')
    if not product.category_name:
        missing.append('не выбрана категория')
    if product.display_price is None or product.display_price <= 0:
        missing.append('не указана цена')
    if not product.images.exists():
        missing.append('нет фотографий')
    return missing


@permission_required('shop.view_product')
def collection_list(request):
    queryset = Product.objects.defer(*PRODUCT_LIST_DEFERRED_FIELDS).filter(
        catalog_scope=Product.CatalogScope.COLLECTION,
    ).prefetch_related('images').order_by('-updated_at')
    query = request.GET.get('q', '').strip()
    visibility = request.GET.get('visibility', '')
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query) | Q(sku__icontains=query)
            | Q(sku_without_size__icontains=query)
            | Q(brand__icontains=query)
        )
    if visibility == 'published':
        queryset = queryset.filter(is_active=True)
    elif visibility == 'draft':
        queryset = queryset.filter(is_active=False)
    if request.method == 'POST':
        if not request.user.has_perm('shop.change_product'):
            raise PermissionDenied
        selected = request.POST.getlist('selected')
        action = request.POST.get('action')
        selected_products = Product.objects.filter(
            pk__in=selected, catalog_scope=Product.CatalogScope.COLLECTION,
        )
        if not selected or action not in {'publish', 'hide'}:
            messages.warning(request, 'Выберите товары и действие.')
        elif action == 'hide':
            updated = selected_products.update(is_active=False)
            messages.success(request, f'Скрыто товаров: {updated}.')
        else:
            ready_ids = [
                product.pk for product in selected_products.prefetch_related('images')
                if not _collection_readiness(product)
            ]
            updated = selected_products.filter(pk__in=ready_ids).update(
                is_active=True, in_stock_ecom=True,
            )
            skipped = selected_products.count() - updated
            messages.success(request, f'Опубликовано товаров: {updated}.')
            if skipped:
                messages.warning(
                    request,
                    f'Не опубликовано незавершённых карточек: {skipped}.',
                )
        return redirect('shop:control_collection')
    page_obj = _page(request, queryset)
    batches = CollectionBatch.objects.annotate(
        total=Count('products'),
        reviewed=Count('products', filter=Q(products__collection_reviewed_at__isnull=False)),
        published=Count('products', filter=Q(products__is_active=True)),
    )[:6]
    return render(request, 'shop/backoffice/collection/list.html', {
        'active_section': 'collection', 'page_obj': page_obj,
        'total_count': page_obj.paginator.count, 'query': query,
        'visibility': visibility, 'batches': batches,
    })


@permission_required('shop.add_product')
def collection_product_create(request):
    product = Product(
        catalog_scope=Product.CatalogScope.COLLECTION,
        is_active=False,
        in_stock_ecom=True,
    )
    form = BackofficeProductForm(request.POST or None, instance=product)
    images = _product_image_forms(request, product)
    if request.method == 'POST' and form.is_valid() and images.is_valid():
        with transaction.atomic():
            product = form.save()
            product.catalog_scope = Product.CatalogScope.COLLECTION
            product.collection_reviewed_at = timezone.now()
            product.save(update_fields=(
                'catalog_scope', 'collection_reviewed_at', 'updated_at',
            ))
            images.instance = product
            images.save()
            missing = _collection_readiness(product)
            if product.is_active and missing:
                product.is_active = False
                product.save(update_fields=('is_active', 'updated_at'))
                messages.warning(
                    request,
                    'Карточка создана как черновик: ' + ', '.join(missing) + '.',
                )
        messages.success(request, 'Карточка FINDER COLLECTION создана.')
        return redirect('shop:control_product_edit', pk=product.pk)
    return render(request, 'shop/backoffice/products/edit.html', {
        'active_section': 'collection', 'product': product, 'form': form,
        'image_formset': images, 'image_limit': 10,
        'can_change': True, 'is_create': True, 'is_collection': True,
        'catalog_back_url': reverse('shop:control_collection'),
        'catalog_back_label': 'FINDER COLLECTION',
        **_product_reference_context(),
    })


def _unique_collection_sku(brand, article):
    base = f'collection:{brand}:{article}'
    candidate = base
    number = 2
    while Product.objects.filter(sku=candidate).exists():
        candidate = f'{base}:{number}'
        number += 1
    return candidate


@permission_required('shop.add_product')
def collection_bulk_create(request):
    batch_form = CollectionBatchForm(request.POST or None, prefix='batch')
    formset = CollectionDraftFormSet(
        request.POST or None, request.FILES or None, prefix='items',
    )
    if request.method == 'POST' and batch_form.is_valid() and formset.is_valid():
        active_forms = [
            form for form in formset
            if form.cleaned_data and not form.cleaned_data.get('DELETE')
        ]
        with transaction.atomic():
            batch = batch_form.save(commit=False)
            batch.created_by = request.user
            batch.save()
            created = []
            for item_form in active_forms:
                data = item_form.cleaned_data
                brand = data['brand_reference']
                product = Product.objects.create(
                    title=data['title'], brand=brand.slug,
                    sku=_unique_collection_sku(brand.slug, data['article']),
                    sku_without_size=data['article'],
                    description=data.get('description', ''),
                    catalog_scope=Product.CatalogScope.COLLECTION,
                    collection_batch=batch,
                    is_active=False,
                    in_stock_ecom=True,
                )
                for position, uploaded in enumerate(
                    file for file in data.get('images', []) if file
                ):
                    image = ProductImage.objects.create(
                        product=product, local_image=uploaded, position=position,
                        tag='main' if position == 0 else 'gallery', alt=product.title,
                    )
                    from .image_processing import create_product_image_preview
                    create_product_image_preview(image, force=True)
                created.append(product)
        messages.success(
            request,
            f'Создана партия «{batch.name}»: {len(created)} черновиков.',
        )
        return redirect('shop:control_product_edit', pk=created[0].pk)
    return render(request, 'shop/backoffice/collection/bulk.html', {
        'active_section': 'collection', 'batch_form': batch_form,
        'formset': formset, 'brand_options': CatalogBrand.objects.filter(is_active=True),
    })


@permission_required('shop.view_product')
def collection_batch(request, pk):
    batch = get_object_or_404(
        CollectionBatch.objects.prefetch_related('products__images'), pk=pk,
    )
    products = list(batch.products.order_by('pk'))
    readiness = {product.pk: _collection_readiness(product) for product in products}
    if request.method == 'POST':
        if not request.user.has_perm('shop.change_product'):
            raise PermissionDenied
        ready_ids = [product.pk for product in products if not readiness[product.pk]]
        updated = batch.products.filter(pk__in=ready_ids).update(
            is_active=True, in_stock_ecom=True,
        )
        messages.success(request, f'Опубликовано готовых карточек: {updated}.')
        if len(products) > updated:
            messages.warning(
                request,
                f'Остались незавершённые карточки: {len(products) - updated}.',
            )
        return redirect('shop:control_collection_batch', pk=batch.pk)
    rows = [
        {'product': product, 'missing': readiness[product.pk]}
        for product in products
    ]
    return render(request, 'shop/backoffice/collection/batch.html', {
        'active_section': 'collection', 'batch': batch, 'rows': rows,
        'ready_count': sum(not row['missing'] for row in rows),
    })


@permission_required('shop.view_product')
def product_duplicate_search(request):
    query = request.GET.get('q', '').strip()
    exclude = request.GET.get('exclude', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})
    queryset = Product.objects.filter(
        Q(title__icontains=query) | Q(sku__icontains=query)
        | Q(sku_without_size__icontains=query)
    )
    if exclude.isdigit():
        queryset = queryset.exclude(pk=int(exclude))
    results = [
        {
            'id': product.pk,
            'title': product.title,
            'article': product.article,
            'brand': product.brand,
            'catalog': product.get_catalog_scope_display(),
            'url': reverse('shop:control_product_edit', args=[product.pk]),
        }
        for product in queryset.order_by('-updated_at')[:8]
    ]
    return JsonResponse({'results': results})


@permission_required('shop.change_product')
def dictionaries(request):
    form_specs = {
        'brand': (CatalogBrandForm, 'brand', 'Бренд добавлен.'),
        'color': (CatalogColorForm, 'color', 'Цвет добавлен.'),
        'material': (CatalogMaterialForm, 'material', 'Материал добавлен.'),
        'category': (CatalogCategoryForm, 'category', 'Категория добавлена.'),
        'size_profile': (SizeProfileForm, 'size_profile', 'Размерный профиль добавлен.'),
        'size': (CatalogSizeForm, 'size', 'Размер добавлен.'),
    }
    forms = {
        key: form_class(prefix=prefix)
        for key, (form_class, prefix, _) in form_specs.items()
    }
    if request.method == 'POST':
        action = request.POST.get('action')
        if action not in form_specs:
            messages.error(request, 'Неизвестное действие.')
        else:
            form_class, prefix, success_message = form_specs[action]
            forms[action] = form_class(request.POST, request.FILES, prefix=prefix)
            if forms[action].is_valid():
                forms[action].save()
                messages.success(request, success_message)
                return redirect('shop:control_dictionaries')
    return render(request, 'shop/backoffice/dictionaries.html', {
        'active_section': 'dictionaries', 'forms': forms,
        'brands': CatalogBrand.objects.all(),
        'colors': CatalogColor.objects.all(),
        'materials': CatalogMaterial.objects.all(),
        'categories': CatalogCategory.objects.all(),
        'size_profiles': SizeProfile.objects.prefetch_related('sizes'),
    })


@permission_required('shop.change_weeklylook')
def weekly_look_edit(request):
    look, _ = WeeklyLook.objects.get_or_create(pk=1)
    form = WeeklyLookForm(request.POST or None, instance=look)
    item_formset = WeeklyLookItemFormSet(
        request.POST or None, request.FILES or None, instance=look, prefix='items',
    )
    if request.method == 'POST':
        if request.POST.get('action') == 'toggle':
            look.is_visible = not look.is_visible
            look.save(update_fields=('is_visible',))
            messages.success(request, 'Образ недели показан.' if look.is_visible else 'Образ недели скрыт.')
            return redirect('shop:control_weekly_look')
        if form.is_valid() and item_formset.is_valid():
            previous_files = _stored_formset_files(item_formset, WeeklyLookItem, 'uploaded_image')
            with transaction.atomic():
                form.save()
                item_formset.save()
                transaction.on_commit(lambda: _delete_unreferenced_media_files(
                    previous_files, WeeklyLookItem, 'uploaded_image'
                ))
            messages.success(request, 'Образ недели сохранён.')
            return redirect('shop:control_weekly_look')
    return render(request, 'shop/backoffice/weekly_look/edit.html', {
        'active_section': 'weekly_look', 'look': look,
        'form': form, 'item_formset': item_formset,
    })


@permission_required('shop.change_banner')
def banners(request):
    defaults = {
        Banner.Slot.FALL_WINTER: {
            'image_count': 5, 'interval_seconds': 5, 'autoplay': True,
            'personalize_by_gender': True,
        },
        Banner.Slot.BAGS: {
            'image_count': 5, 'interval_seconds': 5, 'autoplay': True,
            'personalize_by_gender': False,
        },
    }
    for slot, values in defaults.items():
        Banner.objects.get_or_create(slot=slot, defaults=values)
    return render(request, 'shop/backoffice/banners/list.html', {
        'active_section': 'banners',
        'banners': Banner.objects.prefetch_related('images__catalog_image__product'),
    })


@permission_required('shop.change_banner')
def banner_edit(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    form = BannerForm(request.POST or None, instance=banner)
    image_formset = BannerImageFormSet(
        request.POST or None, request.FILES or None, instance=banner, prefix='images'
    )
    if request.method == 'POST' and form.is_valid() and image_formset.is_valid():
        previous_files = _stored_formset_files(
            image_formset, BannerImage, 'uploaded_image'
        )
        with transaction.atomic():
            form.save()
            image_formset.save()
            transaction.on_commit(lambda: _delete_unreferenced_media_files(
                previous_files, BannerImage, 'uploaded_image'
            ))
        messages.success(request, f'Баннер «{banner}» сохранён.')
        return redirect('shop:control_banner_edit', pk=banner.pk)
    return render(request, 'shop/backoffice/banners/edit.html', {
        'active_section': 'banners', 'banner': banner,
        'form': form, 'image_formset': image_formset,
    })


@staff_required
def banner_image_search(request):
    if not (request.user.has_perm('shop.change_banner') or request.user.has_perm('shop.change_weeklylook')):
        raise PermissionDenied
    query = request.GET.get('q', '').strip()
    gender = request.GET.get('gender', '').strip()
    images = ProductImage.objects.select_related('product').filter(
        product_id__in=available_products(Product.objects.filter(
            is_active=True, catalog_scope=Product.CatalogScope.STORE,
        )).values('pk')
    )
    if query:
        images = images.filter(
            Q(product__title__icontains=query)
            | Q(product__sku__icontains=query)
            | Q(product__sku_without_size__icontains=query)
            | Q(product__current_color__icontains=query)
        )
    if gender in ('women', 'men'):
        images = images.filter(product__gender__0=gender)
    images = images.order_by('-product__updated_at', 'product_id', 'position')[:30]
    return JsonResponse({'results': [{
        'id': image.pk,
        'url': image.preview_url,
        'title': image.product.title,
        'article': image.product.sku_without_size or image.product.sku,
        'color': image.product.current_color,
        'gender': image.product.gender,
        'position': image.position + 1,
    } for image in images if image.url]})


@permission_required('shop.change_product')
def brand_edit(request, pk):
    brand = get_object_or_404(CatalogBrand, pk=pk)
    form = CatalogBrandForm(request.POST or None, request.FILES or None, instance=brand)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Бренд «{brand.name}» сохранён.')
        return redirect('shop:control_dictionaries')
    return render(request, 'shop/backoffice/brands/edit.html', {
        'active_section': 'dictionaries', 'brand': brand, 'form': form,
    })


@staff_required
def user_list(request):
    if not request.user.has_perm('auth.view_user'):
        raise PermissionDenied
    users = get_user_model().objects.select_related('customer_profile').order_by('-date_joined')
    query = request.GET.get('q', '').strip()
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
            | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(customer_profile__phone__icontains=query)
            | Q(customer_profile__telegram__icontains=query)
            | Q(customer_profile__delivery_address__icontains=query)
        ).distinct()
    page_obj = _page(request, users, 30)
    return render(request, 'shop/backoffice/users/list.html', {
        'active_section': 'users', 'page_obj': page_obj,
        'query': query, 'total_count': page_obj.paginator.count,
    })


@staff_required
def user_detail(request, pk):
    if not request.user.has_perm('auth.view_user'):
        raise PermissionDenied
    user = get_object_or_404(get_user_model().objects.select_related('customer_profile'), pk=pk)
    profile = CustomerProfile.objects.filter(user=user).first()
    cart_items = list(CartItem.objects.filter(user=user).select_related(
        'product'
    ).prefetch_related('product__images', 'product__size_variants'))
    period = report_period(request.GET, timezone.localtime())
    since, until = period['since'], period['until']
    user_sessions = _qualified_analytics_sessions(AnalyticsSession.objects.filter(
        Q(user=user) | Q(visitor__user=user),
        started_at__gte=since, started_at__lt=until,
    ))
    user_events = AnalyticsEvent.objects.filter(
        Q(user=user) | Q(visitor__user=user),
        created_at__gte=since, created_at__lt=until,
    )
    user_requests = user.product_requests.filter(
        created_at__gte=since, created_at__lt=until,
    )
    user_paid_orders = user.orders.filter(
        paid_at__gte=since, paid_at__lt=until,
    )
    user_page_views = user_events.filter(event_type='page_view').exclude(path='').count()
    user_product_views = user_events.filter(event_type='product_view').count()
    user_cart_adds = user_events.filter(event_type='cart_add').count()
    user_revenue = (
        user_paid_orders.aggregate(value=Sum('actual_revenue'))['value'] or Decimal('0')
    )
    user_sources = user_sessions.values('source', 'medium').annotate(
        sessions=Count('id'),
    ).order_by('-sessions')[:10]
    user_devices = user_sessions.values('device_type').annotate(
        sessions=Count('id'),
    ).order_by('-sessions')
    device_labels = dict(AnalyticsSession.Device.choices)
    user_device_rows = [
        {**row, 'label': device_labels.get(row['device_type'], row['device_type'])}
        for row in user_devices
    ]
    user_top_products = user_events.filter(
        event_type='product_view', product__isnull=False,
    ).values('product_id', 'product__title', 'product__sku_without_size').annotate(
        views=Count('id'),
    ).order_by('-views')[:10]
    event_labels = {
        'page_view': 'Просмотр страницы', 'catalog_view': 'Просмотр каталога',
        'category_view': 'Просмотр категории', 'brand_view': 'Просмотр бренда',
        'product_view': 'Просмотр товара', 'product_click': 'Переход к товару',
        'cart_add': 'Добавление в корзину', 'cart_view': 'Просмотр корзины',
        'checkout_start': 'Начало оформления', 'checkout_success': 'Заказ оформлен',
        'favorite_add': 'Добавление в избранное',
        'internal_search': 'Поиск по каталогу',
    }
    recent_user_events = list(user_events.select_related(
        'product', 'session'
    ).order_by('-created_at')[:30])
    for event in recent_user_events:
        event.display_name = event_labels.get(
            event.event_type, event.event_type.replace('_', ' ').capitalize()
        )
    telegram_account = TelegramAccount.objects.filter(user=user, is_active=True).first()
    return render(request, 'shop/backoffice/users/detail.html', {
        'active_section': 'users', 'customer': user, 'profile': profile,
        'orders': user.orders.prefetch_related('items').all(),
        'requests': user.product_requests.prefetch_related('items').all(),
        'favorites': Product.objects.filter(
            favorited_by__user=user, is_active=True
        ).prefetch_related('images').order_by('-favorited_by__created_at'),
        'cart_items': cart_items,
        'cart_count': sum(item.quantity for item in cart_items),
        'analytics_days': period['days'],
        'analytics_custom_period': period['custom_period'],
        'analytics_date_from': period['start_date'],
        'analytics_date_to': period['end_date'],
        'analytics_period_label': period['period_label'],
        'user_analytics': {
            'sessions': user_sessions.count(),
            'page_views': user_page_views,
            'pages_per_session': (
                round(user_page_views / user_sessions.count(), 2)
                if user_sessions.count() else 0
            ),
            'avg_duration': round(
                user_sessions.aggregate(value=Avg('duration_seconds'))['value'] or 0
            ),
            'product_views': user_product_views,
            'cart_adds': user_cart_adds,
            'leads': user_requests.count(),
            'orders': user_paid_orders.count(),
            'revenue': user_revenue,
        },
        'user_sources': user_sources,
        'user_devices': user_device_rows,
        'user_top_products': user_top_products,
        'recent_user_events': recent_user_events,
        'telegram_account': telegram_account,
        'bot_username': settings.TELEGRAM_BOT_USERNAME,
    })


@permission_required('shop.add_product')
def product_import(request):
    json_form = ProductImportForm(prefix='json')
    zip_form = ProductZipImportForm(prefix='zip')
    reports = []
    if request.method == 'POST' and request.POST.get('import_type') == 'json':
        json_form = ProductImportForm(request.POST, request.FILES, prefix='json')
        if json_form.is_valid():
            created = updated = 0
            errors = []
            for uploaded_file in json_form.cleaned_data['json_files']:
                try:
                    payloads = parse_json_file(uploaded_file)
                except (ValueError, TypeError) as exc:
                    errors.append(f'{uploaded_file.name}: {exc}')
                    continue
                for index, payload in enumerate(payloads, start=1):
                    try:
                        _, was_created = import_product(
                            payload,
                            replace_relations=json_form.cleaned_data['replace_relations'],
                        )
                        created += int(was_created)
                        updated += int(not was_created)
                    except (ValueError, TypeError) as exc:
                        errors.append(f'{uploaded_file.name}, карточка {index}: {exc}')
            if created or updated:
                messages.success(request, f'Импорт завершён: создано {created}, обновлено {updated}.')
            for error in errors[:20]:
                messages.error(request, error)
            if not errors:
                return redirect('shop:control_products')
    elif request.method == 'POST' and request.POST.get('import_type') == 'zip':
        zip_form = ProductZipImportForm(request.POST, request.FILES, prefix='zip')
        if zip_form.is_valid():
            for uploaded_file in zip_form.cleaned_data['zip_files']:
                try:
                    report = import_zip_archive(
                        uploaded_file,
                        replace_relations=zip_form.cleaned_data['replace_relations'],
                        extract_images=zip_form.cleaned_data['extract_images'],
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    continue
                reports.append(report)
                level = messages.WARNING if report.errors else messages.SUCCESS
                messages.add_message(
                    request, level,
                    f'{report.archive_name}: создано {report.created}, обновлено {report.updated}, '
                    f'изображений привязано {report.images_attached}.',
                )
    return render(request, 'shop/backoffice/products/import.html', {
        'active_section': 'import', 'json_form': json_form,
        'zip_form': zip_form, 'reports': reports,
    })


def _workflow_list(request, model, template, active_section, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied
    queryset = model.objects.prefetch_related('items').order_by('-created_at')
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if query:
        queryset = queryset.filter(
            Q(customer_name__icontains=query) | Q(customer_email__icontains=query)
            | Q(customer_phone__icontains=query) | Q(id__icontains=query)
        ).distinct()
    valid_statuses = {value for value, _ in model.Status.choices}
    if status in valid_statuses:
        queryset = queryset.filter(status=status)
    page_obj = _page(request, queryset, 25)
    return render(request, template, {
        'active_section': active_section, 'page_obj': page_obj,
        'query': query, 'selected_status': status,
        'status_choices': model.Status.choices, 'total_count': page_obj.paginator.count,
    })


@staff_required
def order_list(request):
    return _workflow_list(
        request, Order, 'shop/backoffice/orders/list.html', 'orders', 'shop.view_order'
    )


@permission_required('shop.view_order')
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=pk)
    can_change = request.user.has_perm('shop.change_order')
    if request.method == 'POST' and not can_change:
        raise PermissionDenied
    form = BackofficeOrderForm(request.POST or None, instance=order)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Заказ {order.number} обновлён.')
        return redirect('shop:control_order_detail', pk=order.pk)
    return render(request, 'shop/backoffice/orders/detail.html', {
        'active_section': 'orders', 'order': order, 'form': form,
        'can_change': can_change,
    })


@staff_required
def request_list(request):
    if not request.user.has_perm('shop.view_productrequest'):
        raise PermissionDenied
    queryset = ProductRequest.objects.prefetch_related('items').order_by('-created_at')
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if query:
        queryset = queryset.filter(
            Q(customer_name__icontains=query) | Q(customer_email__icontains=query)
            | Q(customer_phone__icontains=query) | Q(id__icontains=query)
            | Q(source__icontains=query) | Q(utm_campaign__icontains=query)
        ).distinct()
    valid_statuses = {value for value, _ in ProductRequest.LeadStatus.choices}
    if status in valid_statuses:
        queryset = queryset.filter(lead_status=status)
    page_obj = _page(request, queryset, 25)
    return render(request, 'shop/backoffice/requests/list.html', {
        'active_section': 'requests', 'page_obj': page_obj,
        'query': query, 'selected_status': status,
        'status_choices': ProductRequest.LeadStatus.choices,
        'total_count': page_obj.paginator.count,
    })


@permission_required('shop.view_productrequest')
def request_detail(request, pk):
    product_request = get_object_or_404(
        ProductRequest.objects.prefetch_related('items'), pk=pk
    )
    can_change = request.user.has_perm('shop.change_productrequest')
    if request.method == 'POST' and not can_change:
        raise PermissionDenied
    form = BackofficeProductRequestForm(request.POST or None, instance=product_request)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Заявка {product_request.number} обновлена.')
        return redirect('shop:control_request_detail', pk=product_request.pk)
    return render(request, 'shop/backoffice/requests/detail.html', {
        'active_section': 'requests', 'product_request': product_request,
        'form': form, 'can_change': can_change,
    })


@staff_required
def pricing(request):
    pricing_permissions = (
        'shop.change_product', 'shop.change_pricingrule', 'shop.change_exchangerate'
    )
    if not any(request.user.has_perm(code) for code in pricing_permissions):
        raise PermissionDenied
    rates = ExchangeRate.objects.order_by('currency')
    rules = PricingRule.objects.order_by('position', 'id')
    rate_formset = ExchangeRateFormSet(queryset=rates, prefix='rates')
    rule_formset = PricingRuleFormSet(queryset=rules, prefix='rules')
    preview_form = PricePreviewForm(prefix='preview')
    calculation = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_rates':
            if not request.user.has_perm('shop.change_exchangerate'):
                raise PermissionDenied
            try:
                rate_date, updated_rates = update_cbr_rates()
            except Exception as exc:
                messages.error(request, f'Не удалось обновить курсы: {exc}.')
            else:
                messages.success(request, f'Курсы на {rate_date:%d.%m.%Y} обновлены: {", ".join(updated_rates)}.')
            return redirect('shop:control_pricing')
        if action == 'save_rates':
            if not request.user.has_perm('shop.change_exchangerate'):
                raise PermissionDenied
            rate_formset = ExchangeRateFormSet(request.POST, queryset=rates, prefix='rates')
            if rate_formset.is_valid():
                rate_formset.save()
                messages.success(request, 'Курсы валют сохранены.')
                return redirect('shop:control_pricing')
        elif action == 'save_rules':
            if not request.user.has_perm('shop.change_pricingrule'):
                raise PermissionDenied
            rule_formset = PricingRuleFormSet(request.POST, queryset=rules, prefix='rules')
            if rule_formset.is_valid():
                rule_formset.save()
                messages.success(request, 'Формула стоимости сохранена.')
                return redirect('shop:control_pricing')
        elif action == 'preview':
            preview_form = PricePreviewForm(request.POST, prefix='preview')
            if preview_form.is_valid():
                calculation = calculate_price(
                    preview_form.cleaned_data['amount'], preview_form.cleaned_data['currency']
                )
    return render(request, 'shop/backoffice/pricing.html', {
        'active_section': 'pricing', 'rate_formset': rate_formset,
        'rule_formset': rule_formset, 'preview_form': preview_form,
        'calculation': calculation,
    })