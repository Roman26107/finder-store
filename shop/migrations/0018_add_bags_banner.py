from django.db import migrations, models


def create_bags_banner(apps, schema_editor):
    Banner = apps.get_model('shop', 'Banner')
    Banner.objects.get_or_create(
        slot='bags',
        defaults={
            'image_count': 5,
            'interval_seconds': 5,
            'autoplay': True,
            'personalize_by_gender': False,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0017_banner_gender_personalization'),
    ]

    operations = [
        migrations.AlterField(
            model_name='banner',
            name='slot',
            field=models.SlugField(
                choices=[
                    ('fall-winter', 'Осень–зима'),
                    ('craft', 'Мастерство в деталях'),
                    ('bags', 'Сумки'),
                ],
                max_length=40,
                unique=True,
                verbose_name='Расположение',
            ),
        ),
        migrations.RunPython(create_bags_banner, migrations.RunPython.noop),
    ]