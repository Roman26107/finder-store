import logging
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from shop.logging_handlers import DatabaseErrorHandler
from shop.models import CustomerProfile, ErrorLog, SecurityChallenge
from shop.security import create_security_challenge, verify_security_challenge
from shop import error_views
from telegram_bot.linking import (
    claim_link_session, claim_registration_session,
    cleanup_expired_registration_sessions, confirm_link_session, create_link_session,
)
from telegram_bot.models import (
    TelegramAccount, TelegramRegistrationSession,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='secure@example.com', email='secure@example.com',
            password='SecurePass-2026',
        )
        CustomerProfile.objects.create(user=self.user, phone='+79990000001')

    @staticmethod
    def email_code():
        return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)

    def test_password_reset_is_neutral_and_sends_one_time_link_for_existing_user(self):
        existing = self.client.post(reverse('shop:password_reset'), {'email': self.user.email})
        self.assertRedirects(existing, reverse('shop:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/account/password-reset/', mail.outbox[0].body)
        self.assertIn('FINDER', mail.outbox[0].subject)
        self.assertIn('FINDER', mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertEqual(mail.outbox[0].alternatives[0].mimetype, 'text/html')
        self.assertIn('Создать новый пароль', mail.outbox[0].alternatives[0].content)

        unknown = self.client.post(reverse('shop:password_reset'), {'email': 'unknown@example.com'})
        self.assertRedirects(unknown, reverse('shop:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)

    def test_telegram_return_url_restores_link_session_for_authenticated_owner(self):
        link = create_link_session(self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse('shop:telegram_link'), {'token': str(link.token)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session['telegram_link_token'], str(link.token))

    def test_security_code_is_hashed_bound_and_one_time(self):
        challenge = create_security_challenge(
            target_user=self.user, purpose=SecurityChallenge.Purpose.WEB_LOGIN,
        )
        code = self.email_code()
        self.assertIn('FINDER', mail.outbox[-1].subject)
        self.assertEqual(len(mail.outbox[-1].alternatives), 1)
        self.assertIn(code, mail.outbox[-1].alternatives[0].content)
        self.assertNotIn(code, challenge.code_hash)

        verified, _ = verify_security_challenge(
            challenge.pk, code, purpose=SecurityChallenge.Purpose.WEB_LOGIN,
            target_user_id=self.user.pk,
        )
        self.assertIsNotNone(verified)
        repeated, message = verify_security_challenge(challenge.pk, code)
        self.assertIsNone(repeated)
        self.assertIn('уже использован', message)

    @override_settings(TELEGRAM_BOT_USERNAME='aurene_test_bot')
    def test_telegram_link_requires_bot_claim_and_site_code(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('shop:telegram_link_start'))
        self.assertRedirects(response, reverse('shop:telegram_link'))
        token = self.client.session['telegram_link_token']

        page = self.client.get(reverse('shop:telegram_link'))
        self.assertContains(page, f'https://t.me/aurene_test_bot?start=link_{token.replace("-", "")}')
        session, code, error = claim_link_session(token, chat_id=123456, username='secure_tg')
        self.assertEqual(error, '')
        self.assertNotIn(code, session.code_hash)
        _, repeated_code, repeated_message = claim_link_session(
            token, chat_id=123456, username='secure_tg',
        )
        self.assertIsNone(repeated_code)
        self.assertIn('Код уже был выдан', repeated_message)

        wrong = self.client.post(reverse('shop:telegram_link'), {'code': '111111'})
        self.assertContains(wrong, 'Неверный код')
        success = self.client.post(reverse('shop:telegram_link'), {'code': code})
        self.assertRedirects(success, reverse('shop:account'))
        account = TelegramAccount.objects.get(user=self.user)
        self.assertEqual(account.chat_id, 123456)
        self.user.customer_profile.refresh_from_db()
        self.assertEqual(self.user.customer_profile.telegram, '@secure_tg')

    def test_telegram_relink_replaces_chat_and_preserves_administrator_role(self):
        account = TelegramAccount.objects.create(
            user=self.user, chat_id=100, username='old_name',
            role=TelegramAccount.Role.ADMINISTRATOR,
        )
        link = create_link_session(self.user)
        _, code, error = claim_link_session(
            link.token, chat_id=200, username='new_name',
        )
        self.assertEqual(error, '')

        updated, message = confirm_link_session(self.user, link.token, code)

        self.assertIn('успешно', message)
        account.refresh_from_db()
        self.assertEqual(updated.pk, account.pk)
        self.assertEqual(account.chat_id, 200)
        self.assertEqual(account.username, 'new_name')
        self.assertEqual(account.role, TelegramAccount.Role.ADMINISTRATOR)

    @override_settings(TELEGRAM_BOT_USERNAME='aurene_test_bot')
    def test_registration_links_preverified_telegram_only_when_account_is_created(self):
        page = self.client.get(reverse('shop:register'))
        token = page.context['form']['telegram_registration_token'].value()
        _, code, error = claim_registration_session(
            token, chat_id=654321, username='new_customer',
        )
        self.assertEqual(error, '')
        self.assertFalse(get_user_model().objects.filter(email='new@example.com').exists())
        self.assertFalse(TelegramAccount.objects.filter(chat_id=654321).exists())

        response = self.client.post(reverse('shop:register'), {
            'first_name': 'Новая', 'last_name': 'Покупательница',
            'email': 'new@example.com', 'phone': '+79990000002',
            'gender': '', 'password1': 'SecurePass-2026',
            'password2': 'SecurePass-2026',
            'telegram_registration_token': str(token),
            'telegram_code': code,
        })

        self.assertRedirects(response, reverse('shop:verify_email'))
        user = get_user_model().objects.get(email='new@example.com')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        account = TelegramAccount.objects.get(user=user)
        self.assertEqual(account.chat_id, 654321)
        self.assertEqual(account.username, 'new_customer')
        self.assertFalse(TelegramRegistrationSession.objects.filter(token=token).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertIn('Подтверждение e-mail', mail.outbox[0].alternatives[0].content)

    @override_settings(TELEGRAM_BOT_USERNAME='aurene_test_bot')
    def test_wrong_telegram_code_does_not_create_store_account(self):
        page = self.client.get(reverse('shop:register'))
        token = page.context['form']['telegram_registration_token'].value()
        claim_registration_session(token, chat_id=654322, username='wrong_code')
        response = self.client.post(reverse('shop:register'), {
            'first_name': 'Новая', 'last_name': 'Покупательница',
            'email': 'wrong@example.com', 'phone': '+79990000003',
            'gender': '', 'password1': 'SecurePass-2026',
            'password2': 'SecurePass-2026',
            'telegram_registration_token': str(token),
            'telegram_code': '111111',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Неверный код')
        self.assertFalse(get_user_model().objects.filter(email='wrong@example.com').exists())
        self.assertFalse(TelegramAccount.objects.filter(chat_id=654322).exists())

    def test_expired_unfinished_telegram_registration_is_deleted(self):
        expired = TelegramRegistrationSession.objects.create(
            expires_at=timezone.now() - timedelta(seconds=1),
            chat_id=654323,
        )
        self.assertEqual(cleanup_expired_registration_sessions(), 1)
        self.assertFalse(TelegramRegistrationSession.objects.filter(pk=expired.pk).exists())
        self.assertFalse(TelegramAccount.objects.filter(chat_id=654323).exists())

    @override_settings(TELEGRAM_BOT_USERNAME='aurene_test_bot')
    def test_registration_page_has_pre_registration_telegram_code(self):
        response = self.client.get(reverse('shop:register'))
        self.assertContains(response, '>Запустить бота<', html=False)
        self.assertContains(response, 'Telegram (необязательно)')
        self.assertContains(response, 'name="telegram_code"')
        self.assertContains(response, '?start=register_')
        self.assertContains(response, '>Создать аккаунт<', html=False)
        self.assertNotContains(response, 'Подключите Telegram до регистрации')
        self.assertContains(response, 'data-phone-input')
        html = response.content.decode()
        self.assertLess(html.index('name="phone"'), html.index('telegram-registration-field'))
        self.assertLess(html.index('telegram-registration-field'), html.index('name="gender"'))

    def test_public_messages_have_auto_dismiss_hooks(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('shop:logout'), follow=True)
        self.assertContains(response, 'data-messages')
        self.assertContains(response, 'data-message')
        self.assertContains(response, 'Вы вышли из личного кабинета.')

    def test_telegram_chat_cannot_be_taken_from_another_account(self):
        other = get_user_model().objects.create_user(username='other', email='other@example.com')
        TelegramAccount.objects.create(user=other, chat_id=777)
        link = create_link_session(self.user)
        _, code, message = claim_link_session(link.token, chat_id=777, username='attacker')
        self.assertIsNone(code)
        self.assertIn('другому аккаунту', message)

    @override_settings(TESTING=False, ADMIN_2FA_MAX_AGE_SECONDS=60)
    def test_admin_middleware_rejects_missing_or_expired_second_factor(self):
        staff = get_user_model().objects.create_user(
            username='staff', email='staff@example.com', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse('shop:control_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('shop:login'), response.url)

        self.client.force_login(staff)
        session = self.client.session
        session['admin_2fa_verified_at'] = (
            timezone.now() - timedelta(seconds=120)
        ).isoformat()
        session.save()
        self.assertEqual(self.client.get(reverse('shop:control_dashboard')).status_code, 302)

    @override_settings(
        DJANGO_APPLICATION_RATE_LIMIT_ENABLED=True,
        DJANGO_TRUST_X_REAL_IP=False,
    )
    def test_sensitive_action_rate_limit_blocks_login_post_burst(self):
        cache.clear()
        url = reverse('shop:login')
        for _ in range(10):
            response = self.client.post(
                url, {'username': 'missing@example.com', 'password': 'wrong'},
                REMOTE_ADDR='198.51.100.10', HTTP_X_REAL_IP='203.0.113.99',
            )
            self.assertEqual(response.status_code, 200)

        blocked = self.client.post(
            url, {'username': 'missing@example.com', 'password': 'wrong'},
            REMOTE_ADDR='198.51.100.10', HTTP_X_REAL_IP='203.0.113.100',
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked['Retry-After'], '300')
        self.assertEqual(blocked['Cache-Control'], 'no-store')
        self.assertContains(blocked, 'Слишком много', status_code=429)
        self.assertContains(blocked, 'shop/css/errors.css', status_code=429)

        # Без явного доверия X-Real-IP клиент не может обойти лимит подменой заголовка.
        other_socket = self.client.post(
            url, {'username': 'missing@example.com', 'password': 'wrong'},
            REMOTE_ADDR='198.51.100.11', HTTP_X_REAL_IP='203.0.113.100',
        )
        self.assertEqual(other_socket.status_code, 200)
        cache.clear()

    @override_settings(
        DJANGO_APPLICATION_RATE_LIMIT_ENABLED=True,
        DJANGO_TRUST_X_REAL_IP=True,
    )
    def test_sensitive_action_rate_limit_uses_real_ip_only_when_trusted(self):
        cache.clear()
        url = reverse('shop:password_reset')
        for socket_ip in ('172.20.0.2', '172.20.0.3', '172.20.0.4'):
            response = self.client.post(
                url, {'email': 'missing@example.com'},
                REMOTE_ADDR=socket_ip, HTTP_X_REAL_IP='198.51.100.25',
            )
            self.assertEqual(response.status_code, 302)
        blocked = self.client.post(
            url, {'email': 'missing@example.com'},
            REMOTE_ADDR='172.20.0.5', HTTP_X_REAL_IP='198.51.100.25',
        )
        self.assertEqual(blocked.status_code, 429)
        cache.clear()

    def test_error_log_handler_redacts_secret_values(self):
        record = logging.LogRecord(
            'security.test', logging.ERROR, __file__, 1,
            'Request failed password=hunter2 token=abc123 code=654321', (), None,
        )
        DatabaseErrorHandler().emit(record)
        item = ErrorLog.objects.get()
        self.assertNotIn('hunter2', item.message)
        self.assertNotIn('abc123', item.message)
        self.assertNotIn('654321', item.message)
        self.assertIn('[REDACTED]', item.message)

    def test_confirmed_superuser_can_grant_web_admin_only_after_email_code(self):
        admin = get_user_model().objects.create_superuser(
            username='owner', email='owner@example.com', password='OwnerPass-2026',
        )
        target = get_user_model().objects.create_user(
            username='manager', email='manager@example.com', password='ManagerPass-2026',
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse('shop:control_admin_grant_start', args=[target.pk, 'web'])
        )
        self.assertRedirects(response, reverse('shop:control_admin_grant_verify'))
        target.refresh_from_db()
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)

        response = self.client.post(
            reverse('shop:control_admin_grant_verify'), {'code': self.email_code()}
        )
        self.assertRedirects(response, reverse('shop:control_user_detail', args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.is_staff)
        self.assertTrue(target.is_superuser)


class ErrorPageTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get('/broken-page/?from=test')

    def test_all_error_handlers_render_branded_standalone_pages(self):
        responses = {
            400: error_views.bad_request(self.request, Exception()),
            403: error_views.permission_denied(self.request, Exception()),
            404: error_views.page_not_found(self.request, Exception()),
            429: error_views.too_many_requests(self.request, retry_after=120),
            500: error_views.server_error(self.request),
        }

        for status_code, response in responses.items():
            with self.subTest(status_code=status_code):
                self.assertEqual(response.status_code, status_code)
                self.assertIn('FINDER'.encode(), response.content)
                self.assertIn(str(status_code).encode(), response.content)
                self.assertIn(b'shop/css/errors.css', response.content)
                self.assertEqual(response['Cache-Control'], 'no-store')

        self.assertEqual(responses[429]['Retry-After'], '120')

    @override_settings(DEBUG=False)
    def test_real_missing_route_uses_branded_404(self):
        response = self.client.get('/definitely-missing-finder-page/')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Страница', status_code=404)
        self.assertContains(response, 'shop/css/errors.css', status_code=404)