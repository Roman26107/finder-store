import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .emailing import finder_code_email_html
from .models import SecurityChallenge


CHALLENGE_TTL = timedelta(minutes=15)
MAX_CHALLENGE_ATTEMPTS = 5


class SecurityChallengeDeliveryError(RuntimeError):
    pass


SUBJECTS = {
    SecurityChallenge.Purpose.WEB_LOGIN: 'Код входа в панель управления — FINDER',
    SecurityChallenge.Purpose.TELEGRAM_LOGIN: 'Код входа в Telegram-панель — FINDER',
    SecurityChallenge.Purpose.WEB_ADMIN_GRANT: 'Подтверждение назначения администратора — FINDER',
    SecurityChallenge.Purpose.TELEGRAM_ADMIN_GRANT: 'Подтверждение Telegram-администратора — FINDER',
}


def create_security_challenge(*, target_user, purpose, requested_by=None, context=None):
    """Создаёт challenge и отправляет код; открытый код в БД не сохраняется."""
    email = (target_user.email or '').strip()
    if not email:
        raise ValueError('Для пользователя не указан e-mail.')
    now = timezone.now()
    code = f'{secrets.randbelow(1_000_000):06d}'
    with transaction.atomic():
        SecurityChallenge.objects.filter(
            target_user=target_user, purpose=purpose, consumed_at__isnull=True,
        ).update(consumed_at=now)
        challenge = SecurityChallenge.objects.create(
            target_user=target_user,
            requested_by=requested_by,
            purpose=purpose,
            code_hash=make_password(code),
            context=context or {},
            expires_at=now + CHALLENGE_TTL,
        )
        try:
            send_mail(
                SUBJECTS[purpose],
                (
                    f'Ваш код безопасности: {code}\n\n'
                    'Код действует 15 минут и подходит только для одной операции. '
                    'Никому не сообщайте его. Если вы не выполняли это действие, '
                    'не вводите код и смените пароль учётной записи.'
                ),
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
                html_message=finder_code_email_html(
                    heading=SUBJECTS[purpose],
                    intro='Введите этот код для подтверждения защищённого действия в FINDER.',
                    code=code,
                    notice=(
                        'Код действует 15 минут и подходит только для одной операции. '
                        'Никому не сообщайте его. Если вы не выполняли это действие, '
                        'не вводите код и смените пароль учётной записи.'
                    ),
                ),
            )
        except Exception as exc:
            challenge.delete()
            raise SecurityChallengeDeliveryError('Не удалось отправить код безопасности.') from exc
    return challenge


def verify_security_challenge(challenge_id, code, *, purpose=None, target_user_id=None):
    """Атомарно проверяет и поглощает одноразовый challenge."""
    now = timezone.now()
    with transaction.atomic():
        try:
            challenge = SecurityChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        except (ValueError, ValidationError):
            challenge = None
        if challenge is None:
            return None, 'Проверка не найдена. Запросите новый код.'
        if purpose and challenge.purpose != purpose:
            return None, 'Код предназначен для другой операции.'
        if target_user_id and challenge.target_user_id != target_user_id:
            return None, 'Код предназначен для другого пользователя.'
        if challenge.consumed_at:
            return None, 'Код уже использован. Запросите новый.'
        if now > challenge.expires_at:
            return None, 'Срок действия кода истёк. Запросите новый.'
        if challenge.attempts >= MAX_CHALLENGE_ATTEMPTS:
            return None, 'Превышено количество попыток. Запросите новый код.'
        if not check_password(str(code), challenge.code_hash):
            challenge.attempts += 1
            challenge.save(update_fields=('attempts',))
            left = MAX_CHALLENGE_ATTEMPTS - challenge.attempts
            return None, f'Неверный код. Осталось попыток: {left}.'
        challenge.consumed_at = now
        challenge.save(update_fields=('consumed_at',))
    return challenge, 'Код подтверждён.'