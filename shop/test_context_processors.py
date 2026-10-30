from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from .context_processors import cart


class CartContextTests(TestCase):
    def request(self, user=None, url_name='home'):
        request = RequestFactory().get('/')
        request.user = user if user is not None else AnonymousUser()
        request.session = {}
        request.resolver_match = SimpleNamespace(url_name=url_name)
        return request

    def test_guest_counts_session_items_without_database_queries(self):
        request = self.request()
        request.session = {'cart': {'1': '2', '2': 3}, 'favorites': ['1', 1, 'invalid', None]}

        with self.assertNumQueries(0):
            context = cart(request)

        self.assertEqual(context['cart_count'], 5)
        self.assertEqual(context['favorite_ids'], {1})
        self.assertEqual(context['favorite_count'], 1)

    def test_authenticated_cart_does_not_read_stale_session_quantities(self):
        user = get_user_model().objects.create_user(username='customer')
        request = self.request(user)
        request.session = {'cart': {'1': 'stale-value'}}

        with patch('shop.context_processors._favorite_ids', return_value={7}), self.assertNumQueries(1):
            context = cart(request)

        self.assertEqual(context['cart_count'], 0)
        self.assertEqual(context['favorite_ids'], {7})

    def test_backoffice_skips_storefront_context(self):
        request = self.request(url_name='control_dashboard')

        with self.assertNumQueries(0):
            self.assertEqual(cart(request), {})

    def test_unnamed_route_can_render_storefront_context(self):
        with self.assertNumQueries(0):
            self.assertEqual(cart(self.request(url_name=None))['cart_count'], 0)