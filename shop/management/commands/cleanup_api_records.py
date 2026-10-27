from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shop.models import ApiIdempotencyRecord, ApiRequestLog, ApiRequestNonce


class Command(BaseCommand):
    help = 'Удаляет просроченные nonce/idempotency-записи и старый аудит API.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--audit-days', type=int, default=30,
            help='Срок хранения журнала API в днях (по умолчанию 30).',
        )

    def handle(self, *args, **options):
        audit_days = options['audit_days']
        if not 1 <= audit_days <= 3650:
            raise CommandError('--audit-days должен быть от 1 до 3650.')
        now = timezone.now()
        nonces, _ = ApiRequestNonce.objects.filter(expires_at__lt=now).delete()
        records, _ = ApiIdempotencyRecord.objects.filter(expires_at__lt=now).delete()
        logs, _ = ApiRequestLog.objects.filter(
            created_at__lt=now - timedelta(days=audit_days)
        ).delete()
        self.stdout.write(self.style.SUCCESS(
            f'Удалено: nonce={nonces}, idempotency={records}, audit={logs}.'
        ))