from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0035_productrequest_delivery_address_and_more'),
        ('telegram_bot', '0004_manager_contacts'),
    ]
    # Manager contacts are configured explicitly in the control panel.
    operations = []
