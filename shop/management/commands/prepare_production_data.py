"""Удаляет персональные и служебные данные перед первым production-развёртыванием."""

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from shop.models import (
    AnalyticsEvent, AnalyticsSession, AnalyticsVisitor, ApiIdempotencyRecord,
    ApiRequestLog, ApiRequestNonce, CartItem, CustomerProfile, ErrorLog, Favorite,
    MarketingCampaignSpend, NotificationEvent, Order, Product, ProductRequest,
    SecurityChallenge,
)
from telegram_bot.models import (
    TelegramAccount, TelegramEventDelivery, TelegramLinkSession,
    TelegramRegistrationSession,
)


class Command(BaseCommand):
    help = (
        'Удаляет покупателей, заказы, заявки, аналитику и технические журналы, '
        'сохраняя каталог и superuser-администраторов.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', action='store_true',
            help='Подтвердить необратимую очистку рабочей базы.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать объём очистки без удаления.',
        )
        parser.add_argument(
            '--skip-vacuum', action='store_true',
            help='Не выполнять VACUUM после очистки (используется тестами).',
        )

    @staticmethod
    def _targets():
        user_model = get_user_model()
        return (
            ('Доставки Telegram', TelegramEventDelivery.objects.all()),
            ('События уведомлений', NotificationEvent.objects.all()),
            ('Заказы', Order.objects.all()),
            ('Заявки', ProductRequest.objects.all()),
            ('Корзины', CartItem.objects.all()),
            ('Избранное', Favorite.objects.all()),
            ('Расходы маркетинга', MarketingCampaignSpend.objects.all()),
            ('События аналитики', AnalyticsEvent.objects.all()),
            ('Сессии аналитики', AnalyticsSession.objects.all()),
            ('Посетители аналитики', AnalyticsVisitor.objects.all()),
            ('API nonce', ApiRequestNonce.objects.all()),
            ('API idempotency', ApiIdempotencyRecord.objects.all()),
            ('API журнал', ApiRequestLog.objects.all()),
            ('Проверки безопасности', SecurityChallenge.objects.all()),
            ('Журнал ошибок', ErrorLog.objects.all()),
            ('Telegram link-сессии', TelegramLinkSession.objects.all()),
            ('Telegram registration-сессии', TelegramRegistrationSession.objects.all()),
            ('Telegram-привязки', TelegramAccount.objects.all()),
            ('Профили покупателей', CustomerProfile.objects.all()),
            ('Обычные пользователи', user_model.objects.filter(is_superuser=False)),
            ('Django-сессии', Session.objects.all()),
            ('Журнал действий Django Admin', LogEntry.objects.all()),
        )

    def handle(self, *args, **options):
        targets = self._targets()
        product_count = Product.objects.count()
        superusers = list(
            get_user_model().objects.filter(is_superuser=True).values_list('username', flat=True)
        )
        self.stdout.write(f'Карточек товаров будет сохранено: {product_count}.')
        self.stdout.write(
            'Superuser-администраторы будут сохранены: '
            + (', '.join(superusers) if superusers else 'нет')
        )
        for label, queryset in targets:
            self.stdout.write(f'{label}: {queryset.count()}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry-run: база не изменена.'))
            return
        if not options['yes']:
            raise CommandError(
                'Очистка требует явного подтверждения --yes. Сначала выполните --dry-run '
                'и создайте резервную копию базы.'
            )

        with transaction.atomic():
            deleted = {}
            for label, queryset in targets:
                count = queryset.count()
                if count:
                    queryset.delete()
                deleted[label] = count

        if Product.objects.count() != product_count:
            raise CommandError('Количество товаров изменилось во время очистки.')

        if not options['skip_vacuum']:
            with connection.cursor() as cursor:
                cursor.execute('VACUUM')

        self.stdout.write(self.style.SUCCESS(
            f'Очистка завершена. Сохранено товаров: {product_count}; '
            f'удалено записей верхнего уровня: {sum(deleted.values())}.'
        ))