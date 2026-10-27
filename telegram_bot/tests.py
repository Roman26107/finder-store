from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import ManagerContact, TelegramAccount, TelegramUser


class TelegramAdminTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username='admin', email='admin@example.com', password='password',
        )
        self.customer = get_user_model().objects.create_user(
            username='buyer', email='buyer@example.com', password='password',
        )

    def test_telegram_category_lists_every_store_user_before_binding(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:telegram_bot_telegramuser_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'admin@example.com')
        self.assertContains(response, 'buyer@example.com')
        self.assertContains(response, 'Не привязан')
        self.assertIn(TelegramUser, admin.site._registry)

    def test_account_normalizes_username_and_role_is_explicit(self):
        account = TelegramAccount.objects.create(
            user=self.superuser, chat_id=123, username='@admin_name',
        )
        self.assertEqual(account.username, 'admin_name')
        self.assertEqual(account.role, TelegramAccount.Role.CUSTOMER)

    def test_manager_contact_accepts_arbitrary_data(self):
        contact = ManagerContact.objects.create(
            kind=ManagerContact.Kind.CUSTOM,
            label='Чат на сайте', value='Любые данные: кабинет № 7',
            url='https://shop.example.com/support',
        )
        self.assertEqual(contact.value, 'Любые данные: кабинет № 7')
