import re

from django.conf import settings
from django.db.models import Sum

from .availability import available_products
from .models import CartItem, Favorite, Product


TELEGRAM_USERNAME_PATTERN = re.compile(r'[A-Za-z0-9_]{5,32}')


def _public_telegram_context():
    channel_username = getattr(settings, 'TELEGRAM_CHANNEL_USERNAME', '').strip().lstrip('@')
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
    username = channel_username or bot_username
    if not TELEGRAM_USERNAME_PATTERN.fullmatch(username):
        return {
            'telegram_public_username': '',
            'telegram_public_url': '',
            'telegram_public_is_channel': False,
        }
    return {
        'telegram_public_username': username,
        'telegram_public_url': f'https://t.me/{username}',
        'telegram_public_is_channel': bool(channel_username),
    }


def _favorite_ids(request):
    favorites = request.session.get('favorites', [])
    favorite_ids = set()
    for product_id in favorites if isinstance(favorites, (list, tuple)) else []:
        try:
            favorite_ids.add(int(product_id))
        except (TypeError, ValueError):
            continue
    if request.user.is_authenticated:
        favorite_ids.update(
            Favorite.objects.filter(
                user=request.user,
                product_id__in=available_products(Product.objects.filter(
                    is_active=True
                )).values('pk'),
            ).values_list('product_id', flat=True)
        )
    return favorite_ids


def cart(request):
    # Панель управления не использует корзину и избранное.
    match = request.resolver_match
    if match and (match.url_name or '').startswith('control_'):
        return {}
    favorite_ids = _favorite_ids(request)
    if request.user.is_authenticated:
        cart_count = (
            CartItem.objects.filter(user=request.user).aggregate(total=Sum('quantity'))['total']
            or 0
        )
    else:
        cart_count = sum(int(quantity) for quantity in request.session.get('cart', {}).values())
    return {
        'cart_count': cart_count,
        'favorite_count': len(favorite_ids),
        'favorite_ids': favorite_ids,
        **_public_telegram_context(),
    }