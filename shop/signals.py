"""Очистка заменённых пользовательских изображений после успешной транзакции."""

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    BannerImage, CatalogBrand, CustomerProfile, Order, Product, ProductImage,
    ProductRequest, ProductRequestItem, SizeVariant,
)


OPTIMIZED_SOURCE_FIELDS = {
    ProductImage: ('local_image',),
    CatalogBrand: ('image',),
    BannerImage: ('uploaded_image',),
    ProductRequestItem: ('reference_image',),
}
DELETE_IMAGE_FIELDS = {
    **OPTIMIZED_SOURCE_FIELDS,
    ProductImage: ('local_image', 'preview_image', 'gallery_image', 'detail_image'),
}


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
@receiver(post_save, sender=SizeVariant)
@receiver(post_delete, sender=SizeVariant)
@receiver(post_save, sender=CatalogBrand)
@receiver(post_delete, sender=CatalogBrand)
def clear_storefront_catalog_cache(sender, **kwargs):
    """Немедленно публикует изменения каталога внутри текущего web-процесса."""
    from .catalog_storefront import clear_catalog_metadata_cache
    clear_catalog_metadata_cache()


@receiver(post_save, sender=CustomerProfile)
def sync_preferred_contact_to_customer_orders(sender, instance, **kwargs):
    """Поддерживает способ связи в существующих заказах синхронным с профилем."""
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'preferred_contact' not in update_fields:
        return
    now = timezone.now()
    Order.objects.filter(user_id=instance.user_id).exclude(
        preferred_contact=instance.preferred_contact,
    ).update(preferred_contact=instance.preferred_contact, updated_at=now)
    ProductRequest.objects.filter(user_id=instance.user_id).exclude(
        preferred_contact=instance.preferred_contact,
    ).update(preferred_contact=instance.preferred_contact, updated_at=now)


def _delete_if_unreferenced(model, field_name, storage, name):
    if model is ProductImage:
        referenced = model.objects.filter(
            Q(local_image=name) | Q(preview_image=name)
            | Q(gallery_image=name) | Q(detail_image=name)
        ).exists()
    else:
        referenced = model.objects.filter(**{field_name: name}).exists()
    if name and not referenced:
        storage.delete(name)


@receiver(pre_save, sender=ProductImage)
@receiver(pre_save, sender=CatalogBrand)
@receiver(pre_save, sender=BannerImage)
@receiver(pre_save, sender=ProductRequestItem)
def remember_replaced_images(sender, instance, **kwargs):
    fields = OPTIMIZED_SOURCE_FIELDS.get(sender)
    if not fields or not instance.pk:
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and not set(fields).intersection(update_fields):
        return
    tracked_fields = DELETE_IMAGE_FIELDS[sender] if sender is ProductImage else fields
    previous = sender.objects.filter(pk=instance.pk).values(*tracked_fields).first()
    if not previous:
        return
    replaced = [
        (
            field_name,
            sender._meta.get_field(field_name).storage,
            previous[field_name],
        )
        for field_name in fields
        if previous[field_name]
        and previous[field_name] != getattr(instance, field_name).name
    ]
    source_replaced = sender is ProductImage and any(
        field_name == 'local_image' for field_name, _, _ in replaced
    )
    if source_replaced:
        for field_name in ('preview_image', 'gallery_image', 'detail_image'):
            if previous.get(field_name):
                field = sender._meta.get_field(field_name)
                replaced.append((field_name, field.storage, previous[field_name]))
        instance._source_image_replaced = True
    instance._replaced_optimized_images = tuple(replaced)


@receiver(post_save, sender=ProductImage)
@receiver(post_save, sender=CatalogBrand)
@receiver(post_save, sender=BannerImage)
@receiver(post_save, sender=ProductRequestItem)
def delete_replaced_images(sender, instance, **kwargs):
    if sender is ProductImage and getattr(instance, '_source_image_replaced', False):
        sender.objects.filter(pk=instance.pk).update(
            preview_image='', gallery_image='', detail_image='', detail_processed=False,
        )
        instance.preview_image = ''
        instance.gallery_image = ''
        instance.detail_image = ''
        instance.detail_processed = False
    for field_name, storage, name in getattr(instance, '_replaced_optimized_images', ()):
        transaction.on_commit(
            lambda m=sender, f=field_name, s=storage, n=name:
            _delete_if_unreferenced(m, f, s, n)
        )
    if hasattr(instance, '_replaced_optimized_images'):
        del instance._replaced_optimized_images
    if hasattr(instance, '_source_image_replaced'):
        del instance._source_image_replaced


@receiver(post_delete, sender=ProductImage)
@receiver(post_delete, sender=CatalogBrand)
@receiver(post_delete, sender=BannerImage)
@receiver(post_delete, sender=ProductRequestItem)
def delete_removed_images(sender, instance, **kwargs):
    for field_name in DELETE_IMAGE_FIELDS.get(sender, ()):
        image = getattr(instance, field_name)
        if image and image.name:
            try:
                image.close()
            except (AttributeError, OSError):
                pass
            transaction.on_commit(
                lambda m=sender, f=field_name, s=image.storage, n=image.name:
                _delete_if_unreferenced(m, f, s, n)
            )


@receiver(pre_save, sender=Order)
@receiver(pre_save, sender=ProductRequest)
def remember_previous_workflow_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_workflow_status = None
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return
    instance._previous_workflow_status = sender.objects.filter(
        pk=instance.pk
    ).values_list('status', flat=True).first()


@receiver(post_save, sender=Order)
def enqueue_order_notifications(sender, instance, created, **kwargs):
    from .notifications import enqueue_order_created, enqueue_order_status

    if created:
        # Transactional outbox: событие сохраняется в той же транзакции,
        # что и заказ, и автоматически откатывается вместе с ним.
        enqueue_order_created(instance.pk)
        if hasattr(instance, '_previous_workflow_status'):
            del instance._previous_workflow_status
        return
    previous = getattr(instance, '_previous_workflow_status', None)
    if previous is not None and previous != instance.status:
        enqueue_order_status(instance.pk, previous, instance.status)
    if hasattr(instance, '_previous_workflow_status'):
        del instance._previous_workflow_status


@receiver(post_save, sender=ProductRequest)
def enqueue_request_notifications(sender, instance, created, **kwargs):
    from .notifications import enqueue_request_created, enqueue_request_status

    if created:
        enqueue_request_created(instance.pk)
        if hasattr(instance, '_previous_workflow_status'):
            del instance._previous_workflow_status
        return
    previous = getattr(instance, '_previous_workflow_status', None)
    if previous is not None and previous != instance.status:
        enqueue_request_status(instance.pk, previous, instance.status)
    if hasattr(instance, '_previous_workflow_status'):
        del instance._previous_workflow_status