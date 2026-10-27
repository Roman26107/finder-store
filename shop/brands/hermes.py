"""Конфигурация публичного бутика Hermès."""

from .. import catalog_storefront


BRAND_SLUG = 'hermes'
BRAND_NAME = 'Hermès'
CATALOG_URL_NAME = 'shop:hermes_catalog'
BRAND_LOGO = 'shop/brands/hermes/hermes-logo.svg'


def catalog(request):
    return catalog_storefront.catalog(
        request,
        brand_slug=BRAND_SLUG,
        brand_name=BRAND_NAME,
        catalog_template='shop/brands/hermes/catalog.html',
        catalog_url_name=CATALOG_URL_NAME,
        brand_location='Париж, Франция',
        brand_logo=BRAND_LOGO,
        page_class='hermes-page',
    )


def product_detail(request, pk, slug):
    return catalog_storefront.product_detail(
        request, pk, slug,
        brand_slug=BRAND_SLUG,
        brand_name=BRAND_NAME,
        product_template='shop/catalog/product_detail.html',
        catalog_url_name=CATALOG_URL_NAME,
        brand_logo=BRAND_LOGO,
        brand_stylesheet='shop/brands/hermes/hermes.css',
        page_class='hermes-page',
    )