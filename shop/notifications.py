"""Формирование безопасных payload транзакционных уведомлений."""

from .models import NotificationEvent, Order, ProductRequest


def _money(value):
    return format(value, 'f') if value is not None else None


def enqueue_order_created(order_id):
    order = Order.objects.prefetch_related('items').filter(pk=order_id).first()
    if order is None:
        return
    common = {
        'user_id': order.user_id,
        'order': order,
        'payload': {
            'number': order.number,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'customer_telegram': order.customer_telegram,
            'estimated_total': _money(order.estimated_total),
            'currency': order.currency,
            'status': order.status,
            'status_label': order.get_status_display(),
            'items': [{
                'title': item.product_title,
                'brand': item.brand,
                'sku': item.sku,
                'color': item.color,
                'size': item.size,
                'quantity': item.quantity,
            } for item in order.items.all()],
        },
    }
    NotificationEvent.objects.get_or_create(
        event_key=f'order.created:{order.pk}',
        defaults={
            'event_type': NotificationEvent.EventType.ORDER_CREATED,
            'audience': NotificationEvent.Audience.ADMIN,
            **common,
        },
    )
    if order.user_id:
        NotificationEvent.objects.get_or_create(
            event_key=f'order.created:{order.pk}:customer',
            defaults={
                'event_type': NotificationEvent.EventType.ORDER_CREATED,
                'audience': NotificationEvent.Audience.CUSTOMER,
                **common,
            },
        )


def enqueue_order_status(order_id, old_status, new_status):
    order = Order.objects.filter(pk=order_id).first()
    if order is None or not order.user_id:
        return
    old_labels = dict(Order.Status.choices)
    NotificationEvent.objects.create(
        event_type=NotificationEvent.EventType.ORDER_STATUS_CHANGED,
        audience=NotificationEvent.Audience.CUSTOMER,
        user_id=order.user_id,
        order=order,
        payload={
            'number': order.number,
            'old_status': old_status,
            'old_status_label': old_labels.get(old_status, old_status),
            'status': new_status,
            'status_label': order.get_status_display(),
        },
    )


def enqueue_request_created(request_id):
    product_request = ProductRequest.objects.prefetch_related('items').filter(
        pk=request_id
    ).first()
    if product_request is None:
        return
    common = {
        'user_id': product_request.user_id,
        'product_request': product_request,
        'payload': {
            'number': product_request.number,
            'customer_name': product_request.customer_name,
            'customer_phone': product_request.customer_phone,
            'customer_telegram': product_request.customer_telegram,
            'preferred_contact': product_request.preferred_contact or None,
            'preferred_contact_label': (
                product_request.get_preferred_contact_display()
                if product_request.preferred_contact else None
            ),
            'delivery_address': product_request.delivery_address or None,
            'status': product_request.status,
            'status_label': product_request.get_status_display(),
            'items': [{
                'brand': item.brand,
                'title': item.product_name,
                'article': item.article,
                'color': item.color,
                'size': item.size,
            } for item in product_request.items.all()],
        },
    }
    NotificationEvent.objects.get_or_create(
        event_key=f'request.created:{product_request.pk}',
        defaults={
            'event_type': NotificationEvent.EventType.REQUEST_CREATED,
            'audience': NotificationEvent.Audience.ADMIN,
            **common,
        },
    )
    if product_request.user_id:
        NotificationEvent.objects.get_or_create(
            event_key=f'request.created:{product_request.pk}:customer',
            defaults={
                'event_type': NotificationEvent.EventType.REQUEST_CREATED,
                'audience': NotificationEvent.Audience.CUSTOMER,
                **common,
            },
        )


def enqueue_request_status(request_id, old_status, new_status):
    product_request = ProductRequest.objects.filter(pk=request_id).first()
    if product_request is None or not product_request.user_id:
        return
    old_labels = dict(ProductRequest.Status.choices)
    NotificationEvent.objects.create(
        event_type=NotificationEvent.EventType.REQUEST_STATUS_CHANGED,
        audience=NotificationEvent.Audience.CUSTOMER,
        user_id=product_request.user_id,
        product_request=product_request,
        payload={
            'number': product_request.number,
            'old_status': old_status,
            'old_status_label': old_labels.get(old_status, old_status),
            'status': new_status,
            'status_label': product_request.get_status_display(),
        },
    )