from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0021_limit_shoe_size_profiles_by_gender'),
    ]

    operations = [
        migrations.AddField(
            model_name='productimage',
            name='detail_image',
            field=models.ImageField(
                blank=True,
                upload_to='products/details/',
                verbose_name='Нормализованное фото карточки',
            ),
        ),
    ]