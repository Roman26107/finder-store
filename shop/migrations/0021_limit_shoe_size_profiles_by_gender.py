from django.db import migrations


LIMITS = {
    'women-shoes': (34, 42),
    'men-shoes': (39, 47),
}


def set_out_of_range_sizes_active(apps, is_active):
    SizeProfile = apps.get_model('shop', 'SizeProfile')
    CatalogSize = apps.get_model('shop', 'CatalogSize')
    for slug, (minimum, maximum) in LIMITS.items():
        profile = SizeProfile.objects.filter(slug=slug).first()
        if profile is None:
            continue
        for size in CatalogSize.objects.filter(profile=profile):
            try:
                value = float(size.label.replace(',', '.'))
            except (TypeError, ValueError):
                continue
            if value < minimum or value > maximum:
                size.is_active = is_active
                size.save(update_fields=('is_active',))


def limit_shoe_size_profiles(apps, schema_editor):
    set_out_of_range_sizes_active(apps, False)


def restore_shoe_size_profiles(apps, schema_editor):
    set_out_of_range_sizes_active(apps, True)


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0020_productimage_preview_image'),
    ]

    operations = [
        migrations.RunPython(limit_shoe_size_profiles, restore_shoe_size_profiles),
    ]