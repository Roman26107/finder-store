"""First-party аналитика витрины без передачи данных внешним сервисам."""

from datetime import timedelta
from urllib.parse import urlparse
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import AnalyticsEvent, AnalyticsSession, AnalyticsVisitor


VISITOR_COOKIE = 'aurene_visitor'
SESSION_COOKIE = 'aurene_analytics_session'
SESSION_TIMEOUT = timedelta(minutes=30)
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _device_type(user_agent):
    value = (user_agent or '').casefold()
    if any(marker in value for marker in ('ipad', 'tablet', 'kindle', 'silk')):
        return AnalyticsSession.Device.TABLET
    if any(marker in value for marker in ('mobile', 'iphone', 'android')):
        return AnalyticsSession.Device.MOBILE
    return AnalyticsSession.Device.DESKTOP


def _merge_visitor(source, target, user):
    """Переносит историю анонимного/дублирующего cookie в аккаунт без потерь."""
    if source.pk == target.pk:
        return target
    first_seen_at = min(source.first_seen_at, target.first_seen_at)
    last_seen_at = max(source.last_seen_at, target.last_seen_at)
    with transaction.atomic():
        AnalyticsSession.objects.filter(visitor=source).update(visitor=target, user=user)
        AnalyticsEvent.objects.filter(visitor=source).update(visitor=target, user=user)
        source.product_requests.update(analytics_visitor=target)
        source.orders.update(analytics_visitor=target)
        source.delete()
        AnalyticsVisitor.objects.filter(pk=target.pk).update(
            first_seen_at=first_seen_at, last_seen_at=last_seen_at,
        )
    target.first_seen_at = first_seen_at
    target.last_seen_at = last_seen_at
    return target


def _visitor_for_request(request):
    cookie_visitor_id = _uuid(request.COOKIES.get(VISITOR_COOKIE))
    cookie_visitor = (
        AnalyticsVisitor.objects.filter(pk=cookie_visitor_id).first()
        if cookie_visitor_id else None
    )
    if not request.user.is_authenticated:
        if cookie_visitor is not None:
            return cookie_visitor, False
        return AnalyticsVisitor.objects.create(), True

    user = request.user
    canonical = AnalyticsVisitor.objects.filter(user=user).order_by('first_seen_at').first()
    if canonical is not None:
        if cookie_visitor is not None and cookie_visitor.user_id is None:
            canonical = _merge_visitor(cookie_visitor, canonical, user)
        return canonical, False
    if cookie_visitor is not None and cookie_visitor.user_id in (None, user.pk):
        try:
            with transaction.atomic():
                cookie_visitor.user = user
                cookie_visitor.save(update_fields=('user', 'last_seen_at'))
                cookie_visitor.sessions.update(user=user)
                cookie_visitor.events.update(user=user)
            return cookie_visitor, False
        except IntegrityError:
            canonical = AnalyticsVisitor.objects.get(user=user)
            return _merge_visitor(cookie_visitor, canonical, user), False
    visitor, created = AnalyticsVisitor.objects.get_or_create(user=user)
    return visitor, created


def _traffic_source(request):
    utm_source = request.GET.get('utm_source', '').strip()[:255]
    utm_medium = request.GET.get('utm_medium', '').strip()[:255]
    referrer = request.META.get('HTTP_REFERER', '')[:1000]
    if utm_source:
        return utm_source, utm_medium
    host = (urlparse(referrer).hostname or '').casefold()
    if not host:
        return 'direct', ''
    if 'yandex.' in host:
        return 'yandex', 'organic'
    if 'google.' in host:
        return 'google', 'organic'
    if 't.me' in host or 'telegram.' in host:
        return 'telegram', 'social'
    if 'vk.com' in host or 'vk.ru' in host:
        return 'vk', 'social'
    if host == request.get_host().split(':')[0].casefold():
        return 'direct', ''
    return host, 'referral'


def ensure_tracking(request):
    """Возвращает visitor/session и отмечает необходимость установки cookie."""
    now = timezone.now()
    visitor, new_visitor = _visitor_for_request(request)

    analytics_session_id = _uuid(request.COOKIES.get(SESSION_COOKIE))
    analytics_session = (
        AnalyticsSession.objects.filter(
            pk=analytics_session_id, visitor=visitor,
            last_seen_at__gte=now - SESSION_TIMEOUT,
        ).first()
        if analytics_session_id else None
    )
    requested_source = request.GET.get('utm_source', '').strip()
    if analytics_session is not None and requested_source:
        requested_touch = (
            requested_source[:120], request.GET.get('utm_campaign', '')[:255],
            request.GET.get('utm_content', '')[:255],
        )
        current_touch = (
            analytics_session.source, analytics_session.campaign, analytics_session.content,
        )
        if requested_touch != current_touch:
            # Новый рекламный клик — новое last-touch касание, даже если 30 минут не истекли.
            analytics_session = None
    new_session = analytics_session is None
    if analytics_session is None:
        source, medium = _traffic_source(request)
        analytics_session = AnalyticsSession.objects.create(
            visitor=visitor,
            user=request.user if request.user.is_authenticated else None,
            landing_page=request.get_full_path()[:1000],
            referrer=request.META.get('HTTP_REFERER', '')[:1000],
            source=source[:120], medium=medium[:120],
            campaign=request.GET.get('utm_campaign', '')[:255],
            content=request.GET.get('utm_content', '')[:255],
            term=request.GET.get('utm_term', '')[:255],
            ad_id=(request.GET.get('ad_id') or request.GET.get('yclid')
                   or request.GET.get('vk_click_id') or '')[:255],
            device_type=_device_type(request.META.get('HTTP_USER_AGENT', '')),
            is_new_visitor=new_visitor,
        )
    elif request.user.is_authenticated and analytics_session.user_id != request.user.pk:
        analytics_session.user = request.user
        analytics_session.save(update_fields=('user', 'last_seen_at'))
    elif now - analytics_session.last_seen_at >= SESSION_TOUCH_INTERVAL:
        AnalyticsSession.objects.filter(pk=analytics_session.pk).update(last_seen_at=now)
        analytics_session.last_seen_at = now

    request.analytics_visitor = visitor
    request.analytics_session = analytics_session
    request.analytics_new_visitor = new_visitor
    request.analytics_new_session = new_session
    return visitor, analytics_session


def track_event(request, event_type, *, product=None, value=None, metadata=None, **fields):
    visitor, analytics_session = ensure_tracking(request)
    metadata = metadata or {}
    event = AnalyticsEvent.objects.create(
        session=analytics_session, visitor=visitor,
        user=request.user if request.user.is_authenticated else None,
        event_type=str(event_type)[:80],
        path=fields.get('path', request.get_full_path())[:1000],
        referrer_path=fields.get('referrer_path', '')[:1000],
        page_title=fields.get('page_title', '')[:500],
        product=product,
        product_sku=(fields.get('product_sku') or (product.article if product else ''))[:180],
        brand=(fields.get('brand') or (product.brand if product else ''))[:120],
        category=(fields.get('category') or (product.category_path_key if product else ''))[:500],
        value=value,
        metadata=metadata,
    )
    updates = {'last_seen_at': timezone.now()}
    if event_type == 'page_view':
        updates['page_views'] = F('page_views') + 1
    if event_type in ('scroll_50', 'scroll_75', 'scroll_100'):
        updates['engaged'] = True
    if event_type.startswith('checkout_'):
        updates['checkout_stage'] = event_type.removeprefix('checkout_')[:50]
    if event_type in ('cart_add', 'cart_remove', 'cart_quantity_change') and value is not None:
        updates['cart_value'] = value
    AnalyticsSession.objects.filter(pk=analytics_session.pk).update(**updates)
    return event


def attribution(request):
    """Снимок first-touch и last-touch атрибуции для лида или заказа."""
    visitor, analytics_session = ensure_tracking(request)
    first_session = visitor.sessions.order_by('started_at').first() or analytics_session
    return {
        'source': analytics_session.source,
        'utm_source': analytics_session.source,
        'utm_medium': analytics_session.medium,
        'utm_campaign': analytics_session.campaign,
        'utm_content': analytics_session.content,
        'utm_term': analytics_session.term,
        'ad_id': analytics_session.ad_id,
        'landing_page': analytics_session.landing_page,
        'first_source': first_session.source,
        'first_utm_medium': first_session.medium,
        'first_utm_campaign': first_session.campaign,
        'first_utm_content': first_session.content,
        'first_utm_term': first_session.term,
        'first_ad_id': first_session.ad_id,
        'first_landing_page': first_session.landing_page,
        'analytics_visitor': visitor,
        'analytics_session': analytics_session,
    }


class AnalyticsMiddleware:
    """Отслеживает только успешные HTML-страницы, а не каждый GET сканера."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '').casefold()
        automated = (not user_agent and not settings.TESTING) or any(marker in user_agent for marker in (
            'bot', 'spider', 'crawler', 'slurp', 'safeline', 'headless',
            'curl/', 'wget/', 'python-requests', 'python-urllib',
        ))
        public_html = (
            request.method == 'GET' and response.status_code == 200
            and response.get('Content-Type', '').split(';')[0].strip().lower() == 'text/html'
            and not request.path.startswith(
                ('/api/', '/control/', '/admin/', '/static/', '/media/', '/healthz/')
            )
        )
        if (public_html and not automated and not request.user.is_staff
                and not hasattr(request, 'analytics_visitor')):
            ensure_tracking(request)
        if hasattr(request, 'analytics_visitor'):
            response.set_cookie(
                VISITOR_COOKIE, str(request.analytics_visitor.pk), max_age=63072000,
                httponly=True, samesite='Lax', secure=request.is_secure(),
            )
            response.set_cookie(
                SESSION_COOKIE, str(request.analytics_session.pk), max_age=1800,
                httponly=True, samesite='Lax', secure=request.is_secure(),
            )
        return response