import hashlib
import ipaddress
import logging
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import logout
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .error_views import too_many_requests


logger = logging.getLogger(__name__)


class SensitiveActionRateLimitMiddleware:
    """Второй эшелон rate limit для дорогих и чувствительных POST-действий.

    Основной общий лимит находится в Nginx. Этот limiter не пишет счётчики в БД и
    поэтому сам не превращается в усилитель DDoS. При стандартном LocMemCache его
    счётчики локальны для gunicorn worker; это осознанный дополнительный барьер,
    а не замена общему Nginx-limit.
    """

    RULES = {
        '/account/login/': (10, 300, 'ip'),
        '/account/admin-verification/': (10, 300, 'ip'),
        '/account/password-reset/': (3, 900, 'ip'),
        '/account/register/': (5, 600, 'ip'),
        '/account/verify-email/': (10, 300, 'actor'),
        '/account/verify-email/resend/': (3, 600, 'actor'),
        '/request-product/': (3, 900, 'actor'),
        '/cart/checkout/': (5, 600, 'actor'),
        '/analytics/events/': (120, 60, 'ip'),
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rule = self.RULES.get(request.path)
        if (
            getattr(settings, 'DJANGO_APPLICATION_RATE_LIMIT_ENABLED', False)
            and request.method == 'POST'
            and rule is not None
        ):
            limit, window, scope = rule
            actor = self._actor(request, scope)
            if self._increment(request.path, actor, window) > limit:
                self._log_exceeded(request.path, actor)
                return self._limited_response(request, window)
        return self.get_response(request)

    @classmethod
    def _actor(cls, request, scope):
        if scope == 'actor' and request.user.is_authenticated:
            return f'user:{request.user.pk}'
        return f'ip:{cls._client_ip(request)}'

    @staticmethod
    def _client_ip(request):
        value = request.META.get('REMOTE_ADDR', '')
        if getattr(settings, 'DJANGO_TRUST_X_REAL_IP', False):
            value = request.META.get('HTTP_X_REAL_IP', '') or value
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return 'invalid'

    @staticmethod
    def _increment(path, actor, window):
        digest = hashlib.sha256(f'{path}\0{actor}'.encode()).hexdigest()
        key = f'sensitive-rate:{digest}'
        if cache.add(key, 1, timeout=window):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            # Ключ мог истечь между add и incr.
            cache.set(key, 1, timeout=window)
            return 1

    @staticmethod
    def _log_exceeded(path, actor):
        digest = hashlib.sha256(f'{path}\0{actor}'.encode()).hexdigest()
        if cache.add(f'sensitive-rate-log:{digest}', 1, timeout=60):
            logger.warning(
                'Sensitive action rate limit exceeded: path=%s actor=%s',
                path, hashlib.sha256(actor.encode()).hexdigest()[:12],
            )

    @staticmethod
    def _limited_response(request, retry_after):
        if request.path.startswith('/analytics/') or 'application/json' in request.headers.get('Accept', ''):
            response = JsonResponse(
                {'error': 'rate_limit_exceeded', 'message': 'Слишком много запросов. Повторите позже.'},
                status=429,
            )
        else:
            response = too_many_requests(request, retry_after=retry_after)
        response['Retry-After'] = str(retry_after)
        response['Cache-Control'] = 'no-store'
        return response


class AdminSecondFactorMiddleware:
    """Не допускает staff-сессию к административным URL без свежего e-mail 2FA."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'TESTING', False) and self._requires_verification(request):
            if not self._verified(request):
                next_url = request.get_full_path()
                logout(request)
                return redirect(f'{reverse("shop:login")}?{urlencode({"next": next_url})}')
        return self.get_response(request)

    @staticmethod
    def _requires_verification(request):
        if not request.user.is_authenticated or not request.user.is_staff:
            return False
        path = request.path
        protected = path.startswith('/control/') or path.startswith('/admin/')
        exempt = {reverse('shop:admin_access_verify'), reverse('admin_login_redirect')}
        return protected and path not in exempt

    @staticmethod
    def _verified(request):
        raw = request.session.get('admin_2fa_verified_at')
        verified_at = parse_datetime(raw) if isinstance(raw, str) else None
        if verified_at is None:
            return False
        if timezone.is_naive(verified_at):
            verified_at = timezone.make_aware(verified_at)
        age = (timezone.now() - verified_at).total_seconds()
        return 0 <= age <= getattr(settings, 'ADMIN_2FA_MAX_AGE_SECONDS', 28800)