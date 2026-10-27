from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='brand',
            field=models.SlugField(
                db_index=True,
                default='hermes',
                max_length=80,
                verbose_name='Бренд',
            ),
        ),
    ]