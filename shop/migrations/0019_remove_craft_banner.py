from django.db import migrations, models


def remove_craft_banner(apps, schema_editor):
    Banner = apps.get_model('shop', 'Banner')
    Banner.objects.filter(slot='craft').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0018_add_bags_banner'),
    ]

    operations = [
        migrations.RunPython(remove_craft_banner, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='banner',
            name='slot',
            field=models.SlugField(
                choices=[
                    ('fall-winter', 'Осень–зима'),
                    ('bags', 'Сумки'),
                ],
                max_length=40,
                unique=True,
                verbose_name='Расположение',
            ),
        ),
    ]