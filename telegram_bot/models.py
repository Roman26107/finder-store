import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models


class TelegramUser(User):
    """Proxy стандартного User для отдельной категории Telegram в Admin."""

    class Meta:
        proxy = True
        verbose_name = 'пользователь Telegram'
        verbose_name_plural = 'пользователи Telegram'


class TelegramAccount(models.Model):
    class Role(models.TextChoices):
        ADMINISTRATOR = 'administrator', 'Администратор'
        CUSTOMER = 'customer', 'Покупатель'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='telegram_account',
        verbose_name='Пользователь магазина',
    )
    chat_id = models.BigIntegerField(
        'Telegram chat ID', unique=True,
        help_text=(
            'Числовой ID личного чата с ботом. Не используйте @username: '
            'он может быть изменён или передан другому пользователю.'
        ),
    )
    username = models.CharField(
        'Telegram username', max_length=64, blank=True,
        help_text='Информационное поле без @; не используется для авторизации.',
    )
    role = models.CharField(
        'Роль', max_length=20, choices=Role.choices, default=Role.CUSTOMER,
        db_index=True,
    )
    is_active = models.BooleanField('Доступ к боту активен', default=True, db_index=True)
    created_at = models.DateTimeField('Привязан', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'привязка Telegram'
        verbose_name_plural = 'привязки Telegram'
        ordering = ('user__username',)
        indexes = [models.Index(fields=('role', 'is_active'))]

    def __str__(self):
        return f'{self.user} — {self.get_role_display()} ({self.chat_id})'

    def save(self, *args, **kwargs):
        self.username = self.username.strip().lstrip('@')
        super().save(*args, **kwargs)


class TelegramLinkSession(models.Model):
    """Короткоживущая сессия привязки сайта к фактическому private chat Telegram."""

    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='telegram_link_sessions', verbose_name='Пользователь магазина',
    )
    code_hash = models.CharField('Хеш кода', max_length=255, blank=True)
    chat_id = models.BigIntegerField('Telegram chat ID', blank=True, null=True, db_index=True)
    username = models.CharField('Telegram username', max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField('Неверных попыток', default=0)
    expires_at = models.DateTimeField('Действует до', db_index=True)
    confirmed_at = models.DateTimeField('Подтверждена', blank=True, null=True, db_index=True)
    created_at = models.DateTimeField('Создана', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'сессия привязки Telegram'
        verbose_name_plural = 'сессии привязки Telegram'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.user} · {self.token}'


class TelegramRegistrationSession(models.Model):
    """Временное подтверждение Telegram до создания аккаунта магазина."""

    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code_hash = models.CharField('Хеш кода', max_length=255, blank=True)
    chat_id = models.BigIntegerField('Telegram chat ID', blank=True, null=True, db_index=True)
    username = models.CharField('Telegram username', max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField('Неверных попыток', default=0)
    expires_at = models.DateTimeField('Действует до', db_index=True)
    created_at = models.DateTimeField('Создана', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'временная Telegram-регистрация'
        verbose_name_plural = 'временные Telegram-регистрации'
        ordering = ('-created_at',)
        indexes = [models.Index(fields=('expires_at', 'created_at'))]

    def __str__(self):
        return f'Регистрация Telegram · {self.token}'


class ManagerContact(models.Model):
    class Kind(models.TextChoices):
        PHONE = 'phone', 'Телефон'
        TELEGRAM = 'telegram', 'Telegram'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        EMAIL = 'email', 'E-mail'
        CUSTOM = 'custom', 'Другое'

    kind = models.CharField('Тип', max_length=16, choices=Kind.choices, default=Kind.CUSTOM)
    label = models.CharField('Название', max_length=120)
    value = models.TextField(
        'Данные', help_text='Можно указать номер, username, e-mail или любой текст.',
    )
    url = models.URLField(
        'Ссылка', max_length=1200, blank=True,
        help_text='Необязательно. Для произвольного способа связи используйте HTTP(S)-ссылку.',
    )
    position = models.PositiveSmallIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Показывать клиентам', default=True, db_index=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'способ связи с менеджером'
        verbose_name_plural = 'способы связи с менеджером'
        ordering = ('position', 'id')

    def __str__(self):
        return f'{self.label}: {self.value}'


class TelegramEventDelivery(models.Model):
    """Дедупликация outbox-события для каждого Telegram-администратора."""

    event_id = models.UUIDField('ID события', db_index=True)
    account = models.ForeignKey(
        TelegramAccount, on_delete=models.PROTECT,
        related_name='event_deliveries', verbose_name='Получатель',
    )
    message_id = models.BigIntegerField('ID сообщения Telegram', blank=True, null=True)
    delivered_at = models.DateTimeField('Доставлено', auto_now_add=True)

    class Meta:
        verbose_name = 'доставка события Telegram'
        verbose_name_plural = 'доставки событий Telegram'
        constraints = [
            models.UniqueConstraint(
                fields=('event_id', 'account'),
                name='unique_telegram_event_delivery',
            ),
        ]

    def __str__(self):
        return f'{self.event_id} → {self.account.chat_id}'