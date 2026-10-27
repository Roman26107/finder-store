"""HMAC-аутентификация и общие гарантии machine-to-machine API."""

import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
import uuid
from datetime import timedelta
from functools import wraps
from time import monotonic

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import ApiIdempotencyRecord, ApiRequestLog, ApiRequestNonce


API_MAX_BODY_BYTES = 1024 * 1024
API_CLOCK_SKEW_SECONDS = 300
API_NONCE_TTL_SECONDS = 600
API_IDEMPOTENCY_TTL_HOURS = 24
logger = logging.getLogger(__name__)
KEY_ID_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,80}$')


class ApiProblem(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def api_response(data=None, *, status=200, request_id=None, code=None, message=None, details=None):
    if code:
        payload = {'error': {'code': code, 'message': message}}
        if details is not None:
            payload['error']['details'] = details
    else:
        payload = data if data is not None else {}
    if request_id:
        payload['request_id'] = str(request_id)
    response = JsonResponse(payload, status=status, json_dumps_params={'ensure_ascii': False})
    if request_id:
        response['X-Request-ID'] = str(request_id)
    response['Cache-Control'] = 'no-store'
    return response


def parse_json_body(request, *, max_bytes=API_MAX_BODY_BYTES):
    content_length = request.META.get('CONTENT_LENGTH')
    try:
        if content_length and int(content_length) > max_bytes:
            raise ApiProblem(413, 'body_too_large', 'Тело запроса слишком большое.')
    except ValueError as exc:
        raise ApiProblem(400, 'invalid_content_length', 'Некорректный Content-Length.') from exc
    if len(request.body) > max_bytes:
        raise ApiProblem(413, 'body_too_large', 'Тело запроса слишком большое.')
    content_type = request.content_type or ''
    if content_type.split(';', 1)[0].strip().lower() != 'application/json':
        raise ApiProblem(415, 'json_required', 'Ожидается Content-Type application/json.')
    try:
        value = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiProblem(400, 'invalid_json', 'Некорректный JSON.') from exc
    return value


def _configured_keys():
    raw = getattr(settings, 'AURENE_API_KEYS_JSON', '').strip()
    if not raw:
        raise ApiProblem(503, 'api_not_configured', 'API-ключи не настроены на сервере.')
    try:
        keys = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiProblem(503, 'api_not_configured', 'Конфигурация API-ключей некорректна.') from exc
    if not isinstance(keys, dict):
        raise ApiProblem(503, 'api_not_configured', 'Конфигурация API-ключей некорректна.')
    return keys


def _authenticate(request, required_scopes):
    key_id = request.headers.get('X-API-Key-ID', '').strip()
    timestamp_raw = request.headers.get('X-API-Timestamp', '').strip()
    nonce_raw = request.headers.get('X-API-Nonce', '').strip()
    supplied_signature = request.headers.get('X-API-Signature', '').strip().lower()
    if not all((key_id, timestamp_raw, nonce_raw, supplied_signature)):
        raise ApiProblem(401, 'missing_signature', 'Не переданы обязательные заголовки подписи.')
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise ApiProblem(401, 'invalid_credentials', 'Подпись запроса недействительна.')

    config = _configured_keys().get(key_id)
    if not isinstance(config, dict):
        raise ApiProblem(401, 'invalid_credentials', 'Подпись запроса недействительна.')
    secret = config.get('secret')
    scopes = config.get('scopes', [])
    if not isinstance(secret, str) or len(secret.encode('utf-8')) < 32:
        raise ApiProblem(503, 'api_not_configured', 'Секрет API должен содержать не менее 32 байт.')
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise ApiProblem(503, 'api_not_configured', 'Scopes API-ключа настроены некорректно.')
    try:
        timestamp = int(timestamp_raw)
        nonce = uuid.UUID(nonce_raw)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ApiProblem(401, 'invalid_signature_headers', 'Timestamp или nonce некорректен.') from exc
    if abs(int(time.time()) - timestamp) > API_CLOCK_SKEW_SECONDS:
        raise ApiProblem(401, 'expired_signature', 'Время подписи вышло за допустимый интервал.')
    if not supplied_signature or len(supplied_signature) != 64:
        raise ApiProblem(401, 'invalid_credentials', 'Подпись запроса недействительна.')

    body_hash = hashlib.sha256(request.body).hexdigest()
    canonical = '\n'.join((
        request.method.upper(), request.get_full_path(), timestamp_raw,
        str(nonce), body_hash,
    ))
    expected = hmac.new(
        secret.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied_signature):
        raise ApiProblem(401, 'invalid_credentials', 'Подпись запроса недействительна.')

    # Allowlist проверяется только после валидной подписи, чтобы не раскрывать
    # существование key_id неподписанному клиенту.
    allowed_cidrs = config.get('allowed_cidrs', [])
    if not isinstance(allowed_cidrs, list) or not all(
        isinstance(cidr, str) for cidr in allowed_cidrs
    ):
        raise ApiProblem(503, 'api_not_configured', 'CIDR API-ключа настроены некорректно.')
    remote = _remote_address(request)
    if allowed_cidrs:
        try:
            networks = [ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs]
        except ValueError as exc:
            raise ApiProblem(503, 'api_not_configured', 'CIDR API-ключа настроены некорректно.') from exc
        if remote is None or not any(ipaddress.ip_address(remote) in network for network in networks):
            raise ApiProblem(403, 'ip_not_allowed', 'Источник запроса не разрешён для этого ключа.')

    missing = sorted(set(required_scopes) - set(scopes))
    if missing:
        raise ApiProblem(403, 'insufficient_scope', 'У API-ключа недостаточно прав.', missing)

    rate_limit = getattr(settings, 'AURENE_API_RATE_LIMIT_PER_MINUTE', 300)
    recent_requests = ApiRequestLog.objects.filter(
        key_id=key_id,
        created_at__gte=timezone.now() - timedelta(minutes=1),
    ).count()
    if recent_requests >= rate_limit:
        raise ApiProblem(429, 'rate_limit_exceeded', 'Превышен лимит запросов API.')

    try:
        # Собственный savepoint не оставляет внешнюю transaction.atomic()
        # в broken-состоянии при ожидаемом конфликте уникального nonce.
        with transaction.atomic():
            ApiRequestNonce.objects.create(
                nonce=nonce,
                key_id=key_id,
                expires_at=timezone.now() + timedelta(seconds=API_NONCE_TTL_SECONDS),
            )
    except IntegrityError as exc:
        raise ApiProblem(409, 'replayed_request', 'Этот nonce уже был использован.') from exc
    request.api_key_id = key_id
    request.api_scopes = frozenset(scopes)


def _idempotency_record(request):
    raw = request.headers.get('X-Idempotency-Key', '').strip()
    if not raw:
        raise ApiProblem(
            400, 'idempotency_key_required',
            'Для изменяющего запроса требуется X-Idempotency-Key.',
        )
    try:
        key = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ApiProblem(400, 'invalid_idempotency_key', 'X-Idempotency-Key должен быть UUID.') from exc
    request_hash = hashlib.sha256(request.body).hexdigest()
    full_path = request.get_full_path()
    if len(full_path) > 500:
        raise ApiProblem(414, 'uri_too_long', 'URI запроса слишком длинный.')
    now = timezone.now()
    ApiIdempotencyRecord.objects.filter(
        key_id=request.api_key_id, idempotency_key=key, expires_at__lt=now,
    ).delete()
    with transaction.atomic():
        record = ApiIdempotencyRecord.objects.select_for_update().filter(
            key_id=request.api_key_id, idempotency_key=key,
        ).first()
        if record:
            if (
                record.method != request.method or record.path != full_path
                or record.request_hash != request_hash
            ):
                raise ApiProblem(
                    409, 'idempotency_conflict',
                    'Этот X-Idempotency-Key уже использован для другого запроса.',
                )
            if record.response_status is None:
                raise ApiProblem(409, 'request_in_progress', 'Запрос с этим ключом уже выполняется.')
            return record, api_response(
                record.response_body,
                status=record.response_status,
                request_id=request.api_request_id,
            )
        try:
            with transaction.atomic():
                record = ApiIdempotencyRecord.objects.create(
                    key_id=request.api_key_id,
                    idempotency_key=key,
                    method=request.method,
                    path=full_path,
                    request_hash=request_hash,
                    expires_at=now + timedelta(hours=API_IDEMPOTENCY_TTL_HOURS),
                )
        except IntegrityError as exc:
            raise ApiProblem(409, 'request_in_progress', 'Запрос с этим ключом уже выполняется.') from exc
    return record, None


def _remote_address(request):
    value = request.META.get('REMOTE_ADDR', '')
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def api_endpoint(*, methods, scopes=(), idempotent=False):
    """Аутентифицирует endpoint, унифицирует ошибки, аудит и idempotency."""
    allowed = frozenset(method.upper() for method in methods)

    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            started = monotonic()
            request.api_request_id = uuid.uuid4()
            result_code = 'ok'
            response = None
            record = None
            try:
                if request.method.upper() not in allowed:
                    raise ApiProblem(405, 'method_not_allowed', 'Метод запроса не поддерживается.')
                if getattr(settings, 'AURENE_API_REQUIRE_HTTPS', False) and not request.is_secure():
                    raise ApiProblem(400, 'https_required', 'API доступен только по HTTPS.')
                remote = _remote_address(request)
                ip_rate_limit = getattr(settings, 'AURENE_API_IP_RATE_LIMIT_PER_MINUTE', 600)
                if remote and ApiRequestLog.objects.filter(
                    remote_addr=remote,
                    created_at__gte=timezone.now() - timedelta(minutes=1),
                ).count() >= ip_rate_limit:
                    raise ApiProblem(429, 'ip_rate_limit_exceeded', 'Превышен лимит запросов с IP-адреса.')
                content_length = request.META.get('CONTENT_LENGTH')
                if content_length and int(content_length) > API_MAX_BODY_BYTES:
                    raise ApiProblem(413, 'body_too_large', 'Тело запроса слишком большое.')
                if len(request.body) > API_MAX_BODY_BYTES:
                    raise ApiProblem(413, 'body_too_large', 'Тело запроса слишком большое.')
                _authenticate(request, scopes)
                if idempotent:
                    with transaction.atomic():
                        record, cached = _idempotency_record(request)
                        if cached is not None:
                            cached['X-Idempotent-Replay'] = 'true'
                            response = cached
                            result_code = 'idempotent_replay'
                            return cached
                        response = view(request, *args, **kwargs)
                        try:
                            body = json.loads(response.content)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            raise RuntimeError('Idempotent API endpoint returned non-JSON response.')
                        body.pop('request_id', None)
                        record.response_status = response.status_code
                        record.response_body = body
                        record.save(update_fields=('response_status', 'response_body'))
                else:
                    response = view(request, *args, **kwargs)
                return response
            except RequestDataTooBig:
                result_code = 'body_too_large'
                response = api_response(
                    status=413, code=result_code,
                    message='Тело запроса слишком большое.',
                    request_id=request.api_request_id,
                )
                return response
            except ApiProblem as exc:
                result_code = exc.code
                response = api_response(
                    status=exc.status, code=exc.code, message=exc.message,
                    details=exc.details, request_id=request.api_request_id,
                )
                return response
            except (TypeError, ValueError) as exc:
                result_code = 'invalid_request'
                response = api_response(
                    status=400, code=result_code, message='Некорректный запрос.',
                    request_id=request.api_request_id,
                )
                return response
            except Exception:
                result_code = 'internal_error'
                logger.exception('Unhandled API error; request_id=%s', request.api_request_id)
                response = api_response(
                    status=500, code=result_code, message='Внутренняя ошибка API.',
                    request_id=request.api_request_id,
                )
                return response
            finally:
                try:
                    # Не усиливаем DDoS дополнительной записью в БД после того,
                    # как окно rate limit уже заполнено журналом.
                    if result_code not in {'rate_limit_exceeded', 'ip_rate_limit_exceeded'}:
                        ApiRequestLog.objects.create(
                            request_id=request.api_request_id,
                            key_id=getattr(
                                request, 'api_key_id',
                                request.headers.get('X-API-Key-ID', '')[:80],
                            ),
                            method=request.method[:8],
                            path=request.path[:500],
                            status_code=response.status_code if response is not None else 500,
                            result_code=result_code,
                            remote_addr=_remote_address(request),
                            duration_ms=max(0, int((monotonic() - started) * 1000)),
                        )
                except Exception:
                    pass
        return wrapped
    return decorator
