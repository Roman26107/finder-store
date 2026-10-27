from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from shop.services import import_zip_archive


class Command(BaseCommand):
    help = 'Импортирует товары и изображения из ZIP-архива по файловому пути.'

    def add_arguments(self, parser):
        parser.add_argument('archive', help='Путь к ZIP-архиву.')
        parser.add_argument(
            '--no-images', action='store_true',
            help='Импортировать карточки без распаковки локальных изображений.',
        )
        parser.add_argument(
            '--keep-relations', action='store_true',
            help='Не заменять существующие изображения и варианты товаров.',
        )

    def handle(self, *args, **options):
        archive_path = Path(options['archive']).expanduser().resolve()
        if not archive_path.is_file():
            raise CommandError(f'Архив не найден: {archive_path}')
        if archive_path.suffix.lower() != '.zip':
            raise CommandError('Требуется файл с расширением .zip.')

        last_reported = 0

        def show_progress(report):
            nonlocal last_reported
            if report.cards >= last_reported + 100:
                last_reported = report.cards
                self.stdout.write(
                    f'Обработано карточек: {report.cards}; '
                    f'создано: {report.created}; обновлено: {report.updated}; '
                    f'изображений: {report.images_attached}.'
                )

        self.stdout.write(f'Импорт архива: {archive_path}')
        try:
            with archive_path.open('rb') as archive_file:
                report = import_zip_archive(
                    archive_file,
                    replace_relations=not options['keep_relations'],
                    extract_images=not options['no_images'],
                    progress_callback=show_progress,
                )
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        for warning in report.warnings:
            self.stderr.write(self.style.WARNING(f'Предупреждение: {warning}'))
        for error in report.errors[:100]:
            self.stderr.write(self.style.ERROR(error))
        if len(report.errors) > 100:
            self.stderr.write(self.style.ERROR(
                f'Не показано ошибок: {len(report.errors) - 100}.'
            ))

        summary = (
            f'Импорт завершён: JSON-файлов {report.json_files}, '
            f'карточек {report.cards}, создано {report.created}, '
            f'обновлено {report.updated}, изображений найдено {report.image_files}, '
            f'привязано {report.images_attached}, ошибок {len(report.errors)}.'
        )
        if report.errors:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))