from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0005_productrequestitem_brand_article_and_single_item'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='productrequestitem',
            name='one_item_per_product_request',
        ),
    ]