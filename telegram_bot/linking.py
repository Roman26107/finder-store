import secrets
import time
import uuid
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    TelegramAccount, TelegramLinkSession, TelegramRegistrationSession,
)


LINK_TTL = timedelta(minutes=15)
MAX_LINK_ATTEMPTS = 5


def cleanup_expired_registration_sessions(now=None):
    queryset = TelegramRegistrationSession.objects.filter(
        expires_at__lt=now or timezone.now(),
    )
    # Polling вызывается часто: не берём SQLite write-lock пустым DELETE.
    if not queryset.exists():
        return 0
    return queryset.delete()[0]


def create_registration_session():
    now = timezone.now()
    cleanup_expired_registration_sessions(now)
    return TelegramRegistrationSession.objects.create(expires_at=now + LINK_TTL)


def claim_registration_session(token, *, chat_id, username=''):
    """Выдаёт код фактическому private chat до создания пользователя сайта."""
    try:
        token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return None, None, 'Ссылка регистрации недействительна или устарела.'
    session = TelegramRegistrationSession.objects.filter(token=token).first()
    now = timezone.now()
    if session is None or now > session.expires_at:
        return None, None, 'Ссылка регистрации недействительна или устарела.'
    if session.code_hash:
        return session, None, 'Код уже был выдан. Обновите страницу регистрации для нового кода.'
    if TelegramAccount.objects.filter(chat_id=chat_id).exists():
        return session, None, 'Этот Telegram уже привязан к существующему аккаунту магазина.'

    code = f'{secrets.randbelow(1_000_000):06d}'
    code_hash = make_password(code)
    normalized_username = str(username or '').strip().lstrip('@')[:64]
    updated = 0
    for attempt in range(3):
        try:
            updated = TelegramRegistrationSession.objects.filter(
                token=token, expires_at__gte=timezone.now(), code_hash='',
            ).update(
                chat_id=chat_id, username=normalized_username,
                code_hash=code_hash, attempts=0,
            )
            break
        except OperationalError as exc:
            if 'database is locked' not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
    session.refresh_from_db()
    if not updated:
        return session, None, 'Код уже был выдан. Обновите страницу регистрации для нового кода.'
    return session, code, ''


def verify_registration_code(token, code):
    """Проверяет код, но не создаёт и не привязывает аккаунт до submit формы."""
    try:
        token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return None, 'Сессия Telegram устарела. Обновите страницу и запустите бота снова.'
    session = TelegramRegistrationSession.objects.filter(token=token).first()
    now = timezone.now()
    if session is None or now > session.expires_at:
        return None, 'Срок привязки Telegram истёк. Обновите страницу и запустите бота снова.'
    if not session.code_hash or session.chat_id is None:
        return None, 'Сначала нажмите «Запустить бота» и получите код в Telegram.'
    if session.attempts >= MAX_LINK_ATTEMPTS:
        return None, 'Превышено количество попыток. Обновите страницу и получите новый код.'
    if not check_password(str(code), session.code_hash):
        TelegramRegistrationSession.objects.filter(
            token=token, attempts__lt=MAX_LINK_ATTEMPTS,
        ).update(attempts=F('attempts') + 1)
        session.refresh_from_db(fields=('attempts',))
        return None, f'Неверный код. Осталось попыток: {max(0, MAX_LINK_ATTEMPTS - session.attempts)}.'
    if TelegramAccount.objects.filter(chat_id=session.chat_id).exists():
        return None, 'Этот Telegram уже привязан к существующему аккаунту магазина.'
    return session, ''


def create_link_session(user):
    now = timezone.now()
    TelegramLinkSession.objects.filter(
        user=user, confirmed_at__isnull=True,
    ).update(expires_at=now)
    return TelegramLinkSession.objects.create(user=user, expires_at=now + LINK_TTL)


def claim_link_session(token, *, chat_id, username=''):
    """Привязывает deep-link к private chat и возвращает код ровно один раз."""
    now = timezone.now()
    try:
        token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return None, None, 'Ссылка недействительна или устарела.'
    session = TelegramLinkSession.objects.select_related('user').filter(token=token).first()
    if session is None or session.confirmed_at or now > session.expires_at:
        return None, None, 'Ссылка недействительна или устарела.'
    if session.code_hash:
        return session, None, 'Код уже был выдан. Создайте новую ссылку на сайте.'
    conflict = TelegramAccount.objects.filter(chat_id=chat_id).exclude(user=session.user).exists()
    if conflict:
        return session, None, 'Этот Telegram уже привязан к другому аккаунту магазина.'

    # PBKDF2 намеренно выполняется до записи в БД. На SQLite хеширование внутри
    # read-транзакции оставляло lock открытым и приводило к SQLITE_BUSY при частом polling.
    code = f'{secrets.randbelow(1_000_000):06d}'
    code_hash = make_password(code)
    normalized_username = str(username or '').strip().lstrip('@')[:64]
    updated = 0
    for attempt in range(3):
        try:
            updated = TelegramLinkSession.objects.filter(
                token=token,
                confirmed_at__isnull=True,
                expires_at__gte=timezone.now(),
                code_hash='',
            ).update(
                chat_id=chat_id,
                username=normalized_username,
                code_hash=code_hash,
                attempts=0,
            )
            break
        except OperationalError as exc:
            if 'database is locked' not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
    session.refresh_from_db()
    if not updated:
        if session.confirmed_at or timezone.now() > session.expires_at:
            return None, None, 'Ссылка недействительна или устарела.'
        return session, None, 'Код уже был выдан. Создайте новую ссылку на сайте.'
    return session, code, ''


def confirm_link_session(user, token, code):
    now = timezone.now()
    session = TelegramLinkSession.objects.filter(token=token, user=user).first()
    if session is None:
        return None, 'Сессия привязки не найдена. Создайте новую ссылку.'
    if session.confirmed_at:
        return None, 'Этот код уже использован.'
    if now > session.expires_at:
        return None, 'Срок действия кода истёк. Создайте новую ссылку.'
    if not session.code_hash or session.chat_id is None:
        return None, 'Сначала откройте бота по кнопке и нажмите «Запустить».'
    if session.attempts >= MAX_LINK_ATTEMPTS:
        return None, 'Превышено количество попыток. Создайте новую ссылку.'
    if not check_password(str(code), session.code_hash):
        TelegramLinkSession.objects.filter(
            token=token, user=user, confirmed_at__isnull=True,
            attempts__lt=MAX_LINK_ATTEMPTS,
        ).update(attempts=F('attempts') + 1)
        session.refresh_from_db(fields=('attempts',))
        return None, f'Неверный код. Осталось попыток: {max(0, MAX_LINK_ATTEMPTS - session.attempts)}.'
    if TelegramAccount.objects.filter(chat_id=session.chat_id).exclude(user=user).exists():
        return None, 'Этот Telegram уже привязан к другому аккаунту магазина.'

    try:
        with transaction.atomic():
            consumed = TelegramLinkSession.objects.filter(
                token=token,
                user=user,
                confirmed_at__isnull=True,
                expires_at__gte=timezone.now(),
                code_hash=session.code_hash,
                attempts__lt=MAX_LINK_ATTEMPTS,
            ).update(confirmed_at=now, code_hash='')
            if not consumed:
                return None, 'Код уже использован или срок его действия истёк.'
            account, _ = TelegramAccount.objects.update_or_create(
                user=user,
                defaults={
                    'chat_id': session.chat_id,
                    'username': session.username,
                    'is_active': True,
                },
            )
            profile = getattr(user, 'customer_profile', None)
            if profile is not None:
                profile.telegram = f'@{session.username}' if session.username else ''
                profile.save(update_fields=('telegram',))
    except IntegrityError:
        return None, 'Этот Telegram уже привязан к другому аккаунту магазина.'
    return account, 'Telegram успешно привязан к аккаунту.'