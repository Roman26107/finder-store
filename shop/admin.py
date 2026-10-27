from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html

from .forms import (
    ExchangeRateFormSet,
    PricePreviewForm,
    PricingRuleFormSet,
    ProductImportForm,
    ProductZipImportForm,
)
from .models import (
    AnalyticsEvent,
    AnalyticsSession,
    AnalyticsVisitor,
    ApiRequestLog,
    Banner,
    BannerImage,
    CatalogBrand,
    CollectionBatch,
    CustomerAddress,
    CustomerProfile,
    ColorVariant,
    ExchangeRate,
    Favorite,
    ErrorLog,
    MarketingCampaignSpend,
    NotificationEvent,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ProductRequest,
    ProductRequestItem,
    PricingRule,
    SecurityChallenge,
    SizeVariant,
)
from .pricing import calculate_price, update_cbr_rates
from .services import import_product, import_zip_archive, parse_json_file


# Повышение is_staff/is_superuser выполняется только через подтверждаемый flow
# собственной панели. Это закрывает обход через стандартную форму User Admin.
try:
    admin.site.unregister(get_user_model())
except admin.sites.NotRegistered:
    pass


@admin.register(get_user_model())
class ProtectedUserAdmin(UserAdmin):
    """Не позволяет обойти подтверждаемую выдачу административных прав."""

    readonly_fields = ('is_staff', 'is_superuser', 'groups', 'user_permissions')

    def has_add_permission(self, request):
        return False


class ReadOnlySystemAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ErrorLog)
class ErrorLogAdmin(ReadOnlySystemAdmin):
    list_display = ('created_at', 'level', 'logger_name', 'short_message', 'resolved')
    list_filter = ('level', 'resolved', 'logger_name', 'created_at')
    search_fields = ('logger_name', 'message', 'traceback')
    readonly_fields = tuple(field.name for field in ErrorLog._meta.fields)
    date_hierarchy = 'created_at'

    @admin.display(description='Сообщение')
    def short_message(self, obj):
        return obj.message[:140]


@admin.register(SecurityChallenge)
class SecurityChallengeAdmin(ReadOnlySystemAdmin):
    list_display = ('created_at', 'purpose', 'target_user', 'requested_by', 'attempts', 'expires_at', 'consumed_at')
    list_filter = ('purpose', 'created_at', 'consumed_at')
    search_fields = ('target_user__email', 'requested_by__email')
    exclude = ('code_hash',)


@admin.register(ApiRequestLog)
class ApiRequestLogAdmin(ReadOnlySystemAdmin):
    list_display = (
        'created_at', 'request_id', 'key_id', 'method', 'path',
        'status_code', 'result_code', 'remote_addr', 'duration_ms',
    )
    list_filter = ('status_code', 'result_code', 'key_id', 'created_at')
    search_fields = ('=request_id', 'key_id', 'path', 'remote_addr')
    readonly_fields = tuple(field.name for field in ApiRequestLog._meta.fields)
    date_hierarchy = 'created_at'


@admin.register(NotificationEvent)
class NotificationEventAdmin(ReadOnlySystemAdmin):
    list_display = (
        'created_at', 'event_type', 'audience', 'channel', 'order',
        'product_request', 'attempts', 'delivered_at', 'locked_by',
    )
    list_filter = ('event_type', 'audience', 'channel', 'delivered_at', 'created_at')
    search_fields = ('=id', 'event_key', '=order__id', '=product_request__id', 'last_error')
    readonly_fields = tuple(field.name for field in NotificationEvent._meta.fields)
    date_hierarchy = 'created_at'


@admin.register(MarketingCampaignSpend)
class MarketingCampaignSpendAdmin(admin.ModelAdmin):
    list_display = ('source', 'campaign', 'period_start', 'period_end', 'amount')
    list_filter = ('source', 'period_start', 'period_end')
    search_fields = ('source', 'campaign', 'content', 'ad_id')


@admin.register(AnalyticsSession)
class AnalyticsSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'source', 'device_type', 'page_views', 'engaged', 'started_at')
    list_filter = ('source', 'device_type', 'engaged', 'is_new_visitor')
    readonly_fields = tuple(field.name for field in AnalyticsSession._meta.fields)


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'product_sku', 'brand', 'path', 'created_at')
    list_filter = ('event_type', 'brand', 'created_at')
    search_fields = ('product_sku', 'path', 'page_title')
    readonly_fields = tuple(field.name for field in AnalyticsEvent._meta.fields)


@admin.register(AnalyticsVisitor)
class AnalyticsVisitorAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'first_seen_at', 'last_seen_at')
    readonly_fields = tuple(field.name for field in AnalyticsVisitor._meta.fields)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ('preview', 'local_image', 'position', 'tag', 'original', 'large', 'alt')
    readonly_fields = ('preview',)

    @admin.display(description='Превью')
    def preview(self, obj):
        return format_html(
            '<img src="{}" loading="lazy" style="width:70px;height:90px;object-fit:cover">',
            obj.preview_url,
        ) if obj.preview_url else '—'


class ColorVariantInline(admin.TabularInline):
    model = ColorVariant
    extra = 0


class SizeVariantInline(admin.TabularInline):
    model = SizeVariant
    extra = 0


class BannerImageInline(admin.TabularInline):
    model = BannerImage
    extra = 1
    fields = ('catalog_image', 'uploaded_image', 'audience')


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = (
        'slot', 'image_count', 'interval_seconds', 'autoplay',
        'personalize_by_gender', 'updated_at',
    )
    list_editable = ('image_count', 'interval_seconds', 'autoplay', 'personalize_by_gender')
    inlines = (BannerImageInline,)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    change_list_template = 'admin/shop/product/change_list.html'
    list_display = (
        'admin_thumbnail', 'title', 'brand', 'sku', 'category_department_name',
        'category_name', 'catalog_scope', 'collection', 'color_group', 'price_with_currency',
        'current_color', 'current_size', 'in_stock_ecom', 'is_active', 'storefront_link',
        'updated_at',
    )
    list_display_links = ('admin_thumbnail', 'title')
    list_editable = ('in_stock_ecom', 'is_active')
    list_filter = (
        'catalog_scope', 'brand', 'is_active', 'is_featured', 'in_stock_ecom', 'currency',
        'category_department_name', 'category_name', 'collection', 'color_group',
    )
    search_fields = ('title', 'sku', 'sku_without_size', 'product_code', 'current_color')
    readonly_fields = ('created_at', 'updated_at', 'admin_large_preview')
    prepopulated_fields = {}
    inlines = (ProductImageInline, ColorVariantInline, SizeVariantInline)
    actions = ('publish_products', 'hide_products', 'feature_products', 'unfeature_products')
    save_on_top = True
    fieldsets = (
        ('Идентификация', {'fields': ('admin_large_preview', 'brand', 'source_url', 'sku', 'sku_without_size', 'product_code', 'slug')}),
        ('Основное', {'fields': ('title', ('price', 'currency'), ('current_color', 'current_size'), 'gender', 'material', 'in_stock_ecom')}),
        ('Тексты', {'fields': ('description', 'dimensions', 'made_in', 'color_label', 'product_detail_html', 'care_instruction_html', 'story_behind')}),
        ('Категории', {'fields': (
            ('category_department_slug', 'category_department_name'),
            'category_name', 'category_path', 'category_path_key', 'category_keys',
            'collection', 'categories_breadcrumbs',
        )}),
        ('Публикация', {'fields': ('catalog_scope', 'is_active', 'is_featured')}),
        ('Служебное', {'classes': ('collapse',), 'fields': ('fetched_at', 'i18n', 'raw_data', 'created_at', 'updated_at')}),
    )

    def get_urls(self):
        custom_urls = [
            path('pricing/', self.admin_site.admin_view(self.pricing_constructor), name='shop_product_pricing'),
            path('import-json/', self.admin_site.admin_view(self.import_json), name='shop_product_import'),
            path('import-zip/', self.admin_site.admin_view(self.import_zip), name='shop_product_import_zip'),
        ]
        return custom_urls + super().get_urls()

    @staticmethod
    def _has_pricing_permission(request):
        return request.user.is_active and request.user.is_staff and (
            request.user.is_superuser
            or request.user.has_perm('shop.change_product')
            or request.user.has_perm('shop.change_pricingrule')
            or request.user.has_perm('shop.change_exchangerate')
        )

    def pricing_constructor(self, request):
        if not self._has_pricing_permission(request):
            raise PermissionDenied

        currency_order = Case(
            When(currency='CNY', then=Value(0)),
            When(currency='USD', then=Value(1)),
            When(currency='EUR', then=Value(2)),
            When(currency='RUB', then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
        rates = ExchangeRate.objects.annotate(currency_order=currency_order).order_by('currency_order')
        rules = PricingRule.objects.order_by('position', 'id')
        rate_formset = ExchangeRateFormSet(queryset=rates, prefix='rates')
        rule_formset = PricingRuleFormSet(queryset=rules, prefix='rules')
        preview_form = PricePreviewForm(prefix='preview')
        calculation = None

        if request.method == 'POST':
            action = request.POST.get('action')
            if action == 'update_rates':
                try:
                    rate_date, updated_rates = update_cbr_rates()
                except Exception as exc:
                    self.message_user(
                        request,
                        f'Не удалось обновить курсы Банка России: {exc}. Сохранённые курсы не изменены.',
                        messages.ERROR,
                    )
                else:
                    currencies = ', '.join(updated_rates)
                    self.message_user(
                        request,
                        f'Курсы на {rate_date:%d.%m.%Y} обновлены: {currencies}.',
                        messages.SUCCESS,
                    )
                return redirect('admin:shop_product_pricing')

            if action == 'save_rates':
                rate_formset = ExchangeRateFormSet(request.POST, queryset=rates, prefix='rates')
                if rate_formset.is_valid():
                    with transaction.atomic():
                        rate_formset.save()
                    self.message_user(request, 'Курсы валют сохранены.', messages.SUCCESS)
                    return redirect('admin:shop_product_pricing')

            elif action == 'save_rules':
                rule_formset = PricingRuleFormSet(request.POST, queryset=rules, prefix='rules')
                if rule_formset.is_valid():
                    with transaction.atomic():
                        rule_formset.save()
                    self.message_user(request, 'Формула стоимости сохранена.', messages.SUCCESS)
                    return redirect('admin:shop_product_pricing')

            elif action == 'preview':
                preview_form = PricePreviewForm(request.POST, prefix='preview')
                if preview_form.is_valid():
                    calculation = calculate_price(
                        preview_form.cleaned_data['amount'],
                        preview_form.cleaned_data['currency'],
                    )

        context = {
            **self.admin_site.each_context(request),
            'title': 'Конструктор стоимости',
            'opts': self.model._meta,
            'rate_formset': rate_formset,
            'rule_formset': rule_formset,
            'preview_form': preview_form,
            'calculation': calculation,
        }
        return render(request, 'admin/shop/product/pricing.html', context)

    def import_json(self, request):
        if request.method == 'POST':
            form = ProductImportForm(request.POST, request.FILES)
            if form.is_valid():
                created = updated = 0
                errors = []
                for uploaded_file in form.cleaned_data['json_files']:
                    try:
                        cards = parse_json_file(uploaded_file)
                    except ValueError as exc:
                        errors.append(str(exc))
                        continue
                    for index, card in enumerate(cards, start=1):
                        try:
                            with transaction.atomic():
                                _, was_created = import_product(
                                    card,
                                    replace_relations=form.cleaned_data['replace_relations'],
                                )
                            created += int(was_created)
                            updated += int(not was_created)
                        except (ValueError, TypeError) as exc:
                            errors.append(f'{uploaded_file.name}, карточка {index}: {exc}')
                if created or updated:
                    self.message_user(request, f'Импорт завершён: создано {created}, обновлено {updated}.', messages.SUCCESS)
                for error in errors[:20]:
                    self.message_user(request, error, messages.ERROR)
                if len(errors) > 20:
                    self.message_user(request, f'Не показано ошибок: {len(errors) - 20}.', messages.WARNING)
                return redirect('admin:shop_product_changelist')
        else:
            form = ProductImportForm()
        context = {
            **self.admin_site.each_context(request),
            'title': 'Импорт товаров из JSON',
            'form': form,
            'opts': self.model._meta,
        }
        return render(request, 'admin/shop/product/import_json.html', context)

    def import_zip(self, request):
        reports = []
        if request.method == 'POST':
            form = ProductZipImportForm(request.POST, request.FILES)
            if form.is_valid():
                for uploaded_file in form.cleaned_data['zip_files']:
                    try:
                        report = import_zip_archive(
                            uploaded_file,
                            replace_relations=form.cleaned_data['replace_relations'],
                            extract_images=form.cleaned_data['extract_images'],
                        )
                    except ValueError as exc:
                        self.message_user(request, str(exc), messages.ERROR)
                        continue
                    reports.append(report)
                    self.message_user(
                        request,
                        (
                            f'{report.archive_name}: создано {report.created}, '
                            f'обновлено {report.updated}, изображений привязано '
                            f'{report.images_attached}.'
                        ),
                        messages.SUCCESS if not report.errors else messages.WARNING,
                    )
                if reports:
                    return render(request, 'admin/shop/product/import_zip.html', {
                        **self.admin_site.each_context(request),
                        'title': 'Результат импорта ZIP',
                        'form': ProductZipImportForm(),
                        'reports': reports,
                        'opts': self.model._meta,
                    })
        else:
            form = ProductZipImportForm()
        context = {
            **self.admin_site.each_context(request),
            'title': 'Импорт товаров из ZIP',
            'form': form,
            'reports': reports,
            'opts': self.model._meta,
        }
        return render(request, 'admin/shop/product/import_zip.html', context)

    @admin.display(description='Фото')
    def admin_thumbnail(self, obj):
        image = obj.primary_image
        return format_html('<img src="{}" style="width:45px;height:58px;object-fit:cover">', image.url) if image and image.url else '—'

    @admin.display(description='Изображение')
    def admin_large_preview(self, obj):
        image = obj.primary_image if obj.pk else None
        return format_html('<img src="{}" style="max-width:220px;max-height:280px;object-fit:contain">', image.url) if image and image.url else 'Изображение не загружено'

    @admin.display(description='Цена', ordering='price')
    def price_with_currency(self, obj):
        if obj.display_price is None:
            return f'Не рассчитана (исходная: {obj.source_display_price} {obj.currency})'
        return f'{obj.display_price} RUB (исходная: {obj.source_display_price} {obj.currency})'

    @admin.display(description='Витрина')
    def storefront_link(self, obj):
        return format_html(
            '<a class="elane-table-action" href="{}" target="_blank" rel="noopener">Открыть ↗</a>',
            obj.get_absolute_url(),
        )

    @admin.action(description='Показать выбранные товары на сайте')
    def publish_products(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'Опубликовано товаров: {updated}.', messages.SUCCESS)

    @admin.action(description='Скрыть выбранные товары с сайта')
    def hide_products(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'Скрыто товаров: {updated}.', messages.SUCCESS)

    @admin.action(description='Добавить выбранные товары в новинки')
    def feature_products(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f'Добавлено в новинки: {updated}.', messages.SUCCESS)

    @admin.action(description='Убрать выбранные товары из новинок')
    def unfeature_products(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f'Убрано из новинок: {updated}.', messages.SUCCESS)


@admin.register(CollectionBatch)
class CollectionBatchAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at', 'updated_at')
    search_fields = ('name', 'created_by__username', 'created_by__email')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = (
        'product', 'position', 'tag', 'local_image', 'preview_image',
        'gallery_image', 'detail_image', 'detail_processed',
    )
    search_fields = ('product__title', 'product__sku', 'tag')


admin.site.register(ColorVariant)
admin.site.register(SizeVariant)
admin.site.register(CatalogBrand)
admin.site.register(Favorite)
admin.site.register(CustomerAddress)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('currency', 'rubles_per_unit', 'rate_date', 'source', 'updated_at')
    list_filter = ('currency', 'source', 'rate_date')
    readonly_fields = ('updated_at',)
    ordering = ('currency',)


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = (
        'position', 'name', 'adjustment_type', 'value',
        'calculate_from_initial', 'is_active', 'updated_at',
    )
    list_display_links = ('name',)
    list_editable = ('position', 'is_active')
    list_filter = ('adjustment_type', 'calculate_from_initial', 'is_active')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('position', 'id')


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'phone', 'telegram', 'gender', 'preferred_contact',
        'email_verified', 'email_verification_sent_at',
    )
    list_filter = ('email_verified', 'gender', 'preferred_contact')
    search_fields = (
        'user__username', 'user__email', 'user__first_name',
        'user__last_name', 'phone', 'telegram', 'delivery_address',
    )
    readonly_fields = (
        'email_verification_code_hash',
        'email_verification_sent_at',
        'email_verification_expires_at',
        'email_verification_attempts',
    )


class ProductRequestItemInline(admin.TabularInline):
    model = ProductRequestItem
    extra = 0
    max_num = 10
    fields = (
        'brand', 'product_name', 'article', 'color', 'size',
        'source_url', 'reference_image',
    )


@admin.register(ProductRequest)
class ProductRequestAdmin(admin.ModelAdmin):
    list_display = (
        'number_display', 'customer_name', 'customer_email', 'customer_phone',
        'customer_telegram',
        'preferred_contact', 'delivery_address',
        'requested_items', 'items_count', 'lead_status', 'status', 'source', 'created_at',
    )
    list_display_links = ('number_display', 'customer_name')
    list_editable = ('lead_status', 'status')
    list_filter = ('lead_status', 'status', 'source', 'is_repeat_customer', 'created_at')
    search_fields = (
        '=id', 'customer_name', 'customer_email', 'customer_phone', 'customer_telegram',
        'items__brand', 'items__product_name', 'items__article',
        'items__color', 'items__size', 'source', 'utm_campaign', 'ad_id',
    )
    readonly_fields = (
        'user', 'customer_name', 'customer_email', 'customer_phone', 'customer_telegram',
        'preferred_contact', 'delivery_address',
        'source', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
        'utm_term', 'ad_id', 'landing_page', 'lead_type', 'is_repeat_customer',
        'first_source', 'first_utm_medium', 'first_utm_campaign',
        'first_utm_content', 'first_utm_term', 'first_ad_id', 'first_landing_page',
        'analytics_visitor', 'analytics_session',
        'contacted_at', 'qualified_at', 'offered_at', 'ordered_at',
        'created_at', 'updated_at',
    )
    fields = (
        ('lead_status', 'status', 'user'),
        ('customer_name', 'customer_email', 'customer_phone', 'customer_telegram'),
        ('preferred_contact', 'delivery_address'),
        ('estimated_value', 'lead_type', 'is_repeat_customer'),
        ('source', 'utm_source', 'utm_medium'),
        ('utm_campaign', 'utm_content', 'utm_term'),
        ('ad_id', 'landing_page'),
        ('first_source', 'first_utm_medium'),
        ('first_utm_campaign', 'first_utm_content', 'first_utm_term'),
        ('first_ad_id', 'first_landing_page'),
        ('analytics_visitor', 'analytics_session'),
        ('loss_reason', 'loss_reason_note'),
        'converted_order',
        ('contacted_at', 'qualified_at', 'offered_at', 'ordered_at'),
        'manager_comment',
        ('created_at', 'updated_at'),
    )
    inlines = (ProductRequestItemInline,)
    actions = ('mark_processing', 'mark_confirmation', 'mark_completed')
    date_hierarchy = 'created_at'

    @admin.display(description='Номер', ordering='id')
    def number_display(self, obj):
        return obj.number

    @admin.display(description='Товаров')
    def items_count(self, obj):
        return len(obj.items.all())

    @admin.display(description='Что ищут')
    def requested_items(self, obj):
        labels = []
        for item in obj.items.all()[:3]:
            article = f' · {item.article}' if item.article else ''
            labels.append(f'{item.brand} — {item.product_name}{article}')
        result = ', '.join(labels)
        return f'{result}…' if len(obj.items.all()) > 3 else result or '—'

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('items')

    @admin.action(description='Перевести выбранные заявки в обработку')
    def mark_processing(self, request, queryset):
        self._set_status(request, queryset, ProductRequest.Status.PROCESSING)

    @admin.action(description='Отметить: ожидает подтверждения')
    def mark_confirmation(self, request, queryset):
        self._set_status(request, queryset, ProductRequest.Status.CONFIRMATION)

    @admin.action(description='Завершить выбранные заявки')
    def mark_completed(self, request, queryset):
        self._set_status(request, queryset, ProductRequest.Status.COMPLETED)

    def _set_status(self, request, queryset, status):
        updated = 0
        for product_request in queryset:
            product_request.status = status
            product_request.save(update_fields=('status',))
            updated += 1
        label = ProductRequest.Status(status).label
        self.message_user(
            request, f'Статус «{label}» установлен для заявок: {updated}.', messages.SUCCESS
        )


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = (
        'product', 'product_title', 'brand', 'sku', 'color', 'size',
        'unit_price', 'currency', 'quantity',
    )
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'number_display', 'customer_name', 'customer_phone', 'preferred_contact',
        'items_count', 'estimated_total_display', 'status', 'created_at',
    )
    list_display_links = ('number_display', 'customer_name')
    list_editable = ('status',)
    list_filter = ('status', 'preferred_contact', 'currency', 'created_at')
    search_fields = (
        '=id', 'customer_name', 'customer_email', 'customer_phone',
        'customer_telegram', 'delivery_address', 'items__product_title', 'items__sku',
    )
    readonly_fields = (
        'user', 'customer_name', 'customer_email', 'customer_phone',
        'customer_telegram', 'delivery_address', 'preferred_contact',
        'subtotal', 'discount_amount', 'promo_code', 'delivery_method',
        'delivery_cost', 'payment_method', 'estimated_total', 'currency',
        'source', 'utm_medium', 'utm_campaign',
        'utm_content', 'ad_id', 'first_source', 'first_utm_medium',
        'first_utm_campaign', 'first_utm_content', 'first_ad_id',
        'analytics_visitor', 'analytics_session', 'created_at', 'updated_at',
    )
    fields = (
        ('status', 'user'),
        ('customer_name', 'customer_email'),
        ('customer_phone', 'customer_telegram'),
        ('preferred_contact', 'delivery_address'),
        ('subtotal', 'discount_amount', 'promo_code'),
        ('delivery_method', 'delivery_cost', 'payment_method'),
        ('estimated_total', 'currency'),
        ('actual_revenue', 'cost_of_goods', 'paid_at'),
        ('source', 'utm_medium', 'utm_campaign'),
        ('utm_content', 'ad_id'),
        ('first_source', 'first_utm_medium', 'first_utm_campaign'),
        ('first_utm_content', 'first_ad_id'),
        ('analytics_visitor', 'analytics_session'),
        'manager_comment',
        ('created_at', 'updated_at'),
    )
    inlines = (OrderItemInline,)
    actions = ('mark_processing', 'mark_confirmation', 'mark_completed')
    date_hierarchy = 'created_at'

    @admin.display(description='Номер', ordering='id')
    def number_display(self, obj):
        return obj.number

    @admin.display(description='Товаров')
    def items_count(self, obj):
        return len(obj.items.all())

    @admin.display(description='Предварительная стоимость', ordering='estimated_total')
    def estimated_total_display(self, obj):
        return f'{obj.estimated_total} {obj.currency}'

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('items')

    @admin.action(description='Перевести выбранные заказы в обработку')
    def mark_processing(self, request, queryset):
        self._set_status(request, queryset, Order.Status.PROCESSING)

    @admin.action(description='Ожидают подтверждения клиента')
    def mark_confirmation(self, request, queryset):
        self._set_status(request, queryset, Order.Status.CONFIRMATION)

    @admin.action(description='Завершить выбранные заказы')
    def mark_completed(self, request, queryset):
        self._set_status(request, queryset, Order.Status.COMPLETED)

    def _set_status(self, request, queryset, status):
        updated = 0
        for order in queryset:
            order.status = status
            order.save(update_fields=('status',))
            updated += 1
        label = Order.Status(status).label
        self.message_user(
            request, f'Статус «{label}» установлен для заказов: {updated}.', messages.SUCCESS
        )
