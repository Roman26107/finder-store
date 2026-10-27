from django.db import migrations, models


def keep_one_item_per_request(apps, schema_editor):
    ProductRequestItem = apps.get_model('shop', 'ProductRequestItem')
    request_ids = ProductRequestItem.objects.values_list('request_id', flat=True).distinct()
    for request_id in request_ids.iterator():
        item_ids = list(
            ProductRequestItem.objects.filter(request_id=request_id)
            .order_by('position', 'id')
            .values_list('id', flat=True)
        )
        if len(item_ids) > 1:
            ProductRequestItem.objects.filter(id__in=item_ids[1:]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0004_productrequest_productrequestitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='productrequestitem',
            name='article',
            field=models.CharField(blank=True, max_length=255, verbose_name='Артикул'),
        ),
        migrations.AddField(
            model_name='productrequestitem',
            name='brand',
            field=models.CharField(default='', max_length=255, verbose_name='Бренд'),
            preserve_default=False,
        ),
        migrations.RunPython(keep_one_item_per_request, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='productrequestitem',
            constraint=models.UniqueConstraint(
                fields=('request',), name='one_item_per_product_request'
            ),
        ),
    ]