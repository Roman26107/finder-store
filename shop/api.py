"""Версионированный machine-to-machine API каталога, заказов и уведомлений."""

import base64
import binascii
import re
import uuid
from math import ceil
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .api_auth import ApiProblem, api_endpoint, api_response, parse_json_body
from .models import (
    AnalyticsSession, NotificationEvent, Order, Product, ProductRequest,
    SecurityChallenge,
)
from .analytics_reports import identity_count, qualified_analytics_sessions
from .pricing import normalize_currency
from .statuses import status_icon


CATALOG_PAGE_MAX = 100
CATALOG_BULK_MAX = 100
NOTIFICATION_CLAIM_MAX = 100
ADMIN_ORDER_SEARCH_MAX_LENGTH = 100
ADMIN_ORDER_TYPES = {'', 'cart', 'personal'}


def _iso(value):
    return value.isoformat() if value else None


def _money(value):
    return format(value, 'f') if value is not None else None


def _absolute_url(request, value):
    return request.build_absolute_uri(value) if value else ''


def _original_product_title(product):
    if product is None or not isinstance(product.raw_data, dict):
        return None
    value = product.raw_data.get('title')
    if isinstance(value, dict):
        value = value.get('raw')
    if not isinstance(value, str):
        return None
    original = ' '.join(value.split())
    translated = ' '.join((product.title or '').split())
    if not original or original.casefold() == translated.casefold():
        return None
    return original


def serialize_product_summary(product):
    return {
        'id': product.pk,
        'catalog_scope': product.catalog_scope,
        'sku': product.sku,
        'article': product.article,
        'brand': product.brand,
        'title': product.title,
        'source_url': product.source_url,
        'source_price': _money(product.price),
        'source_currency': product.currency,
        'display_price_rub': _money(product.display_price),
        'in_stock': product.in_stock_ecom,
        'is_active': product.is_active,
        'updated_at': _iso(product.updated_at),
    }


def serialize_product(request, product):
    return {
        **serialize_product_summary(product),
        'sku_without_size': product.sku_without_size,
        'product_code': product.product_code,
        'slug': product.slug,
        'storefront_url': request.build_absolute_uri(product.get_absolute_url()),
        'pricing': {
            'mode': product.pricing_mode,
            'fixed_price_rub': _money(product.fixed_price_rub),
            'display_price_rub': _money(product.display_price),
        },
        'attributes': {
            'current_color': product.current_color,
            'color_group': product.color_group,
            'current_size': product.current_size,
            'gender': product.gender,
            'materials': product.material,
            'dimensions': product.dimensions,
            'made_in': product.made_in,
            'collection': product.collection,
        },
        'content': {
            'description': product.description,
            'product_detail_html': product.product_detail_html,
            'care_instruction_html': product.care_instruction_html,
            'story_behind': product.story_behind,
            'i18n': product.i18n,
        },
        'category': {
            'name': product.category_name,
            'department_slug': product.category_department_slug,
            'department_name': product.category_department_name,
            'path': product.category_path,
            'path_key': product.category_path_key,
            'keys': product.category_keys,
            'breadcrumbs': product.categories_breadcrumbs,
        },
        'family_key': product.family_key,
        'fetched_at': product.fetched_at,
        'created_at': _iso(product.created_at),
        'images': [{
            'id': image.pk,
            'tag': image.tag,
            'position': image.position,
            'alt': image.alt,
            'source_local_path': image.source_local_path,
            'urls': {
                'thumbnail_source': image.thumbnail,
                'medium_source': image.medium,
                'large_source': image.large,
                'original_source': image.original,
                'local': _absolute_url(
                    request, image.local_image.url if image.local_image else ''
                ),
                'preview': _absolute_url(request, image.preview_url),
                'gallery': _absolute_url(
                    request, image.gallery_image.url if image.gallery_image else ''
                ),
                'detail': _absolute_url(
                    request, image.detail_image.url if image.detail_image else ''
                ),
                'display': _absolute_url(request, image.product_page_url),
            },
        } for image in product.images.all()],
        'color_variants': [{
            'id': item.pk,
            'sku': item.sku,
            'color': item.color,
            'price': _money(item.price),
            'source_url': item.source_url,
            'thumbnail': item.thumbnail,
            'in_stock': item.in_stock_ecom,
        } for item in product.color_variants.all()],
        'size_variants': [{
            'id': item.pk,
            'sku': item.sku,
            'size': item.size,
            'price': _money(item.price),
            'in_stock': item.in_stock_ecom,
        } for item in product.size_variants.all()],
        'raw_data': product.raw_data,
    }


def serialize_order(order, *, include_private=False):
    payload = {
        'id': order.pk,
        'number': order.number,
        'user_id': order.user_id,
        'status': order.status,
        'status_label': order.get_status_display(),
        'status_icon': status_icon(order.status),
        'estimated_total': _money(order.estimated_total),
        'subtotal': _money(order.subtotal),
        'discount_amount': _money(order.discount_amount),
        'promo_code': order.promo_code,
        'delivery_method': order.delivery_method,
        'delivery_method_label': order.get_delivery_method_display() if order.delivery_method else None,
        'delivery_cost': _money(order.delivery_cost),
        'payment_method': order.payment_method,
        'payment_method_label': order.get_payment_method_display() if order.payment_method else None,
        'currency': order.currency,
        'paid_at': _iso(order.paid_at),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'items': [{
            'product_id': item.product_id,
            'title': item.product_title,
            'brand': item.brand,
            'sku': item.sku,
            'color': item.color,
            'size': item.size,
            'unit_price': _money(item.unit_price),
            'currency': item.currency,
            'quantity': item.quantity,
            'subtotal': _money(item.subtotal),
        } for item in order.items.all()],
    }
    if include_private:
        payload['customer'] = {
            'name': order.customer_name,
            'email': order.customer_email,
            'phone': order.customer_phone,
            'telegram': order.customer_telegram,
            'preferred_contact': order.preferred_contact,
            'delivery_address': order.delivery_address,
        }
    return payload


def serialize_product_request(request, product_request, *, include_private=False):
    payload = {
        'id': product_request.pk,
        'number': product_request.number,
        'user_id': product_request.user_id,
        'status': product_request.status,
        'status_label': product_request.get_status_display(),
        'status_icon': status_icon(product_request.status),
        'estimated_value': _money(product_request.estimated_value),
        'created_at': _iso(product_request.created_at),
        'updated_at': _iso(product_request.updated_at),
        'items': [{
            'brand': item.brand,
            'title': item.product_name,
            'article': item.article,
            'color': item.color,
            'size': item.size,
            'source_url': item.source_url,
            'reference_image': _absolute_url(
                request, item.reference_image.url if item.reference_image else ''
            ),
        } for item in product_request.items.all()],
    }
    if include_private:
        payload['customer'] = {
            'name': product_request.customer_name,
            'email': product_request.customer_email,
            'phone': product_request.customer_phone,
            'telegram': product_request.customer_telegram,
            'preferred_contact': product_request.preferred_contact or None,
            'preferred_contact_label': (
                product_request.get_preferred_contact_display()
                if product_request.preferred_contact else None
            ),
            'delivery_address': product_request.delivery_address or None,
        }
    return payload


def _status_choices(model):
    return [
        {'value': value, 'label': label, 'icon': status_icon(value)}
        for value, label in model.Status.choices
    ]


def serialize_admin_order(request, order):
    payload = serialize_order(order, include_private=True)
    payload.update({
        'type': 'cart',
        'type_label': 'Заказ',
        'admin_url': request.build_absolute_uri(
            reverse('shop:control_order_detail', args=[order.pk])
        ),
        'status_choices': _status_choices(Order),
    })
    payload['customer']['preferred_contact_label'] = order.get_preferred_contact_display()
    for payload_item, item in zip(payload['items'], order.items.all()):
        product = item.product
        payload_item.update({
            'original_title': _original_product_title(product),
            'product_url': (
                request.build_absolute_uri(product.get_absolute_url()) if product else ''
            ),
            'source_url': product.source_url if product else '',
        })
    return payload


def serialize_admin_product_request(request, product_request):
    payload = serialize_product_request(request, product_request, include_private=True)
    payload.update({
        'type': 'personal',
        'type_label': 'Заявка',
        'currency': None,
        'total': _money(product_request.estimated_value),
        'admin_url': request.build_absolute_uri(
            reverse('shop:control_request_detail', args=[product_request.pk])
        ),
        'status_choices': _status_choices(ProductRequest),
    })
    for item in payload['items']:
        item.update({
            'unit_price': None, 'currency': None,
            'quantity': 1, 'subtotal': None,
            'original_title': None,
            'product_url': '',
        })
    return payload


def serialize_notification(event):
    event_payload = dict(event.payload)
    if event.event_type == NotificationEvent.EventType.ORDER_CREATED and event.order_id:
        event_payload.update({
            'customer_email': event.order.customer_email,
            'preferred_contact': event.order.preferred_contact,
            'preferred_contact_label': event.order.get_preferred_contact_display(),
            'delivery_address': event.order.delivery_address,
            'items': [{
            'title': item.product_title,
            'brand': item.brand,
            'sku': item.sku,
            'color': item.color,
            'size': item.size,
            'unit_price': _money(item.unit_price),
            'currency': item.currency,
            'quantity': item.quantity,
            'subtotal': _money(item.subtotal),
        } for item in event.order.items.all()],
        })
    elif (
        event.event_type == NotificationEvent.EventType.REQUEST_CREATED
        and event.product_request_id
    ):
        event_payload['items'] = [{
            'brand': item.brand,
            'title': item.product_name,
            'article': item.article,
            'color': item.color,
            'size': item.size,
            'unit_price': None,
            'currency': None,
            'quantity': 1,
            'subtotal': None,
        } for item in event.product_request.items.all()]
        event_payload.update({
            'customer_email': event.product_request.customer_email,
            'preferred_contact': event.product_request.preferred_contact or None,
            'preferred_contact_label': (
                event.product_request.get_preferred_contact_display()
                if event.product_request.preferred_contact else None
            ),
            'delivery_address': event.product_request.delivery_address or None,
            'estimated_total': _money(event.product_request.estimated_value),
            'currency': None,
        })
    payload = {
        'id': str(event.pk),
        'type': event.event_type,
        'audience': event.audience,
        'channel': event.channel,
        'user_id': event.user_id,
        'order_id': event.order_id,
        'order_number': event.order.number if event.order_id and event.order else None,
        'request_id': event.product_request_id,
        'request_number': (
            event.product_request.number
            if event.product_request_id and event.product_request else None
        ),
        'payload': event_payload,
        'attempts': event.attempts,
        'lease_id': str(event.lease_id) if event.lease_id else None,
        'created_at': _iso(event.created_at),
    }
    return payload


def _integer_param(request, name, default, minimum, maximum):
    raw = request.GET.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiProblem(400, f'invalid_{name}', f'Параметр {name} должен быть целым числом.') from exc
    if not minimum <= value <= maximum:
        raise ApiProblem(
            400, f'invalid_{name}', f'Параметр {name} должен быть от {minimum} до {maximum}.',
        )
    return value


def _encode_cursor(product):
    value = f'{product.updated_at.isoformat()}|{product.pk}'.encode('utf-8')
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _decode_cursor(value):
    try:
        padded = value + '=' * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode('utf-8')
        timestamp_raw, pk_raw = decoded.rsplit('|', 1)
        timestamp = parse_datetime(timestamp_raw)
        pk = int(pk_raw)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ApiProblem(400, 'invalid_cursor', 'Некорректный cursor.') from exc
    if timestamp is None or timezone.is_naive(timestamp) or pk < 1:
        raise ApiProblem(400, 'invalid_cursor', 'Некорректный cursor.')
    return timestamp, pk


@api_endpoint(methods=('GET',), scopes=('catalog:read',))
def catalog_products(request):
    limit = _integer_param(request, 'limit', 50, 1, CATALOG_PAGE_MAX)
    queryset = Product.objects.order_by('updated_at', 'pk')
    updated_since = request.GET.get('updated_since', '').strip()
    if updated_since:
        parsed = parse_datetime(updated_since)
        if parsed is None or timezone.is_naive(parsed):
            raise ApiProblem(
                400, 'invalid_updated_since',
                'updated_since должен быть ISO 8601 с часовым поясом.',
            )
        queryset = queryset.filter(updated_at__gte=parsed)
    cursor = request.GET.get('cursor', '').strip()
    if cursor:
        cursor_time, cursor_pk = _decode_cursor(cursor)
        queryset = queryset.filter(
            Q(updated_at__gt=cursor_time)
            | Q(updated_at=cursor_time, pk__gt=cursor_pk)
        )
    rows = list(queryset[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return api_response({
        'results': [serialize_product_summary(product) for product in rows],
        'next_cursor': _encode_cursor(rows[-1]) if has_more and rows else None,
        'has_more': has_more,
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('catalog:read',))
def catalog_product_detail(request, product_id):
    product = Product.objects.prefetch_related(
        'images', 'color_variants', 'size_variants'
    ).filter(pk=product_id).first()
    if product is None:
        raise ApiProblem(404, 'product_not_found', 'Товар не найден.')
    return api_response(
        {'product': serialize_product(request, product)},
        request_id=request.api_request_id,
    )


def _decimal(value, field, *, minimum=Decimal('0')):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ApiProblem(400, 'invalid_price', f'{field} должен быть числом.') from exc
    if not parsed.is_finite() or parsed < minimum or parsed >= Decimal('1000000000000'):
        raise ApiProblem(400, 'invalid_price', f'{field} выходит за допустимый диапазон.')
    try:
        return parsed.quantize(Decimal('0.01'))
    except InvalidOperation as exc:
        raise ApiProblem(400, 'invalid_price', f'{field} имеет недопустимую точность.') from exc


def _aware_datetime(value, field):
    parsed = parse_datetime(str(value or ''))
    if parsed is None or timezone.is_naive(parsed):
        raise ApiProblem(400, 'invalid_timestamp', f'{field} должен быть ISO 8601 с часовым поясом.')
    return parsed


def _apply_variant_updates(product, key, model, rows):
    if rows is None:
        return 0
    if not isinstance(rows, list) or len(rows) > 200:
        raise ApiProblem(400, 'invalid_variants', f'{key} должен быть массивом до 200 элементов.')
    allowed = {'id', 'sku', 'price', 'in_stock'}
    updates = 0
    existing_rows = list(model.objects.filter(product=product))
    existing_by_id = {item.pk: item for item in existing_rows}
    existing_by_sku = {}
    for item in existing_rows:
        existing_by_sku.setdefault(item.sku, []).append(item)
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - allowed:
            raise ApiProblem(400, 'invalid_variants', f'{key}[{index}] содержит недопустимые поля.')
        sku = str(row.get('sku') or '').strip()
        item_id = row.get('id')
        if item_id is not None:
            if not isinstance(item_id, int) or isinstance(item_id, bool):
                raise ApiProblem(400, 'invalid_variant_id', f'{key}[{index}].id должен быть целым числом.')
            item = existing_by_id.get(item_id)
            if item is not None and sku and item.sku != sku:
                raise ApiProblem(409, 'variant_mismatch', f'{key}[{index}] SKU не соответствует id.')
        else:
            matches = existing_by_sku.get(sku, [])
            if len(matches) > 1:
                raise ApiProblem(
                    409, 'ambiguous_variant',
                    f'{key}[{index}] имеет неуникальный SKU; используйте id.',
                )
            item = matches[0] if matches else None
        if item is None:
            raise ApiProblem(400, 'unknown_variant', f'Вариант {sku or index} не найден.')
        if item.pk in seen:
            raise ApiProblem(400, 'duplicate_variant', f'{key}[{index}] передан повторно.')
        seen.add(item.pk)
        changed = []
        if 'price' in row:
            item.price = _decimal(row['price'], f'{key}[{index}].price')
            changed.append('price')
        if 'in_stock' in row:
            if not isinstance(row['in_stock'], bool):
                raise ApiProblem(400, 'invalid_stock', f'{key}[{index}].in_stock должен быть boolean.')
            item.in_stock_ecom = row['in_stock']
            changed.append('in_stock_ecom')
        if changed:
            item.save(update_fields=changed)
            updates += 1
    return updates


def _update_product(payload, *, product_id=None):
    if not isinstance(payload, dict):
        raise ApiProblem(400, 'invalid_product', 'Карточка обновления должна быть объектом.')
    allowed = {
        'sku', 'price', 'currency', 'in_stock', 'expected_updated_at',
        'color_variants', 'size_variants',
    }
    extra = sorted(set(payload) - allowed)
    if extra:
        raise ApiProblem(400, 'unknown_fields', 'Переданы недопустимые поля.', extra)
    sku = str(payload.get('sku') or '').strip()
    if product_id is None and not sku:
        raise ApiProblem(400, 'sku_required', 'Поле sku обязательно.')
    lookup = {'pk': product_id} if product_id is not None else {'sku': sku}
    product = Product.objects.select_for_update().filter(**lookup).first()
    if product is None:
        raise ApiProblem(404, 'product_not_found', 'Товар не найден.')
    if sku and sku != product.sku:
        raise ApiProblem(409, 'sku_mismatch', 'SKU не соответствует product_id.')
    sku = product.sku
    expected = _aware_datetime(payload.get('expected_updated_at'), 'expected_updated_at')
    if product.updated_at != expected:
        raise ApiProblem(409, 'stale_product', 'Карточка товара уже была изменена.', {
            'sku': sku, 'current_updated_at': _iso(product.updated_at),
        })
    changed = []
    if 'price' in payload:
        product.price = _decimal(payload['price'], 'price')
        changed.append('price')
    if 'currency' in payload:
        currency = normalize_currency(payload['currency'])
        if currency not in {'CNY', 'USD', 'EUR', 'RUB'}:
            raise ApiProblem(400, 'invalid_currency', 'Неподдерживаемая валюта.')
        product.currency = currency
        changed.append('currency')
    if 'in_stock' in payload:
        if not isinstance(payload['in_stock'], bool):
            raise ApiProblem(400, 'invalid_stock', 'in_stock должен быть boolean.')
        product.in_stock_ecom = payload['in_stock']
        changed.append('in_stock_ecom')
    if changed:
        # auto_now не добавляется при update_fields автоматически.
        product.updated_at = timezone.now()
        product.save(update_fields=(*changed, 'updated_at'))
    color_count = _apply_variant_updates(
        product, 'color_variants', product.color_variants.model,
        payload.get('color_variants'),
    )
    size_count = _apply_variant_updates(
        product, 'size_variants', product.size_variants.model,
        payload.get('size_variants'),
    )
    if not changed and (color_count or size_count):
        product.updated_at = timezone.now()
        product.save(update_fields=('updated_at',))
    return product, color_count + size_count


@api_endpoint(methods=('PATCH',), scopes=('catalog:write',), idempotent=True)
def catalog_product_update(request, product_id):
    payload = parse_json_body(request)
    if not isinstance(payload, dict):
        raise ApiProblem(400, 'invalid_product', 'Тело запроса должно быть объектом.')
    with transaction.atomic():
        product, variants_updated = _update_product(payload, product_id=product_id)
    return api_response({
        'product': serialize_product_summary(product),
        'variants_updated': variants_updated,
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('catalog:write',), idempotent=True)
def catalog_bulk_update(request):
    payload = parse_json_body(request)
    rows = payload.get('products') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= CATALOG_BULK_MAX:
        raise ApiProblem(
            400, 'invalid_products',
            f'products должен содержать от 1 до {CATALOG_BULK_MAX} объектов.',
        )
    with transaction.atomic():
        results = []
        for row in rows:
            product, variants_updated = _update_product(row)
            results.append({
                'sku': product.sku,
                'updated_at': _iso(product.updated_at),
                'variants_updated': variants_updated,
            })
    return api_response(
        {'updated': len(results), 'results': results},
        request_id=request.api_request_id,
    )


@api_endpoint(methods=('GET',), scopes=('orders:read',))
def orders(request):
    user_raw = request.GET.get('user_id', '').strip()
    if not user_raw:
        raise ApiProblem(400, 'user_id_required', 'Параметр user_id обязателен.')
    try:
        user_id = int(user_raw)
    except ValueError as exc:
        raise ApiProblem(400, 'invalid_user_id', 'user_id должен быть целым числом.') from exc
    if user_id < 1:
        raise ApiProblem(400, 'invalid_user_id', 'user_id должен быть положительным.')
    rows = Order.objects.filter(user_id=user_id).prefetch_related('items')[:50]
    requests = ProductRequest.objects.filter(user_id=user_id).prefetch_related('items')[:50]
    return api_response({
        'orders': [serialize_order(order) for order in rows],
        'product_requests': [
            serialize_product_request(request, product_request)
            for product_request in requests
        ],
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('orders:read',))
def order_detail(request, number):
    if not (
        (number.startswith('ORD-') or number.startswith('REQ-'))
        and number[4:].isdigit()
    ):
        raise ApiProblem(404, 'order_not_found', 'Заказ не найден.')
    user_raw = request.GET.get('user_id', '').strip()
    try:
        user_id = int(user_raw)
    except ValueError as exc:
        raise ApiProblem(400, 'invalid_user_id', 'Для заказа требуется корректный user_id.') from exc
    if user_id < 1:
        raise ApiProblem(400, 'invalid_user_id', 'Для заказа требуется корректный user_id.')
    if number.startswith('ORD-'):
        order = Order.objects.prefetch_related('items').filter(
            pk=int(number[4:]), user_id=user_id,
        ).first()
        if order is None:
            raise ApiProblem(404, 'order_not_found', 'Заказ не найден.')
        payload = {'type': 'cart', 'order': serialize_order(order, include_private=True)}
    else:
        product_request = ProductRequest.objects.prefetch_related('items').filter(
            pk=int(number[4:]), user_id=user_id,
        ).first()
        if product_request is None:
            raise ApiProblem(404, 'order_not_found', 'Заказ не найден.')
        payload = {
            'type': 'personal',
            'order': serialize_product_request(
                request, product_request, include_private=True
            ),
        }
    return api_response(payload, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('telegram:admins:read',))
def telegram_administrators(request):
    from telegram_bot.models import TelegramAccount

    accounts = TelegramAccount.objects.filter(
        role=TelegramAccount.Role.ADMINISTRATOR,
        is_active=True,
        user__is_active=True,
    ).select_related('user').order_by('pk')
    verified_since = timezone.now() - timedelta(hours=8)
    results = []
    for account in accounts:
        verified = SecurityChallenge.objects.filter(
            target_user_id=account.user_id,
            purpose=SecurityChallenge.Purpose.TELEGRAM_LOGIN,
            consumed_at__gte=verified_since,
        ).order_by('-consumed_at').first()
        results.append({
            'user_id': account.user_id,
            'chat_id': account.chat_id,
            'username': account.username,
            'name': account.user.get_full_name() or account.user.username,
            'admin_verified': verified is not None,
            'admin_verified_at': verified.consumed_at.isoformat() if verified else None,
        })
    return api_response({
        'results': results,
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('telegram:admins:read',))
def telegram_account(request, chat_id):
    from telegram_bot.models import TelegramAccount

    account = TelegramAccount.objects.filter(
        chat_id=chat_id, is_active=True, user__is_active=True,
    ).select_related('user').first()
    if account is None:
        raise ApiProblem(404, 'telegram_account_not_found', 'Привязка Telegram не найдена.')
    return api_response({
        'account': {
            'user_id': account.user_id,
            'chat_id': account.chat_id,
            'username': account.username,
            'name': account.user.get_full_name() or account.user.username,
            'role': account.role,
        },
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('telegram:admins:read',))
def telegram_link_claim(request):
    from telegram_bot.linking import claim_link_session

    payload = parse_json_body(request)
    token = str(payload.get('token') or '').strip()
    try:
        chat_id = int(payload.get('chat_id'))
    except (TypeError, ValueError) as exc:
        raise ApiProblem(400, 'invalid_chat_id', 'Некорректный Telegram chat ID.') from exc
    session, code, message = claim_link_session(
        token, chat_id=chat_id, username=payload.get('username', ''),
    )
    if session is None:
        raise ApiProblem(404, 'link_session_not_found', message)
    if code is None:
        raise ApiProblem(409, 'link_session_unavailable', message)
    return api_response({
        'code': code,
        'expires_at': session.expires_at.isoformat(),
        'account_name': session.user.get_full_name() or session.user.username,
        'confirm_url': request.build_absolute_uri(
            f'{reverse("shop:telegram_link")}?token={session.token}'
        ),
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('telegram:admins:read',))
def telegram_registration_claim(request):
    from telegram_bot.linking import claim_registration_session

    payload = parse_json_body(request)
    token = str(payload.get('token') or '').strip()
    try:
        chat_id = int(payload.get('chat_id'))
    except (TypeError, ValueError) as exc:
        raise ApiProblem(400, 'invalid_chat_id', 'Некорректный Telegram chat ID.') from exc
    session, code, message = claim_registration_session(
        token, chat_id=chat_id, username=payload.get('username', ''),
    )
    if session is None:
        raise ApiProblem(404, 'registration_session_not_found', message)
    if code is None:
        raise ApiProblem(409, 'registration_session_unavailable', message)
    return api_response({
        'code': code,
        'expires_at': session.expires_at.isoformat(),
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('telegram:admins:read',))
def telegram_admin_challenge(request, chat_id):
    from datetime import timedelta
    from telegram_bot.models import TelegramAccount
    from .security import SecurityChallengeDeliveryError, create_security_challenge

    account = TelegramAccount.objects.filter(
        chat_id=chat_id, role=TelegramAccount.Role.ADMINISTRATOR,
        is_active=True, user__is_active=True,
    ).select_related('user').first()
    if account is None:
        raise ApiProblem(403, 'admin_access_denied', 'Административный доступ не разрешён.')
    latest = SecurityChallenge.objects.filter(
        target_user=account.user,
        purpose=SecurityChallenge.Purpose.TELEGRAM_LOGIN,
        consumed_at__isnull=True,
        created_at__gte=timezone.now() - timedelta(minutes=1),
    ).first()
    if latest:
        raise ApiProblem(429, 'challenge_rate_limited', 'Новый код можно запросить через минуту.')
    try:
        challenge = create_security_challenge(
            target_user=account.user,
            purpose=SecurityChallenge.Purpose.TELEGRAM_LOGIN,
            context={'chat_id': chat_id},
        )
    except (SecurityChallengeDeliveryError, ValueError) as exc:
        raise ApiProblem(503, 'email_delivery_failed', 'Не удалось отправить код на e-mail.') from exc
    return api_response({
        'challenge_id': str(challenge.pk),
        'expires_at': challenge.expires_at.isoformat(),
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('telegram:admins:read',))
def telegram_admin_verify(request, chat_id):
    from telegram_bot.models import TelegramAccount
    from .security import verify_security_challenge

    account = TelegramAccount.objects.filter(
        chat_id=chat_id, role=TelegramAccount.Role.ADMINISTRATOR,
        is_active=True, user__is_active=True,
    ).select_related('user').first()
    if account is None:
        raise ApiProblem(403, 'admin_access_denied', 'Административный доступ не разрешён.')
    payload = parse_json_body(request)
    challenge, message = verify_security_challenge(
        payload.get('challenge_id'), str(payload.get('code') or ''),
        purpose=SecurityChallenge.Purpose.TELEGRAM_LOGIN,
        target_user_id=account.user_id,
    )
    if challenge is None or challenge.context.get('chat_id') != chat_id:
        raise ApiProblem(400, 'invalid_security_code', message)
    return api_response({'verified': True}, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('telegram:admins:read',))
def manager_contacts(request):
    from telegram_bot.models import ManagerContact

    contacts = list(ManagerContact.objects.filter(is_active=True).order_by('position', 'pk'))
    kind_order = {'phone': 0, 'telegram': 1, 'whatsapp': 2, 'email': 3, 'custom': 4}
    contacts.sort(key=lambda contact: (kind_order.get(contact.kind, 4), contact.position, contact.pk))
    return api_response({
        'results': [{
            'id': contact.pk,
            'kind': contact.kind,
            'label': contact.label,
            'value': contact.value,
            'url': contact.url,
        } for contact in contacts],
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('telegram:admins:read',))
def telegram_customer_recipient(request, user_id):
    from telegram_bot.models import TelegramAccount

    account = TelegramAccount.objects.filter(
        user_id=user_id, is_active=True, user__is_active=True,
    ).select_related('user').first()
    if account is None:
        raise ApiProblem(404, 'recipient_not_found', 'Telegram получателя не привязан.')
    return api_response({
        'account': {
            'user_id': account.user_id,
            'chat_id': account.chat_id,
            'username': account.username,
            'name': account.user.get_full_name() or account.user.username,
            'role': account.role,
        },
    }, request_id=request.api_request_id)


def _customer_user_id(request):
    user_raw = request.GET.get('user_id', '').strip()
    try:
        user_id = int(user_raw)
    except ValueError as exc:
        raise ApiProblem(400, 'invalid_user_id', 'Требуется корректный user_id.') from exc
    if not 1 <= user_id <= 9223372036854775807:
        raise ApiProblem(400, 'invalid_user_id', 'Требуется корректный user_id.')
    return user_id


def _customer_order_object(request, number):
    user_id = _customer_user_id(request)
    if not (
        isinstance(number, str)
        and (number.startswith('ORD-') or number.startswith('REQ-'))
        and number[4:].isdigit()
    ):
        raise ApiProblem(404, 'order_not_found', 'Заказ или заявка не найдены.')
    model = Order if number.startswith('ORD-') else ProductRequest
    prefetch = 'items__product' if model is Order else 'items'
    instance = model.objects.prefetch_related(prefetch).filter(
        pk=int(number[4:]), user_id=user_id,
    ).first()
    if instance is None:
        raise ApiProblem(404, 'order_not_found', 'Заказ или заявка не найдены.')
    return instance


def _customer_order_payload(request, instance):
    if isinstance(instance, Order):
        payload = serialize_admin_order(request, instance)
    else:
        payload = serialize_admin_product_request(request, instance)
    payload.pop('admin_url', None)
    payload.pop('customer', None)
    payload.pop('status_choices', None)
    payload.pop('user_id', None)
    return payload


@api_endpoint(methods=('GET',), scopes=('orders:read',))
def customer_order_history(request):
    user_id = _customer_user_id(request)
    page = _integer_param(request, 'page', 1, 1, 1000000)
    limit = _integer_param(request, 'limit', 8, 1, 20)
    orders = Order.objects.filter(user_id=user_id)
    requests = ProductRequest.objects.filter(user_id=user_id)
    total = orders.count() + requests.count()
    total_pages = max(1, ceil(total / limit))
    end = page * limit
    order_rows = list(orders.only(
        'id', 'status', 'created_at', 'updated_at', 'customer_name',
    ).order_by('-created_at', '-pk')[:end])
    request_rows = list(requests.only(
        'id', 'status', 'created_at', 'updated_at', 'customer_name',
    ).order_by('-created_at', '-pk')[:end])
    combined = [{
        'type': 'cart', 'type_label': 'Заказ', 'id': item.pk,
        'number': item.number, 'status': item.status,
        'status_label': item.get_status_display(), 'status_icon': status_icon(item.status),
        'created_at': _iso(item.created_at), 'updated_at': _iso(item.updated_at),
    } for item in order_rows] + [{
        'type': 'personal', 'type_label': 'Заявка', 'id': item.pk,
        'number': item.number, 'status': item.status,
        'status_label': item.get_status_display(), 'status_icon': status_icon(item.status),
        'created_at': _iso(item.created_at), 'updated_at': _iso(item.updated_at),
    } for item in request_rows]
    combined.sort(key=lambda item: (item['created_at'], item['id']), reverse=True)
    start = (page - 1) * limit
    return api_response({
        'results': combined[start:end] if page <= total_pages else [],
        'pagination': {
            'page': page, 'limit': limit, 'total': total,
            'total_pages': total_pages, 'has_previous': page > 1,
            'has_next': page < total_pages,
        },
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('orders:read',))
def customer_order_detail(request, number):
    instance = _customer_order_object(request, number)
    return api_response({
        'order': _customer_order_payload(request, instance),
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('orders:admin',))
def admin_dashboard(request):
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_start = timezone.make_aware(
        local_now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    )
    tomorrow_start = today_start + timedelta(days=1)
    sessions = qualified_analytics_sessions(AnalyticsSession.objects.filter(
        started_at__gte=today_start, started_at__lt=tomorrow_start,
    ))
    order_counts = dict(Order.objects.values_list('status').annotate(total=Count('id')))
    request_counts = dict(
        ProductRequest.objects.values_list('status').annotate(total=Count('id'))
    )
    paid_today = Order.objects.filter(
        paid_at__gte=today_start, paid_at__lt=tomorrow_start,
    )
    terminal_order_statuses = (Order.Status.COMPLETED, Order.Status.CANCELLED)
    terminal_request_statuses = (
        ProductRequest.Status.COMPLETED, ProductRequest.Status.CANCELLED,
    )
    return api_response({
        'generated_at': _iso(now),
        'period_label': f'Сегодня, {local_now:%d.%m.%Y}',
        'statuses': {
            'orders': [{
                'value': value, 'label': label, 'icon': status_icon(value),
                'count': order_counts.get(value, 0),
            } for value, label in Order.Status.choices],
            'requests': [{
                'value': value, 'label': label, 'icon': status_icon(value),
                'count': request_counts.get(value, 0),
            } for value, label in ProductRequest.Status.choices],
        },
        'traffic': {
            'unique_visitors': identity_count(sessions),
            'sessions': sessions.count(),
            'page_views': sessions.aggregate(total=Sum('page_views'))['total'] or 0,
            'engaged_sessions': sessions.filter(engaged=True).count(),
            'active_last_15_minutes': qualified_analytics_sessions(AnalyticsSession.objects.filter(
                last_seen_at__gte=now - timedelta(minutes=15),
            )).count(),
        },
        'operations': {
            'new_items': (
                Order.objects.filter(status=Order.Status.NEW).count()
                + ProductRequest.objects.filter(status=ProductRequest.Status.NEW).count()
            ),
            'active_orders': Order.objects.exclude(
                status__in=terminal_order_statuses,
            ).count(),
            'active_requests': ProductRequest.objects.exclude(
                status__in=terminal_request_statuses,
            ).count(),
            'created_today': (
                Order.objects.filter(
                    created_at__gte=today_start, created_at__lt=tomorrow_start,
                ).count()
                + ProductRequest.objects.filter(
                    created_at__gte=today_start, created_at__lt=tomorrow_start,
                ).count()
            ),
            'paid_orders_today': paid_today.count(),
            'revenue_today_rub': _money(
                paid_today.aggregate(total=Sum('actual_revenue'))['total'] or Decimal('0')
            ),
            'total_orders': Order.objects.count(),
            'total_requests': ProductRequest.objects.count(),
        },
    }, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('orders:admin',))
def admin_order_history(request):
    page = _integer_param(request, 'page', 1, 1, 1000000)
    limit = _integer_param(request, 'limit', 8, 1, 20)
    query = request.GET.get('q', '').strip()
    order_type = request.GET.get('type', '').strip()
    if order_type not in ADMIN_ORDER_TYPES:
        raise ApiProblem(
            400, 'invalid_type', 'Параметр type должен быть cart или personal.',
        )
    if len(query) > ADMIN_ORDER_SEARCH_MAX_LENGTH:
        raise ApiProblem(
            400, 'invalid_query',
            f'Параметр q должен содержать не более {ADMIN_ORDER_SEARCH_MAX_LENGTH} символов.',
        )

    orders = Order.objects.all()
    requests = ProductRequest.objects.all()
    if order_type == 'cart':
        requests = ProductRequest.objects.none()
    elif order_type == 'personal':
        orders = Order.objects.none()
    if query:
        # SQLite LIKE не умеет полноценно менять регистр кириллицы. Варианты ниже
        # сохраняют частичный поиск для типичных ФИО и одновременно остаются
        # обычными параметризованными ORM-условиями на других СУБД.
        variants = {
            query, query.lower(), query.upper(), query.title(), query.capitalize(),
        }
        contact_filter = Q()
        for value in variants:
            contact_filter |= (
                Q(customer_name__icontains=value)
                | Q(customer_phone__icontains=value)
                | Q(customer_email__icontains=value)
                | Q(customer_telegram__icontains=value)
                | Q(delivery_address__icontains=value)
            )
        order_filter = contact_filter
        request_filter = contact_filter
        number_match = re.fullmatch(r'(?:(ORD|REQ)-?)?0*(\d+)', query.upper())
        if number_match:
            prefix, raw_id = number_match.groups()
            object_id = int(raw_id)
            valid_id = 0 < object_id <= 9223372036854775807
            if valid_id and prefix in (None, 'ORD'):
                order_filter |= Q(pk=object_id)
            if valid_id and prefix in (None, 'REQ'):
                request_filter |= Q(pk=object_id)
        orders = orders.filter(order_filter)
        requests = requests.filter(request_filter)

    total = orders.count() + requests.count()
    total_pages = max(1, ceil(total / limit))
    if page > total_pages:
        return api_response({
            'results': [],
            'query': query,
            'type': order_type,
            'pagination': {
                'page': page, 'limit': limit, 'total': total,
                'total_pages': total_pages, 'has_previous': page > 1,
                'has_next': False,
            },
        }, request_id=request.api_request_id)
    end = page * limit
    orders_rows = list(orders.only(
        'id', 'customer_name', 'status', 'created_at', 'updated_at'
    ).order_by('-created_at', '-pk')[:end])
    request_rows = list(requests.only(
        'id', 'customer_name', 'status', 'created_at', 'updated_at'
    ).order_by('-created_at', '-pk')[:end])
    combined = [
        {
            'type': 'cart', 'type_label': 'Заказ', 'id': item.pk,
            'number': item.number, 'customer_name': item.customer_name,
            'status': item.status, 'status_label': item.get_status_display(),
            'status_icon': status_icon(item.status),
            'created_at': _iso(item.created_at), 'updated_at': _iso(item.updated_at),
        }
        for item in orders_rows
    ] + [
        {
            'type': 'personal', 'type_label': 'Заявка', 'id': item.pk,
            'number': item.number, 'customer_name': item.customer_name,
            'status': item.status, 'status_label': item.get_status_display(),
            'status_icon': status_icon(item.status),
            'created_at': _iso(item.created_at), 'updated_at': _iso(item.updated_at),
        }
        for item in request_rows
    ]
    combined.sort(key=lambda item: (item['created_at'], item['id']), reverse=True)
    start = (page - 1) * limit
    return api_response({
        'results': combined[start:end],
        'query': query,
        'type': order_type,
        'pagination': {
            'page': page, 'limit': limit, 'total': total,
            'total_pages': total_pages,
            'has_previous': page > 1,
            'has_next': page < total_pages,
        },
    }, request_id=request.api_request_id)


def _admin_order_object(number, *, for_update=False):
    if not (
        isinstance(number, str)
        and (number.startswith('ORD-') or number.startswith('REQ-'))
        and number[4:].isdigit()
    ):
        raise ApiProblem(404, 'order_not_found', 'Заказ или заявка не найдены.')
    model = Order if number.startswith('ORD-') else ProductRequest
    queryset = model.objects
    if for_update:
        queryset = queryset.select_for_update()
    prefetch = 'items__product' if model is Order else 'items'
    instance = queryset.prefetch_related(prefetch).filter(pk=int(number[4:])).first()
    if instance is None:
        raise ApiProblem(404, 'order_not_found', 'Заказ или заявка не найдены.')
    return instance


@api_endpoint(methods=('GET',), scopes=('orders:admin',))
def admin_order_detail(request, number):
    instance = _admin_order_object(number)
    serializer = serialize_admin_order if isinstance(instance, Order) else serialize_admin_product_request
    return api_response({
        'order': serializer(request, instance),
    }, request_id=request.api_request_id)


@api_endpoint(methods=('PATCH',), scopes=('orders:write',), idempotent=True)
def admin_order_status(request, number):
    payload = parse_json_body(request)
    if not isinstance(payload, dict) or set(payload) - {'status', 'expected_updated_at'}:
        raise ApiProblem(400, 'invalid_status_update', 'Некорректное изменение статуса.')
    with transaction.atomic():
        instance = _admin_order_object(number, for_update=True)
        model = type(instance)
        status = payload.get('status')
        valid_statuses = {value for value, _ in model.Status.choices}
        if status not in valid_statuses:
            raise ApiProblem(400, 'invalid_status', 'Неизвестный статус заказа.')
        expected = _aware_datetime(payload.get('expected_updated_at'), 'expected_updated_at')
        if instance.updated_at != expected:
            raise ApiProblem(409, 'stale_order', 'Заказ уже был изменён.', {
                'number': instance.number,
                'current_updated_at': _iso(instance.updated_at),
            })
        if instance.status != status:
            instance.status = status
            instance.updated_at = timezone.now()
            instance.save(update_fields=('status', 'updated_at'))
    serializer = serialize_admin_order if isinstance(instance, Order) else serialize_admin_product_request
    return api_response({
        'order': serializer(request, instance),
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('notifications:read',), idempotent=True)
def notification_claim(request):
    from telegram_bot.linking import cleanup_expired_registration_sessions

    cleanup_expired_registration_sessions()
    payload = parse_json_body(request)
    if not isinstance(payload, dict) or set(payload) - {'limit', 'lease_seconds', 'audience'}:
        raise ApiProblem(400, 'invalid_claim', 'Тело запроса должно быть объектом.')
    limit = payload.get('limit', 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= NOTIFICATION_CLAIM_MAX:
        raise ApiProblem(400, 'invalid_limit', f'limit должен быть от 1 до {NOTIFICATION_CLAIM_MAX}.')
    lease_seconds = payload.get('lease_seconds', 120)
    if (
        not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool)
        or not 30 <= lease_seconds <= 900
    ):
        raise ApiProblem(400, 'invalid_lease', 'lease_seconds должен быть от 30 до 900.')
    now = timezone.now()
    audience = payload.get('audience')
    if audience is not None and audience not in NotificationEvent.Audience.values:
        raise ApiProblem(400, 'invalid_audience', 'Неизвестная аудитория уведомлений.')
    lease_id = uuid.uuid4()
    with transaction.atomic():
        available = NotificationEvent.objects.filter(
            delivered_at__isnull=True,
            available_at__lte=now,
        ).filter(
            Q(locked_until__isnull=True) | Q(locked_until__lt=now)
        )
        if audience:
            available = available.filter(audience=audience)
        candidate_ids = list(available.values_list('pk', flat=True)[:limit])
        locked_until = now + timedelta(seconds=lease_seconds)
        available.filter(pk__in=candidate_ids).update(
            locked_until=locked_until,
            locked_by=request.api_key_id,
            lease_id=lease_id,
        )
        events = list(NotificationEvent.objects.filter(
            lease_id=lease_id, locked_by=request.api_key_id,
        ).select_related('order', 'product_request').prefetch_related(
            'order__items', 'product_request__items',
        ))
    return api_response({
        'events': [serialize_notification(event) for event in events],
        'locked_until': _iso(locked_until) if events else None,
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('notifications:write',), idempotent=True)
def notification_ack(request, event_id):
    payload = parse_json_body(request)
    if not isinstance(payload, dict) or set(payload) - {
        'status', 'lease_id', 'error', 'retry_after_seconds',
    }:
        raise ApiProblem(400, 'invalid_ack', 'Некорректное подтверждение события.')
    status = payload.get('status')
    if status not in {'delivered', 'failed'}:
        raise ApiProblem(400, 'invalid_status', 'status должен быть delivered или failed.')
    try:
        lease_id = uuid.UUID(str(payload.get('lease_id') or ''))
    except ValueError as exc:
        raise ApiProblem(400, 'invalid_lease_id', 'Для ACK требуется корректный lease_id.') from exc
    with transaction.atomic():
        event = NotificationEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            raise ApiProblem(404, 'event_not_found', 'Событие не найдено.')
        if event.delivered_at:
            return api_response(
                {'event_id': str(event.pk), 'status': 'delivered'},
                request_id=request.api_request_id,
            )
        if event.locked_by != request.api_key_id or event.lease_id != lease_id:
            raise ApiProblem(409, 'event_not_claimed', 'Событие не зарезервировано этим сервисом.')
        event.attempts += 1
        if status == 'delivered':
            event.delivered_at = timezone.now()
            event.last_error = ''
            event.locked_until = None
            event.locked_by = ''
            event.lease_id = None
        else:
            error = str(payload.get('error') or '').strip()[:1000]
            if not error:
                raise ApiProblem(400, 'error_required', 'Для failed необходимо поле error.')
            retry = payload.get('retry_after_seconds', min(3600, 30 * (2 ** min(event.attempts, 6))))
            if not isinstance(retry, int) or isinstance(retry, bool) or not 10 <= retry <= 86400:
                raise ApiProblem(400, 'invalid_retry', 'retry_after_seconds должен быть от 10 до 86400.')
            event.last_error = error
            event.available_at = timezone.now() + timedelta(seconds=retry)
            event.locked_until = None
            event.locked_by = ''
            event.lease_id = None
        event.save(update_fields=(
            'attempts', 'delivered_at', 'last_error', 'available_at',
            'locked_until', 'locked_by', 'lease_id',
        ))
    return api_response({
        'event_id': str(event.pk), 'status': status, 'attempts': event.attempts,
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('notifications:write',), idempotent=True)
def notification_recipient_delivered(request, event_id):
    from telegram_bot.models import TelegramAccount, TelegramEventDelivery

    payload = parse_json_body(request)
    if not isinstance(payload, dict) or set(payload) - {'chat_id', 'message_id', 'lease_id'}:
        raise ApiProblem(400, 'invalid_delivery', 'Некорректная доставка получателю.')
    chat_id = payload.get('chat_id')
    message_id = payload.get('message_id')
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        raise ApiProblem(400, 'invalid_chat_id', 'chat_id должен быть целым числом.')
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id < 1:
        raise ApiProblem(400, 'invalid_message_id', 'message_id должен быть положительным числом.')
    try:
        lease_id = uuid.UUID(str(payload.get('lease_id') or ''))
    except ValueError as exc:
        raise ApiProblem(400, 'invalid_lease_id', 'Для доставки требуется корректный lease_id.') from exc
    event = NotificationEvent.objects.filter(pk=event_id).first()
    if event is None:
        raise ApiProblem(404, 'event_not_found', 'Событие не найдено.')
    if event.locked_by != request.api_key_id or event.lease_id != lease_id:
        raise ApiProblem(409, 'event_not_claimed', 'Событие не зарезервировано этим сервисом.')
    accounts = TelegramAccount.objects.filter(
        chat_id=chat_id,
        is_active=True,
        user__is_active=True,
    )
    if event.audience == NotificationEvent.Audience.ADMIN:
        accounts = accounts.filter(role=TelegramAccount.Role.ADMINISTRATOR)
    elif event.audience == NotificationEvent.Audience.CUSTOMER and event.user_id:
        accounts = accounts.filter(user_id=event.user_id)
    else:
        raise ApiProblem(409, 'invalid_event_audience', 'Событие не поддерживает Telegram-доставку.')
    account = accounts.first()
    if account is None:
        raise ApiProblem(404, 'recipient_not_found', 'Получатель Telegram не найден.')
    delivery, created = TelegramEventDelivery.objects.get_or_create(
        event_id=event_id,
        account=account,
        defaults={'message_id': message_id},
    )
    return api_response({
        'event_id': str(event_id), 'chat_id': chat_id,
        'message_id': delivery.message_id, 'created': created,
    }, status=201 if created else 200, request_id=request.api_request_id)


@api_endpoint(methods=('GET',), scopes=('notifications:read',))
def notification_deliveries(request, event_id):
    from telegram_bot.models import TelegramEventDelivery

    event = NotificationEvent.objects.filter(pk=event_id).first()
    if event is None:
        raise ApiProblem(404, 'event_not_found', 'Событие не найдено.')
    deliveries = TelegramEventDelivery.objects.filter(
        event_id=event_id,
    ).select_related('account')
    return api_response({
        'results': [{
            'chat_id': item.account.chat_id,
            'message_id': item.message_id,
            'delivered_at': _iso(item.delivered_at),
        } for item in deliveries],
    }, request_id=request.api_request_id)


@api_endpoint(methods=('POST',), scopes=('broadcasts:write',), idempotent=True)
def broadcast_create(request):
    payload = parse_json_body(request)
    allowed = {'title', 'message', 'segment', 'parse_mode'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ApiProblem(400, 'invalid_broadcast', 'Некорректные поля рассылки.')
    title = str(payload.get('title') or '').strip()
    message = str(payload.get('message') or '').strip()
    segment = str(payload.get('segment') or 'all').strip()
    parse_mode = str(payload.get('parse_mode') or 'plain').strip()
    if not 1 <= len(title) <= 200 or not 1 <= len(message) <= 4000:
        raise ApiProblem(400, 'invalid_broadcast', 'Заголовок или текст рассылки имеет недопустимую длину.')
    if segment not in {'all', 'customers', 'verified_customers'}:
        raise ApiProblem(400, 'invalid_segment', 'Неизвестный сегмент рассылки.')
    if parse_mode not in {'plain', 'HTML'}:
        raise ApiProblem(400, 'invalid_parse_mode', 'Допустимы plain и HTML.')
    event = NotificationEvent.objects.create(
        event_type=NotificationEvent.EventType.BROADCAST,
        audience=NotificationEvent.Audience.BROADCAST,
        payload={
            'title': title, 'message': message,
            'segment': segment, 'parse_mode': parse_mode,
        },
    )
    return api_response(
        {'event': serialize_notification(event)}, status=201,
        request_id=request.api_request_id,
    )
