import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.utils import timezone

from .emailing import finder_code_email_html
from .models import CustomerProfile


CODE_TTL = timedelta(minutes=15)
RESEND_COOLDOWN = timedelta(minutes=1)
MAX_VERIFICATION_ATTEMPTS = 5


def normalize_phone(value):
    """Проверяет телефон и приводит его к международному формату E.164."""
    raw = str(value or '').strip()
    if not raw or not re.fullmatch(r'\+?[\d\s()\-]+', raw):
        raise ValueError('Введите корректный номер, например +7 999 123-45-67.')
    if raw.count('+') > 1 or ('+' in raw and not raw.startswith('+')):
        raise ValueError('Введите корректный номер, например +7 999 123-45-67.')
    digits = re.sub(r'\D', '', raw)

    if raw.startswith('+'):
        if digits.startswith('7'):
            if len(digits) != 11:
                raise ValueError('Российский номер должен содержать 10 цифр после +7.')
        elif not 10 <= len(digits) <= 15:
            raise ValueError('Международный номер должен содержать от 10 до 15 цифр.')
        return f'+{digits}'

    if digits.startswith('8'):
        if len(digits) != 11:
            raise ValueError('Российский номер должен содержать 10 цифр после 8.')
        digits = f'7{digits[1:]}'
    elif digits.startswith('7'):
        if len(digits) != 11:
            raise ValueError('Российский номер должен содержать 10 цифр после 7.')
    elif len(digits) == 10:
        digits = f'7{digits}'
    else:
        raise ValueError(
            'Введите российский номер из 10 цифр или международный номер с символом +.'
        )
    return f'+{digits}'


def normalize_telegram(value):
    """Приводит Telegram username или t.me-ссылку к виду @username."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    raw = re.sub(r'^https?://', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'^(?:www\.)?(?:t\.me|telegram\.me)/', '', raw, flags=re.IGNORECASE)
    username = raw.split('?', 1)[0].strip().strip('/').lstrip('@')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username):
        raise ValueError(
            'Введите Telegram в формате @username или https://t.me/username.'
        )
    return f'@{username}'


def get_customer_profile(user):
    profile, _ = CustomerProfile.objects.get_or_create(user=user)
    return profile


def can_resend_verification(profile, now=None):
    now = now or timezone.now()
    return (
        not profile.email_verification_sent_at
        or now - profile.email_verification_sent_at >= RESEND_COOLDOWN
    )


def verification_retry_seconds(profile, now=None):
    now = now or timezone.now()
    if can_resend_verification(profile, now):
        return 0
    retry_at = profile.email_verification_sent_at + RESEND_COOLDOWN
    return max(1, int((retry_at - now).total_seconds()) + 1)


def send_email_verification(profile):
    if profile.email_verified:
        return False
    now = timezone.now()
    if not can_resend_verification(profile, now):
        return False
    code = f'{secrets.randbelow(1_000_000):06d}'
    profile.email_verification_code_hash = make_password(code)
    profile.email_verification_sent_at = now
    profile.email_verification_expires_at = now + CODE_TTL
    profile.email_verification_attempts = 0
    profile.save(update_fields=(
        'email_verification_code_hash',
        'email_verification_sent_at',
        'email_verification_expires_at',
        'email_verification_attempts',
    ))
    send_mail(
        'Код подтверждения e-mail — FINDER',
        (
            f'Ваш код подтверждения: {code}\n\n'
            'Код действует 15 минут. Если вы не регистрировались в FINDER, '
            'просто проигнорируйте это письмо.'
        ),
        settings.DEFAULT_FROM_EMAIL,
        [profile.user.email],
        fail_silently=False,
        html_message=finder_code_email_html(
            heading='Подтверждение e-mail',
            intro='Введите этот код на сайте FINDER, чтобы завершить регистрацию.',
            code=code,
            notice=(
                'Код действует 15 минут. Если вы не регистрировались в FINDER, '
                'просто проигнорируйте это письмо.'
            ),
        ),
    )
    return True


def verify_email_code(profile, code):
    now = timezone.now()
    if profile.email_verified:
        return True, 'E-mail уже подтверждён.'
    if not profile.email_verification_code_hash or not profile.email_verification_expires_at:
        return False, 'Сначала запросите новый код подтверждения.'
    if now > profile.email_verification_expires_at:
        return False, 'Срок действия кода истёк. Запросите новый код.'
    if profile.email_verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
        return False, 'Превышено количество попыток. Запросите новый код.'
    if not check_password(code, profile.email_verification_code_hash):
        profile.email_verification_attempts += 1
        profile.save(update_fields=('email_verification_attempts',))
        attempts_left = MAX_VERIFICATION_ATTEMPTS - profile.email_verification_attempts
        return False, f'Неверный код. Осталось попыток: {attempts_left}.'
    profile.email_verified = True
    profile.email_verification_code_hash = ''
    profile.email_verification_expires_at = None
    profile.email_verification_attempts = 0
    profile.save(update_fields=(
        'email_verified',
        'email_verification_code_hash',
        'email_verification_expires_at',
        'email_verification_attempts',
    ))
    return True, 'E-mail успешно подтверждён.'


def reset_email_verification(profile):
    profile.email_verified = False
    profile.email_verification_code_hash = ''
    profile.email_verification_sent_at = None
    profile.email_verification_expires_at = None
    profile.email_verification_attempts = 0
    profile.save(update_fields=(
        'email_verified',
        'email_verification_code_hash',
        'email_verification_sent_at',
        'email_verification_expires_at',
        'email_verification_attempts',
    ))


def clear_failed_verification_delivery(profile):
    """Разрешает немедленный повтор после технической ошибки почтового backend."""
    profile.email_verification_code_hash = ''
    profile.email_verification_sent_at = None
    profile.email_verification_expires_at = None
    profile.email_verification_attempts = 0
    profile.save(update_fields=(
        'email_verification_code_hash',
        'email_verification_sent_at',
        'email_verification_expires_at',
        'email_verification_attempts',
    ))