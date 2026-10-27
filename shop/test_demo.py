from io import StringIO
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Product, ProductImage, SizeVariant, WeeklyLookItem


class DemoCatalogueTests(TestCase):
    def test_demo_is_repeatable_and_does_not_create_accounts(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            output = StringIO()
            call_command('seed_demo', stdout=output)
            self.assertEqual(Product.objects.filter(sku__startswith='demo:').count(), 8)
            self.assertEqual(ProductImage.objects.count(), 24)
            self.assertEqual(WeeklyLookItem.objects.count(), 4)
            self.assertTrue(SizeVariant.objects.filter(in_stock_ecom=False).exists())
            self.assertEqual(get_user_model().objects.count(), 0)
            original_images = list(ProductImage.objects.values_list('id', 'local_image'))
            call_command('seed_demo', stdout=output)
            self.assertEqual(Product.objects.count(), 8)
            self.assertEqual(list(ProductImage.objects.values_list('id', 'local_image')), original_images)
            for photo in ProductImage.objects.all():
                self.assertTrue(photo.local_image.storage.exists(photo.local_image.name))