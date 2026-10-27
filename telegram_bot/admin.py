from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html

from .models import (
    ManagerContact, TelegramAccount, TelegramEventDelivery,
    TelegramRegistrationSession, TelegramUser,
)


@admin.register(TelegramUser)
class TelegramUserAdmin(UserAdmin):
    """Все пользователи магазина, включая ещё не привязанных к Telegram."""

    list_display = (
        'username', 'email', 'full_name', 'telegram_chat_id',
        'telegram_role', 'telegram_access', 'is_staff', 'is_active', 'date_joined',
    )
    list_select_related = ('telegram_account',)
    ordering = ('-date_joined',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='ФИО', ordering='first_name')
    def full_name(self, obj):
        return obj.get_full_name() or '—'

    @admin.display(description='Telegram chat ID', ordering='telegram_account__chat_id')
    def telegram_chat_id(self, obj):
        account = getattr(obj, 'telegram_account', None)
        return account.chat_id if account else 'Не привязан'

    @admin.display(description='Роль Telegram', ordering='telegram_account__role')
    def telegram_role(self, obj):
        account = getattr(obj, 'telegram_account', None)
        return account.get_role_display() if account else '—'

    @admin.display(boolean=True, description='Доступ к боту')
    def telegram_access(self, obj):
        account = getattr(obj, 'telegram_account', None)
        return bool(account and account.is_active)


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'chat_id', 'username', 'role', 'is_active', 'bot_modes', 'updated_at',
    )
    list_editable = ('is_active',)
    list_filter = ('role', 'is_active', 'created_at')
    search_fields = (
        '=chat_id', 'username', 'user__username', 'user__email',
        'user__first_name', 'user__last_name',
    )
    autocomplete_fields = ('user',)
    readonly_fields = ('role', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.role = TelegramAccount.Role.CUSTOMER
        super().save_model(request, obj, form, change)

    @admin.display(description='Открыть бота')
    def bot_modes(self, obj):
        username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
        if not username:
            return 'TELEGRAM_BOT_USERNAME не задан'
        base = f'https://t.me/{username}'
        if obj.role == TelegramAccount.Role.ADMINISTRATOR:
            return format_html(
                '<a href="{}?start=customer" target="_blank">Как пользователь</a> · '
                '<a href="{}?start=admin" target="_blank">Как администратор</a>',
                base, base,
            )
        return format_html(
            '<a href="{}?start=customer" target="_blank">Как пользователь</a>', base,
        )


@admin.register(ManagerContact)
class ManagerContactAdmin(admin.ModelAdmin):
    list_display = ('position', 'kind', 'label', 'value', 'is_active', 'updated_at')
    list_display_links = ('label',)
    list_editable = ('position', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('label', 'value', 'url')


@admin.register(TelegramEventDelivery)
class TelegramEventDeliveryAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'account', 'message_id', 'delivered_at')
    list_filter = ('delivered_at',)
    search_fields = ('=event_id', '=account__chat_id', 'account__user__email')
    readonly_fields = tuple(field.name for field in TelegramEventDelivery._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TelegramRegistrationSession)
class TelegramRegistrationSessionAdmin(admin.ModelAdmin):
    list_display = ('token', 'chat_id', 'username', 'attempts', 'expires_at', 'created_at')
    list_filter = ('created_at', 'expires_at')
    search_fields = ('=token', '=chat_id', 'username')
    exclude = ('code_hash',)
    readonly_fields = tuple(
        field.name for field in TelegramRegistrationSession._meta.fields
        if field.name != 'code_hash'
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False