import json
import uuid
from decimal import Decimal
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.mail import BadHeaderError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.query import prefetch_related_objects
from django.db import connection
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .accounts import (
    clear_failed_verification_delivery,
    get_customer_profile,
    reset_email_verification,
    send_email_verification,
    verification_retry_seconds,
    verify_email_code,
)
from .availability import (
    available_products, available_size_variants, is_product_available,
    is_shoe_product, order_size_variant,
)
from .analytics import attribution, track_event
from .forms import (
    CheckoutForm,
    CustomerAddressForm,
    EmailVerificationForm,
    ProductRequestForm,
    ProductRequestItemFormSet,
    ProfileForm,
    RegistrationForm,
    SecurityCodeForm,
    StoreAuthenticationForm,
    TelegramLinkCodeForm,
)
from .models import (
    Banner, BannerImage, CartItem, CatalogBrand, CustomerAddress, CustomerProfile, Favorite, Order, OrderItem, Product, ProductImage,
    ProductRequest, ProductRequestItem,
    SecurityChallenge, WeeklyLook,
)
from .brands import hermes, miumiu
from . import catalog_storefront
from .security import (
    SecurityChallengeDeliveryError, create_security_challenge,
    verify_security_challenge,
)
from telegram_bot.linking import (
    confirm_link_session, create_link_session, create_registration_session,
)
from telegram_bot.models import (
    TelegramAccount, TelegramLinkSession, TelegramRegistrationSession,
)


HOME_DEFERRED_PRODUCT_FIELDS = (
    'source_url', 'description', 'dimensions', 'made_in', 'color_label',
    'product_detail_html', 'care_instruction_html', 'story_behind',
    'categories_breadcrumbs', 'category_path', 'category_keys', 'material',
    'i18n', 'raw_data',
)

ANALYTICS_EVENT_TYPES = {
    'page_view', 'session_engaged', 'scroll_25', 'scroll_50', 'scroll_75',
    'scroll_100', 'catalog_view', 'category_view', 'brand_view',
    'product_click', 'product_view', 'product_photo_view', 'size_select',
    'color_select', 'internal_search', 'filter_use', 'sort_use',
    'favorite_add', 'favorite_remove', 'cart_view', 'cart_add', 'cart_remove',
    'cart_quantity_change', 'checkout_start', 'checkout_contacts',
    'checkout_delivery', 'checkout_payment', 'checkout_success',
    'payment_success', 'payment_error', 'availability_request',
    'personal_order_view', 'personal_order_start', 'personal_order_brand',
    'personal_order_model', 'personal_order_details', 'personal_order_add_item',
    'personal_order_submit', 'personal_order_form_error',
}

PENDING_PRODUCT_REQUEST_SESSION_KEY = 'pending_product_request'
PENDING_CHECKOUT_SESSION_KEY = 'pending_checkout'


def healthcheck(request):
    """Лёгкая проверка web-процесса и соединения с БД без создания аналитики."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return JsonResponse({'status': 'ok'})


@require_POST
def analytics_event(request):
    """Принимает поведенческие события собственного счётчика."""
    if len(request.body) > 65536:
        return HttpResponseBadRequest('Слишком большой пакет')
    try:
        payload = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest('Некорректный JSON')
    events = payload.get('events', [payload]) if isinstance(payload, dict) else []
    if not isinstance(events, list) or len(events) > 20:
        return HttpResponseBadRequest('Некорректный пакет событий')
    saved = 0
    for item in events:
        if not isinstance(item, dict) or item.get('type') not in ANALYTICS_EVENT_TYPES:
            continue
        product = Product.objects.filter(pk=item.get('product_id')).first()
        metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        duration = max(0, min(int(metadata.get('duration_seconds') or 0), 86400))
        event = track_event(
            request, item['type'], product=product,
            path=str(item.get('path') or request.META.get('HTTP_REFERER') or '')[:1000],
            referrer_path=str(item.get('referrer_path') or '')[:1000],
            page_title=str(item.get('page_title') or '')[:500],
            product_sku=str(item.get('product_sku') or '')[:180],
            brand=str(item.get('brand') or '')[:120],
            category=str(item.get('category') or '')[:500],
            metadata=metadata,
        )
        if duration:
            event.session.duration_seconds = max(event.session.duration_seconds, duration)
            event.session.engaged = event.session.engaged or duration >= 10
            event.session.save(update_fields=('duration_seconds', 'engaged', 'last_seen_at'))
        saved += 1
    return JsonResponse({'saved': saved})


def _products(*related):
    return available_products(Product.objects.filter(is_active=True)).prefetch_related(
        *(related or ('images', 'color_variants', 'size_variants'))
    )


def _storefront_gender(request):
    """Возвращает необязательное предпочтение для персонализации витрины."""
    if not request.user.is_authenticated:
        return ''
    try:
        return request.user.customer_profile.gender
    except CustomerProfile.DoesNotExist:
        return ''


def _personalized_products(request, queryset):
    """Общая точка персонализации для главной и её будущих категорий."""
    gender = _storefront_gender(request)
    if gender:
        queryset = queryset.filter(gender__0=gender)
    return queryset, gender


def _attach_catalog_brand_names(products):
    """Добавляет отображаемое имя бренда без запроса на каждую карточку."""
    product_list = list(products)
    names = dict(CatalogBrand.objects.filter(
        slug__in={product.brand for product in product_list}
    ).values_list('slug', 'name'))
    for product in product_list:
        product.catalog_brand_name = names.get(product.brand, product.brand)
    return product_list


def home(request):
    ordering = ('-fetched_at', '-created_at')
    family_seen = set()
    newest = []

    def append_newest(queryset):
        for product in queryset.order_by(*ordering).iterator(chunk_size=100):
            family_key = product.family_key or product.sku
            if family_key in family_seen:
                continue
            family_seen.add(family_key)
            newest.append(product)
            if len(newest) == 4:
                return

    # На главной сначала показываем актуальную осенне-зимнюю коллекцию.
    # Остальной каталог используется как fallback, если сезон ещё не наполнен.
    active_products, preferred_gender = _personalized_products(
        request,
        available_products(Product.objects.filter(
            is_active=True, catalog_scope=Product.CatalogScope.STORE,
        )).defer(
            *HOME_DEFERRED_PRODUCT_FIELDS
        ),
    )
    append_newest(active_products.filter(collection='fall-winter'))
    if len(newest) < 4:
        append_newest(active_products.exclude(collection='fall-winter'))
    prefetch_related_objects(newest, 'images')
    _attach_catalog_brand_names(newest)
    # Selection занимает ту же четырёхколоночную сетку, что и блок новинок.
    # Сначала берём отмеченные позиции, затем свежие модели вне верхнего блока;
    # если каталог мал, дополняем сетку уже показанными новинками.
    selection_products = []
    selection_families = set(family_seen)

    def append_selection(queryset):
        for product in queryset.iterator(chunk_size=100):
            family_key = product.family_key or product.sku
            if family_key in selection_families:
                continue
            selection_families.add(family_key)
            selection_products.append(product)
            if len(selection_products) == 4:
                return

    selection_candidates = active_products.filter(images__isnull=False).distinct()
    append_selection(selection_candidates.order_by('-is_featured', *ordering))
    if len(selection_products) < 4:
        selected_ids = {product.pk for product in selection_products}
        selection_products.extend(
            product for product in newest
            if product.pk not in selected_ids
        )
        selection_products = selection_products[:4]
    prefetch_related_objects(selection_products, 'images')
    _attach_catalog_brand_names(selection_products)
    weekly_look = WeeklyLook.objects.filter(pk=1).prefetch_related(
        'items__catalog_image__product__images',
    ).first()
    weekly_look_items = []
    if weekly_look and weekly_look.is_visible:
        available_look_product_ids = set(available_products(Product.objects.filter(
            pk__in=[item.catalog_image.product_id for item in weekly_look.items.all()],
            is_active=True, catalog_scope=Product.CatalogScope.STORE,
        )).values_list('pk', flat=True))
        weekly_look_items = [
            item for item in weekly_look.items.all()
            if item.catalog_image.product_id in available_look_product_ids and item.image_url
        ][:4]
        _attach_catalog_brand_names([item.catalog_image.product for item in weekly_look_items])

    promo_products = available_products(Product.objects.filter(
        is_active=True, images__isnull=False,
        catalog_scope=Product.CatalogScope.STORE,
    )).distinct()
    # Для гендерных имиджевых баннеров выбираем только одежду.
    promo_ordering = ('-fetched_at', '-created_at', '-id')
    women_promo_id = men_promo_id = accessory_promo_id = None
    promo_candidates = promo_products.filter(
        Q(gender__0='women', category_path_key__startswith='women/clothing')
        | Q(gender__0='men', category_path_key__startswith='men/clothing')
        | Q(category_path_key__startswith='women/accessories')
        | Q(category_path_key__startswith='men/accessories')
    ).order_by(*promo_ordering).values_list('pk', 'gender', 'category_path_key')
    for product_id, product_genders, category_path_key in promo_candidates.iterator(
        chunk_size=100
    ):
        gender = (product_genders or [None])[0]
        if women_promo_id is None and (
            gender == 'women' and category_path_key.startswith('women/clothing')
        ):
            women_promo_id = product_id
        if men_promo_id is None and (
            gender == 'men' and category_path_key.startswith('men/clothing')
        ):
            men_promo_id = product_id
        if accessory_promo_id is None and (
            category_path_key.startswith('women/accessories')
            or category_path_key.startswith('men/accessories')
        ):
            accessory_promo_id = product_id
        if all((women_promo_id, men_promo_id, accessory_promo_id)):
            break
    promo_ids = [
        product_id for product_id in (
            women_promo_id, men_promo_id, accessory_promo_id,
        ) if product_id
    ]
    promo_by_id = {
        product.pk: product
        for product in Product.objects.filter(pk__in=promo_ids).defer(
            *HOME_DEFERRED_PRODUCT_FIELDS
        ).prefetch_related('images')
    }
    women_promo = promo_by_id.get(women_promo_id)
    men_promo = promo_by_id.get(men_promo_id)
    accessory_promo = promo_by_id.get(accessory_promo_id)

    fall_banner, _ = Banner.objects.get_or_create(
        slot=Banner.Slot.FALL_WINTER,
        defaults={
            'image_count': 5, 'interval_seconds': 5, 'autoplay': True,
            'personalize_by_gender': True,
        },
    )
    bags_banner, _ = Banner.objects.get_or_create(
        slot=Banner.Slot.BAGS,
        defaults={
            'image_count': 5, 'interval_seconds': 5, 'autoplay': True,
            'personalize_by_gender': False,
        },
    )
    configured_hero_images = list(
        fall_banner.images.select_related('catalog_image__product').filter(
            Q(catalog_image__isnull=True)
            | Q(catalog_image__product__catalog_scope=Product.CatalogScope.STORE)
        ).order_by('position', 'id')
    )
    if fall_banner.personalize_by_gender and preferred_gender:
        configured_hero_images = [
            image for image in configured_hero_images
            if image.audience in (BannerImage.Audience.ALL, preferred_gender)
        ]
    hero_slides = [
        image for image in configured_hero_images if image.url
    ][:fall_banner.image_count]
    hero_urls = set()
    hero_families = set()

    def append_hero_images(queryset):
        """Одним SQL-проходом берёт разные модели, затем запасные кадры."""
        images = queryset.select_related('product').order_by(
            '-product__fetched_at', '-product__created_at', 'position', 'id'
        )
        fallback = []
        fallback_urls = set()
        for image in images.iterator(chunk_size=100):
            image_url = image.url
            family_key = image.product.family_key or image.product.sku
            if not image_url or image_url in hero_urls or image_url in fallback_urls:
                continue
            if family_key in hero_families:
                if len(fallback) < fall_banner.image_count:
                    fallback.append(image)
                    fallback_urls.add(image_url)
                continue
            hero_urls.add(image_url)
            hero_families.add(family_key)
            hero_slides.append(image)
            if len(hero_slides) >= fall_banner.image_count:
                return
        for image in fallback:
            if image.url in hero_urls:
                continue
            hero_urls.add(image.url)
            hero_slides.append(image)
            if len(hero_slides) >= fall_banner.image_count:
                return

    active_images = ProductImage.objects.filter(product__in=active_products)
    winter_images = active_images.filter(product__collection='fall-winter')
    other_images = active_images.exclude(product__collection='fall-winter')
    if not hero_slides:
        # Пока баннер не настроен вручную, формируем его из каталога.
        append_hero_images(winter_images)
        if len(hero_slides) < fall_banner.image_count:
            append_hero_images(other_images)

    configured_bag_images = bags_banner.images.select_related(
        'catalog_image__product'
    ).filter(
        Q(catalog_image__isnull=True)
        | Q(catalog_image__product__catalog_scope=Product.CatalogScope.STORE)
    ).order_by('position', 'id')
    if bags_banner.personalize_by_gender and preferred_gender:
        configured_bag_images = configured_bag_images.filter(
            audience__in=(BannerImage.Audience.ALL, preferred_gender)
        )
    bag_slides = [
        image for image in configured_bag_images if image.url
    ][:bags_banner.image_count]
    bag_urls = {image.url for image in bag_slides}
    bag_families = {
        image.product.family_key or image.product.sku
        for image in bag_slides if image.product
    }
    bag_products = promo_products.filter(
        Q(category_path_key__startswith='women/bags')
        | Q(category_path_key__startswith='men/bags')
        | Q(category_path_key__startswith='leather_goods/bags')
        | Q(category_name__iexact='Сумки')
    )
    if preferred_gender:
        bag_products = bag_products.filter(gender__0=preferred_gender)
    bag_images = ProductImage.objects.filter(product__in=bag_products).select_related(
        'product'
    ).defer(
        *(f'product__{field}' for field in HOME_DEFERRED_PRODUCT_FIELDS)
    ).order_by('-product__fetched_at', '-product__created_at', 'position', 'id')
    for image in bag_images.iterator(chunk_size=100):
        family_key = image.product.family_key or image.product.sku
        if not image.url or image.url in bag_urls or family_key in bag_families:
            continue
        bag_urls.add(image.url)
        bag_families.add(family_key)
        bag_slides.append(image)
        if len(bag_slides) >= bags_banner.image_count:
            break

    hero = hero_slides[0].product if hero_slides else (newest[0] if newest else None)
    return render(request, 'shop/home.html', {
        'products': newest,
        'selection_products': selection_products,
        'weekly_look': weekly_look,
        'weekly_look_items': weekly_look_items,
        'weekly_look_entries': weekly_look_items if weekly_look_items else selection_products,
        'bag_slides': bag_slides,
        'women_promo': women_promo,
        'men_promo': men_promo,
        'accessory_promo': accessory_promo,
        'hero': hero,
        'hero_slides': hero_slides,
        'fall_banner': fall_banner,
        'bags_banner': bags_banner,
        'preferred_gender': preferred_gender,
    })


def stores(request):
    brands = list(CatalogBrand.objects.filter(is_active=True))
    known_urls = {
        hermes.BRAND_SLUG: reverse('shop:hermes_catalog'),
        miumiu.BRAND_SLUG: reverse('shop:miumiu_catalog'),
    }
    for brand in brands:
        brand.catalog_url = known_urls.get(
            brand.slug, reverse('shop:brand_catalog', kwargs={'brand_slug': brand.slug})
        )
    return render(request, 'shop/stores.html', {
        'brands': brands,
    })


def _login_for_pending_action(request, next_url, message):
    messages.info(request, message)
    login_url = reverse('shop:login')
    return redirect(f'{login_url}?{urlencode({"next": next_url})}')


def _profile_completion_url(next_url):
    return f'{reverse("shop:account")}?{urlencode({"tab": "profile", "next": next_url})}'


def _create_product_request(request, profile, items):
    with transaction.atomic():
        lead_data = attribution(request)
        product_request = ProductRequest.objects.create(
            user=request.user,
            customer_name=(
                request.user.get_full_name().strip() or request.user.username
            ),
            customer_email=request.user.email,
            customer_phone=profile.phone,
            customer_telegram=profile.telegram,
            preferred_contact=profile.preferred_contact,
            delivery_address=profile.delivery_address,
            is_repeat_customer=(
                request.user.orders.exists()
                or request.user.product_requests.exists()
            ),
            **lead_data,
        )
        ProductRequestItem.objects.bulk_create([
            ProductRequestItem(
                request=product_request,
                brand=item['brand'].strip(),
                product_name=item['product_name'].strip(),
                article=item.get('article', '').strip(),
                color=item.get('color', '').strip(),
                size=item.get('size', '').strip(),
                position=index,
            )
            for index, item in enumerate(items)
        ])
    first_item = product_request.items.first()
    track_event(request, 'personal_order_submit', metadata={
        'lead_id': product_request.number,
        'items_count': product_request.items.count(),
        'brand': first_item.brand if first_item else '',
        'product': first_item.product_name if first_item else '',
    })
    messages.success(
        request,
        f'Заказ {product_request.number} оформлен. Мы свяжемся с вами.',
    )
    return product_request


def _create_public_product_request(request, form):
    contact_method = form.cleaned_data['contact_method']
    normalized_contact = form.cleaned_data['normalized_contact']
    profile = get_customer_profile(request.user) if request.user.is_authenticated else None
    customer_email = request.user.email if request.user.is_authenticated else ''
    customer_phone = profile.phone if profile is not None and profile.phone else ''
    customer_telegram = profile.telegram if profile is not None else ''
    if contact_method == CustomerProfile.ContactMethod.EMAIL:
        customer_email = normalized_contact
    elif contact_method == CustomerProfile.ContactMethod.TELEGRAM:
        customer_telegram = normalized_contact
    else:
        customer_phone = normalized_contact

    query = form.cleaned_data['query']
    brand = query.split(',', 1)[0].strip()[:255]
    with transaction.atomic():
        product_request = ProductRequest.objects.create(
            user=request.user if request.user.is_authenticated else None,
            customer_name=form.cleaned_data['customer_name'],
            customer_email=customer_email,
            customer_phone=customer_phone,
            customer_telegram=customer_telegram,
            preferred_contact=contact_method,
            delivery_address=profile.delivery_address if profile is not None else '',
            is_repeat_customer=(
                request.user.is_authenticated
                and (
                    request.user.orders.exists()
                    or request.user.product_requests.exists()
                )
            ),
            **attribution(request),
        )
        ProductRequestItem.objects.create(
            request=product_request,
            brand=brand,
            product_name=query,
            reference_image=form.cleaned_data.get('reference_image'),
        )
    track_event(request, 'personal_order_submit', metadata={
        'lead_id': product_request.number,
        'items_count': 1,
        'brand': brand,
        'product': query,
    })
    messages.success(
        request,
        f'Запрос {product_request.number} отправлен. Мы свяжемся с вами.',
    )
    return product_request


def product_request_create(request):
    if request.method == 'GET':
        track_event(request, 'personal_order_view')

    resume_url = f'{reverse("shop:product_request_create")}?resume=1'
    pending_items = request.session.get(PENDING_PRODUCT_REQUEST_SESSION_KEY)
    profile = get_customer_profile(request.user) if request.user.is_authenticated else None

    if request.user.is_authenticated and request.GET.get('resume') == '1' and pending_items:
        if not profile.phone:
            messages.warning(
                request,
                'Добавьте номер телефона, и сохранённый заказ будет оформлен автоматически.',
            )
            return redirect(_profile_completion_url(resume_url))
        product_request = _create_product_request(request, profile, pending_items)
        request.session.pop(PENDING_PRODUCT_REQUEST_SESSION_KEY, None)
        request.session.modified = True
        return redirect(product_request)

    # Сохраняем поддержку старой многострочной формы для незавершённых сессий и
    # внешних клиентов, но новый интерфейс использует компактную форму с фото.
    legacy_post = request.method == 'POST' and 'items-TOTAL_FORMS' in request.POST
    formset = ProductRequestItemFormSet(
        request.POST if legacy_post else None,
        request.FILES if legacy_post else None,
        prefix='items',
    )
    if legacy_post and formset.is_valid():
        items = [
            {
                'brand': form.cleaned_data['brand'],
                'product_name': form.cleaned_data['product_name'],
                'article': form.cleaned_data.get('article', ''),
                'color': form.cleaned_data.get('color', ''),
                'size': form.cleaned_data.get('size', ''),
            }
            for form in formset
            if form.cleaned_data and not form.cleaned_data.get('DELETE')
        ]
        if not request.user.is_authenticated:
            request.session[PENDING_PRODUCT_REQUEST_SESSION_KEY] = items
            request.session.modified = True
            return _login_for_pending_action(
                request,
                resume_url,
                'Заявка сохранена. Войдите или зарегистрируйтесь — после этого заказ оформится автоматически.',
            )
        product_request = _create_product_request(request, profile, items)
        return redirect(product_request)
    initial = {}
    if request.user.is_authenticated:
        initial['customer_name'] = (
            request.user.get_full_name().strip() or request.user.username
        )
        if profile.telegram:
            initial['contact'] = profile.telegram
        elif profile.phone:
            initial['contact'] = profile.phone
        else:
            initial['contact'] = request.user.email
    form = ProductRequestForm(
        request.POST if request.method == 'POST' and not legacy_post else None,
        request.FILES if request.method == 'POST' and not legacy_post else None,
        initial=initial,
    )
    if request.method == 'POST' and not legacy_post and form.is_valid():
        product_request = _create_public_product_request(request, form)
        if request.user.is_authenticated:
            return redirect(product_request)
        return redirect('shop:home')
    if request.method == 'POST' and (not legacy_post or not formset.is_valid()):
        track_event(request, 'personal_order_form_error', metadata={
            'errors': form.errors if not legacy_post else formset.errors,
        })
    return render(request, 'shop/product_request/form.html', {
        'form': form,
        'formset': formset,
        'profile': profile,
    })


@login_required
def order_list(request):
    return render(
        request, 'shop/account/dashboard.html',
        _account_context(request, active_tab='orders'),
    )


@login_required
def product_request_list(request):
    return order_list(request)


@login_required
def product_request_detail(request, pk):
    product_request = get_object_or_404(
        ProductRequest.objects.prefetch_related('items'),
        pk=pk,
        user=request.user,
    )
    return render(request, 'shop/product_request/detail.html', {
        'product_request': product_request,
    })


def _safe_next_url(request, fallback='shop:account'):
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse(fallback)


def admin_login_redirect(request):
    """Единая авторизация сайта вместо отдельной формы входа Django Admin."""
    admin_url = reverse('admin:index')
    next_url = request.GET.get('next', admin_url)
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = admin_url
    if request.user.is_active and request.user.is_staff:
        return redirect(next_url)
    if request.user.is_authenticated:
        messages.error(request, 'У вашего аккаунта нет прав для доступа к панели управления.')
        return redirect('shop:account')
    return redirect(f'{reverse("shop:login")}?next={next_url}')


def account_login(request):
    if request.user.is_authenticated:
        return redirect(_safe_next_url(request))
    form = StoreAuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        next_url = _safe_next_url(request)
        if user.is_staff:
            try:
                challenge = create_security_challenge(
                    target_user=user,
                    purpose=SecurityChallenge.Purpose.WEB_LOGIN,
                )
            except (SecurityChallengeDeliveryError, ValueError):
                form.add_error(None, 'Не удалось отправить код безопасности. Попробуйте позднее.')
            else:
                request.session['pending_admin_login'] = {
                    'user_id': user.pk,
                    'challenge_id': str(challenge.pk),
                    'next': next_url,
                }
                return redirect('shop:admin_access_verify')
        else:
            login(request, user)
            # _favorite_keys переносит гостевые сердечки в постоянное избранное.
            _favorite_keys(request)
            _sync_cart(request, merge_session=True)
            messages.success(request, 'Вы вошли в личный кабинет.')
            return redirect(next_url)
    return render(request, 'shop/account/login.html', {
        'form': form,
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


def account_register(request):
    if request.user.is_authenticated:
        return redirect(_safe_next_url(request))
    registration_session = None
    if request.method == 'GET' and settings.TELEGRAM_BOT_USERNAME:
        registration_session = create_registration_session()
    form = RegistrationForm(
        request.POST or None,
        initial={
            'telegram_registration_token': (
                registration_session.token if registration_session else None
            ),
        },
    )
    if request.method == 'POST' and form.is_valid():
        telegram_session = form.telegram_registration_session
        try:
            with transaction.atomic():
                if telegram_session:
                    current_session = TelegramRegistrationSession.objects.select_for_update().filter(
                        token=telegram_session.token,
                        expires_at__gte=timezone.now(),
                        code_hash=telegram_session.code_hash,
                        attempts__lt=5,
                    ).first()
                    if current_session is None:
                        raise IntegrityError('Telegram registration session was consumed.')
                    telegram_session = current_session
                user = form.save()
                profile = CustomerProfile.objects.create(
                    user=user,
                    phone=form.cleaned_data['phone'],
                    telegram=(
                        f'@{telegram_session.username}'
                        if telegram_session and telegram_session.username else ''
                    ),
                    gender=form.cleaned_data['gender'],
                )
                if telegram_session:
                    TelegramAccount.objects.create(
                        user=user,
                        chat_id=telegram_session.chat_id,
                        username=telegram_session.username,
                    )
                    telegram_session.delete()
                else:
                    TelegramRegistrationSession.objects.filter(
                        token=form.cleaned_data.get('telegram_registration_token'),
                    ).delete()
        except IntegrityError:
            form.add_error(
                'telegram_code',
                'Этот Telegram уже привязан к другому аккаунту. '
                'Обновите страницу и получите новый код.',
            )
            return render(request, 'shop/account/register.html', {
                'form': form,
                'next': request.POST.get('next', ''),
                'bot_username': settings.TELEGRAM_BOT_USERNAME,
                'telegram_bot_url': _registration_bot_url(request.POST.get(
                    'telegram_registration_token', ''
                )),
            })
        login(request, user)
        _sync_cart(request, merge_session=True)
        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            request.session['after_email_verification_url'] = next_url
        try:
            send_email_verification(profile)
            messages.success(request, 'Регистрация завершена. Код отправлен на ваш e-mail.')
        except (OSError, BadHeaderError):
            clear_failed_verification_delivery(profile)
            messages.warning(
                request,
                'Аккаунт создан, но письмо сейчас не удалось отправить. Запросите код повторно.',
            )
        return redirect('shop:verify_email')
    token = (
        registration_session.token if registration_session
        else request.POST.get('telegram_registration_token', '')
    )
    return render(request, 'shop/account/register.html', {
        'form': form,
        'next': request.POST.get('next') or request.GET.get('next', ''),
        'bot_username': settings.TELEGRAM_BOT_USERNAME,
        'telegram_bot_url': _registration_bot_url(token),
    })


def _registration_bot_url(token):
    if not settings.TELEGRAM_BOT_USERNAME or not token:
        return ''
    try:
        token = uuid.UUID(str(token))
    except (TypeError, ValueError, AttributeError):
        return ''
    return f'https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=register_{token.hex}'


@login_required
def account(request):
    profile = get_customer_profile(request.user)
    form = ProfileForm(request.POST or None, instance=request.user)
    next_url = _safe_next_url(request)
    if request.method == 'POST' and form.is_valid():
        email_changed = form.cleaned_data['email'] != form.original_email
        form.save()
        profile.phone = form.cleaned_data['phone']
        profile.gender = form.cleaned_data['gender']
        profile.delivery_address = form.cleaned_data['delivery_address'].strip()
        profile.preferred_contact = form.cleaned_data['preferred_contact']
        profile.save(update_fields=(
            'phone', 'gender', 'delivery_address', 'preferred_contact',
        ))
        if email_changed:
            reset_email_verification(profile)
            try:
                send_email_verification(profile)
                messages.warning(request, 'E-mail изменён. На новый адрес отправлен код подтверждения.')
            except (OSError, BadHeaderError):
                clear_failed_verification_delivery(profile)
                messages.warning(request, 'E-mail изменён, но письмо не отправлено. Запросите код повторно.')
            request.session['after_email_verification_url'] = next_url
            return redirect('shop:verify_email')
        messages.success(request, 'Личные данные сохранены.')
        return redirect(next_url)
    active_tab = 'profile' if request.method == 'POST' else request.GET.get('tab', 'orders')
    if active_tab not in {'orders', 'favorites', 'addresses', 'profile'}:
        active_tab = 'profile'
    return render(
        request, 'shop/account/dashboard.html',
        _account_context(
            request, active_tab=active_tab, profile=profile, profile_form=form,
        ),
    )


def _account_context(request, *, active_tab='profile', profile=None, profile_form=None):
    profile = profile or get_customer_profile(request.user)
    orders = request.user.orders.prefetch_related('items__product__images')
    personal_orders = request.user.product_requests.prefetch_related('items')
    favorite_products = list(_products('images').filter(
        favorited_by__user=request.user,
    ).order_by('-favorited_by__created_at'))
    _attach_catalog_brand_names(favorite_products)
    addresses = request.user.delivery_addresses.all()
    return {
        'active_tab': active_tab,
        'form': profile_form or ProfileForm(instance=request.user),
        'profile': profile,
        'next': request.POST.get('next') or request.GET.get('next', ''),
        'telegram_account': TelegramAccount.objects.filter(user=request.user).first(),
        'bot_username': settings.TELEGRAM_BOT_USERNAME,
        'orders': orders,
        'personal_orders': personal_orders,
        'favorite_products': favorite_products,
        'addresses': addresses,
        'address_form': CustomerAddressForm(),
        'order_count': orders.count() + personal_orders.count(),
        'account_favorite_count': len(favorite_products),
        'address_count': addresses.count(),
    }


def _sync_profile_default_address(user):
    profile = get_customer_profile(user)
    address = user.delivery_addresses.filter(is_default=True).first()
    profile.delivery_address = address.full_address if address else ''
    profile.save(update_fields=('delivery_address',))


@login_required
@require_POST
def account_address_add(request):
    form = CustomerAddressForm(request.POST)
    if form.is_valid():
        address = form.save(commit=False)
        address.user = request.user
        address.save()
        _sync_profile_default_address(request.user)
        messages.success(request, 'Адрес сохранён.')
    else:
        messages.error(request, 'Проверьте данные адреса.')
    return redirect(f'{reverse("shop:account")}?tab=addresses')


@login_required
@require_POST
def account_address_edit(request, pk):
    address = get_object_or_404(CustomerAddress, pk=pk, user=request.user)
    form = CustomerAddressForm(request.POST, instance=address)
    if form.is_valid():
        form.save()
        _sync_profile_default_address(request.user)
        messages.success(request, 'Адрес обновлён.')
    else:
        messages.error(request, 'Проверьте данные адреса.')
    return redirect(f'{reverse("shop:account")}?tab=addresses')


@login_required
@require_POST
def account_address_default(request, pk):
    address = get_object_or_404(CustomerAddress, pk=pk, user=request.user)
    address.is_default = True
    address.save(update_fields=('is_default', 'updated_at'))
    _sync_profile_default_address(request.user)
    messages.success(request, 'Основной адрес изменён.')
    return redirect(f'{reverse("shop:account")}?tab=addresses')


@login_required
@require_POST
def account_address_delete(request, pk):
    address = get_object_or_404(CustomerAddress, pk=pk, user=request.user)
    address.delete()
    _sync_profile_default_address(request.user)
    messages.success(request, 'Адрес удалён.')
    return redirect(f'{reverse("shop:account")}?tab=addresses')


def _masked_email(email):
    local, separator, domain = str(email or '').partition('@')
    if not separator:
        return 'указанный в аккаунте e-mail'
    visible = local[:2] if len(local) > 2 else local[:1]
    return f'{visible}{"•" * max(3, len(local) - len(visible))}@{domain}'


def admin_access_verify(request):
    pending = request.session.get('pending_admin_login') or {}
    user = get_user_model().objects.filter(
        pk=pending.get('user_id'), is_active=True, is_staff=True,
    ).first()
    if user is None or not pending.get('challenge_id'):
        messages.warning(request, 'Сессия входа истекла. Введите логин и пароль ещё раз.')
        return redirect('shop:login')
    form = SecurityCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        challenge, message = verify_security_challenge(
            pending['challenge_id'], form.cleaned_data['code'],
            purpose=SecurityChallenge.Purpose.WEB_LOGIN,
            target_user_id=user.pk,
        )
        if challenge:
            login(request, user)
            request.session['admin_2fa_verified_at'] = timezone.now().isoformat()
            request.session.pop('pending_admin_login', None)
            _favorite_keys(request)
            _sync_cart(request, merge_session=True)
            messages.success(request, 'Вход подтверждён. Добро пожаловать в панель управления.')
            return redirect(pending.get('next') or reverse('shop:control_dashboard'))
        form.add_error('code', message)
    return render(request, 'shop/account/admin_access_verify.html', {
        'form': form,
        'masked_email': _masked_email(user.email),
    })


@login_required
@require_POST
def telegram_link_start(request):
    if not settings.TELEGRAM_BOT_USERNAME:
        messages.error(request, 'Привязка временно недоступна: бот не настроен.')
        return redirect('shop:account')
    link_session = create_link_session(request.user)
    request.session['telegram_link_token'] = str(link_session.token)
    return redirect('shop:telegram_link')


@login_required
def telegram_link(request):
    token = request.GET.get('token', '').strip() or request.session.get('telegram_link_token', '')
    link_session = TelegramLinkSession.objects.filter(token=token, user=request.user).first()
    if link_session is None or link_session.confirmed_at or timezone.now() > link_session.expires_at:
        messages.warning(request, 'Ссылка привязки истекла. Создайте новую в личном кабинете.')
        return redirect('shop:account')
    request.session['telegram_link_token'] = str(link_session.token)
    form = TelegramLinkCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        account, message = confirm_link_session(
            request.user, link_session.token, form.cleaned_data['code'],
        )
        if account:
            request.session.pop('telegram_link_token', None)
            messages.success(request, message)
            return redirect('shop:account')
        form.add_error('code', message)
    return render(request, 'shop/account/telegram_link.html', {
        'form': form,
        'bot_url': f'https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=link_{link_session.token.hex}',
        'expires_at': link_session.expires_at,
    })


@login_required
def verify_email(request):
    profile = get_customer_profile(request.user)
    if profile.email_verified:
        messages.info(request, 'Ваш e-mail уже подтверждён.')
        return redirect(
            request.session.pop('after_email_verification_url', reverse('shop:account'))
        )
    form = EmailVerificationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        verified, message = verify_email_code(profile, form.cleaned_data['code'])
        if verified:
            messages.success(request, message)
            return redirect(
                request.session.pop('after_email_verification_url', reverse('shop:account'))
            )
        form.add_error('code', message)
    return render(request, 'shop/account/verify_email.html', {
        'form': form,
        'profile': profile,
        'retry_seconds': verification_retry_seconds(profile),
    })


@login_required
@require_POST
def resend_email_verification(request):
    profile = get_customer_profile(request.user)
    if profile.email_verified:
        messages.info(request, 'Ваш e-mail уже подтверждён.')
        return redirect('shop:account')
    retry_seconds = verification_retry_seconds(profile)
    if retry_seconds:
        messages.warning(request, f'Новый код можно запросить через {retry_seconds} сек.')
        return redirect('shop:verify_email')
    try:
        send_email_verification(profile)
        messages.success(request, 'Новый код отправлен на ваш e-mail.')
    except (OSError, BadHeaderError):
        clear_failed_verification_delivery(profile)
        messages.error(request, 'Не удалось отправить письмо. Попробуйте позднее.')
    return redirect('shop:verify_email')


@login_required
def account_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Пароль успешно изменён.')
        return redirect('shop:account')
    return render(request, 'shop/account/password_change.html', {'form': form})


@require_POST
def account_logout(request):
    logout(request)
    messages.success(request, 'Вы вышли из личного кабинета.')
    return redirect('shop:home')


def catalog(request):
    """Единый каталог товаров всех активных брендов."""
    return catalog_storefront.catalog(request)


def collection(request):
    """Отдельный ассортимент товаров, уже находящихся в России."""
    return catalog_storefront.catalog(
        request,
        catalog_url_name='shop:collection',
        catalog_scope=Product.CatalogScope.COLLECTION,
        catalog_heading_override='FINDER COLLECTION',
        catalog_eyebrow='Найдено. Отобрано. Уже здесь.',
        catalog_description=(
            'Редкие и актуальные позиции, которые уже находятся в России. '
            'Без ожидания выкупа из зарубежного бутика.'
        ),
        page_class='catalog-page collection-page',
        is_finder_collection=True,
    )


def hermes_catalog(request):
    get_object_or_404(CatalogBrand, slug=hermes.BRAND_SLUG, is_active=True)
    return hermes.catalog(request)


def miumiu_catalog(request):
    get_object_or_404(CatalogBrand, slug=miumiu.BRAND_SLUG, is_active=True)
    return miumiu.catalog(request)


def brand_catalog(request, brand_slug):
    """Универсальная витрина активного бутика без отдельного view на бренд."""
    brand = get_object_or_404(CatalogBrand, slug=brand_slug, is_active=True)
    known = {
        'hermes': ('Париж, Франция', hermes.BRAND_LOGO, 'hermes-page'),
        'miu-miu': ('Милан, Италия', miumiu.BRAND_LOGO, 'miumiu-page'),
        'the-row': ('Нью-Йорк, США', '', 'brand-boutique-page'),
        'off-white': ('Милан, Италия', '', 'brand-boutique-page'),
    }
    location, logo, page_class = known.get(
        brand.slug, ('Коллекция бренда', '', 'brand-boutique-page')
    )
    return catalog_storefront.catalog(
        request,
        brand_slug=brand.slug,
        brand_name=brand.name,
        catalog_url_name='shop:brand_catalog',
        brand_location=location,
        brand_logo=logo,
        brand_logo_url=brand.image.url if brand.image else '',
        page_class=page_class,
        flatten_single_department=True,
    )


def product_detail(request, pk, slug):
    # Одним лёгким запросом одновременно проверяем товар, его доступность и
    # активность бренда. Полную карточку со связями ниже загрузит storefront.
    product_scope = get_object_or_404(
        available_products(Product.objects.filter(
            pk=pk, slug=slug, is_active=True,
        )).only('catalog_scope'),
    ).catalog_scope
    brand = get_object_or_404(
        CatalogBrand.objects.filter(
            is_active=True,
            slug__in=available_products(Product.objects.filter(
                pk=pk, slug=slug, is_active=True,
            )).values('brand'),
        )
    )
    if product_scope == Product.CatalogScope.COLLECTION:
        return catalog_storefront.product_detail(
            request, pk, slug,
            brand_slug=None,
            brand_name=brand.name,
            catalog_url_name='shop:collection',
            catalog_scope=Product.CatalogScope.COLLECTION,
            page_class='catalog-page collection-page',
            is_finder_collection=True,
        )
    if brand.slug == hermes.BRAND_SLUG:
        return hermes.product_detail(request, pk, slug)
    if brand.slug == miumiu.BRAND_SLUG:
        return miumiu.product_detail(request, pk, slug)
    return catalog_storefront.product_detail(
        request, pk, slug,
        brand_slug=brand.slug,
        brand_name=brand.name,
        catalog_url_name='shop:brand_catalog',
        brand_logo_url=brand.image.url if brand.image else '',
    )


def product_quick_view(request, product_id):
    product = get_object_or_404(
        _products('images', 'size_variants').filter(is_active=True), pk=product_id,
    )
    _attach_catalog_brand_names([product])
    available_sizes = available_size_variants(product)
    selected_size = order_size_variant(product)
    if available_sizes and selected_size is None:
        selected_size = available_sizes[0]
    return render(request, 'shop/catalog/includes/quick_view.html', {
        'product': product,
        'available_sizes': available_sizes,
        'selected_size': selected_size,
    })


def _favorite_keys(request):
    """Возвращает уникальные корректные ключи товаров в порядке их добавления."""
    stored = request.session.get('favorites', [])
    keys = []
    seen = set()
    if not isinstance(stored, (list, tuple)):
        return keys
    for product_id in stored:
        try:
            key = str(int(product_id))
        except (TypeError, ValueError):
            continue
        if key not in seen:
            keys.append(key)
            seen.add(key)
    if request.user.is_authenticated:
        valid_session_keys = {
            str(product_id) for product_id in available_products(Product.objects.filter(
                pk__in=keys, is_active=True
            )).values_list('pk', flat=True)
        }
        database_keys = [
            str(product_id) for product_id in Favorite.objects.filter(
                user=request.user,
                product_id__in=available_products(Product.objects.filter(
                    is_active=True
                )).values('pk'),
            ).values_list('product_id', flat=True)
        ]
        # Переносим гостевое избранное после входа, не теряя прежние записи.
        Favorite.objects.bulk_create([
            Favorite(user=request.user, product_id=int(key))
            for key in keys if key in valid_session_keys and key not in database_keys
        ], ignore_conflicts=True)
        keys = [key for key in keys if key in valid_session_keys]
        for key in database_keys:
            if key not in seen:
                keys.append(key)
                seen.add(key)
        request.session['favorites'] = keys
    return keys


def favorite_detail(request):
    keys = _favorite_keys(request)
    products_by_id = {
        str(product.pk): product for product in _products('images').filter(pk__in=keys)
    }
    products = [products_by_id[key] for key in keys if key in products_by_id]
    _attach_catalog_brand_names(products)
    clean_keys = [str(product.pk) for product in products]
    if clean_keys != keys:
        request.session['favorites'] = clean_keys
        request.session.modified = True
    return render(request, 'shop/favorites.html', {'favorite_products': products})


@require_POST
def favorite_toggle(request, product_id):
    product = get_object_or_404(
        available_products(Product.objects.filter(is_active=True)), pk=product_id
    )
    keys = _favorite_keys(request)
    key = str(product.pk)
    if key in keys:
        keys.remove(key)
        if request.user.is_authenticated:
            Favorite.objects.filter(user=request.user, product=product).delete()
        is_favorite = False
    else:
        keys.insert(0, key)
        if request.user.is_authenticated:
            Favorite.objects.get_or_create(user=request.user, product=product)
        is_favorite = True
    request.session['favorites'] = keys
    request.session.modified = True
    track_event(
        request, 'favorite_add' if is_favorite else 'favorite_remove', product=product
    )

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'product_id': product.pk,
            'is_favorite': is_favorite,
            'favorite_count': len(keys),
        })
    return redirect(_safe_next_url(request, fallback='shop:favorites'))


def cart_detail(request):
    context = _cart_context(request)
    track_event(request, 'cart_view', value=context['cart_total'], metadata={
        'items_count': sum(item['quantity'] for item in context['cart_items'])
    })
    return render(request, 'shop/cart.html', context)


def _cart_context(request):
    from .pricing import calculate_price, price_calculation_snapshot

    _sync_cart(request)
    cart = request.session.get('cart', {})
    cart_options = request.session.get('cart_options', {})
    products = {
        str(item.pk): item
        for item in _products('images', 'size_variants').filter(pk__in=cart.keys())
    }
    items = []
    total = Decimal('0')
    stale_keys = []
    unavailable_prices = []
    for product_id, quantity in cart.items():
        product = products.get(str(product_id))
        if not product:
            stale_keys.append(product_id)
            continue
        quantity = max(1, int(quantity))
        if product.pricing_mode == Product.PricingMode.FIXED_RUB:
            calculation = None
            unit_price = product.fixed_price_rub
        else:
            calculation = calculate_price(product.price, product.currency)
            unit_price = calculation.total if calculation else None
        product_options = cart_options.get(str(product_id), {})
        requested_size_sku = (
            product_options.get('size_sku', '') if isinstance(product_options, dict) else ''
        )
        size_variant = order_size_variant(product, requested_size_sku)
        if requested_size_sku and size_variant is None:
            size_variant = order_size_variant(product)
        if size_variant is None:
            size_variant = next(iter(available_size_variants(product)), None)
        selected_size = (
            size_variant.size if size_variant else
            ('' if product.current_size == 'SANS_TAILLE' else product.current_size)
        )
        selected_sku = size_variant.sku if size_variant else product.sku
        if unit_price is None:
            unavailable_prices.append(product)
            subtotal = None
        else:
            subtotal = unit_price * quantity
            total += subtotal
        items.append({
            'product': product,
            'quantity': quantity,
            'unit_price': unit_price,
            'subtotal': subtotal,
            'selected_size': selected_size,
            'selected_sku': selected_sku,
            'price_breakdown': price_calculation_snapshot(
                calculation, pricing_mode=product.pricing_mode,
            ),
        })
    if stale_keys:
        for key in stale_keys:
            cart.pop(key, None)
            cart_options.pop(str(key), None)
        request.session['cart'] = cart
        request.session['cart_options'] = cart_options
    return {
        'cart_items': items,
        'cart_total': total,
        'cart_currency': 'RUB',
        'unavailable_prices': unavailable_prices,
    }


def _sync_cart(request, merge_session=False):
    """Синхронизирует сессию с постоянной корзиной после авторизации."""
    if not request.user.is_authenticated:
        return
    session_cart = request.session.get('cart', {})
    session_options = request.session.get('cart_options', {})
    stored_items = {
        str(item.product_id): item
        for item in CartItem.objects.filter(user=request.user)
    }
    # Это также поддерживает вход через стандартные Django-механизмы (например,
    # force_login в тестах), где явный обработчик account_login не вызывается.
    if not stored_items and session_cart:
        merge_session = True
    valid_ids = {
        str(product_id) for product_id in available_products(Product.objects.filter(
            is_active=True, pk__in=set(session_cart) | set(stored_items)
        )).values_list('pk', flat=True)
    }
    for key, quantity in (session_cart.items() if merge_session else ()):
        if key not in valid_ids:
            continue
        try:
            quantity = min(max(int(quantity), 1), 99)
        except (TypeError, ValueError):
            continue
        option = session_options.get(key, {})
        size_sku = option.get('size_sku', '') if isinstance(option, dict) else ''
        item = stored_items.get(key)
        if item:
            item.quantity = min(item.quantity + quantity, 99)
            if size_sku:
                item.size_sku = size_sku
            item.save(update_fields=('quantity', 'size_sku', 'updated_at'))
        else:
            item = CartItem.objects.create(
                user=request.user, product_id=int(key), quantity=quantity,
                size_sku=size_sku,
            )
            stored_items[key] = item
    cart = {
        key: item.quantity for key, item in stored_items.items() if key in valid_ids
    }
    cart_options = {
        key: {'size_sku': item.size_sku}
        for key, item in stored_items.items() if key in valid_ids and item.size_sku
    }
    request.session['cart'] = cart
    request.session['cart_options'] = cart_options
    request.session.modified = True


def _save_cart_item(request, product_id):
    """Записывает одну позицию сессии в постоянную корзину."""
    if not request.user.is_authenticated:
        return
    key = str(product_id)
    cart = request.session.get('cart', {})
    options = request.session.get('cart_options', {}).get(key, {})
    if key not in cart:
        CartItem.objects.filter(user=request.user, product_id=product_id).delete()
        return
    CartItem.objects.update_or_create(
        user=request.user, product_id=product_id,
        defaults={
            'quantity': cart[key],
            'size_sku': options.get('size_sku', '') if isinstance(options, dict) else '',
        },
    )


def _create_checkout_order(request, profile, form, context):
    promo_code = form.cleaned_data.get('promo_code', '')
    delivery_method = form.cleaned_data.get('delivery_method', '')
    delivery_cost = CheckoutForm.DELIVERY_PRICES.get(delivery_method, Decimal('0'))
    payment_method = form.cleaned_data.get('payment_method', '')
    discount_amount = (
        (context['cart_total'] * Decimal('0.10')).quantize(Decimal('0.01'))
        if promo_code == 'WELCOME10' else Decimal('0')
    )
    estimated_total = context['cart_total'] - discount_amount + delivery_cost
    with transaction.atomic():
        order_attribution = attribution(request)
        order = Order.objects.create(
            user=request.user,
            customer_name=request.user.get_full_name().strip() or request.user.username,
            customer_email=request.user.email,
            customer_phone=profile.phone,
            customer_telegram=profile.telegram,
            delivery_address=form.cleaned_data['delivery_address'],
            delivery_method=delivery_method,
            delivery_cost=delivery_cost,
            payment_method=payment_method,
            preferred_contact=form.cleaned_data['preferred_contact'],
            estimated_total=estimated_total,
            subtotal=context['cart_total'],
            discount_amount=discount_amount,
            promo_code=promo_code,
            currency=context['cart_currency'],
            source=order_attribution['source'],
            utm_medium=order_attribution['utm_medium'],
            utm_campaign=order_attribution['utm_campaign'],
            utm_content=order_attribution['utm_content'],
            ad_id=order_attribution['ad_id'],
            first_source=order_attribution['first_source'],
            first_utm_medium=order_attribution['first_utm_medium'],
            first_utm_campaign=order_attribution['first_utm_campaign'],
            first_utm_content=order_attribution['first_utm_content'],
            first_ad_id=order_attribution['first_ad_id'],
            analytics_visitor=order_attribution['analytics_visitor'],
            analytics_session=order_attribution['analytics_session'],
        )
        OrderItem.objects.bulk_create([
            OrderItem(
                order=order,
                product=item['product'],
                product_title=item['product'].title,
                brand=item['product'].brand,
                sku=item['selected_sku'],
                color=item['product'].current_color,
                size=item['selected_size'],
                unit_price=item['unit_price'],
                currency='RUB',
                quantity=item['quantity'],
                price_breakdown=item['price_breakdown'],
            )
            for item in context['cart_items']
        ])
        profile.delivery_address = form.cleaned_data['delivery_address']
        profile.preferred_contact = form.cleaned_data['preferred_contact']
        profile.save(update_fields=('delivery_address', 'preferred_contact'))
    request.session['cart'] = {}
    request.session['cart_options'] = {}
    request.session.modified = True
    CartItem.objects.filter(user=request.user).delete()
    track_event(request, 'checkout_success', value=order.estimated_total, metadata={
        'order_id': order.number,
    })
    return order


def checkout(request):
    context = _cart_context(request)
    profile = get_customer_profile(request.user) if request.user.is_authenticated else None
    resume_url = f'{reverse("shop:checkout")}?resume=1'
    pending_checkout = request.session.get(PENDING_CHECKOUT_SESSION_KEY)
    track_event(
        request, 'checkout_start' if request.method == 'GET' else 'checkout_contacts',
        value=context['cart_total'],
    )
    if not context['cart_items']:
        messages.info(request, 'Добавьте товары в корзину перед оформлением заказа.')
        return redirect('shop:cart')
    if context['unavailable_prices']:
        messages.error(
            request,
            'Не удалось рассчитать рублёвую стоимость некоторых товаров. '
            'Менеджеру необходимо обновить курсы валют.',
        )
        return redirect('shop:cart')

    if request.user.is_authenticated and request.GET.get('resume') == '1' and pending_checkout:
        if not profile.phone:
            messages.warning(
                request,
                'Добавьте номер телефона, и сохранённый заказ будет оформлен автоматически.',
            )
            return redirect(_profile_completion_url(resume_url))
        form = CheckoutForm(pending_checkout, profile=profile)
        if form.is_valid():
            order = _create_checkout_order(request, profile, form, context)
            request.session.pop(PENDING_CHECKOUT_SESSION_KEY, None)
            request.session.modified = True
            return redirect('shop:order_success', pk=order.pk)
    else:
        if profile is not None and not profile.phone:
            messages.warning(
                request,
                'Добавьте номер телефона в профиле, чтобы менеджер мог связаться с вами.',
            )
            return redirect(_profile_completion_url(reverse('shop:checkout')))
        form = CheckoutForm(request.POST or None, profile=profile)

    if request.method == 'POST' and form.is_valid():
        if not request.user.is_authenticated:
            request.session[PENDING_CHECKOUT_SESSION_KEY] = {
                'delivery_address': form.cleaned_data['delivery_address'],
                'preferred_contact': form.cleaned_data['preferred_contact'],
                'delivery_method': form.cleaned_data.get('delivery_method', ''),
                'payment_method': form.cleaned_data.get('payment_method', ''),
                'promo_code': form.cleaned_data.get('promo_code', ''),
            }
            request.session.modified = True
            return _login_for_pending_action(
                request,
                resume_url,
                'Данные заказа сохранены. Войдите или зарегистрируйтесь — заказ оформится автоматически.',
            )
        order = _create_checkout_order(request, profile, form, context)
        return redirect('shop:order_success', pk=order.pk)

    if request.user.is_authenticated and request.GET.get('resume') == '1' and pending_checkout:
        messages.warning(
            request,
            'Проверьте сохранённые данные заказа и подтвердите их ещё раз.',
        )

    promo_code = ''
    if form.is_bound:
        promo_code = str(form.data.get('promo_code', '')).strip().upper()
    elif form.initial:
        promo_code = str(form.initial.get('promo_code', '')).strip().upper()
    checkout_discount = (
        (context['cart_total'] * Decimal('0.10')).quantize(Decimal('0.01'))
        if promo_code == 'WELCOME10' else Decimal('0')
    )
    delivery_method = str(form['delivery_method'].value() or '')
    checkout_delivery = CheckoutForm.DELIVERY_PRICES.get(delivery_method, Decimal('0'))
    context.update({
        'form': form,
        'profile': profile,
        'saved_addresses': (
            request.user.delivery_addresses.all() if request.user.is_authenticated else ()
        ),
        'checkout_discount': checkout_discount,
        'checkout_delivery': checkout_delivery,
        'checkout_total': context['cart_total'] - checkout_discount + checkout_delivery,
    })
    return render(request, 'shop/orders/checkout.html', context)


@login_required
def order_success(request, pk):
    order = get_object_or_404(
        Order.objects.prefetch_related('items'), pk=pk, user=request.user
    )
    return render(request, 'shop/orders/success.html', {'order': order})


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.prefetch_related('items'), pk=pk, user=request.user
    )
    return render(request, 'shop/orders/detail.html', {'order': order})


@login_required
@require_POST
def order_repeat(request, pk):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product__size_variants'),
        pk=pk, user=request.user,
    )
    cart = request.session.get('cart', {})
    cart_options = request.session.get('cart_options', {})
    added = 0
    skipped = 0
    for item in order.items.all():
        product = item.product
        if (
            product is None or not product.is_active
            or not is_product_available(product) or product.display_price is None
        ):
            skipped += 1
            continue
        key = str(product.pk)
        cart[key] = min(int(cart.get(key, 0)) + item.quantity, 99)
        if item.sku and order_size_variant(product, item.sku):
            cart_options[key] = {'size_sku': item.sku}
        added += 1
    request.session['cart'] = cart
    request.session['cart_options'] = cart_options
    request.session.modified = True
    for product_id in cart:
        _save_cart_item(request, int(product_id))
    if added:
        messages.success(request, 'Доступные товары из заказа добавлены в корзину.')
    if skipped:
        messages.warning(request, f'Недоступные позиции не добавлены: {skipped}.')
    return redirect('shop:cart')


def things_panel(request):
    """Актуальное содержимое выезжающей панели корзины и избранного."""
    context = _cart_context(request)
    keys = _favorite_keys(request)
    products_by_id = {
        str(product.pk): product
        for product in _products('images').filter(pk__in=keys)
    }
    favorite_products = [products_by_id[key] for key in keys if key in products_by_id]
    _attach_catalog_brand_names(favorite_products)
    context['favorite_products'] = favorite_products
    return render(request, 'shop/includes/things_panel_content.html', context)


@require_POST
def cart_add(request, product_id):
    product = get_object_or_404(
        Product.objects.prefetch_related('size_variants'), pk=product_id, is_active=True
    )
    if not is_product_available(product):
        messages.error(request, 'Товар сейчас недоступен для заказа.')
        return redirect(product)
    if product.display_price is None:
        messages.error(
            request,
            'Цена товара пока не рассчитана. Попробуйте позднее или свяжитесь с менеджером.',
        )
        return redirect(product)
    requested_size_sku = request.POST.get('size_sku', '').strip()
    available_sizes = available_size_variants(product)
    has_in_stock_sizes = any(
        variant.in_stock_ecom for variant in product.size_variants.all()
    )
    if is_shoe_product(product) and has_in_stock_sizes and not available_sizes:
        messages.error(request, 'Для этого раздела сейчас нет доступных размеров.')
        return redirect(product)
    size_variant = order_size_variant(product, requested_size_sku)
    if requested_size_sku and size_variant is None:
        messages.error(request, 'Выбранный размер сейчас недоступен.')
        return redirect(product)
    if available_sizes and size_variant is None:
        size_variant = order_size_variant(product) or available_sizes[0]
    cart = request.session.get('cart', {})
    cart_options = request.session.get('cart_options', {})
    key = str(product.pk)
    cart[key] = min(int(cart.get(key, 0)) + 1, 99)
    if size_variant:
        cart_options[key] = {'size_sku': size_variant.sku}
    else:
        cart_options.pop(key, None)
    request.session['cart'] = cart
    request.session['cart_options'] = cart_options
    request.session.modified = True
    _save_cart_item(request, product.pk)
    cart_context = _cart_context(request)
    track_event(
        request, 'cart_add', product=product, value=cart_context['cart_total'],
        metadata={'quantity': cart[key], 'size_sku': size_variant.sku if size_variant else ''},
    )
    messages.success(request, 'Товар добавлен в корзину.')
    return redirect('shop:cart')


@require_POST
def cart_update(request, product_id):
    cart = request.session.get('cart', {})
    cart_options = request.session.get('cart_options', {})
    key = str(product_id)
    try:
        quantity = int(request.POST.get('quantity', 1))
    except ValueError:
        return HttpResponseBadRequest('Некорректное количество')
    if quantity <= 0:
        cart.pop(key, None)
        cart_options.pop(key, None)
    elif key in cart:
        cart[key] = min(quantity, 99)
    request.session['cart'] = cart
    request.session['cart_options'] = cart_options
    request.session.modified = True
    _save_cart_item(request, product_id)
    product = Product.objects.filter(pk=product_id).first()
    context = _cart_context(request)
    track_event(request, 'cart_quantity_change', product=product, value=context['cart_total'], metadata={
        'quantity': cart.get(key, 0),
    })
    return redirect('shop:cart')


@require_POST
def cart_remove(request, product_id):
    cart = request.session.get('cart', {})
    cart_options = request.session.get('cart_options', {})
    cart.pop(str(product_id), None)
    cart_options.pop(str(product_id), None)
    request.session['cart'] = cart
    request.session['cart_options'] = cart_options
    request.session.modified = True
    _save_cart_item(request, product_id)
    product = Product.objects.filter(pk=product_id).first()
    context = _cart_context(request)
    track_event(request, 'cart_remove', product=product, value=context['cart_total'])
    return redirect('shop:cart')
