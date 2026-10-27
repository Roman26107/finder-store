from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .models import CatalogBrand, CustomerProfile, ExchangeRate, Order, PricingRule, Product
from .pricing import (
    calculate_price,
    clear_pricing_cache,
    fetch_cbr_rates,
    normalize_currency,
    update_cbr_rates,
)


class PricingCalculationTests(TestCase):
    def setUp(self):
        clear_pricing_cache()

    def tearDown(self):
        clear_pricing_cache()

    def test_currency_aliases_are_normalized(self):
        aliases = {
            'cny': 'CNY', '¥': 'CNY', '￥': 'CNY', 'RMB': 'CNY',
            '$': 'USD', 'us$': 'USD', '€': 'EUR',
            '₽': 'RUB', 'rur': 'RUB', ' rub ': 'RUB',
        }
        for source, expected in aliases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_currency(source), expected)

    def test_cbr_xml_rate_is_divided_by_nominal(self):
        xml = b'''<?xml version="1.0" encoding="windows-1251"?>
        <ValCurs Date="05.09.2026">
          <Valute><CharCode>CNY</CharCode><Nominal>10</Nominal><Value>128,8490</Value></Valute>
          <Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>86,5857</Value></Valute>
          <Valute><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>100,5693</Value></Valute>
        </ValCurs>'''
        response = BytesIO(xml)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch('shop.pricing.urlopen', return_value=response):
            rate_date, rates = fetch_cbr_rates()

        self.assertEqual(rate_date, date(2026, 9, 5))
        self.assertEqual(rates['CNY'], Decimal('12.8849'))
        self.assertEqual(rates['USD'], Decimal('86.5857'))
        self.assertEqual(rates['EUR'], Decimal('100.5693'))
        self.assertEqual(rates['RUB'], Decimal('1'))

    def test_adjustments_are_applied_sequentially_in_configured_order(self):
        ExchangeRate.objects.filter(currency='CNY').update(rubles_per_unit=Decimal('10'))
        PricingRule.objects.create(
            name='Комиссия', adjustment_type='percent', value=Decimal('10'), position=10,
        )
        PricingRule.objects.create(
            name='Доставка', adjustment_type='fixed', value=Decimal('50'), position=20,
        )
        PricingRule.objects.create(
            name='Сервис', adjustment_type='percent', value=Decimal('20'), position=30,
        )
        clear_pricing_cache()

        result = calculate_price(Decimal('100'), '¥')

        self.assertEqual(result.base_rubles, Decimal('1000'))
        self.assertEqual([step.name for step in result.steps], ['Комиссия', 'Доставка', 'Сервис'])
        self.assertEqual([step.amount for step in result.steps], [
            Decimal('100'), Decimal('50'), Decimal('230'),
        ])
        self.assertEqual(result.total, Decimal('1380'))

    def test_percent_can_be_calculated_from_initial_ruble_amount(self):
        ExchangeRate.objects.filter(currency='CNY').update(rubles_per_unit=Decimal('10'))
        PricingRule.objects.create(
            name='Комиссия', adjustment_type='percent', value=Decimal('10'), position=10,
        )
        PricingRule.objects.create(
            name='Налог', adjustment_type='percent', value=Decimal('20'), position=20,
            calculate_from_initial=True,
        )
        clear_pricing_cache()

        result = calculate_price(Decimal('100'), 'CNY')

        self.assertEqual([step.amount for step in result.steps], [
            Decimal('100'), Decimal('200'),
        ])
        self.assertFalse(result.steps[0].calculate_from_initial)
        self.assertTrue(result.steps[1].calculate_from_initial)
        self.assertEqual(result.total, Decimal('1300'))

    def test_inactive_rule_is_ignored_and_rubles_use_rate_one(self):
        PricingRule.objects.create(
            name='Не применять', adjustment_type='fixed', value=Decimal('500'),
            position=1, is_active=False,
        )
        clear_pricing_cache()

        result = calculate_price('199.50', '₽')

        self.assertEqual(result.rate, Decimal('1'))
        self.assertEqual(result.steps, ())
        self.assertEqual(result.total, Decimal('200'))

    def test_ruble_rate_is_always_saved_as_one(self):
        ruble = ExchangeRate.objects.get(currency='RUB')
        ruble.rubles_per_unit = Decimal('2')
        ruble.save()

        ruble.refresh_from_db()
        self.assertEqual(ruble.rubles_per_unit, Decimal('1'))

    def test_missing_or_unknown_rate_returns_none(self):
        ExchangeRate.objects.filter(currency='CNY').delete()
        clear_pricing_cache()

        self.assertIsNone(calculate_price(100, 'CNY'))
        self.assertIsNone(calculate_price(100, 'GBP'))

    def test_product_without_rate_cannot_be_added_to_cart(self):
        CatalogBrand.objects.get_or_create(name='Hermès', slug='hermes')
        product = Product.objects.create(
            sku='NO-RATE-1', title='Товар без курса', price=Decimal('100'),
            currency='GBP', is_active=True, in_stock_ecom=True,
        )

        response = self.client.post(reverse('shop:cart_add', args=[product.pk]), follow=True)

        self.assertRedirects(response, product.get_absolute_url())
        self.assertContains(response, 'Цена товара пока не рассчитана')
        self.assertNotIn(str(product.pk), self.client.session.get('cart', {}))

    def test_update_helper_persists_fetched_rates(self):
        fetched = {
            'RUB': Decimal('1'), 'CNY': Decimal('13.5'),
            'USD': Decimal('90'), 'EUR': Decimal('105'),
        }
        with patch('shop.pricing.fetch_cbr_rates', return_value=(date(2026, 9, 6), fetched)):
            rate_date, rates = update_cbr_rates()

        cny = ExchangeRate.objects.get(currency='CNY')
        self.assertEqual(rate_date, date(2026, 9, 6))
        self.assertEqual(rates, fetched)
        self.assertEqual(cny.rubles_per_unit, Decimal('13.5'))
        self.assertEqual(cny.rate_date, date(2026, 9, 6))
        self.assertEqual(cny.source, 'Банк России')

    def test_price_cache_reloads_database_in_next_ttl_bucket(self):
        ExchangeRate.objects.filter(currency='USD').update(rubles_per_unit=Decimal('90'))
        clear_pricing_cache()
        with patch('shop.pricing.monotonic', return_value=100):
            self.assertEqual(calculate_price(1, 'USD').total, Decimal('90'))

        ExchangeRate.objects.filter(currency='USD').update(rubles_per_unit=Decimal('95'))
        with patch('shop.pricing.monotonic', return_value=161):
            self.assertEqual(calculate_price(1, 'USD').total, Decimal('95'))


class ExchangeRateCommandTests(TestCase):
    @patch('shop.management.commands.update_exchange_rates.update_cbr_rates')
    def test_once_updates_rates_and_exits(self, updater):
        updater.return_value = (
            date(2026, 9, 9),
            {'RUB': Decimal('1'), 'CNY': Decimal('13')},
        )

        call_command('update_exchange_rates', once=True)

        updater.assert_called_once_with()

    @patch('shop.management.commands.update_exchange_rates.update_cbr_rates')
    def test_once_raises_command_error_but_keeps_process_failure_explicit(self, updater):
        updater.side_effect = OSError('ЦБ недоступен')

        with self.assertRaisesMessage(CommandError, 'Курсы не обновлены'):
            call_command('update_exchange_rates', once=True)

    @patch('shop.management.commands.update_exchange_rates.time.sleep')
    @patch('shop.management.commands.update_exchange_rates.update_cbr_rates')
    def test_watch_mode_uses_five_minute_interval(self, updater, sleep):
        updater.return_value = (
            date(2026, 9, 9),
            {'RUB': Decimal('1'), 'USD': Decimal('90')},
        )
        sleep.side_effect = KeyboardInterrupt

        call_command('update_exchange_rates', interval=300)

        updater.assert_called_once_with()
        sleep.assert_called_once_with(300)

    def test_interval_cannot_be_zero(self):
        with self.assertRaisesMessage(CommandError, 'не меньше 1 секунды'):
            call_command('update_exchange_rates', interval=0)


class PricingConstructorAdminTests(TestCase):
    def setUp(self):
        self.url = reverse('admin:shop_product_pricing')
        self.superuser = get_user_model().objects.create_superuser(
            username='pricing-admin@example.com',
            email='pricing-admin@example.com',
            password='SecurePass-2026',
        )

    def test_constructor_requires_staff_and_pricing_permission(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

        no_permission = get_user_model().objects.create_user(
            username='staff-no-permission@example.com',
            password='SecurePass-2026',
            is_staff=True,
        )
        self.client.force_login(no_permission)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        no_permission.user_permissions.add(Permission.objects.get(
            content_type__app_label='shop', codename='change_pricingrule',
        ))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_constructor_renders_rates_rules_and_preview(self):
        PricingRule.objects.create(
            name='Доставка', adjustment_type='fixed', value=Decimal('1500'), position=10,
        )
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, {
            'action': 'preview', 'preview-amount': '100', 'preview-currency': 'CNY',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Конструктор стоимости')
        self.assertContains(response, 'Доставка')
        self.assertContains(response, '2 788 ₽')
        self.assertContains(response, 'pricing-constructor.js')

    def test_rules_can_be_created_and_deleted_with_formset(self):
        existing = PricingRule.objects.create(
            name='Старая доставка', adjustment_type='fixed', value=Decimal('100'), position=1,
        )
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, {
            'action': 'save_rules',
            'rules-TOTAL_FORMS': '2', 'rules-INITIAL_FORMS': '1',
            'rules-MIN_NUM_FORMS': '0', 'rules-MAX_NUM_FORMS': '1000',
            'rules-0-id': str(existing.pk), 'rules-0-position': '1',
            'rules-0-name': 'Старая доставка', 'rules-0-adjustment_type': 'fixed',
            'rules-0-value': '100', 'rules-0-is_active': 'on', 'rules-0-DELETE': 'on',
            'rules-1-id': '', 'rules-1-position': '20', 'rules-1-name': 'Новая комиссия',
            'rules-1-adjustment_type': 'percent', 'rules-1-value': '7.5',
            'rules-1-is_active': 'on',
        })

        self.assertRedirects(response, self.url)
        self.assertFalse(PricingRule.objects.filter(pk=existing.pk).exists())
        created = PricingRule.objects.get()
        self.assertEqual(created.name, 'Новая комиссия')
        self.assertEqual(created.value, Decimal('7.5'))

    def test_rates_can_be_edited_manually(self):
        self.client.force_login(self.superuser)
        rates = list(ExchangeRate.objects.order_by('currency'))
        data = {
            'action': 'save_rates', 'rates-TOTAL_FORMS': str(len(rates)),
            'rates-INITIAL_FORMS': str(len(rates)), 'rates-MIN_NUM_FORMS': '0',
            'rates-MAX_NUM_FORMS': '1000',
        }
        for index, rate in enumerate(rates):
            data.update({
                f'rates-{index}-id': str(rate.pk),
                f'rates-{index}-rubles_per_unit': '13.250000' if rate.currency == 'CNY' else str(rate.rubles_per_unit),
                f'rates-{index}-rate_date': '2026-09-07',
                f'rates-{index}-source': 'Ручная корректировка',
            })

        response = self.client.post(self.url, data)

        self.assertRedirects(response, self.url)
        cny = ExchangeRate.objects.get(currency='CNY')
        self.assertEqual(cny.rubles_per_unit, Decimal('13.25'))
        self.assertEqual(cny.rate_date, date(2026, 9, 7))
        self.assertEqual(cny.source, 'Ручная корректировка')

    def test_cbr_refresh_is_post_only_and_failure_keeps_stored_rates(self):
        self.client.force_login(self.superuser)
        original = ExchangeRate.objects.get(currency='CNY').rubles_per_unit
        with patch('shop.admin.update_cbr_rates', side_effect=OSError('ЦБ недоступен')) as updater:
            get_response = self.client.get(self.url, {'action': 'update_rates'})
            post_response = self.client.post(self.url, {'action': 'update_rates'}, follow=True)

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(updater.call_count, 1)
        self.assertContains(post_response, 'Сохранённые курсы не изменены')
        self.assertEqual(ExchangeRate.objects.get(currency='CNY').rubles_per_unit, original)

    def test_successful_cbr_refresh_is_triggered_by_post(self):
        self.client.force_login(self.superuser)
        refreshed = {
            'RUB': Decimal('1'), 'CNY': Decimal('13'),
            'USD': Decimal('90'), 'EUR': Decimal('105'),
        }
        with patch(
            'shop.admin.update_cbr_rates',
            return_value=(date(2026, 9, 8), refreshed),
        ) as updater:
            response = self.client.post(self.url, {'action': 'update_rates'}, follow=True)

        updater.assert_called_once_with()
        self.assertContains(response, 'Курсы на 08.09.2026 обновлены')


class RubleOrderSnapshotTests(TestCase):
    def setUp(self):
        clear_pricing_cache()
        ExchangeRate.objects.filter(currency='USD').update(rubles_per_unit=Decimal('90'))
        self.rule = PricingRule.objects.create(
            name='Доставка', adjustment_type='fixed', value=Decimal('1000'), position=1,
        )
        clear_pricing_cache()
        self.product = Product.objects.create(
            sku='USD-ORDER-1', title='Товар в долларах', price=Decimal('100'),
            currency='USD', is_active=True, in_stock_ecom=True,
        )
        self.user = get_user_model().objects.create_user(
            username='snapshot@example.com', email='snapshot@example.com',
            first_name='Анна', password='SecurePass-2026',
        )
        CustomerProfile.objects.create(
            user=self.user, phone='+79991234567', email_verified=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session['cart'] = {str(self.product.pk): 2}
        session.save()

    def tearDown(self):
        clear_pricing_cache()

    def test_checkout_captures_ruble_price_and_future_rules_do_not_change_order(self):
        response = self.client.post(reverse('shop:checkout'), {
            'delivery_address': 'Москва, ул. Тверская, д. 1',
            'preferred_contact': CustomerProfile.ContactMethod.PHONE,
        })

        order = Order.objects.get()
        item = order.items.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.unit_price, Decimal('10000'))
        self.assertEqual(item.currency, 'RUB')
        self.assertEqual(item.price_breakdown['base_rubles'], '9000.00')
        self.assertEqual(item.price_breakdown['steps'][0]['name'], 'Доставка')
        self.assertEqual(item.price_breakdown['steps'][0]['amount'], '1000.00')
        self.assertEqual(order.estimated_total, Decimal('20000'))
        self.assertEqual(order.currency, 'RUB')

        self.rule.value = Decimal('5000')
        self.rule.save()
        self.assertEqual(self.product.display_price, Decimal('14000'))
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.estimated_total, Decimal('20000'))
        self.assertEqual(item.unit_price, Decimal('10000'))