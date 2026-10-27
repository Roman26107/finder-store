from django.db import migrations, models
from django.db.models import Q


def merge_duplicate_user_visitors(apps, schema_editor):
    AnalyticsVisitor = apps.get_model('shop', 'AnalyticsVisitor')
    AnalyticsSession = apps.get_model('shop', 'AnalyticsSession')
    AnalyticsEvent = apps.get_model('shop', 'AnalyticsEvent')
    ProductRequest = apps.get_model('shop', 'ProductRequest')
    Order = apps.get_model('shop', 'Order')

    user_ids = AnalyticsVisitor.objects.exclude(user_id=None).values_list(
        'user_id', flat=True
    ).distinct()
    for user_id in user_ids.iterator():
        visitors = list(AnalyticsVisitor.objects.filter(user_id=user_id).order_by(
            'first_seen_at', 'pk'
        ))
        if len(visitors) < 2:
            continue
        canonical = visitors[0]
        first_seen_at = min(visitor.first_seen_at for visitor in visitors)
        last_seen_at = max(visitor.last_seen_at for visitor in visitors)
        for duplicate in visitors[1:]:
            AnalyticsSession.objects.filter(visitor_id=duplicate.pk).update(
                visitor_id=canonical.pk, user_id=user_id,
            )
            AnalyticsEvent.objects.filter(visitor_id=duplicate.pk).update(
                visitor_id=canonical.pk, user_id=user_id,
            )
            ProductRequest.objects.filter(analytics_visitor_id=duplicate.pk).update(
                analytics_visitor_id=canonical.pk,
            )
            Order.objects.filter(analytics_visitor_id=duplicate.pk).update(
                analytics_visitor_id=canonical.pk,
            )
            duplicate.delete()
        AnalyticsVisitor.objects.filter(pk=canonical.pk).update(
            first_seen_at=first_seen_at, last_seen_at=last_seen_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0029_productimage_gallery_image'),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_user_visitors, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='analyticsvisitor',
            constraint=models.UniqueConstraint(
                condition=Q(user__isnull=False), fields=('user',),
                name='unique_analytics_visitor_per_user',
            ),
        ),
    ]