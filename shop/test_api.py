import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    ApiIdempotencyRecord, ApiRequestLog, ApiRequestNonce, ColorVariant,
    NotificationEvent, Order, Product, ProductRequest, SizeVariant,
)


API_SECRET = 'test-secret-with-at-least-thirty-two-bytes-123456'
API_KEYS = json.dumps({
    'catalog-reader': {'secret': API_SECRET, 'scopes': ['catalog:read']},
    'parser': {'secret': API_SECRET, 'scopes': ['catalog:read', 'catalog:write']},
    'telegram': {
        'secret': API_SECRET,
        'scopes': [
            'orders:read', 'orders:admin', 'orders:write',
            'notifications:read', 'notifications:write', 'telegram:admins:read',
        ],
    },
    'other-worker': {
        'secret': API_SECRET,
        'scopes': ['notifications:read', 'notifications:write'],
    },
    'marketing': {'secret': API_SECRET, 'scopes': ['broadcasts:write']},
})


@override_settings(AURENE_API_KEYS_JSON=API_KEYS, AURENE_API_RATE_LIMIT_PER_MINUTE=300)
class ApiTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            sku='source:SKU/ONE', title='Сумка', brand='test-brand',
            price=Decimal('100.00'), currency='CNY', in_stock_ecom=True,
            description='Описание', raw_data={'source': {'id': 7}},
            gender=['women'], material=['leather'], category_name='Сумки',
        )
        self.image = self.product.images.create(
            position=0, original='https://source.example/image.jpg', alt='Сумка',
        )
        self.color = ColorVariant.objects.create(
            product=self.product, sku='COLOR-1', color='Чёрный',
            price=Decimal('100'), in_stock_ecom=True,
        )
        self.size = SizeVariant.objects.create(
            product=self.product, sku='SIZE-1', size='25',
            price=Decimal('100'), in_stock_ecom=True,
        )
        self.user = get_user_model().objects.create_user(
            username='api-customer@example.com', email='api-customer@example.com',
            password='Strong-password-2026',
        )

    def api_request(
        self, method, path, *, key_id='catalog-reader', payload=None,
        nonce=None, timestamp=None, idempotency_key=None, secret=API_SECRET,
        signature_path=None, secure=False, remote_addr='127.0.0.1',
    ):
        body = b'' if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(',', ':'),
        ).encode('utf-8')
        nonce = nonce or uuid.uuid4()
        timestamp = timestamp if timestamp is not None else int(time.time())
        canonical = '\n'.join((
            method.upper(), signature_path or path, str(timestamp), str(nonce),
            hashlib.sha256(body).hexdigest(),
        ))
        signature = hmac.new(
            secret.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256,
        ).hexdigest()
        headers = {
            'HTTP_X_API_KEY_ID': key_id,
            'HTTP_X_API_TIMESTAMP': str(timestamp),
            'HTTP_X_API_NONCE': str(nonce),
            'HTTP_X_API_SIGNATURE': signature,
        }
        if idempotency_key:
            headers['HTTP_X_IDEMPOTENCY_KEY'] = str(idempotency_key)
        return self.client.generic(
            method.upper(), path, data=body,
            content_type='application/json' if payload is not None else None,
            secure=secure, REMOTE_ADDR=remote_addr,
            **headers,
        )

    def commercial_url(self, product=None):
        return reverse(
            'shop:api_catalog_product_update',
            args=[(product or self.product).pk],
        )

    def commercial_payload(self, product=None, **changes):
        product = product or self.product
        product.refresh_from_db()
        return {
            'sku': product.sku,
            'expected_updated_at': product.updated_at.isoformat(),
            **changes,
        }

    def test_signature_is_required_and_failures_are_audited_without_body(self):
        url = reverse('shop:api_catalog_products')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'missing_signature')
        log = ApiRequestLog.objects.get()
        self.assertEqual(log.path, url)
        self.assertEqual(log.status_code, 401)
        self.assertFalse(hasattr(log, 'request_body'))

    def test_oversized_body_returns_safe_413(self):
        url = reverse('shop:api_catalog_bulk_update')
        body = b'x' * (1024 * 1024 + 1)
        response = self.client.generic(
            'POST', url, data=body, content_type='application/json',
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error']['code'], 'body_too_large')

    def test_signature_covers_query_string_and_rejects_tampering(self):
        url = reverse('shop:api_catalog_products') + '?limit=1'
        response = self.api_request(
            'GET', url, signature_path=reverse('shop:api_catalog_products') + '?limit=2'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'invalid_credentials')

    def test_expired_timestamp_and_replayed_nonce_are_rejected(self):
        url = reverse('shop:api_catalog_products')
        expired = self.api_request('GET', url, timestamp=int(time.time()) - 301)
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.json()['error']['code'], 'expired_signature')

        nonce = uuid.uuid4()
        first = self.api_request('GET', url, nonce=nonce)
        replay = self.api_request('GET', url, nonce=nonce)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()['error']['code'], 'replayed_request')
        self.assertEqual(ApiRequestNonce.objects.filter(nonce=nonce).count(), 1)

    def test_scope_is_enforced(self):
        response = self.api_request(
            'PATCH', self.commercial_url(),
            payload=self.commercial_payload(price='200'),
            idempotency_key=uuid.uuid4(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'insufficient_scope')
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('100'))

    @override_settings(AURENE_API_REQUIRE_HTTPS=True)
    def test_https_can_be_required(self):
        url = reverse('shop:api_catalog_products')
        denied = self.api_request('GET', url)
        allowed = self.api_request('GET', url, secure=True)
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()['error']['code'], 'https_required')
        self.assertEqual(allowed.status_code, 200)

    def test_api_key_cidr_allowlist_uses_socket_address(self):
        keys = json.loads(API_KEYS)
        keys['catalog-reader']['allowed_cidrs'] = ['10.20.0.0/16']
        with self.settings(AURENE_API_KEYS_JSON=json.dumps(keys)):
            url = reverse('shop:api_catalog_products')
            denied = self.api_request('GET', url, remote_addr='127.0.0.1')
            allowed = self.api_request('GET', url, remote_addr='10.20.1.5')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error']['code'], 'ip_not_allowed')
        self.assertEqual(allowed.status_code, 200)

    @override_settings(AURENE_API_RATE_LIMIT_PER_MINUTE=1)
    def test_rate_limit_is_shared_through_request_log(self):
        url = reverse('shop:api_catalog_products')
        self.assertEqual(self.api_request('GET', url).status_code, 200)
        response = self.api_request('GET', url)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error']['code'], 'rate_limit_exceeded')

    def test_catalog_list_cursor_and_complete_product_card(self):
        second = Product.objects.create(
            sku='second', title='Второй', brand='test-brand', price=200,
        )
        list_url = reverse('shop:api_catalog_products') + '?limit=1'
        first_page = self.api_request('GET', list_url)
        self.assertEqual(first_page.status_code, 200)
        self.assertTrue(first_page.json()['has_more'])
        cursor = first_page.json()['next_cursor']
        next_url = reverse('shop:api_catalog_products') + f'?limit=1&cursor={cursor}'
        second_page = self.api_request('GET', next_url)
        self.assertEqual(second_page.status_code, 200)
        returned_ids = {
            first_page.json()['results'][0]['id'], second_page.json()['results'][0]['id']
        }
        self.assertEqual(returned_ids, {self.product.pk, second.pk})

        detail_url = reverse('shop:api_catalog_product_detail', args=[self.product.pk])
        response = self.api_request('GET', detail_url)
        self.assertEqual(response.status_code, 200)
        card = response.json()['product']
        self.assertEqual(card['sku'], 'source:SKU/ONE')
        self.assertEqual(card['catalog_scope'], Product.CatalogScope.STORE)
        self.assertEqual(card['raw_data'], {'source': {'id': 7}})
        self.assertEqual(card['images'][0]['urls']['original_source'], self.image.original)
        self.assertEqual(card['color_variants'][0]['sku'], self.color.sku)
        self.assertEqual(card['size_variants'][0]['sku'], self.size.sku)
        self.assertIn('/product/', card['storefront_url'])

    def test_commercial_update_is_limited_locked_and_idempotent(self):
        idempotency_key = uuid.uuid4()
        payload = self.commercial_payload(
            price='250.55', currency='USD', in_stock=False,
            color_variants=[{'sku': self.color.sku, 'price': '255', 'in_stock': False}],
        )
        response = self.api_request(
            'PATCH', self.commercial_url(), key_id='parser', payload=payload,
            idempotency_key=idempotency_key,
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.color.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('250.55'))
        self.assertEqual(self.product.currency, 'USD')
        self.assertFalse(self.product.in_stock_ecom)
        self.assertEqual(self.color.price, Decimal('255'))

        replay = self.api_request(
            'PATCH', self.commercial_url(), key_id='parser', payload=payload,
            idempotency_key=idempotency_key,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay['X-Idempotent-Replay'], 'true')
        self.assertEqual(ApiIdempotencyRecord.objects.count(), 1)

        conflict_payload = {**payload, 'price': '999'}
        conflict = self.api_request(
            'PATCH', self.commercial_url(), key_id='parser', payload=conflict_payload,
            idempotency_key=idempotency_key,
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['error']['code'], 'idempotency_conflict')

    def test_commercial_update_rejects_stale_version_and_unknown_fields(self):
        stale = self.commercial_payload(price='200')
        self.product.price = Decimal('150')
        self.product.save(update_fields=('price', 'updated_at'))
        response = self.api_request(
            'PATCH', self.commercial_url(), key_id='parser', payload=stale,
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'stale_product')
        self.assertFalse(ApiIdempotencyRecord.objects.exists())

        invalid = self.commercial_payload(title='Взлом', price='300')
        response = self.api_request(
            'PATCH', self.commercial_url(), key_id='parser', payload=invalid,
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'unknown_fields')
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, 'Сумка')

    def test_bulk_update_is_atomic(self):
        another = Product.objects.create(
            sku='another', title='Другой', price=Decimal('300'), brand='test-brand',
        )
        payload = {'products': [
            self.commercial_payload(price='111'),
            {'sku': 'missing', 'price': '222', 'expected_updated_at': another.updated_at.isoformat()},
        ]}
        response = self.api_request(
            'POST', reverse('shop:api_catalog_bulk_update'), key_id='parser',
            payload=payload, idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(response.status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal('100'))

    def create_order(self, user=None):
        user = user or self.user
        with self.captureOnCommitCallbacks(execute=True):
            order = Order.objects.create(
                user=user, customer_name='Анна', customer_email=user.email,
                customer_phone='+79990000000', customer_telegram='@anna',
                delivery_address='Москва', estimated_total=Decimal('1000'), currency='RUB',
            )
        order.items.create(
            product=self.product, product_title=self.product.title,
            sku=self.product.sku, unit_price=1000, currency='RUB', quantity=1,
        )
        return order

    def test_orders_are_bound_to_internal_user_and_include_personal_requests(self):
        order = self.create_order()
        with self.captureOnCommitCallbacks(execute=True):
            personal = ProductRequest.objects.create(
                user=self.user, customer_name='Анна', customer_email=self.user.email,
                customer_phone='+79990000000',
            )
        personal.items.create(brand='Test', product_name='Редкая сумка')

        list_url = reverse('shop:api_orders') + f'?user_id={self.user.pk}'
        response = self.api_request('GET', list_url, key_id='telegram')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['orders'][0]['number'], order.number)
        self.assertEqual(response.json()['product_requests'][0]['number'], personal.number)
        self.assertNotIn('customer', response.json()['orders'][0])

        detail_url = reverse('shop:api_order_detail', args=[order.number])
        own = self.api_request(
            'GET', detail_url + f'?user_id={self.user.pk}', key_id='telegram'
        )
        foreign = self.api_request(
            'GET', detail_url + f'?user_id={self.user.pk + 999}', key_id='telegram'
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()['order']['customer']['delivery_address'], 'Москва')
        self.assertEqual(foreign.status_code, 404)

        personal_url = reverse('shop:api_order_detail', args=[personal.number])
        personal_response = self.api_request(
            'GET', personal_url + f'?user_id={self.user.pk}', key_id='telegram'
        )
        self.assertEqual(personal_response.status_code, 200)
        self.assertEqual(personal_response.json()['type'], 'personal')

    def test_telegram_admin_directory_contains_only_active_administrators(self):
        from telegram_bot.models import TelegramAccount

        TelegramAccount.objects.create(
            user=self.user, chat_id=101, username='anna',
            role=TelegramAccount.Role.ADMINISTRATOR,
        )
        other = get_user_model().objects.create_user(username='other')
        TelegramAccount.objects.create(
            user=other, chat_id=202, role=TelegramAccount.Role.CUSTOMER,
        )
        inactive = get_user_model().objects.create_user(username='inactive')
        TelegramAccount.objects.create(
            user=inactive, chat_id=303,
            role=TelegramAccount.Role.ADMINISTRATOR, is_active=False,
        )

        response = self.api_request(
            'GET', reverse('shop:api_telegram_administrators'), key_id='telegram',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['chat_id'], 101)
        self.assertFalse(response.json()['results'][0]['admin_verified'])
        self.assertEqual(len(response.json()['results']), 1)

    def test_customer_telegram_endpoints_are_bound_to_account_owner(self):
        from telegram_bot.models import ManagerContact, TelegramAccount

        account = TelegramAccount.objects.create(
            user=self.user, chat_id=101, username='anna',
            role=TelegramAccount.Role.CUSTOMER,
        )
        ManagerContact.objects.create(
            kind=ManagerContact.Kind.EMAIL, label='Почта',
            value='manager@example.com', position=40,
        )
        ManagerContact.objects.create(
            kind=ManagerContact.Kind.PHONE, label='Телефон',
            value='+79990000000', position=10,
        )
        order = self.create_order()
        other_user = get_user_model().objects.create_user(
            username='foreign-customer', email='foreign@example.com',
        )
        foreign = self.create_order(other_user)

        account_response = self.api_request(
            'GET', reverse('shop:api_telegram_account', args=[account.chat_id]),
            key_id='telegram',
        ).json()['account']
        recipient_response = self.api_request(
            'GET', reverse('shop:api_telegram_customer_recipient', args=[self.user.pk]),
            key_id='telegram',
        ).json()['account']
        contacts = self.api_request(
            'GET', reverse('shop:api_manager_contacts'), key_id='telegram',
        ).json()['results']
        history = self.api_request(
            'GET', reverse('shop:api_customer_order_history')
            + f'?user_id={self.user.pk}&page=1&limit=8', key_id='telegram',
        ).json()
        detail = self.api_request(
            'GET', reverse('shop:api_customer_order_detail', args=[order.number])
            + f'?user_id={self.user.pk}', key_id='telegram',
        )
        denied = self.api_request(
            'GET', reverse('shop:api_customer_order_detail', args=[foreign.number])
            + f'?user_id={self.user.pk}', key_id='telegram',
        )

        self.assertEqual(account_response['user_id'], self.user.pk)
        self.assertEqual(account_response['role'], TelegramAccount.Role.CUSTOMER)
        self.assertEqual(recipient_response['chat_id'], account.chat_id)
        self.assertEqual(recipient_response['user_id'], self.user.pk)
        self.assertEqual([item['kind'] for item in contacts], ['phone', 'email'])
        self.assertEqual([item['number'] for item in history['results']], [order.number])
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()['order']
        self.assertNotIn('user_id', payload)
        self.assertNotIn('customer', payload)
        self.assertNotIn('admin_url', payload)
        self.assertEqual(payload['status_icon'], '🆕')
        self.assertEqual(denied.status_code, 404)

    def test_customer_order_endpoints_require_valid_user_id(self):
        order = self.create_order()
        history_url = reverse('shop:api_customer_order_history')
        detail_url = reverse('shop:api_customer_order_detail', args=[order.number])

        for path in (history_url, detail_url):
            missing = self.api_request('GET', path, key_id='telegram')
            invalid = self.api_request('GET', path + '?user_id=not-a-number', key_id='telegram')
            non_positive = self.api_request('GET', path + '?user_id=0', key_id='telegram')

            self.assertEqual(missing.status_code, 400)
            self.assertEqual(missing.json()['error']['code'], 'invalid_user_id')
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.json()['error']['code'], 'invalid_user_id')
            self.assertEqual(non_positive.status_code, 400)
            self.assertEqual(non_positive.json()['error']['code'], 'invalid_user_id')

        missing_recipient = self.api_request(
            'GET', reverse(
                'shop:api_telegram_customer_recipient', args=[self.user.pk + 999],
            ), key_id='telegram',
        )
        self.assertEqual(missing_recipient.status_code, 404)
        self.assertEqual(missing_recipient.json()['error']['code'], 'recipient_not_found')

    def test_customer_delivery_accepts_only_owner_telegram_account(self):
        from telegram_bot.models import TelegramAccount, TelegramEventDelivery

        owner = TelegramAccount.objects.create(
            user=self.user, chat_id=101, role=TelegramAccount.Role.CUSTOMER,
        )
        other_user = get_user_model().objects.create_user(username='other-recipient')
        other = TelegramAccount.objects.create(
            user=other_user, chat_id=202, role=TelegramAccount.Role.CUSTOMER,
        )
        order = self.create_order()
        claim = self.api_request(
            'POST', reverse('shop:api_notification_claim'), key_id='telegram',
            payload={'limit': 10, 'audience': 'customer'},
            idempotency_key=uuid.uuid4(),
        ).json()
        event = next(item for item in claim['events'] if item['type'] == 'order.created')
        url = reverse('shop:api_notification_recipient_delivered', args=[event['id']])
        denied = self.api_request(
            'POST', url, key_id='telegram',
            payload={'lease_id': event['lease_id'], 'chat_id': other.chat_id, 'message_id': 1},
            idempotency_key=uuid.uuid4(),
        )
        allowed = self.api_request(
            'POST', url, key_id='telegram',
            payload={'lease_id': event['lease_id'], 'chat_id': owner.chat_id, 'message_id': 2},
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(allowed.status_code, 201)
        self.assertTrue(TelegramEventDelivery.objects.filter(
            event_id=event['id'], account=owner, message_id=2,
        ).exists())

    def test_admin_history_and_details_include_complete_order_information(self):
        order = self.create_order()
        self.product.raw_data = {
            'title': {'raw': 'Original Bag', 'i18n': {'ru': 'Сумка'}},
        }
        self.product.source_url = 'https://source.example.com/original-bag'
        self.product.save(update_fields=('raw_data', 'source_url'))
        item = order.items.get()
        item.color = 'Чёрный'
        item.size = '25'
        item.save(update_fields=('color', 'size'))
        personal = ProductRequest.objects.create(
            user=self.user, customer_name='Анна', customer_email=self.user.email,
            customer_phone='+79990000000',
        )
        personal.items.create(
            brand='Test', product_name='Редкая сумка', color='Белый', size='Mini',
            source_url='https://source.example.com/rare-bag',
        )

        history_url = reverse('shop:api_admin_order_history') + '?page=1&limit=1'
        history_response = self.api_request('GET', history_url, key_id='telegram')
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(len(history_response.json()['results']), 1)
        self.assertEqual(history_response.json()['pagination']['total'], 2)
        self.assertTrue(history_response.json()['pagination']['has_next'])

        detail = self.api_request(
            'GET', reverse('shop:api_admin_order_detail', args=[order.number]),
            key_id='telegram',
        )
        self.assertEqual(detail.status_code, 200)
        data = detail.json()['order']
        self.assertEqual(data['customer']['preferred_contact'], 'phone')
        self.assertEqual(data['customer']['delivery_address'], 'Москва')
        self.assertEqual(data['items'][0]['color'], 'Чёрный')
        self.assertEqual(data['items'][0]['size'], '25')
        self.assertEqual(data['items'][0]['subtotal'], '1000.00')
        self.assertEqual(data['items'][0]['original_title'], 'Original Bag')
        self.assertEqual(
            data['items'][0]['source_url'], 'https://source.example.com/original-bag',
        )
        self.assertIn(self.product.get_absolute_url(), data['items'][0]['product_url'])
        self.assertEqual(data['estimated_total'], '1000.00')
        self.assertIn(f'/control/orders/{order.pk}/', data['admin_url'])

        request_detail = self.api_request(
            'GET', reverse('shop:api_admin_order_detail', args=[personal.number]),
            key_id='telegram',
        ).json()['order']
        self.assertIsNone(request_detail['customer']['preferred_contact'])
        self.assertIsNone(request_detail['customer']['delivery_address'])
        self.assertIsNone(request_detail['items'][0]['subtotal'])
        self.assertIsNone(request_detail['items'][0]['original_title'])
        self.assertEqual(
            request_detail['items'][0]['source_url'],
            'https://source.example.com/rare-bag',
        )
        self.assertIn(f'/control/requests/{personal.pk}/', request_detail['admin_url'])

    def test_admin_dashboard_aggregates_statuses_traffic_and_operations(self):
        from shop.models import AnalyticsEvent, AnalyticsSession, AnalyticsVisitor

        visitor = AnalyticsVisitor.objects.create()
        first_session = AnalyticsSession.objects.create(
            visitor=visitor, page_views=2, engaged=True,
        )
        second_session = AnalyticsSession.objects.create(
            visitor=visitor, page_views=3, engaged=False,
        )
        for session in (first_session, second_session):
            AnalyticsEvent.objects.create(
                session=session, visitor=visitor, event_type='page_view', path='/',
            )
        first = self.create_order()
        second = self.create_order()
        second.status = Order.Status.PROCESSING
        second.save(update_fields=('status',))
        ProductRequest.objects.create(
            user=self.user, customer_name='Анна', customer_email=self.user.email,
            customer_phone='+79990000000',
        )

        response = self.api_request(
            'GET', reverse('shop:api_admin_dashboard'), key_id='telegram',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        order_statuses = {item['value']: item for item in data['statuses']['orders']}
        request_statuses = {item['value']: item for item in data['statuses']['requests']}
        self.assertEqual(ProductRequest.Status.choices, Order.Status.choices)
        self.assertEqual(
            [item['value'] for item in data['statuses']['requests']],
            [item['value'] for item in data['statuses']['orders']],
        )
        self.assertEqual(order_statuses[Order.Status.NEW]['count'], 1)
        self.assertEqual(order_statuses[Order.Status.PROCESSING]['count'], 1)
        self.assertEqual(order_statuses[Order.Status.NEW]['icon'], '🆕')
        self.assertEqual(request_statuses[ProductRequest.Status.NEW]['count'], 1)
        self.assertEqual(data['traffic']['unique_visitors'], 1)
        self.assertEqual(data['traffic']['sessions'], 2)
        self.assertEqual(data['traffic']['page_views'], 5)
        self.assertEqual(data['traffic']['engaged_sessions'], 1)
        self.assertEqual(data['traffic']['active_last_15_minutes'], 2)
        self.assertEqual(data['operations']['new_items'], 2)
        self.assertEqual(data['operations']['active_orders'], 2)
        self.assertEqual(data['operations']['active_requests'], 1)
        self.assertEqual(data['operations']['created_today'], 3)
        self.assertEqual(data['operations']['total_orders'], 2)
        self.assertEqual(data['operations']['total_requests'], 1)
        self.assertEqual(first.status, Order.Status.NEW)

        denied = self.api_request(
            'GET', reverse('shop:api_admin_dashboard'), key_id='catalog-reader',
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()['error']['code'], 'insufficient_scope')

    def test_admin_history_searches_partial_contacts_and_order_numbers(self):
        order = self.create_order()
        order.customer_name = 'Иванов Иван'
        order.customer_email = 'ivan.ivanov@example.com'
        order.customer_phone = '+79991234567'
        order.customer_telegram = '@ivan_ivanov'
        order.delivery_address = 'Москва, улица Ивановская'
        order.save()
        personal = ProductRequest.objects.create(
            user=self.user, customer_name='Мария Петрова',
            customer_email='petrova@example.com', customer_phone='+78880001122',
            customer_telegram='@petrova_shop', delivery_address='Санкт-Петербург',
        )

        def search(query):
            url = reverse('shop:api_admin_order_history') + '?' + urlencode({
                'page': 1, 'limit': 8, 'q': query,
            })
            return self.api_request('GET', url, key_id='telegram').json()

        for query in ('иванов', 'ivan.iva', '991234', 'ivan_iva', 'Ивановская'):
            with self.subTest(query=query):
                result = search(query)
                self.assertEqual([item['number'] for item in result['results']], [order.number])
                self.assertEqual(result['query'], query)

        for query, expected in (
            (order.number, order.number),
            (f'ORD{order.pk:06d}', order.number),
            (str(order.pk), order.number),
            (personal.number, personal.number),
        ):
            with self.subTest(query=query):
                numbers = [item['number'] for item in search(query)['results']]
                self.assertIn(expected, numbers)

        self.assertEqual(search('нет-такого-клиента')['pagination']['total'], 0)

    def test_admin_history_rejects_too_long_search_query(self):
        url = reverse('shop:api_admin_order_history') + '?q=' + ('x' * 101)
        response = self.api_request('GET', url, key_id='telegram')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'invalid_query')

    def test_admin_history_filters_orders_and_requests_by_type(self):
        order = self.create_order()
        personal = ProductRequest.objects.create(
            user=self.user, customer_name='Анна', customer_email=self.user.email,
            customer_phone='+79990000000',
        )
        base_url = reverse('shop:api_admin_order_history')

        orders = self.api_request(
            'GET', base_url + '?page=1&limit=8&type=cart', key_id='telegram',
        ).json()
        requests = self.api_request(
            'GET', base_url + '?page=1&limit=8&type=personal', key_id='telegram',
        ).json()
        invalid = self.api_request(
            'GET', base_url + '?type=unknown', key_id='telegram',
        )

        self.assertEqual([item['number'] for item in orders['results']], [order.number])
        self.assertEqual(orders['type'], 'cart')
        self.assertEqual([item['number'] for item in requests['results']], [personal.number])
        self.assertEqual(requests['type'], 'personal')
        combined = self.api_request(
            'GET', base_url + '?page=1&limit=8&type=personal&q=%D0%90%D0%BD%D0%BD%D0%B0',
            key_id='telegram',
        ).json()
        self.assertEqual([item['number'] for item in combined['results']], [personal.number])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['error']['code'], 'invalid_type')

    def test_customer_order_scope_cannot_read_administrative_history(self):
        url = reverse('shop:api_admin_order_history') + '?page=1&limit=8'
        response = self.api_request(
            'GET', url, key_id='catalog-reader',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'insufficient_scope')

    def test_admin_can_update_order_and_request_status_with_locking(self):
        order = self.create_order()
        detail_url = reverse('shop:api_admin_order_detail', args=[order.number])
        current = self.api_request('GET', detail_url, key_id='telegram').json()['order']
        status_url = reverse('shop:api_admin_order_status', args=[order.number])
        updated = self.api_request(
            'PATCH', status_url, key_id='telegram',
            payload={
                'status': Order.Status.PROCESSING,
                'expected_updated_at': current['updated_at'],
            },
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['order']['status'], Order.Status.PROCESSING)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PROCESSING)

        stale = self.api_request(
            'PATCH', status_url, key_id='telegram',
            payload={
                'status': Order.Status.COMPLETED,
                'expected_updated_at': current['updated_at'],
            },
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['error']['code'], 'stale_order')

    def test_notification_claim_filters_audience_and_tracks_each_recipient(self):
        from telegram_bot.models import TelegramAccount, TelegramEventDelivery

        admin = TelegramAccount.objects.create(
            user=self.user, chat_id=101,
            role=TelegramAccount.Role.ADMINISTRATOR,
        )
        order = self.create_order()
        NotificationEvent.objects.create(
            event_type=NotificationEvent.EventType.ORDER_STATUS_CHANGED,
            audience=NotificationEvent.Audience.CUSTOMER,
            order=order,
        )
        claim = self.api_request(
            'POST', reverse('shop:api_notification_claim'), key_id='telegram',
            payload={'limit': 10, 'audience': 'admin'},
            idempotency_key=uuid.uuid4(),
        ).json()
        self.assertEqual(len(claim['events']), 1)
        event = claim['events'][0]
        delivery_url = reverse(
            'shop:api_notification_recipient_delivered', args=[event['id']],
        )
        payload = {
            'lease_id': event['lease_id'], 'chat_id': admin.chat_id, 'message_id': 77,
        }
        saved = self.api_request(
            'POST', delivery_url, key_id='telegram', payload=payload,
            idempotency_key=uuid.uuid4(),
        )
        replay = self.api_request(
            'POST', delivery_url, key_id='telegram', payload=payload,
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(TelegramEventDelivery.objects.count(), 1)
        deliveries = self.api_request(
            'GET', reverse('shop:api_notification_deliveries', args=[event['id']]),
            key_id='telegram',
        )
        self.assertEqual(deliveries.json()['results'][0]['message_id'], 77)

    def test_order_outbox_created_and_status_events_are_transactional(self):
        order = self.create_order()
        created = NotificationEvent.objects.get(
            event_type=NotificationEvent.EventType.ORDER_CREATED,
            audience=NotificationEvent.Audience.ADMIN,
        )
        self.assertEqual(created.audience, NotificationEvent.Audience.ADMIN)
        self.assertEqual(created.payload['number'], order.number)
        customer_created = NotificationEvent.objects.get(
            event_type=NotificationEvent.EventType.ORDER_CREATED,
            audience=NotificationEvent.Audience.CUSTOMER,
        )
        self.assertEqual(customer_created.user_id, self.user.pk)

        with self.captureOnCommitCallbacks(execute=True):
            order.status = Order.Status.SHIPPING
            order.save(update_fields=('status',))
        changed = NotificationEvent.objects.get(
            event_type=NotificationEvent.EventType.ORDER_STATUS_CHANGED,
        )
        self.assertEqual(changed.audience, NotificationEvent.Audience.CUSTOMER)
        self.assertEqual(changed.payload['old_status'], Order.Status.NEW)
        self.assertEqual(changed.payload['status'], Order.Status.SHIPPING)

        with self.captureOnCommitCallbacks(execute=True):
            order.manager_comment = 'Комментарий'
            order.save(update_fields=('manager_comment',))
        self.assertEqual(
            NotificationEvent.objects.filter(
                event_type=NotificationEvent.EventType.ORDER_STATUS_CHANGED,
            ).count(), 1,
        )

    def test_order_and_outbox_are_rolled_back_together(self):
        try:
            with transaction.atomic():
                Order.objects.create(
                    user=self.user, customer_name='Анна', customer_email=self.user.email,
                    customer_phone='+79990000000', delivery_address='Москва',
                )
                raise RuntimeError('rollback')
        except RuntimeError:
            pass
        self.assertFalse(Order.objects.exists())
        self.assertFalse(NotificationEvent.objects.exists())

    def test_personal_request_created_and_status_events_are_queued(self):
        personal = ProductRequest.objects.create(
            user=self.user, customer_name='Анна', customer_email=self.user.email,
            customer_phone='+79990000000',
        )
        created = NotificationEvent.objects.get(
            event_type=NotificationEvent.EventType.REQUEST_CREATED,
            audience=NotificationEvent.Audience.ADMIN,
        )
        self.assertEqual(created.product_request_id, personal.pk)
        self.assertEqual(created.audience, NotificationEvent.Audience.ADMIN)
        self.assertTrue(NotificationEvent.objects.filter(
            event_type=NotificationEvent.EventType.REQUEST_CREATED,
            audience=NotificationEvent.Audience.CUSTOMER,
            user_id=self.user.pk,
        ).exists())

        personal.status = ProductRequest.Status.CONFIRMATION
        personal.save(update_fields=('status',))
        changed = NotificationEvent.objects.get(
            event_type=NotificationEvent.EventType.REQUEST_STATUS_CHANGED,
        )
        self.assertEqual(changed.user_id, self.user.pk)
        self.assertEqual(changed.payload['old_status'], ProductRequest.Status.NEW)
        self.assertEqual(changed.payload['status'], ProductRequest.Status.CONFIRMATION)

    def test_notification_claim_ack_and_worker_ownership(self):
        order = self.create_order()
        claim_key = uuid.uuid4()
        claim = self.api_request(
            'POST', reverse('shop:api_notification_claim'), key_id='telegram',
            payload={'limit': 10, 'lease_seconds': 60}, idempotency_key=claim_key,
        )
        self.assertEqual(claim.status_code, 200)
        event_id = claim.json()['events'][0]['id']
        lease_id = claim.json()['events'][0]['lease_id']
        self.assertEqual(claim.json()['events'][0]['payload']['items'][0]['sku'], self.product.sku)
        event = NotificationEvent.objects.get(pk=event_id)
        self.assertEqual(event.order_id, order.pk)
        self.assertEqual(event.locked_by, 'telegram')

        ack_url = reverse('shop:api_notification_ack', args=[event_id])
        foreign = self.api_request(
            'POST', ack_url, key_id='other-worker',
            payload={'status': 'delivered', 'lease_id': lease_id},
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(foreign.status_code, 409)
        delivered = self.api_request(
            'POST', ack_url, key_id='telegram',
            payload={'status': 'delivered', 'lease_id': lease_id},
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(delivered.status_code, 200)
        event.refresh_from_db()
        self.assertIsNotNone(event.delivered_at)
        self.assertEqual(event.attempts, 1)

        next_claim = self.api_request(
            'POST', reverse('shop:api_notification_claim'), key_id='telegram',
            payload={'limit': 10}, idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(next_claim.json()['events'], [])

    def test_failed_notification_is_released_for_retry(self):
        self.create_order()
        claim = self.api_request(
            'POST', reverse('shop:api_notification_claim'), key_id='telegram',
            payload={'limit': 1}, idempotency_key=uuid.uuid4(),
        )
        event_id = claim.json()['events'][0]['id']
        lease_id = claim.json()['events'][0]['lease_id']
        response = self.api_request(
            'POST', reverse('shop:api_notification_ack', args=[event_id]),
            key_id='telegram',
            payload={
                'status': 'failed', 'lease_id': lease_id,
                'error': 'Telegram timeout', 'retry_after_seconds': 10,
            },
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(response.status_code, 200)
        event = NotificationEvent.objects.get(pk=event_id)
        self.assertIsNone(event.delivered_at)
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.last_error, 'Telegram timeout')
        self.assertEqual(event.locked_by, '')

    def test_broadcast_requires_separate_scope_and_is_idempotent(self):
        url = reverse('shop:api_broadcast_create')
        payload = {'title': 'Новинки', 'message': 'Новая коллекция', 'segment': 'all'}
        denied = self.api_request(
            'POST', url, key_id='telegram', payload=payload,
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(denied.status_code, 403)

        key = uuid.uuid4()
        created = self.api_request(
            'POST', url, key_id='marketing', payload=payload, idempotency_key=key,
        )
        replay = self.api_request(
            'POST', url, key_id='marketing', payload=payload, idempotency_key=key,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(NotificationEvent.objects.filter(
            event_type=NotificationEvent.EventType.BROADCAST,
        ).count(), 1)