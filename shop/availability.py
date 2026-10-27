"""Единые правила доступности товаров на публичной витрине."""

from decimal import Decimal, InvalidOperation

from django.db.models import Exists, OuterRef, Q

from .models import Product, SizeVariant


SHOE_SIZE_RANGES = {
    'women': (Decimal('34'), Decimal('42')),
    'men': (Decimal('39'), Decimal('47')),
}


def is_shoe_product(product):
    keys = [product.category_path_key, *(product.category_keys or [])]
    return any(
        key == 'shoes' or '/shoes' in str(key or '')
        for key in keys
    )


def is_size_variant_compatible(product, variant):
    """Не смешивает женский и мужской EU-ряды обуви на витрине."""
    if not is_shoe_product(product):
        return True
    genders = set(product.gender or [])
    gender = next(iter(genders)) if len(genders) == 1 else ''
    limits = SHOE_SIZE_RANGES.get(gender)
    if not limits:
        return True
    try:
        value = Decimal(str(variant.size).strip().replace(',', '.'))
    except (InvalidOperation, ValueError):
        return True
    return limits[0] <= value <= limits[1]


def available_size_variants(product):
    """Возвращает доступные покупателю размеры с учётом пола и типа товара."""
    prefetched = getattr(product, '_prefetched_objects_cache', {}).get('size_variants')
    variants = prefetched if prefetched is not None else product.size_variants.all()
    return [
        variant for variant in variants
        if variant.in_stock_ecom and is_size_variant_compatible(product, variant)
    ]


def available_products(queryset=None):
    """Оставляет модели, доступные напрямую или хотя бы в одном размере."""
    queryset = queryset if queryset is not None else Product.objects.all()
    available_sizes = SizeVariant.objects.filter(
        product_id=OuterRef('pk'), in_stock_ecom=True,
    )
    return queryset.filter(
        Q(in_stock_ecom=True) | Exists(available_sizes)
    )


def is_product_available(product):
    """Проверяет фактическое наличие с учётом размерных вариантов."""
    if product.in_stock_ecom:
        return True
    prefetched = getattr(product, '_prefetched_objects_cache', {}).get('size_variants')
    if prefetched:
        return bool(available_size_variants(product))
    if prefetched is not None:
        return False
    return any(
        is_size_variant_compatible(product, variant)
        for variant in product.size_variants.filter(in_stock_ecom=True)
    )


def order_size_variant(product, requested_sku=''):
    """Возвращает выбранный или наиболее подходящий доступный размер."""
    available = available_size_variants(product)
    if not available:
        return None
    requested_sku = str(requested_sku or '').strip().casefold()
    if requested_sku:
        return next((
            variant for variant in available
            if variant.sku.strip().casefold() == requested_sku
        ), None)
    product_sku = product.sku.split(':', 1)[-1].strip().casefold()
    current_size = (product.current_size or '').strip().casefold()
    current = next((
        variant for variant in available
        if variant.sku.strip().casefold() == product_sku
        or variant.size.strip().casefold() == current_size
    ), None)
    if current:
        return current
    return None if product.in_stock_ecom else available[0]