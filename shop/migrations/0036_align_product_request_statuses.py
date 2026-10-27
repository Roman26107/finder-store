from django.db import migrations, models


def align_request_statuses(apps, schema_editor):
    ProductRequest = apps.get_model('shop', 'ProductRequest')
    NotificationEvent = apps.get_model('shop', 'NotificationEvent')
    ProductRequest.objects.filter(status='offer_ready').update(status='confirmation')
    for event in NotificationEvent.objects.filter(
        event_type='request.status_changed',
    ).iterator():
        payload = dict(event.payload or {})
        changed = False
        if payload.get('old_status') == 'offer_ready':
            payload['old_status'] = 'confirmation'
            payload['old_status_label'] = 'Ожидает подтверждения'
            changed = True
        if payload.get('status') == 'offer_ready':
            payload['status'] = 'confirmation'
            payload['status_label'] = 'Ожидает подтверждения'
            changed = True
        if changed:
            event.payload = payload
            event.save(update_fields=('payload',))


def restore_request_statuses(apps, schema_editor):
    ProductRequest = apps.get_model('shop', 'ProductRequest')
    NotificationEvent = apps.get_model('shop', 'NotificationEvent')
    ProductRequest.objects.filter(status='confirmation').update(status='offer_ready')
    ProductRequest.objects.filter(status__in=('purchased', 'shipping')).update(
        status='processing'
    )
    for event in NotificationEvent.objects.filter(
        event_type='request.status_changed',
    ).iterator():
        payload = dict(event.payload or {})
        changed = False
        if payload.get('old_status') == 'confirmation':
            payload['old_status'] = 'offer_ready'
            payload['old_status_label'] = 'Предложение готово'
            changed = True
        elif payload.get('old_status') in {'purchased', 'shipping'}:
            payload['old_status'] = 'processing'
            payload['old_status_label'] = 'В обработке'
            changed = True
        if payload.get('status') == 'confirmation':
            payload['status'] = 'offer_ready'
            payload['status_label'] = 'Предложение готово'
            changed = True
        elif payload.get('status') in {'purchased', 'shipping'}:
            payload['status'] = 'processing'
            payload['status_label'] = 'В обработке'
            changed = True
        if changed:
            event.payload = payload
            event.save(update_fields=('payload',))


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0035_productrequest_delivery_address_and_more'),
    ]

    operations = [
        migrations.RunPython(align_request_statuses, restore_request_statuses),
        migrations.AlterField(
            model_name='productrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'Новый'),
                    ('processing', 'В обработке'),
                    ('confirmation', 'Ожидает подтверждения'),
                    ('purchased', 'Выкуплен'),
                    ('shipping', 'Передан в доставку'),
                    ('completed', 'Завершён'),
                    ('cancelled', 'Отменён'),
                ],
                db_index=True,
                default='new',
                max_length=20,
                verbose_name='Статус',
            ),
        ),
    ]