"""Публичная витрина бутика Miu Miu."""

from .. import catalog_storefront


BRAND_SLUG = 'miu-miu'
BRAND_NAME = 'Miu Miu'
CATALOG_URL_NAME = 'shop:miumiu_catalog'
BRAND_LOGO = 'shop/brands/miumiu/miumiu-logo.svg'


def catalog(request):
    return catalog_storefront.catalog(
        request,
        brand_slug=BRAND_SLUG,
        brand_name=BRAND_NAME,
        catalog_template='shop/brands/miumiu/catalog.html',
        catalog_url_name=CATALOG_URL_NAME,
        brand_location='Милан, Италия',
        brand_logo=BRAND_LOGO,
        page_class='miumiu-page',
        grouped_fashion_navigation=False,
        flatten_single_department=True,
    )


def product_detail(request, pk, slug):
    return catalog_storefront.product_detail(
        request, pk, slug,
        brand_slug=BRAND_SLUG,
        brand_name=BRAND_NAME,
        product_template='shop/catalog/product_detail.html',
        catalog_url_name=CATALOG_URL_NAME,
        brand_logo=BRAND_LOGO,
        brand_stylesheet='shop/brands/miumiu/miumiu.css',
        page_class='miumiu-page',
    )