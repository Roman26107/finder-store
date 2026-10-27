from django.db import migrations, models


def mark_existing_details_processed(apps, schema_editor):
    ProductImage = apps.get_model('shop', 'ProductImage')
    ProductImage.objects.exclude(detail_image='').update(detail_processed=True)


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0022_productimage_detail_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='detail_processed',
            field=models.BooleanField(
                default=False,
                verbose_name='Фото проверено для карточки',
            ),
        ),
        migrations.RunPython(
            mark_existing_details_processed,
            migrations.RunPython.noop,
        ),
    ]