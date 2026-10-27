import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from shop.pricing import update_cbr_rates


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Обновляет курсы CNY, USD, EUR и RUB из Банка России.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Выполнить одно обновление и завершить процесс.',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=settings.CBR_UPDATE_INTERVAL_SECONDS,
            help='Интервал между обновлениями в секундах (по умолчанию: 300).',
        )

    def handle(self, *args, **options):
        interval = options['interval']
        if interval < 1:
            raise CommandError('Интервал обновления должен быть не меньше 1 секунды.')

        if options['once']:
            if not self._update():
                raise CommandError('Курсы не обновлены. Сохранённые значения оставлены без изменений.')
            return

        self.stdout.write(
            f'Автоматическое обновление курсов запущено. Интервал: {interval} секунд.'
        )
        self.stdout.write('Для остановки нажмите Ctrl+C.')
        try:
            while True:
                self._update()
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('Автоматическое обновление остановлено.'))

    def _update(self):
        try:
            rate_date, rates = update_cbr_rates()
        except Exception:
            logger.exception('Не удалось автоматически обновить курсы Банка России.')
            self.stderr.write(
                self.style.ERROR(
                    'Не удалось обновить курсы Банка России. '
                    'Сохранённые значения оставлены без изменений.'
                )
            )
            return False

        values = ', '.join(f'{currency}={value}' for currency, value in rates.items())
        message = f'Курсы на {rate_date:%d.%m.%Y} обновлены: {values}.'
        logger.info(message)
        self.stdout.write(self.style.SUCCESS(message))
        return True