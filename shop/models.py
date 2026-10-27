import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify

from .image_fields import OptimizedImageField


class CustomerProfile(models.Model):
    class ContactMethod(models.TextChoices):
        PHONE = 'phone', 'Телефон'
        EMAIL = 'email', 'E-mail'
        TELEGRAM = 'telegram', 'Telegram'
        WHATSAPP = 'whatsapp', 'WhatsApp'

    class Gender(models.TextChoices):
        WOMEN = 'women', 'Женский'
        MEN = 'men', 'Мужской'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='customer_profile',
        verbose_name='Пользователь',
    )
    phone = models.CharField(
        'Телефон', max_length=16, unique=True, blank=True, null=True
    )
    telegram = models.CharField('Telegram', max_length=33, blank=True)
    gender = models.CharField(
        'Пол', max_length=8, choices=Gender.choices, blank=True,
        help_text='Используется для персонализации каталога.',
    )
    delivery_address = models.TextField('Адрес доставки', blank=True)
    preferred_contact = models.CharField(
        'Предпочтительный способ связи',
        max_length=16,
        choices=ContactMethod.choices,
        default=ContactMethod.PHONE,
    )
    email_verified = models.BooleanField('E-mail подтверждён', default=False)
    email_verification_code_hash = models.CharField(
        'Хеш кода подтверждения', max_length=255, blank=True
    )
    email_verification_sent_at = models.DateTimeField(
        'Код отправлен', blank=True, null=True
    )
    email_verification_expires_at = models.DateTimeField(
        'Код действует до', blank=True, null=True
    )
    email_verification_attempts = models.PositiveSmallIntegerField(
        'Неверных попыток', default=0
    )

    class Meta:
        verbose_name = 'профиль покупателя'
        verbose_name_plural = 'профили покупателей'

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class CustomerAddress(models.Model):
    """Сохранённый адрес покупателя из раздела «Адреса» личного кабинета."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='delivery_addresses',
        verbose_name='Пользователь',
    )
    label = models.CharField('Название', max_length=80, default='Адрес доставки')
    city = models.CharField('Город', max_length=160)
    street = models.CharField('Улица и дом', max_length=300)
    apartment = models.CharField('Квартира или офис', max_length=120, blank=True)
    postal_code = models.CharField('Индекс', max_length=20, blank=True)
    is_default = models.BooleanField('Основной адрес', default=False, db_index=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'адрес покупателя'
        verbose_name_plural = 'адреса покупателей'
        ordering = ('-is_default', '-updated_at')
        constraints = [
            models.UniqueConstraint(
                fields=('user',), condition=models.Q(is_default=True),
                name='unique_default_customer_address',
            ),
        ]

    def __str__(self):
        return f'{self.label}: {self.full_address}'

    @property
    def full_address(self):
        return ', '.join(filter(None, (
            self.postal_code, self.city, self.street, self.apartment,
        )))

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_default:
                CustomerAddress.objects.filter(
                    user=self.user, is_default=True,
                ).exclude(pk=self.pk).update(is_default=False)
            elif not self.pk and not CustomerAddress.objects.filter(user=self.user).exists():
                self.is_default = True
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        user = self.user
        was_default = self.is_default
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            if was_default:
                replacement = CustomerAddress.objects.filter(user=user).first()
                if replacement:
                    replacement.is_default = True
                    replacement.save(update_fields=('is_default', 'updated_at'))
            return result


class ProductRequest(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новый'
        PROCESSING = 'processing', 'В обработке'
        CONFIRMATION = 'confirmation', 'Ожидает подтверждения'
        PURCHASED = 'purchased', 'Выкуплен'
        SHIPPING = 'shipping', 'Передан в доставку'
        COMPLETED = 'completed', 'Завершён'
        CANCELLED = 'cancelled', 'Отменён'

    class LeadStatus(models.TextChoices):
        NEW = 'new', 'Новый'
        CONTACTED = 'contacted', 'Связались'
        QUALIFIED = 'qualified', 'Квалифицированный'
        UNQUALIFIED = 'unqualified', 'Нецелевой'
        THINKING = 'thinking', 'Думает'
        OFFER = 'offer', 'Выставлено предложение'
        ORDER = 'order', 'Заказ'
        REJECTED = 'rejected', 'Отказ'

    class LossReason(models.TextChoices):
        TOO_EXPENSIVE = 'too_expensive', 'Дорого'
        NOT_FOUND = 'not_found', 'Не нашли товар'
        NO_RESPONSE = 'no_response', 'Не отвечает'
        CHANGED_MIND = 'changed_mind', 'Передумал'
        OUT_OF_STOCK = 'out_of_stock', 'Нет в наличии'
        OTHER = 'other', 'Другое'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='product_requests',
        verbose_name='Пользователь',
        blank=True,
        null=True,
    )
    customer_name = models.CharField('Имя клиента', max_length=301)
    customer_email = models.EmailField('E-mail клиента')
    customer_phone = models.CharField('Телефон клиента', max_length=16)
    customer_telegram = models.CharField('Telegram клиента', max_length=33, blank=True)
    preferred_contact = models.CharField(
        'Предпочтительный способ связи', max_length=16,
        choices=CustomerProfile.ContactMethod.choices,
        blank=True,
    )
    delivery_address = models.TextField('Адрес доставки', blank=True)
    status = models.CharField(
        'Статус', max_length=20, choices=Status.choices, default=Status.NEW, db_index=True
    )
    manager_comment = models.TextField('Комментарий менеджера', blank=True)
    lead_status = models.CharField(
        'Статус лида', max_length=20, choices=LeadStatus.choices,
        default=LeadStatus.NEW, db_index=True,
    )
    lead_type = models.CharField(
        'Тип заявки', max_length=40, default='personal_order', blank=True
    )
    source = models.CharField('Источник', max_length=120, blank=True, db_index=True)
    utm_source = models.CharField('UTM source', max_length=255, blank=True)
    utm_medium = models.CharField('UTM medium', max_length=255, blank=True)
    utm_campaign = models.CharField('Рекламная кампания', max_length=255, blank=True)
    utm_content = models.CharField('Объявление / UTM content', max_length=255, blank=True)
    utm_term = models.CharField('UTM term', max_length=255, blank=True)
    ad_id = models.CharField('ID объявления', max_length=255, blank=True)
    landing_page = models.CharField('Посадочная страница', max_length=1000, blank=True)
    first_source = models.CharField('Первый источник', max_length=120, blank=True, db_index=True)
    first_utm_medium = models.CharField('Первый UTM medium', max_length=255, blank=True)
    first_utm_campaign = models.CharField('Первая кампания', max_length=255, blank=True)
    first_utm_content = models.CharField('Первое объявление', max_length=255, blank=True)
    first_utm_term = models.CharField('Первый UTM term', max_length=255, blank=True)
    first_ad_id = models.CharField('Первый ID объявления', max_length=255, blank=True)
    first_landing_page = models.CharField('Первая посадочная страница', max_length=1000, blank=True)
    loss_reason = models.CharField(
        'Причина отказа', max_length=32, choices=LossReason.choices, blank=True, db_index=True,
    )
    loss_reason_note = models.CharField('Комментарий к причине', max_length=500, blank=True)
    converted_order = models.ForeignKey(
        'Order', on_delete=models.SET_NULL, related_name='source_leads',
        verbose_name='Созданный заказ', blank=True, null=True,
    )
    analytics_visitor = models.ForeignKey(
        'AnalyticsVisitor', on_delete=models.SET_NULL, related_name='product_requests',
        blank=True, null=True, editable=False,
    )
    analytics_session = models.ForeignKey(
        'AnalyticsSession', on_delete=models.SET_NULL, related_name='product_requests',
        blank=True, null=True, editable=False,
    )
    contacted_at = models.DateTimeField('Связались', blank=True, null=True)
    qualified_at = models.DateTimeField('Квалифицирован', blank=True, null=True)
    offered_at = models.DateTimeField('Предложение сделано', blank=True, null=True)
    ordered_at = models.DateTimeField('Конвертирован в заказ', blank=True, null=True)
    estimated_value = models.DecimalField(
        'Ориентировочная стоимость', max_digits=14, decimal_places=2,
        blank=True, null=True,
    )
    is_repeat_customer = models.BooleanField('Повторный клиент', default=False)
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлена', auto_now=True)

    class Meta:
        verbose_name = 'персональный заказ'
        verbose_name_plural = 'персональные заказы'
        ordering = ('-created_at',)

    def __str__(self):
        return f'Персональный заказ {self.number} — {self.customer_name}'

    @property
    def number(self):
        return f'REQ-{self.pk:06d}' if self.pk else 'Новый'

    def get_absolute_url(self):
        return reverse('shop:product_request_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        now = timezone.now()
        rank = {
            self.LeadStatus.NEW: 0,
            self.LeadStatus.CONTACTED: 1,
            self.LeadStatus.UNQUALIFIED: 1,
            self.LeadStatus.REJECTED: 1,
            self.LeadStatus.QUALIFIED: 2,
            self.LeadStatus.THINKING: 2,
            self.LeadStatus.OFFER: 3,
            self.LeadStatus.ORDER: 4,
        }.get(self.lead_status, 0)
        milestone_fields = []
        for minimum, field in (
            (1, 'contacted_at'), (2, 'qualified_at'),
            (3, 'offered_at'), (4, 'ordered_at'),
        ):
            if rank >= minimum and getattr(self, field) is None:
                setattr(self, field, now)
                milestone_fields.append(field)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and milestone_fields:
            kwargs['update_fields'] = tuple(set(update_fields) | set(milestone_fields))
        # post_save формирует outbox-событие; атомарный блок гарантирует, что
        # заявка и событие commit/rollback выполняют вместе даже в autocommit.
        with transaction.atomic():
            super().save(*args, **kwargs)


class ProductRequestItem(models.Model):
    request = models.ForeignKey(
        ProductRequest,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Заявка',
    )
    brand = models.CharField('Бренд', max_length=255)
    product_name = models.CharField('Название товара', max_length=500)
    article = models.CharField('Артикул', max_length=255, blank=True)
    color = models.CharField('Цвет', max_length=255, blank=True)
    size = models.CharField('Размер', max_length=100, blank=True)
    source_url = models.URLField('Ссылка на товар', max_length=1200, blank=True)
    reference_image = OptimizedImageField(
        'Фотография товара', upload_to='requests/%Y/%m/', blank=True
    )
    position = models.PositiveSmallIntegerField('Позиция', default=0)

    class Meta:
        verbose_name = 'позиция заявки'
        verbose_name_plural = 'позиции заявки'
        ordering = ('position', 'id')

    def __str__(self):
        return self.product_name


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новый'
        PROCESSING = 'processing', 'В обработке'
        CONFIRMATION = 'confirmation', 'Ожидает подтверждения'
        PURCHASED = 'purchased', 'Выкуплен'
        SHIPPING = 'shipping', 'Передан в доставку'
        COMPLETED = 'completed', 'Завершён'
        CANCELLED = 'cancelled', 'Отменён'

    class DeliveryMethod(models.TextChoices):
        COURIER = 'courier', 'Курьер'
        EXPRESS = 'express', 'Экспресс-курьер'
        INTERNATIONAL = 'international', 'Международная доставка'

    class PaymentMethod(models.TextChoices):
        CARD = 'card', 'Банковская карта'
        SBP = 'sbp', 'СБП'
        COD = 'cod', 'При получении'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='orders',
        verbose_name='Пользователь',
        blank=True,
        null=True,
    )
    customer_name = models.CharField('Имя клиента', max_length=301)
    customer_email = models.EmailField('E-mail клиента')
    customer_phone = models.CharField('Телефон клиента', max_length=16)
    customer_telegram = models.CharField('Telegram клиента', max_length=33, blank=True)
    delivery_address = models.TextField('Адрес доставки')
    delivery_method = models.CharField(
        'Способ доставки', max_length=24, choices=DeliveryMethod.choices, blank=True,
    )
    delivery_cost = models.DecimalField(
        'Стоимость доставки', max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal('0'))],
    )
    payment_method = models.CharField(
        'Способ оплаты', max_length=16, choices=PaymentMethod.choices, blank=True,
    )
    preferred_contact = models.CharField(
        'Предпочтительный способ связи',
        max_length=16,
        choices=CustomerProfile.ContactMethod.choices,
        default=CustomerProfile.ContactMethod.PHONE,
    )
    estimated_total = models.DecimalField(
        'Предварительная стоимость', max_digits=14, decimal_places=2, default=0
    )
    subtotal = models.DecimalField(
        'Стоимость товаров до скидки', max_digits=14, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        'Скидка', max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal('0'))],
    )
    promo_code = models.CharField('Промокод', max_length=40, blank=True)
    currency = models.CharField('Валюта', max_length=8, default='CNY')
    actual_revenue = models.DecimalField(
        'Фактическая выручка, ₽', max_digits=14, decimal_places=2,
        blank=True, null=True, validators=[MinValueValidator(Decimal('0'))],
    )
    cost_of_goods = models.DecimalField(
        'Фактическая себестоимость, ₽', max_digits=14, decimal_places=2,
        blank=True, null=True, validators=[MinValueValidator(Decimal('0'))],
    )
    paid_at = models.DateTimeField('Оплачен', blank=True, null=True, db_index=True)
    source = models.CharField('Последний источник', max_length=120, blank=True, db_index=True)
    utm_medium = models.CharField('Последний UTM medium', max_length=255, blank=True)
    utm_campaign = models.CharField('Последняя кампания', max_length=255, blank=True)
    utm_content = models.CharField('Последнее объявление', max_length=255, blank=True)
    ad_id = models.CharField('Последний ID объявления', max_length=255, blank=True)
    first_source = models.CharField('Первый источник', max_length=120, blank=True, db_index=True)
    first_utm_medium = models.CharField('Первый UTM medium', max_length=255, blank=True)
    first_utm_campaign = models.CharField('Первая кампания', max_length=255, blank=True)
    first_utm_content = models.CharField('Первое объявление', max_length=255, blank=True)
    first_ad_id = models.CharField('Первый ID объявления', max_length=255, blank=True)
    analytics_visitor = models.ForeignKey(
        'AnalyticsVisitor', on_delete=models.SET_NULL, related_name='orders',
        blank=True, null=True, editable=False,
    )
    analytics_session = models.ForeignKey(
        'AnalyticsSession', on_delete=models.SET_NULL, related_name='orders',
        blank=True, null=True, editable=False,
    )
    status = models.CharField(
        'Статус', max_length=20, choices=Status.choices, default=Status.NEW, db_index=True
    )
    manager_comment = models.TextField('Комментарий менеджера', blank=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'заказ из корзины'
        verbose_name_plural = 'заказы из корзины'
        ordering = ('-created_at',)

    def __str__(self):
        return f'Заказ {self.number} — {self.customer_name}'

    @property
    def number(self):
        return f'ORD-{self.pk:06d}' if self.pk else 'Новый'

    def get_absolute_url(self):
        return reverse('shop:order_detail', kwargs={'pk': self.pk})

    @property
    def gross_profit(self):
        if self.actual_revenue is None or self.cost_of_goods is None:
            return None
        return self.actual_revenue - self.cost_of_goods

    def save(self, *args, **kwargs):
        paid_statuses = {
            self.Status.PURCHASED, self.Status.SHIPPING, self.Status.COMPLETED,
        }
        if self.status in paid_statuses:
            if self.actual_revenue is None:
                self.actual_revenue = self.estimated_total
            if self.paid_at is None:
                self.paid_at = timezone.now()
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = tuple(set(update_fields) | {'actual_revenue', 'paid_at'})
        # post_save формирует outbox-событие; атомарный блок гарантирует, что
        # заказ и событие commit/rollback выполняют вместе даже в autocommit.
        with transaction.atomic():
            super().save(*args, **kwargs)


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name='items', verbose_name='Заказ'
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        related_name='order_items',
        verbose_name='Товар в каталоге',
        blank=True,
        null=True,
    )
    product_title = models.CharField('Название товара', max_length=500)
    brand = models.CharField('Бренд', max_length=80, blank=True)
    sku = models.CharField('Артикул', max_length=180, blank=True)
    color = models.CharField('Цвет', max_length=255, blank=True)
    size = models.CharField('Размер', max_length=100, blank=True)
    unit_price = models.DecimalField('Цена за единицу', max_digits=14, decimal_places=2)
    currency = models.CharField('Валюта', max_length=8, default='CNY')
    quantity = models.PositiveSmallIntegerField('Количество', default=1)
    price_breakdown = models.JSONField(
        'Состав цены на момент заказа', default=dict, blank=True, editable=False,
    )

    class Meta:
        verbose_name = 'позиция заказа'
        verbose_name_plural = 'позиции заказа'
        ordering = ('id',)

    def __str__(self):
        return self.product_title

    @property
    def subtotal(self):
        return self.unit_price * self.quantity


class ExchangeRate(models.Model):
    class Currency(models.TextChoices):
        CNY = 'CNY', 'Китайский юань (¥)'
        USD = 'USD', 'Доллар США ($)'
        EUR = 'EUR', 'Евро (€)'
        RUB = 'RUB', 'Российский рубль (₽)'

    currency = models.CharField(
        'Валюта', max_length=3, choices=Currency.choices, unique=True
    )
    rubles_per_unit = models.DecimalField(
        'Рублей за единицу', max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001'))],
    )
    rate_date = models.DateField('Дата курса')
    source = models.CharField('Источник', max_length=120, default='Банк России')
    updated_at = models.DateTimeField('Обновлён', auto_now=True)

    class Meta:
        verbose_name = 'курс валюты'
        verbose_name_plural = 'курсы валют'
        ordering = ('currency',)

    def __str__(self):
        return f'1 {self.currency} = {self.rubles_per_unit} RUB'

    def save(self, *args, **kwargs):
        if self.currency == self.Currency.RUB:
            self.rubles_per_unit = Decimal('1')
        super().save(*args, **kwargs)
        from .pricing import clear_pricing_cache
        clear_pricing_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from .pricing import clear_pricing_cache
        clear_pricing_cache()
        return result


class PricingRule(models.Model):
    class AdjustmentType(models.TextChoices):
        PERCENT = 'percent', 'Процент'
        FIXED = 'fixed', 'Фиксированная сумма в рублях'

    name = models.CharField('Название', max_length=120)
    adjustment_type = models.CharField(
        'Тип надбавки', max_length=12, choices=AdjustmentType.choices
    )
    value = models.DecimalField(
        'Значение', max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    calculate_from_initial = models.BooleanField(
        'Считать от первоначальной суммы', default=False,
        help_text=(
            'Для процентной надбавки использовать стоимость после перевода в рубли, '
            'не включая предыдущие строки формулы.'
        ),
    )
    position = models.PositiveSmallIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Использовать в расчёте', default=True)
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    updated_at = models.DateTimeField('Изменена', auto_now=True)

    class Meta:
        verbose_name = 'надбавка к стоимости'
        verbose_name_plural = 'надбавки к стоимости'
        ordering = ('position', 'id')

    def __str__(self):
        suffix = '%' if self.adjustment_type == self.AdjustmentType.PERCENT else '₽'
        return f'{self.name}: {self.value} {suffix}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .pricing import clear_pricing_cache
        clear_pricing_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from .pricing import clear_pricing_cache
        clear_pricing_cache()
        return result


class CatalogBrand(models.Model):
    name = models.CharField('Название', max_length=120, unique=True)
    slug = models.SlugField('Код', max_length=80, unique=True)
    image = OptimizedImageField(
        'Изображение для раздела «Бутики»', upload_to='brands/%Y/%m/', blank=True
    )
    is_active = models.BooleanField('Доступен для выбора', default=True)

    class Meta:
        verbose_name = 'бренд каталога'
        verbose_name_plural = 'бренды каталога'
        ordering = ('name',)

    def __str__(self):
        return self.name


class CatalogColor(models.Model):
    name = models.CharField('Название', max_length=120, unique=True)
    slug = models.SlugField('Код', max_length=80, unique=True)
    group_slug = models.SlugField('Группа цвета', max_length=32, blank=True)
    swatch = models.CharField('Цвет образца', max_length=32, default='#b5b0aa')
    is_active = models.BooleanField('Доступен для выбора', default=True)

    class Meta:
        verbose_name = 'цвет каталога'
        verbose_name_plural = 'цвета каталога'
        ordering = ('name',)

    def __str__(self):
        return self.name

    @property
    def search_name(self):
        return self.name.replace('ё', 'е').replace('Ё', 'Е')

    @property
    def group_name(self):
        from .catalog_data import COLOR_GROUP_BY_SLUG
        return COLOR_GROUP_BY_SLUG.get(self.group_slug, {}).get('name', 'Другой')


class CatalogMaterial(models.Model):
    name = models.CharField('Название', max_length=120, unique=True)
    slug = models.SlugField('Код', max_length=100, unique=True)
    is_active = models.BooleanField('Доступен для выбора', default=True)

    class Meta:
        verbose_name = 'материал каталога'
        verbose_name_plural = 'материалы каталога'
        ordering = ('name',)

    def __str__(self):
        return self.name


class CatalogCategory(models.Model):
    name = models.CharField('Название', max_length=160)
    key = models.CharField('Ключ категории', max_length=500, unique=True)
    department_slug = models.SlugField('Код раздела', max_length=80, blank=True)
    department_name = models.CharField('Название раздела', max_length=120, blank=True)
    path = models.JSONField('Навигационный путь', default=list, blank=True)
    is_active = models.BooleanField('Доступна для выбора', default=True)

    class Meta:
        verbose_name = 'категория справочника'
        verbose_name_plural = 'категории справочника'
        ordering = ('department_name', 'name')

    def __str__(self):
        return f'{self.department_name} / {self.name}' if self.department_name else self.name


class SizeProfile(models.Model):
    class ProductType(models.TextChoices):
        CLOTHING = 'clothing', 'Одежда'
        SHOES = 'shoes', 'Обувь'
        RINGS = 'rings', 'Кольца'
        BRACELETS = 'bracelets', 'Браслеты и ремни'
        WATCHES = 'watches', 'Часы'
        ACCESSORIES = 'accessories', 'Аксессуары'
        UNIVERSAL = 'universal', 'Универсальный'

    class Gender(models.TextChoices):
        WOMEN = 'women', 'Женский'
        MEN = 'men', 'Мужской'
        UNISEX = 'unisex', 'Унисекс'

    name = models.CharField('Название', max_length=120, unique=True)
    slug = models.SlugField('Код', max_length=100, unique=True)
    product_type = models.CharField(
        'Тип товара', max_length=20, choices=ProductType.choices,
        default=ProductType.UNIVERSAL,
    )
    gender = models.CharField(
        'Пол', max_length=12, choices=Gender.choices, default=Gender.UNISEX
    )
    is_active = models.BooleanField('Доступен для выбора', default=True)

    class Meta:
        verbose_name = 'размерный профиль'
        verbose_name_plural = 'размерные профили'
        ordering = ('product_type', 'gender', 'name')

    def __str__(self):
        return self.name


class CatalogSize(models.Model):
    profile = models.ForeignKey(
        SizeProfile, on_delete=models.CASCADE, related_name='sizes', verbose_name='Профиль'
    )
    label = models.CharField('Размер', max_length=100)
    position = models.PositiveSmallIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Доступен для выбора', default=True)

    class Meta:
        verbose_name = 'размер справочника'
        verbose_name_plural = 'размеры справочника'
        ordering = ('position', 'id')
        constraints = [
            models.UniqueConstraint(fields=('profile', 'label'), name='unique_size_in_profile')
        ]

    def __str__(self):
        return f'{self.profile.name}: {self.label}'


class Product(models.Model):
    """Карточка конкретного SKU (цвета/размера)."""

    class CatalogScope(models.TextChoices):
        STORE = 'store', 'Основной каталог'
        COLLECTION = 'collection', 'FINDER COLLECTION'

    brand = models.SlugField('Бренд', max_length=80, default='hermes', db_index=True)
    source_url = models.URLField('Ссылка на источник', max_length=1200, blank=True)
    sku = models.CharField('SKU', max_length=180, unique=True)
    sku_without_size = models.CharField(
        'Артикул модели без размера', max_length=180, blank=True, null=True
    )
    product_code = models.CharField('Код категории', max_length=180, blank=True)
    slug = models.SlugField('URL-имя', max_length=220, blank=True)

    title = models.CharField('Название', max_length=500)
    price = models.DecimalField('Цена', max_digits=14, decimal_places=2, default=0)
    currency = models.CharField('Валюта', max_length=8, default='CNY')
    class PricingMode(models.TextChoices):
        CALCULATED = 'calculated', 'Рассчитывать по курсу'
        FIXED_RUB = 'fixed_rub', 'Фиксированная цена в рублях'

    pricing_mode = models.CharField(
        'Режим цены', max_length=16, choices=PricingMode.choices,
        default=PricingMode.CALCULATED,
    )
    fixed_price_rub = models.DecimalField(
        'Фиксированная цена, ₽', max_digits=14, decimal_places=2,
        blank=True, null=True, validators=[MinValueValidator(Decimal('0'))],
    )
    current_color = models.CharField('Текущий цвет', max_length=255, blank=True)
    current_size = models.CharField('Текущий размер', max_length=100, default='SANS_TAILLE')
    size_profile = models.ForeignKey(
        SizeProfile, on_delete=models.SET_NULL, related_name='products',
        verbose_name='Тип размерного ряда', blank=True, null=True,
    )
    gender = models.JSONField('Пол (список)', default=list, blank=True)
    material = models.JSONField('Материалы (список)', default=list, blank=True)
    in_stock_ecom = models.BooleanField('В наличии', default=False)

    description = models.TextField('Описание', blank=True)
    dimensions = models.TextField('Размеры товара', blank=True)
    made_in = models.CharField('Страна производства', max_length=500, blank=True)
    color_label = models.CharField('Название поля цвета', max_length=255, blank=True)
    product_detail_html = models.TextField('Состав и детали (HTML)', blank=True)
    care_instruction_html = models.TextField('Инструкция по уходу (HTML)', blank=True)
    story_behind = models.TextField('История модели (HTML)', blank=True, null=True)

    categories_breadcrumbs = models.JSONField('Категории и breadcrumbs', default=list, blank=True)
    category_name = models.CharField('Категория для витрины', max_length=255, blank=True)
    category_department_slug = models.SlugField(
        'Раздел каталога', max_length=80, blank=True, db_index=True
    )
    category_department_name = models.CharField(
        'Название раздела', max_length=120, blank=True
    )
    category_path = models.JSONField('Путь подкатегорий', default=list, blank=True)
    category_path_key = models.CharField(
        'Ключ пути категорий', max_length=500, blank=True, db_index=True
    )
    category_keys = models.JSONField('Категории товара', default=list, blank=True)
    color_group = models.SlugField(
        'Основная группа цвета', max_length=32, blank=True, db_index=True
    )
    family_key = models.CharField(
        'Группа цветовых вариантов', max_length=180, blank=True, db_index=True
    )
    collection = models.SlugField(
        'Сезонная коллекция', max_length=32, blank=True, db_index=True
    )
    catalog_scope = models.CharField(
        'Каталог', max_length=16, choices=CatalogScope.choices,
        default=CatalogScope.STORE, db_index=True,
    )
    collection_batch = models.ForeignKey(
        'CollectionBatch', on_delete=models.SET_NULL, related_name='products',
        verbose_name='Партия COLLECTION', blank=True, null=True,
    )
    collection_reviewed_at = models.DateTimeField(
        'Карточка COLLECTION проверена', blank=True, null=True,
    )
    fetched_at = models.BigIntegerField('Время получения (Unix)', blank=True, null=True)
    i18n = models.JSONField('Переводы i18n', default=dict, blank=True)
    raw_data = models.JSONField('Исходная карточка JSON', default=dict, blank=True)

    is_active = models.BooleanField('Показывать на сайте', default=True)
    is_featured = models.BooleanField('Показывать в новинках', default=False)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'товар'
        verbose_name_plural = 'товары'
        ordering = ('-is_featured', '-created_at')
        indexes = [
            models.Index(fields=('is_active', 'category_name')),
            models.Index(fields=('sku_without_size',)),
            models.Index(fields=('catalog_scope', 'is_active', 'created_at')),
        ]

    def __str__(self):
        return f'{self.title} — {self.sku}'

    def save(self, *args, **kwargs):
        if not self.slug:
            # URL converter <slug:...> принимает только ASCII; для русских
            # названий надёжным fallback остаётся SKU.
            base = slugify(self.title)[:170] or 'product'
            self.slug = f'{base}-{slugify(self.sku)[:40]}'.strip('-')
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('shop:product_detail', kwargs={'pk': self.pk, 'slug': self.slug})

    @property
    def article(self):
        """Публичный артикул без внутреннего префикса источника."""
        if self.sku_without_size:
            return self.sku_without_size
        return self.sku.split(':', 1)[-1]

    @property
    def primary_image(self):
        prefetched = list(self.images.all())
        return prefetched[0] if prefetched else None

    @property
    def display_price(self):
        if hasattr(self, '_request_display_price'):
            return self._request_display_price
        if self.pricing_mode == self.PricingMode.FIXED_RUB:
            return self.fixed_price_rub
        from .pricing import calculate_price
        result = calculate_price(self.price, self.currency)
        return result.total if result else None

    @property
    def source_display_price(self):
        return self.price.quantize(Decimal('1')) if self.price == self.price.to_integral() else self.price

    @property
    def display_currency(self):
        return 'RUB'

    @property
    def is_available(self):
        from .availability import is_product_available
        return is_product_available(self)


class CollectionBatch(models.Model):
    """Партия черновиков FINDER COLLECTION для последовательной подготовки."""

    name = models.CharField('Название партии', max_length=180)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='collection_batches', verbose_name='Создал',
        blank=True, null=True,
    )
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    updated_at = models.DateTimeField('Изменена', auto_now=True)

    class Meta:
        verbose_name = 'партия FINDER COLLECTION'
        verbose_name_plural = 'партии FINDER COLLECTION'
        ordering = ('-created_at',)

    def __str__(self):
        return self.name


class ProductImage(models.Model):
    COMPACT_SOURCE_MAX_BYTES = 150 * 1024
    COMPACT_SOURCE_MAX_EDGE = 1600

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images', verbose_name='Товар'
    )
    tag = models.CharField('Тег', max_length=100, blank=True)
    position = models.PositiveIntegerField('Позиция', default=0)
    thumbnail = models.URLField('Thumbnail URL', max_length=1200, blank=True)
    medium = models.URLField('Medium URL', max_length=1200, blank=True)
    large = models.URLField('Large URL', max_length=1200, blank=True)
    original = models.URLField('Original URL', max_length=1200, blank=True)
    source_local_path = models.CharField('Путь из JSON', max_length=1200, blank=True)
    local_image = OptimizedImageField(
        'Загруженное изображение', upload_to='products/%Y/%m/', blank=True,
        help_text='Автоматически сохраняется в WebP, до 1600 px по большей стороне.',
    )
    preview_image = models.ImageField(
        'Оптимизированное превью', upload_to='products/previews/', blank=True
    )
    gallery_image = models.ImageField(
        'Оптимизированное фото галереи', upload_to='products/gallery/', blank=True
    )
    detail_image = models.ImageField(
        'Нормализованное фото карточки', upload_to='products/details/', blank=True
    )
    detail_processed = models.BooleanField(
        'Фото проверено для карточки', default=False
    )
    alt = models.CharField('Alt-текст', max_length=500, blank=True)

    class Meta:
        verbose_name = 'изображение'
        verbose_name_plural = 'изображения'
        ordering = ('position', 'id')

    def __str__(self):
        return self.alt or f'Фото {self.position + 1}'

    @property
    def url(self):
        if self.gallery_image:
            return self.gallery_image.url
        if self.local_image:
            return self.local_image.url
        return self.original or self.large or self.medium or self.thumbnail

    @property
    def preview_url(self):
        if self.preview_image:
            return self.preview_image.url
        return self.url

    @property
    def detail_url(self):
        if self.gallery_image:
            return self.gallery_image.url
        if self.detail_image:
            return self.detail_image.url
        return self.url

    @property
    def card_url(self):
        """Возвращает чёткое фото карточки, оставляя 640 px только fallback-ом."""
        if self._has_compact_webp_source:
            return self.local_image.url
        if self.gallery_image or self.detail_image:
            return self.detail_url
        return self.original_url or self.preview_url

    @property
    def original_url(self):
        """Возвращает локальный исходник либо лучший URL источника."""
        if self.local_image:
            return self.local_image.url
        return self.original or self.large or self.medium or self.thumbnail

    @cached_property
    def _has_compact_webp_source(self):
        """Проверяет, можно ли отдать исходный WebP без лишнего веса."""
        if not self.local_image or not self.local_image.name.lower().endswith('.webp'):
            return False
        try:
            return (
                self.local_image.size <= self.COMPACT_SOURCE_MAX_BYTES
                and max(self.local_image.width, self.local_image.height)
                <= self.COMPACT_SOURCE_MAX_EDGE
            )
        except (FileNotFoundError, OSError, ValueError):
            return False

    @property
    def product_page_url(self):
        """Сохраняет детали компактного WebP, но не отдаёт тяжёлые оригиналы."""
        if self._has_compact_webp_source:
            return self.local_image.url
        return self.detail_url

    @property
    def banner_url(self):
        """Возвращает крупную версию для полноширинных блоков витрины."""
        return self.product_page_url


class WeeklyLook(models.Model):
    """Настройка редакционной подборки на главной странице."""

    is_visible = models.BooleanField('Показывать образ недели', default=True)
    title = models.CharField('Заголовок', max_length=160, default='Образ недели')
    description = models.CharField(
        'Подпись', max_length=300,
        default='Готовая подборка вещей, которые работают вместе.',
    )
    headline = models.CharField('Название образа', max_length=160, default='Тихая роскошь. Четыре акцента.')
    text = models.CharField(
        'Описание образа', max_length=300,
        default='Выберите точку на образе или вещь в списке, чтобы рассмотреть подборку.',
    )

    class Meta:
        verbose_name = 'образ недели'
        verbose_name_plural = 'образ недели'

    def __str__(self):
        return self.title


class WeeklyLookItem(models.Model):
    look = models.ForeignKey(WeeklyLook, on_delete=models.CASCADE, related_name='items')
    catalog_image = models.ForeignKey(
        ProductImage, on_delete=models.CASCADE, related_name='weekly_look_placements',
        verbose_name='Фото товара из каталога',
    )
    uploaded_image = OptimizedImageField(
        'Своё фото товара', upload_to='weekly-looks/%Y/%m/', blank=True,
    )
    position = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'вещь образа недели'
        verbose_name_plural = 'вещи образа недели'
        ordering = ('position', 'id')

    @property
    def image_url(self):
        return self.uploaded_image.url if self.uploaded_image else self.catalog_image.preview_url


class Banner(models.Model):
    """Управляемый рекламный баннер витрины."""

    class Slot(models.TextChoices):
        FALL_WINTER = 'fall-winter', 'Осень–зима'
        BAGS = 'bags', 'Сумки'

    slot = models.SlugField('Расположение', max_length=40, choices=Slot.choices, unique=True)
    image_count = models.PositiveSmallIntegerField(
        'Количество фотографий', default=1,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text='На витрине будут показаны первые фотографии согласно их порядку.',
    )
    interval_seconds = models.PositiveSmallIntegerField(
        'Интервал смены, секунд', default=5,
        validators=[MinValueValidator(1)],
    )
    autoplay = models.BooleanField('Автоматически менять фотографии', default=True)
    personalize_by_gender = models.BooleanField(
        'Учитывать пол посетителя', default=False,
        help_text=(
            'Включите, если для женщин и мужчин должны использоваться разные фотографии.'
        ),
    )
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'баннер'
        verbose_name_plural = 'баннеры'
        ordering = ('id',)

    def __str__(self):
        return self.get_slot_display()


class BannerImage(models.Model):
    """Фото баннера: выбранное из каталога либо загруженное отдельно."""

    class Audience(models.TextChoices):
        ALL = 'all', 'Для всех'
        WOMEN = 'women', 'Для женщин'
        MEN = 'men', 'Для мужчин'

    banner = models.ForeignKey(
        Banner, on_delete=models.CASCADE, related_name='images', verbose_name='Баннер'
    )
    catalog_image = models.ForeignKey(
        ProductImage, on_delete=models.SET_NULL, related_name='banner_placements',
        verbose_name='Фото из каталога', blank=True, null=True,
    )
    uploaded_image = OptimizedImageField(
        'Собственное фото', upload_to='banners/%Y/%m/', blank=True
    )
    audience = models.CharField(
        'Аудитория', max_length=8, choices=Audience.choices, default=Audience.ALL,
        help_text='Определяет, кому показывать этот слайд на персонализированной главной.',
    )
    alt = models.CharField('Описание изображения', max_length=500, blank=True)
    position = models.PositiveSmallIntegerField('Порядок', default=0)

    class Meta:
        verbose_name = 'фотография баннера'
        verbose_name_plural = 'фотографии баннеров'
        ordering = ('position', 'id')

    def __str__(self):
        return self.alt or f'{self.banner}: фото {self.position + 1}'

    @property
    def url(self):
        if self.uploaded_image:
            return self.uploaded_image.url
        return self.catalog_image.url if self.catalog_image_id and self.catalog_image else ''

    @property
    def preview_url(self):
        """Использует лёгкое превью каталога, не уменьшая ручные баннеры."""
        if self.uploaded_image:
            return self.uploaded_image.url
        return (
            self.catalog_image.preview_url
            if self.catalog_image_id and self.catalog_image else ''
        )

    @property
    def banner_url(self):
        """Не растягивает каталожное превью 640 px в крупном баннере."""
        if self.uploaded_image:
            return self.uploaded_image.url
        return (
            self.catalog_image.banner_url
            if self.catalog_image_id and self.catalog_image else ''
        )

    @property
    def effective_alt(self):
        if self.catalog_image_id and self.catalog_image:
            return self.catalog_image.product.title
        return self.banner.get_slot_display()

    @property
    def product(self):
        return self.catalog_image.product if self.catalog_image_id and self.catalog_image else None


class Favorite(models.Model):
    """Постоянное избранное авторизованного покупателя."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='favorite_products', verbose_name='Пользователь',
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='favorited_by', verbose_name='Товар',
    )
    created_at = models.DateTimeField('Добавлен', auto_now_add=True)

    class Meta:
        verbose_name = 'избранный товар'
        verbose_name_plural = 'избранные товары'
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'product'), name='unique_user_favorite_product'
            )
        ]

    def __str__(self):
        return f'{self.user} — {self.product}'


class CartItem(models.Model):
    """Постоянная корзина авторизованного покупателя."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='cart_items', verbose_name='Пользователь',
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='in_carts', verbose_name='Товар',
    )
    quantity = models.PositiveSmallIntegerField(
        'Количество', default=1,
        validators=[MinValueValidator(1), MaxValueValidator(99)],
    )
    size_sku = models.CharField('SKU выбранного размера', max_length=180, blank=True)
    created_at = models.DateTimeField('Добавлен', auto_now_add=True)
    updated_at = models.DateTimeField('Изменён', auto_now=True)

    class Meta:
        verbose_name = 'товар в корзине'
        verbose_name_plural = 'товары в корзинах'
        ordering = ('-updated_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'product'), name='unique_user_cart_product'
            )
        ]

    def __str__(self):
        return f'{self.user} — {self.product} × {self.quantity}'

    @property
    def selected_size(self):
        if not self.size_sku:
            return ''
        return next((
            variant.size for variant in self.product.size_variants.all()
            if variant.sku == self.size_sku
        ), '')

    @property
    def subtotal(self):
        price = self.product.display_price
        return price * self.quantity if price is not None else None


class AnalyticsVisitor(models.Model):
    """Человек: один аккаунт либо один анонимный first-party cookie."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='analytics_visitors', blank=True, null=True,
    )
    first_seen_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'посетитель аналитики'
        verbose_name_plural = 'посетители аналитики'
        constraints = [
            models.UniqueConstraint(
                fields=('user',), condition=models.Q(user__isnull=False),
                name='unique_analytics_visitor_per_user',
            )
        ]


class AnalyticsSession(models.Model):
    class Device(models.TextChoices):
        MOBILE = 'mobile', 'Mobile'
        DESKTOP = 'desktop', 'Desktop'
        TABLET = 'tablet', 'Tablet'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visitor = models.ForeignKey(
        AnalyticsVisitor, on_delete=models.CASCADE, related_name='sessions'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='analytics_sessions', blank=True, null=True,
    )
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_seen_at = models.DateTimeField(auto_now=True, db_index=True)
    landing_page = models.CharField(max_length=1000, blank=True)
    referrer = models.CharField(max_length=1000, blank=True)
    source = models.CharField(max_length=120, default='direct', db_index=True)
    medium = models.CharField(max_length=120, blank=True)
    campaign = models.CharField(max_length=255, blank=True)
    content = models.CharField(max_length=255, blank=True)
    term = models.CharField(max_length=255, blank=True)
    ad_id = models.CharField(max_length=255, blank=True)
    device_type = models.CharField(
        max_length=12, choices=Device.choices, default=Device.DESKTOP,
        db_index=True,
    )
    is_new_visitor = models.BooleanField(default=True, db_index=True)
    page_views = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(default=0)
    engaged = models.BooleanField(default=False, db_index=True)
    cart_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    checkout_stage = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = 'сессия аналитики'
        verbose_name_plural = 'сессии аналитики'
        ordering = ('-started_at',)


class AnalyticsEvent(models.Model):
    session = models.ForeignKey(
        AnalyticsSession, on_delete=models.CASCADE, related_name='events'
    )
    visitor = models.ForeignKey(
        AnalyticsVisitor, on_delete=models.CASCADE, related_name='events'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='analytics_events', blank=True, null=True,
    )
    event_type = models.CharField(max_length=80, db_index=True)
    path = models.CharField(max_length=1000, blank=True, db_index=True)
    referrer_path = models.CharField(max_length=1000, blank=True)
    page_title = models.CharField(max_length=500, blank=True)
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, related_name='analytics_events',
        blank=True, null=True,
    )
    product_sku = models.CharField(max_length=180, blank=True)
    brand = models.CharField(max_length=120, blank=True, db_index=True)
    category = models.CharField(max_length=500, blank=True, db_index=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'событие аналитики'
        verbose_name_plural = 'события аналитики'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('event_type', 'created_at')),
            models.Index(fields=('product', 'event_type', 'created_at')),
            models.Index(fields=('session', 'created_at')),
        ]


class MarketingCampaignSpend(models.Model):
    """Расходы на рекламу для расчёта CPL по атрибуции лидов."""

    period_start = models.DateField('Начало периода')
    period_end = models.DateField('Конец периода')
    source = models.CharField('Источник / UTM source', max_length=120)
    campaign = models.CharField('Кампания', max_length=255, blank=True)
    content = models.CharField('Объявление / content', max_length=255, blank=True)
    ad_id = models.CharField('ID объявления', max_length=255, blank=True)
    amount = models.DecimalField(
        'Расход, ₽', max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'расход рекламной кампании'
        verbose_name_plural = 'расходы рекламных кампаний'
        ordering = ('-period_end', '-id')

    def __str__(self):
        return f'{self.source} / {self.campaign or "без кампании"}: {self.amount} ₽'


class ColorVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='color_variants', verbose_name='Товар'
    )
    sku = models.CharField('SKU варианта', max_length=180)
    color = models.CharField('Цвет', max_length=255, blank=True)
    price = models.DecimalField('Цена', max_digits=14, decimal_places=2, default=0)
    source_url = models.URLField('Ссылка', max_length=1200, blank=True)
    in_stock_ecom = models.BooleanField('В наличии', default=False)
    thumbnail = models.URLField('Превью', max_length=1200, blank=True)

    class Meta:
        verbose_name = 'вариант цвета'
        verbose_name_plural = 'варианты цвета'
        ordering = ('id',)

    def __str__(self):
        return self.color or self.sku


class SizeVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='size_variants', verbose_name='Товар'
    )
    sku = models.CharField('SKU варианта', max_length=180)
    size = models.CharField('Размер', max_length=100)
    price = models.DecimalField('Цена', max_digits=14, decimal_places=2, default=0)
    in_stock_ecom = models.BooleanField('В наличии', default=False)

    class Meta:
        verbose_name = 'вариант размера'
        verbose_name_plural = 'варианты размеров'
        ordering = ('id',)

    def __str__(self):
        return self.size


class ApiRequestNonce(models.Model):
    """Одноразовый nonce подписанного API-запроса для защиты от replay."""

    nonce = models.UUIDField(primary_key=True, editable=False)
    key_id = models.CharField('Идентификатор API-ключа', max_length=80, db_index=True)
    expires_at = models.DateTimeField('Удалить после', db_index=True)
    created_at = models.DateTimeField('Получен', auto_now_add=True)

    class Meta:
        verbose_name = 'nonce API-запроса'
        verbose_name_plural = 'nonce API-запросов'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.key_id}: {self.nonce}'


class ApiIdempotencyRecord(models.Model):
    """Сохранённый ответ изменяющего API-запроса для безопасного retry."""

    key_id = models.CharField('Идентификатор API-ключа', max_length=80)
    idempotency_key = models.UUIDField('Ключ идемпотентности')
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=500)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(blank=True, null=True)
    response_body = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = 'идемпотентный API-запрос'
        verbose_name_plural = 'идемпотентные API-запросы'
        constraints = [
            models.UniqueConstraint(
                fields=('key_id', 'idempotency_key'),
                name='unique_api_idempotency_key_per_client',
            ),
        ]
        indexes = [models.Index(fields=('key_id', 'created_at'))]

    def __str__(self):
        return f'{self.key_id}: {self.idempotency_key}'


class ApiRequestLog(models.Model):
    """Минимальный аудит API без секретов, query string и тела запроса."""

    request_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    key_id = models.CharField(max_length=80, blank=True, db_index=True)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=500)
    status_code = models.PositiveSmallIntegerField()
    result_code = models.CharField(max_length=80, blank=True, db_index=True)
    remote_addr = models.GenericIPAddressField(blank=True, null=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'обращение к API'
        verbose_name_plural = 'обращения к API'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=('key_id', 'created_at')),
            models.Index(fields=('remote_addr', 'created_at')),
        ]

    def __str__(self):
        return f'{self.method} {self.path}: {self.status_code}'


class SecurityChallenge(models.Model):
    """Одноразовая e-mail-проверка для административных операций."""

    class Purpose(models.TextChoices):
        WEB_LOGIN = 'web_login', 'Вход в панель управления'
        TELEGRAM_LOGIN = 'telegram_login', 'Вход в Telegram-панель'
        WEB_ADMIN_GRANT = 'web_admin_grant', 'Назначение web-администратора'
        TELEGRAM_ADMIN_GRANT = 'telegram_admin_grant', 'Назначение Telegram-администратора'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField('Назначение', max_length=32, choices=Purpose.choices, db_index=True)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='security_challenges', verbose_name='Пользователь',
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True,
        related_name='requested_security_challenges', verbose_name='Запросил',
    )
    code_hash = models.CharField('Хеш кода', max_length=255)
    context = models.JSONField('Контекст', default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField('Неверных попыток', default=0)
    expires_at = models.DateTimeField('Действует до', db_index=True)
    consumed_at = models.DateTimeField('Использован', blank=True, null=True, db_index=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'проверка безопасности'
        verbose_name_plural = 'проверки безопасности'
        ordering = ('-created_at',)
        indexes = [models.Index(fields=('purpose', 'target_user', 'created_at'))]

    def __str__(self):
        return f'{self.get_purpose_display()}: {self.target_user}'


class ErrorLog(models.Model):
    """Санитизированный журнал ошибок приложения для диагностики в админке."""

    level = models.CharField('Уровень', max_length=16, db_index=True)
    logger_name = models.CharField('Источник', max_length=255, db_index=True)
    message = models.TextField('Сообщение')
    traceback = models.TextField('Технические сведения', blank=True)
    resolved = models.BooleanField('Обработана', default=False, db_index=True)
    created_at = models.DateTimeField('Дата', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'ошибка приложения'
        verbose_name_plural = 'ошибки приложения'
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.level} · {self.logger_name} · {self.created_at:%d.%m.%Y %H:%M}'


class NotificationEvent(models.Model):
    """Транзакционный outbox для Telegram и будущих каналов доставки."""

    class EventType(models.TextChoices):
        ORDER_CREATED = 'order.created', 'Новый заказ'
        ORDER_STATUS_CHANGED = 'order.status_changed', 'Статус заказа изменён'
        REQUEST_CREATED = 'request.created', 'Новый персональный заказ'
        REQUEST_STATUS_CHANGED = 'request.status_changed', 'Статус заявки изменён'
        BROADCAST = 'broadcast', 'Рассылка'

    class Audience(models.TextChoices):
        ADMIN = 'admin', 'Администратор'
        CUSTOMER = 'customer', 'Клиент'
        BROADCAST = 'broadcast', 'Рассылка'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_key = models.CharField(
        'Ключ дедупликации', max_length=255, unique=True, blank=True, null=True,
    )
    event_type = models.CharField(
        'Тип события', max_length=40, choices=EventType.choices, db_index=True,
    )
    audience = models.CharField(
        'Получатель', max_length=16, choices=Audience.choices, db_index=True,
    )
    channel = models.CharField('Канал', max_length=20, default='telegram')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='notification_events', blank=True, null=True,
    )
    order = models.ForeignKey(
        Order, on_delete=models.SET_NULL, related_name='notification_events',
        blank=True, null=True,
    )
    product_request = models.ForeignKey(
        ProductRequest, on_delete=models.SET_NULL, related_name='notification_events',
        blank=True, null=True,
    )
    payload = models.JSONField('Данные события', default=dict, blank=True)
    available_at = models.DateTimeField('Доступно для доставки', default=timezone.now, db_index=True)
    locked_until = models.DateTimeField('Зарезервировано до', blank=True, null=True, db_index=True)
    locked_by = models.CharField('Зарезервировано сервисом', max_length=80, blank=True)
    lease_id = models.UUIDField('Идентификатор аренды', blank=True, null=True, db_index=True)
    attempts = models.PositiveSmallIntegerField('Попыток доставки', default=0)
    delivered_at = models.DateTimeField('Доставлено', blank=True, null=True, db_index=True)
    last_error = models.CharField('Последняя ошибка', max_length=1000, blank=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'событие уведомления'
        verbose_name_plural = 'события уведомлений'
        ordering = ('created_at', 'id')
        indexes = [
            models.Index(fields=('delivered_at', 'available_at', 'locked_until')),
        ]

    def __str__(self):
        return f'{self.event_type}: {self.id}'
