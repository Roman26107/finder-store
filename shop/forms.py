from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils.text import slugify

from .accounts import normalize_phone, normalize_telegram
from .availability import available_products
from .image_fields import validate_optimized_image
from .models import (
    Banner, BannerImage, CatalogBrand, CatalogCategory, CatalogColor, CatalogMaterial,
    CatalogSize, CollectionBatch, CustomerAddress, CustomerProfile, ExchangeRate, MarketingCampaignSpend,
    Order, PricingRule, Product, ProductImage, ProductRequest, ProductRequestItem,
    SizeProfile, WeeklyLook, WeeklyLookItem,
)


User = get_user_model()


class StoreAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label='E-mail или логин',
        widget=forms.TextInput(attrs={
            'autocomplete': 'username',
            'autofocus': True,
            'placeholder': 'name@example.com',
        }),
    )
    password = forms.CharField(
        label='Пароль',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'current-password',
            'placeholder': 'Введите пароль',
        }),
    )

    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        return value.lower() if '@' in value else value


class RegistrationForm(UserCreationForm):
    first_name = forms.CharField(
        label='Имя', max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'given-name', 'placeholder': 'Ваше имя'}),
    )
    last_name = forms.CharField(
        label='Фамилия', max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'family-name', 'placeholder': 'Ваша фамилия'}),
    )
    email = forms.EmailField(
        label='E-mail',
        widget=forms.EmailInput(attrs={
            'autocomplete': 'email',
            'placeholder': 'name@example.com',
        }),
    )
    phone = forms.CharField(
        label='Телефон', max_length=25,
        widget=forms.TelInput(attrs={
            'autocomplete': 'tel',
            'inputmode': 'tel',
            'placeholder': '+7 999 123-45-67',
            'data-phone-input': '',
        }),
    )
    telegram_registration_token = forms.UUIDField(
        required=False, widget=forms.HiddenInput(),
    )
    telegram_code = forms.RegexField(
        label='Код из Telegram (необязательно)', required=False,
        regex=r'^\d{6}$',
        error_messages={'invalid': 'Код должен состоять из 6 цифр.'},
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code', 'inputmode': 'numeric',
            'maxlength': '6', 'placeholder': '000000',
        }),
    )
    gender = forms.ChoiceField(
        label='Пол (необязательно)', required=False,
        choices=(('', 'Не указывать'), *CustomerProfile.Gender.choices),
        help_text='Необязательно. Поможет показывать подходящие новинки и коллекции.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'autocomplete': 'new-password', 'placeholder': 'Не менее 8 символов',
        })
        self.fields['password2'].widget.attrs.update({
            'autocomplete': 'new-password', 'placeholder': 'Повторите пароль',
        })

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            'first_name', 'last_name', 'email', 'phone',
            'telegram_registration_token', 'telegram_code', 'gender',
            'password1', 'password2',
        )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(
            Q(email__iexact=email) | Q(username__iexact=email)
        ).exists():
            raise forms.ValidationError('Пользователь с таким e-mail уже зарегистрирован.')
        return email

    def clean_phone(self):
        try:
            phone = normalize_phone(self.cleaned_data['phone'])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        if CustomerProfile.objects.filter(phone=phone).exists():
            raise forms.ValidationError('Этот номер телефона уже используется.')
        return phone

    def clean(self):
        cleaned_data = super().clean()
        code = cleaned_data.get('telegram_code')
        token = cleaned_data.get('telegram_registration_token')
        self.telegram_registration_session = None
        if not code:
            return cleaned_data
        if not token:
            self.add_error(
                'telegram_code',
                'Сессия Telegram не найдена. Обновите страницу и запустите бота снова.',
            )
            return cleaned_data
        from telegram_bot.linking import verify_registration_code

        session, message = verify_registration_code(token, code)
        if session is None:
            self.add_error('telegram_code', message)
        else:
            self.telegram_registration_session = session
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.username = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    phone = forms.CharField(
        label='Телефон', max_length=25,
        widget=forms.TelInput(attrs={
            'autocomplete': 'tel',
            'inputmode': 'tel',
            'placeholder': '+7 999 123-45-67',
            'data-phone-input': '',
        }),
    )
    delivery_address = forms.CharField(
        label='Адрес доставки (необязательно)', required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'autocomplete': 'street-address',
            'placeholder': 'Город, улица, дом, квартира',
        }),
    )
    preferred_contact = forms.ChoiceField(
        label='Предпочтительный способ связи',
        choices=CustomerProfile.ContactMethod.choices,
        required=False,
    )
    gender = forms.ChoiceField(
        label='Пол (необязательно)', required=False,
        choices=(('', 'Не указывать'), *CustomerProfile.Gender.choices),
        help_text='Используется для персонализации новинок и коллекций на главной.',
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email')
        labels = {'first_name': 'Имя', 'last_name': 'Фамилия', 'email': 'E-mail'}
        widgets = {
            'first_name': forms.TextInput(attrs={'autocomplete': 'given-name', 'placeholder': 'Ваше имя'}),
            'last_name': forms.TextInput(attrs={'autocomplete': 'family-name', 'placeholder': 'Ваша фамилия'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'name@example.com'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_email = self.instance.email
        self.original_username = self.instance.username
        profile = getattr(self.instance, 'customer_profile', None)
        if profile:
            self.fields['phone'].initial = profile.phone or ''
            self.fields['delivery_address'].initial = profile.delivery_address
            self.fields['preferred_contact'].initial = profile.preferred_contact
            self.fields['gender'].initial = profile.gender

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(
            Q(email__iexact=email) | Q(username__iexact=email)
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Этот e-mail уже используется другим пользователем.')
        return email

    def clean_phone(self):
        try:
            phone = normalize_phone(self.cleaned_data['phone'])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        if CustomerProfile.objects.filter(phone=phone).exclude(user=self.instance).exists():
            raise forms.ValidationError('Этот номер телефона уже используется.')
        return phone

    def clean_preferred_contact(self):
        return self.cleaned_data.get('preferred_contact') or CustomerProfile.ContactMethod.PHONE

    def save(self, commit=True):
        user = super().save(commit=False)
        # Для зарегистрированных через витрину username совпадает с e-mail.
        # Административные аккаунты с отдельным логином не переименовываем.
        if self.original_username == self.original_email:
            user.username = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class CustomerAddressForm(forms.ModelForm):
    class Meta:
        model = CustomerAddress
        fields = ('label', 'city', 'street', 'apartment', 'postal_code', 'is_default')
        labels = {
            'label': 'Название адреса',
            'city': 'Город',
            'street': 'Улица и дом',
            'apartment': 'Квартира или офис',
            'postal_code': 'Индекс',
            'is_default': 'Сделать основным',
        }
        widgets = {
            'label': forms.TextInput(attrs={'placeholder': 'Дом или офис'}),
            'city': forms.TextInput(attrs={'autocomplete': 'address-level2'}),
            'street': forms.TextInput(attrs={'autocomplete': 'address-line1'}),
            'apartment': forms.TextInput(attrs={'autocomplete': 'address-line2'}),
            'postal_code': forms.TextInput(attrs={
                'autocomplete': 'postal-code', 'inputmode': 'numeric',
            }),
        }

    def clean_city(self):
        city = self.cleaned_data['city'].strip()
        if len(city) < 2:
            raise forms.ValidationError('Укажите город.')
        return city

    def clean_street(self):
        street = self.cleaned_data['street'].strip()
        if len(street) < 3:
            raise forms.ValidationError('Укажите улицу и дом.')
        return street


class EmailVerificationForm(forms.Form):
    code = forms.RegexField(
        label='Код из письма',
        regex=r'^\d{6}$',
        error_messages={'invalid': 'Код должен состоять из 6 цифр.'},
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'maxlength': '6',
            'placeholder': '000000',
        }),
    )


class SecurityCodeForm(EmailVerificationForm):
    code = forms.RegexField(
        label='Код безопасности', regex=r'^\d{6}$',
        error_messages={'invalid': 'Код должен состоять из 6 цифр.'},
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code', 'inputmode': 'numeric',
            'maxlength': '6', 'placeholder': '000000',
        }),
    )


class TelegramLinkCodeForm(SecurityCodeForm):
    code = forms.RegexField(
        label='Код из Telegram', regex=r'^\d{6}$',
        error_messages={'invalid': 'Код должен состоять из 6 цифр.'},
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code', 'inputmode': 'numeric',
            'maxlength': '6', 'placeholder': '000000',
        }),
    )


class CheckoutForm(forms.Form):
    DELIVERY_PRICES = {
        Order.DeliveryMethod.COURIER: Decimal('590'),
        Order.DeliveryMethod.EXPRESS: Decimal('1490'),
        Order.DeliveryMethod.INTERNATIONAL: Decimal('3900'),
    }
    delivery_address = forms.CharField(
        label='Адрес доставки',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'autocomplete': 'street-address',
            'placeholder': 'Укажите город, улицу, дом, квартиру и индекс при необходимости',
        }),
    )
    preferred_contact = forms.ChoiceField(
        label='Как менеджеру связаться с вами',
        choices=CustomerProfile.ContactMethod.choices,
        widget=forms.RadioSelect,
    )
    delivery_method = forms.ChoiceField(
        label='Способ доставки', choices=Order.DeliveryMethod.choices,
        required=False, widget=forms.RadioSelect,
    )
    payment_method = forms.ChoiceField(
        label='Способ оплаты', choices=Order.PaymentMethod.choices,
        required=False, widget=forms.RadioSelect,
    )
    promo_code = forms.CharField(
        label='Промокод', required=False, max_length=40,
        widget=forms.TextInput(attrs={
            'placeholder': 'Промокод', 'autocomplete': 'off',
        }),
    )

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        if profile is not None:
            self.fields['delivery_address'].initial = profile.delivery_address
            self.fields['preferred_contact'].initial = profile.preferred_contact
        else:
            self.fields['preferred_contact'].initial = CustomerProfile.ContactMethod.PHONE
        self.fields['delivery_method'].initial = Order.DeliveryMethod.COURIER
        self.fields['payment_method'].initial = Order.PaymentMethod.CARD

    def clean_delivery_address(self):
        address = self.cleaned_data['delivery_address'].strip()
        if len(address) < 8:
            raise forms.ValidationError('Укажите полный адрес доставки.')
        return address

    def clean_preferred_contact(self):
        method = self.cleaned_data['preferred_contact']
        if (
            self.profile is not None
            and method == CustomerProfile.ContactMethod.TELEGRAM
            and not self.profile.telegram
        ):
            raise forms.ValidationError(
                'Добавьте Telegram в личном кабинете или выберите другой способ связи.'
            )
        return method

    def clean_promo_code(self):
        code = self.cleaned_data.get('promo_code', '').strip().upper()
        if code and code != 'WELCOME10':
            raise forms.ValidationError('Промокод не найден.')
        return code


class ProductRequestItemForm(forms.ModelForm):
    class Meta:
        model = ProductRequestItem
        fields = (
            'brand', 'product_name', 'article', 'color', 'size',
        )
        labels = {
            'brand': 'Бренд',
            'product_name': 'Название товара',
            'article': 'Артикул',
            'color': 'Цвет',
            'size': 'Размер',
        }
        widgets = {
            'brand': forms.TextInput(attrs={
                'placeholder': 'Например, Hermès',
                'autocomplete': 'organization',
            }),
            'product_name': forms.TextInput(attrs={
                'placeholder': 'Например, Kelly 25',
                'autocomplete': 'off',
            }),
            'article': forms.TextInput(attrs={
                'placeholder': 'Необязательно',
                'autocomplete': 'off',
            }),
            'color': forms.TextInput(attrs={
                'placeholder': 'Необязательно',
                'autocomplete': 'off',
            }),
            'size': forms.TextInput(attrs={
                'placeholder': 'Необязательно',
                'autocomplete': 'off',
            }),
        }


class ProductRequestForm(forms.Form):
    query = forms.CharField(
        label='Что найти', max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Бренд, модель, цвет, размер',
            'autocomplete': 'off',
        }),
    )
    customer_name = forms.CharField(
        label='Имя', max_length=301,
        widget=forms.TextInput(attrs={
            'autocomplete': 'name',
            'placeholder': 'Ваше имя',
        }),
    )
    contact = forms.CharField(
        label='Телефон, e-mail или Telegram', max_length=254,
        widget=forms.TextInput(attrs={
            'autocomplete': 'email',
            'placeholder': '+7 ... или @username',
        }),
    )
    reference_image = forms.ImageField(
        label='Фото (по желанию)', required=False,
        validators=[validate_optimized_image],
        widget=forms.ClearableFileInput(attrs={
            'accept': 'image/jpeg,image/png,image/webp,image/gif',
            'data-request-photo': '',
        }),
    )
    consent = forms.BooleanField(
        label='Согласен на обработку персональных данных',
        error_messages={'required': 'Подтвердите согласие на обработку персональных данных.'},
    )

    def clean_query(self):
        value = self.cleaned_data['query'].strip()
        if len(value) < 3:
            raise forms.ValidationError('Опишите вещь подробнее.')
        return value

    def clean_customer_name(self):
        return self.cleaned_data['customer_name'].strip()

    def clean_contact(self):
        value = self.cleaned_data['contact'].strip()
        if '@' in value and not value.startswith('@'):
            email = value.lower()
            try:
                validate_email(email)
            except ValidationError as exc:
                raise forms.ValidationError('Введите корректный e-mail.') from exc
            self.cleaned_data['contact_method'] = CustomerProfile.ContactMethod.EMAIL
            self.cleaned_data['normalized_contact'] = email
            return value
        if value.startswith('@') or 't.me/' in value.lower() or 'telegram.me/' in value.lower():
            try:
                telegram = normalize_telegram(value)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
            self.cleaned_data['contact_method'] = CustomerProfile.ContactMethod.TELEGRAM
            self.cleaned_data['normalized_contact'] = telegram
            return value
        try:
            phone = normalize_phone(value)
        except ValueError as exc:
            raise forms.ValidationError(
                'Введите телефон, e-mail или Telegram в формате @username.'
            ) from exc
        self.cleaned_data['contact_method'] = CustomerProfile.ContactMethod.PHONE
        self.cleaned_data['normalized_contact'] = phone
        return value


class BaseProductRequestItemFormSet(forms.BaseFormSet):
    default_error_messages = {
        **forms.BaseFormSet.default_error_messages,
        'too_few_forms': 'Укажите хотя бы один товар.',
        'too_many_forms': 'В одной заявке можно указать не более 10 товаров.',
    }


ProductRequestItemFormSet = forms.formset_factory(
    ProductRequestItemForm,
    formset=BaseProductRequestItemFormSet,
    # min_num создаёт одну начальную форму. extra должен оставаться равным
    # нулю, иначе Django сразу отрисует вторую пустую позицию.
    extra=0,
    min_num=1,
    max_num=10,
    validate_min=True,
    validate_max=True,
    can_delete=True,
)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        accept = kwargs.pop('accept', '.json,application/json')
        kwargs.setdefault('widget', MultipleFileInput(attrs={'accept': accept}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)]


class CollectionBatchForm(forms.ModelForm):
    class Meta:
        model = CollectionBatch
        fields = ('name',)
        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'Например, Поступление 16 сентября',
                'autocomplete': 'off',
            }),
        }


class CollectionDraftForm(forms.Form):
    title = forms.CharField(
        label='Название товара', max_length=500,
        widget=forms.TextInput(attrs={
            'placeholder': 'Например, сумка Kelly 25', 'autocomplete': 'off',
            'data-duplicate-check': 'title',
        }),
    )
    brand = forms.CharField(
        label='Бренд', max_length=120,
        widget=forms.TextInput(attrs={
            'list': 'collection-brand-options', 'autocomplete': 'off',
            'placeholder': 'Начните вводить бренд',
        }),
    )
    article = forms.CharField(
        label='Артикул', max_length=180,
        widget=forms.TextInput(attrs={
            'placeholder': 'Например, H123456', 'autocomplete': 'off',
            'data-duplicate-check': 'article',
        }),
    )
    description = forms.CharField(
        label='Краткое описание', required=False,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Ключевые особенности, состояние и комплектация',
        }),
    )
    images = MultipleFileField(
        label='Фотографии', required=False,
        accept='image/jpeg,image/png,image/webp,image/gif',
        help_text='Можно выбрать несколько изображений. Порядок сохранится.',
    )

    def clean_brand(self):
        value = self.cleaned_data['brand'].strip()
        reference = CatalogBrand.objects.filter(
            Q(name__iexact=value) | Q(slug__iexact=value), is_active=True,
        ).first()
        if not reference:
            raise forms.ValidationError(
                'Выберите бренд из справочника или сначала добавьте его.'
            )
        self.cleaned_data['brand_reference'] = reference
        return reference.name

    def clean_article(self):
        return self.cleaned_data['article'].strip()

    def clean_title(self):
        return self.cleaned_data['title'].strip()


CollectionDraftFormSet = forms.formset_factory(
    CollectionDraftForm,
    extra=3,
    min_num=1,
    max_num=20,
    validate_min=True,
    validate_max=True,
    can_delete=True,
)


class ProductImportForm(forms.Form):
    json_files = MultipleFileField(
        label='JSON-файлы',
        help_text='Можно выбрать сразу несколько файлов. Каждый файл может содержать карточку или массив карточек.',
    )
    replace_relations = forms.BooleanField(
        label='Полностью заменить изображения и варианты при обновлении',
        required=False,
        initial=True,
    )


class ProductZipImportForm(forms.Form):
    zip_files = MultipleFileField(
        label='ZIP-архивы',
        accept='.zip,application/zip,application/x-zip-compressed',
        help_text=(
            'Можно выбрать несколько архивов. JSON-файлы будут импортированы, '
            'а изображения из local_path — распакованы и привязаны к товарам.'
        ),
    )
    replace_relations = forms.BooleanField(
        label='Полностью заменить изображения и варианты при обновлении',
        required=False,
        initial=True,
    )
    extract_images = forms.BooleanField(
        label='Распаковать и использовать локальные изображения',
        required=False,
        initial=True,
    )

    def clean_zip_files(self):
        files = self.cleaned_data['zip_files']
        max_size = 200 * 1024 * 1024
        for uploaded_file in files:
            if uploaded_file.size > max_size:
                raise forms.ValidationError(
                    f'{uploaded_file.name}: архив больше допустимых 200 МБ.'
                )
            if not uploaded_file.name.lower().endswith('.zip'):
                raise forms.ValidationError(f'{uploaded_file.name}: требуется файл .zip.')
        return files


class ExchangeRateForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.currency == ExchangeRate.Currency.RUB:
            self.initial['rubles_per_unit'] = Decimal('1')
            self.fields['rubles_per_unit'].disabled = True
            self.fields['rubles_per_unit'].help_text = 'Курс рубля всегда равен 1.'

    class Meta:
        model = ExchangeRate
        fields = ('rubles_per_unit', 'rate_date', 'source')
        widgets = {
            'rubles_per_unit': forms.NumberInput(attrs={'min': '0.000001', 'step': '0.000001'}),
            'rate_date': forms.DateInput(attrs={'type': 'date'}),
        }


ExchangeRateFormSet = forms.modelformset_factory(
    ExchangeRate,
    form=ExchangeRateForm,
    fields=('rubles_per_unit', 'rate_date', 'source'),
    extra=0,
)


class PricingRuleForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        if cleaned.get('adjustment_type') != PricingRule.AdjustmentType.PERCENT:
            cleaned['calculate_from_initial'] = False
        return cleaned

    class Meta:
        model = PricingRule
        fields = (
            'position', 'name', 'adjustment_type', 'value',
            'calculate_from_initial', 'is_active',
        )
        widgets = {
            'position': forms.NumberInput(attrs={'min': 0}),
            'value': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
        }


PricingRuleFormSet = forms.modelformset_factory(
    PricingRule,
    form=PricingRuleForm,
    fields=(
        'position', 'name', 'adjustment_type', 'value',
        'calculate_from_initial', 'is_active',
    ),
    extra=1,
    can_delete=True,
)


class PricePreviewForm(forms.Form):
    amount = forms.DecimalField(
        label='Цена товара', min_value=0, decimal_places=2, max_digits=14,
        initial=10000,
    )
    currency = forms.ChoiceField(
        label='Исходная валюта', choices=ExchangeRate.Currency.choices,
        initial=ExchangeRate.Currency.CNY,
    )


class ProfileSizeSelect(forms.SelectMultiple):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-profile'] = str(instance.profile_id)
        return option


class BackofficeProductForm(forms.ModelForm):
    GENDER_CHOICES = (
        ('women', 'Женский'),
        ('men', 'Мужской'),
        ('unisex', 'Унисекс'),
    )
    sku = forms.CharField(required=False, widget=forms.HiddenInput)
    brand = forms.CharField(
        label='Бренд', widget=forms.TextInput(attrs={
            'list': 'brand-options', 'autocomplete': 'off', 'placeholder': 'Начните вводить бренд',
        })
    )
    current_color = forms.CharField(
        label='Точный оттенок', required=False, widget=forms.TextInput(attrs={
            'list': 'color-options', 'autocomplete': 'off', 'placeholder': 'Например, Чёрный',
        })
    )
    color_group_reference = forms.ChoiceField(
        label='Основной цвет', required=False, choices=(),
        help_text='Понятная группа для фильтра на витрине.',
    )
    gender_choice = forms.ChoiceField(
        label='Пол', choices=GENDER_CHOICES, initial='unisex', required=False
    )
    category_reference = forms.CharField(
        label='Категория', required=False, widget=forms.TextInput(attrs={
            'list': 'category-options', 'autocomplete': 'off',
            'placeholder': 'Начните вводить категорию',
        })
    )
    materials = forms.ModelMultipleChoiceField(
        label='Материалы', required=False,
        queryset=CatalogMaterial.objects.none(),
        widget=forms.SelectMultiple(attrs={'data-searchable-multiple': 'materials'}),
    )
    selected_sizes = forms.ModelMultipleChoiceField(
        label='Размерный ряд', required=False,
        queryset=CatalogSize.objects.none(), widget=ProfileSizeSelect(attrs={
            'data-size-values': '', 'size': 8,
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['materials'].queryset = CatalogMaterial.objects.filter(is_active=True)
        self.fields['selected_sizes'].queryset = CatalogSize.objects.filter(
            is_active=True, profile__is_active=True
        ).select_related('profile')
        self.fields['size_profile'].queryset = SizeProfile.objects.filter(is_active=True)
        self.fields['size_profile'].empty_label = 'Без размерного ряда'
        from .catalog_data import COLOR_GROUPS
        self.fields['color_group_reference'].choices = [
            ('', 'Не указан'),
            *((slug, name) for slug, name, _, _ in COLOR_GROUPS),
        ]
        available_currencies = list(
            ExchangeRate.objects.order_by('currency').values_list('currency', flat=True)
        )
        labels = dict(ExchangeRate.Currency.choices)
        self.fields['currency'].widget = forms.Select(choices=[
            (currency, labels.get(currency, currency)) for currency in available_currencies
        ])
        self.fields['sku_without_size'].required = True
        self.fields['sku_without_size'].label = 'Артикул'
        self.fields['sku_without_size'].widget.attrs.update({
            'placeholder': 'Например, H123456', 'autocomplete': 'off',
            'data-duplicate-check': 'article',
        })
        self.fields['title'].widget.attrs.update({
            'autocomplete': 'off', 'data-duplicate-check': 'title',
        })
        self.fields['price'].required = False
        self.fields['pricing_mode'].widget.attrs['data-pricing-mode'] = ''
        self.fields['fixed_price_rub'].widget.attrs.update({
            'data-fixed-price': '', 'placeholder': 'Например, 250000', 'step': '1',
        })
        if self.instance and self.instance.pk:
            brand = CatalogBrand.objects.filter(slug=self.instance.brand).first()
            self.initial['brand'] = brand.name if brand else self.instance.brand
            self.initial['color_group_reference'] = self.instance.color_group
            current_genders = set(self.instance.gender or [])
            self.initial['gender_choice'] = (
                'unisex' if {'women', 'men'}.issubset(current_genders)
                else ('women' if 'women' in current_genders else 'men' if 'men' in current_genders else 'unisex')
            )
            self.initial['materials'] = CatalogMaterial.objects.filter(
                name__in=self.instance.material or []
            )
            key = self.instance.category_path_key
            category = CatalogCategory.objects.filter(key=key).first() if key else None
            if category is None and self.instance.category_name:
                category = CatalogCategory.objects.filter(
                    name=self.instance.category_name,
                    department_slug=self.instance.category_department_slug,
                ).first()
            if category:
                self.initial['category_reference'] = self.category_label(category)
            self.initial['selected_sizes'] = CatalogSize.objects.filter(
                profile=self.instance.size_profile,
                label__in=self.instance.size_variants.values_list('size', flat=True),
            ) if self.instance.size_profile_id else CatalogSize.objects.none()

    @staticmethod
    def category_label(category):
        return f'{category.department_name} / {category.name}' if category.department_name else category.name

    def clean_brand(self):
        value = self.cleaned_data['brand'].strip()
        reference = CatalogBrand.objects.filter(Q(name__iexact=value) | Q(slug__iexact=value), is_active=True).first()
        if not reference:
            raise forms.ValidationError('Выберите бренд из справочника или сначала добавьте его.')
        self.cleaned_data['brand_reference'] = reference
        return reference.slug

    def clean_current_color(self):
        value = self.cleaned_data.get('current_color', '').strip()
        if not value:
            self.cleaned_data['color_reference'] = None
            return ''
        normalized = value.casefold().replace('ё', 'е')
        reference = next((
            item for item in CatalogColor.objects.filter(is_active=True)
            if item.name.casefold().replace('ё', 'е') == normalized
        ), None)
        if not reference:
            raise forms.ValidationError('Выберите цвет из справочника или сначала добавьте его.')
        self.cleaned_data['color_reference'] = reference
        return reference.name

    def clean_category_reference(self):
        value = self.cleaned_data.get('category_reference', '').strip()
        if not value:
            return None
        references = [
            category for category in CatalogCategory.objects.filter(is_active=True)
            if self.category_label(category).casefold() == value.casefold()
        ]
        if len(references) != 1:
            raise forms.ValidationError('Выберите точную категорию из выпадающего списка.')
        return references[0]

    def clean_currency(self):
        value = self.cleaned_data.get('currency', '').strip().upper()
        if not ExchangeRate.objects.filter(currency=value).exists():
            raise forms.ValidationError('Выберите валюту из доступных курсов.')
        return value

    def clean(self):
        cleaned = super().clean()
        profile = cleaned.get('size_profile')
        sizes = cleaned.get('selected_sizes')
        if sizes and (not profile or any(item.profile_id != profile.pk for item in sizes)):
            self.add_error('selected_sizes', 'Выберите размеры только из указанного типа размерного ряда.')
        if cleaned.get('pricing_mode') == Product.PricingMode.CALCULATED and cleaned.get('price') is None:
            self.add_error('price', 'Укажите исходную цену для расчёта по курсу.')
        elif cleaned.get('price') is None:
            cleaned['price'] = Decimal('0')
        if cleaned.get('pricing_mode') == Product.PricingMode.FIXED_RUB and cleaned.get('fixed_price_rub') is None:
            self.add_error('fixed_price_rub', 'Укажите фиксированную цену в рублях.')
        article = (cleaned.get('sku_without_size') or '').strip()
        if article:
            cleaned['sku_without_size'] = article
            # SKU — внутренний ключ. Для ручной карточки он создаётся из бренда
            # и артикула, поэтому сотруднику не нужно разбираться в этом поле.
            if not self.instance.pk and not cleaned.get('sku'):
                base = f"manual:{cleaned.get('brand', 'product')}:{article}"
                candidate = base
                number = 2
                existing = Product.objects.all()
                if self.instance.pk:
                    existing = existing.exclude(pk=self.instance.pk)
                while existing.filter(sku=candidate).exists():
                    candidate = f'{base}:{number}'
                    number += 1
                cleaned['sku'] = candidate
        return cleaned

    def save(self, commit=True):
        product = super().save(commit=False)
        if not product.sku:
            product.sku = self.cleaned_data['sku']
        product.material = [item.name for item in self.cleaned_data.get('materials', [])]
        color = self.cleaned_data.get('color_reference')
        product.color_group = self.cleaned_data.get('color_group_reference') or (
            color.group_slug if color else ''
        )
        gender = self.cleaned_data.get('gender_choice')
        if not gender:
            legacy_gender = str(self.data.get('gender', '')).casefold()
            gender = 'women' if 'women' in legacy_gender else 'men' if 'men' in legacy_gender else 'unisex'
        product.gender = ['women', 'men'] if gender == 'unisex' else [gender]
        category = self.cleaned_data.get('category_reference')
        if category:
            product.category_name = category.name
            product.category_department_slug = category.department_slug
            product.category_department_name = category.department_name
            product.category_path_key = category.key
            product.category_keys = [category.key]
            product.category_path = category.path or [{
                'slug': category.key.rsplit('/', 1)[-1],
                'name': category.name,
                'level': 2,
                'key': category.key,
                'parent_key': category.department_slug,
            }]
            product.product_code = category.key
        if product.pricing_mode == Product.PricingMode.CALCULATED:
            product.fixed_price_rub = None
        if commit:
            product.save()
            if product.size_profile_id:
                chosen_sizes = list(self.cleaned_data.get('selected_sizes', []))
                chosen_labels = [item.label for item in chosen_sizes]
                product.size_variants.exclude(size__in=chosen_labels).delete()
                existing = set(product.size_variants.values_list('size', flat=True))
                for size in chosen_sizes:
                    if size.label not in existing:
                        product.size_variants.create(
                            sku=f'{product.sku}-{slugify(size.label) or size.pk}',
                            size=size.label, price=product.price,
                            in_stock_ecom=product.in_stock_ecom,
                        )
                if chosen_labels and product.current_size == 'SANS_TAILLE':
                    product.current_size = chosen_labels[0]
                    product.save(update_fields=('current_size',))
        return product

    class Meta:
        model = Product
        fields = (
            'title', 'brand', 'sku', 'sku_without_size', 'source_url',
            'price', 'currency', 'pricing_mode', 'fixed_price_rub',
            'current_color', 'current_size', 'size_profile',
            'collection', 'description', 'dimensions', 'made_in',
            'in_stock_ecom', 'is_active', 'is_featured',
        )
        widgets = {
            'pricing_mode': forms.RadioSelect,
            'description': forms.Textarea(attrs={'rows': 6}),
            'dimensions': forms.Textarea(attrs={'rows': 3}),
        }


class BackofficeProductImageFormSetBase(forms.BaseInlineFormSet):
    ordering_widget = forms.HiddenInput

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active_count = sum(
            1 for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get('DELETE')
            and (form.cleaned_data.get('local_image') or form.cleaned_data.get('original') or form.instance.pk)
        )
        # Сохраняем карточки с превышенным лимитом фото, но не даём добавлять новые.
        allowed = max(10, self.initial_form_count())
        if active_count > allowed:
            raise forms.ValidationError(f'Можно сохранить не более {allowed} фотографий.')

    def save(self, commit=True):
        instances = super().save(commit=commit)
        if not commit:
            return instances
        position = 0
        for form in self.ordered_forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE') or not form.instance.pk:
                continue
            image = form.instance
            image.position = position
            image.alt = self.instance.title
            image.tag = 'main' if position == 0 else 'gallery'
            image.save(update_fields=('position', 'alt', 'tag'))
            if 'local_image' in form.changed_data:
                from .image_processing import create_product_image_preview
                create_product_image_preview(image, force=True)
            position += 1
        return instances


class BackofficeProductImageForm(forms.ModelForm):
    def has_changed(self):
        if self.instance.pk:
            return super().has_changed()
        # Скрытый ORDER заполняется JavaScript даже у единственной пустой строки.
        # Без выбранного файла такая строка не должна создавать пустую запись.
        return bool(self.files.get(self.add_prefix('local_image')))

    class Meta:
        model = ProductImage
        fields = ('local_image',)
        widgets = {
            'local_image': forms.FileInput(attrs={
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
            }),
        }


BackofficeProductImageFormSet = forms.inlineformset_factory(
    Product,
    ProductImage,
    form=BackofficeProductImageForm,
    formset=BackofficeProductImageFormSetBase,
    fields=('local_image',),
    extra=1,
    can_delete=True,
    can_order=True,
    max_num=10,
    validate_max=False,
)


class WeeklyLookForm(forms.ModelForm):
    class Meta:
        model = WeeklyLook
        fields = ('is_visible', 'title', 'description', 'headline', 'text')


class WeeklyLookItemForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ids = []
        if self.instance.catalog_image_id:
            ids.append(self.instance.catalog_image_id)
        if self.is_bound:
            posted_id = self.data.get(self.add_prefix('catalog_image'))
            if posted_id and str(posted_id).isdigit():
                ids.append(int(posted_id))
        self.fields['catalog_image'].queryset = ProductImage.objects.filter(
            pk__in=ids,
            product_id__in=available_products(Product.objects.filter(
                is_active=True, catalog_scope=Product.CatalogScope.STORE,
            )).values('pk'),
        ).select_related('product')

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('DELETE') and cleaned.get('uploaded_image') and not cleaned.get('catalog_image'):
            self.add_error('catalog_image', 'Выберите товар для загруженного фото.')
        return cleaned

    class Meta:
        model = WeeklyLookItem
        fields = ('catalog_image', 'uploaded_image')
        widgets = {
            'catalog_image': forms.HiddenInput(attrs={'data-banner-catalog-image': ''}),
            'uploaded_image': forms.FileInput(attrs={
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
            }),
        }


class WeeklyLookItemFormSetBase(forms.BaseInlineFormSet):
    ordering_widget = forms.HiddenInput

    def save(self, commit=True):
        instances = super().save(commit=commit)
        if commit:
            for position, form in enumerate(self.ordered_forms):
                if form.cleaned_data and not form.cleaned_data.get('DELETE') and form.instance.pk:
                    form.instance.position = position
                    form.instance.save(update_fields=('position',))
        return instances


WeeklyLookItemFormSet = forms.inlineformset_factory(
    WeeklyLook, WeeklyLookItem, form=WeeklyLookItemForm,
    formset=WeeklyLookItemFormSetBase, extra=1,
    can_order=True, can_delete=True, max_num=4, validate_max=True,
)


class BannerForm(forms.ModelForm):
    class Meta:
        model = Banner
        fields = ('image_count', 'interval_seconds', 'autoplay', 'personalize_by_gender')

class BannerImageForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Пустые дополнительные строки formset должны оставаться необязательными.
        self.fields['audience'].required = False
        # В HTML оставляем только выбранное фото; остальные ищутся через API.
        allowed_ids = []
        if self.instance.catalog_image_id:
            allowed_ids.append(self.instance.catalog_image_id)
        if self.is_bound:
            posted_id = self.data.get(self.add_prefix('catalog_image'))
            if posted_id and str(posted_id).isdigit():
                allowed_ids.append(int(posted_id))
        self.fields['catalog_image'].queryset = ProductImage.objects.filter(
            pk__in=allowed_ids,
            product_id__in=available_products(Product.objects.filter(
                is_active=True, catalog_scope=Product.CatalogScope.STORE,
            )).values('pk'),
        ).select_related('product')

    def has_changed(self):
        if self.instance.pk:
            return super().has_changed()
        catalog_key = self.add_prefix('catalog_image')
        upload_key = self.add_prefix('uploaded_image')
        if not self.data.get(catalog_key) and not self.files.get(upload_key):
            return False
        return super().has_changed()

    def clean(self):
        cleaned = super().clean()
        cleaned['audience'] = cleaned.get('audience') or BannerImage.Audience.ALL
        if cleaned.get('DELETE'):
            return cleaned
        if not cleaned.get('catalog_image') and not cleaned.get('uploaded_image'):
            return cleaned
        return cleaned

    class Meta:
        model = BannerImage
        fields = ('catalog_image', 'uploaded_image', 'audience')
        widgets = {
            'catalog_image': forms.HiddenInput(attrs={'data-banner-catalog-image': ''}),
            'uploaded_image': forms.FileInput(attrs={
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
            }),
            'audience': forms.RadioSelect,
        }


class BannerImageFormSetBase(forms.BaseInlineFormSet):
    ordering_widget = forms.HiddenInput

    def save(self, commit=True):
        instances = super().save(commit=commit)
        if not commit:
            return instances
        position = 0
        personalize = self.instance.personalize_by_gender
        for form in self.ordered_forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE') or not form.instance.pk:
                continue
            image = form.instance
            image.position = position
            image.audience = (
                form.cleaned_data.get('audience') or BannerImage.Audience.ALL
                if personalize else BannerImage.Audience.ALL
            )
            image.alt = (
                image.catalog_image.product.title
                if image.catalog_image_id else self.instance.get_slot_display()
            )
            image.save(update_fields=('position', 'audience', 'alt'))
            position += 1
        return instances


BannerImageFormSet = forms.inlineformset_factory(
    Banner,
    BannerImage,
    form=BannerImageForm,
    formset=BannerImageFormSetBase,
    extra=1,
    can_delete=True,
    can_order=True,
    max_num=20,
    validate_max=True,
)


class BackofficeOrderForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        revenue = cleaned.get('actual_revenue')
        cost = cleaned.get('cost_of_goods')
        if revenue is not None and cost is not None and cost > revenue:
            self.add_error(
                'cost_of_goods',
                'Себестоимость выше выручки. Проверьте сумму или сохраните, если убыток корректен, через Django Admin.',
            )
        return cleaned

    class Meta:
        model = Order
        fields = ('status', 'actual_revenue', 'cost_of_goods', 'paid_at', 'manager_comment')
        widgets = {
            'actual_revenue': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'cost_of_goods': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
            'paid_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'manager_comment': forms.Textarea(attrs={'rows': 5}),
        }


class BackofficeProductRequestForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Совместимость со старыми действиями, отправляющими только рабочий статус.
        self.fields['lead_status'].required = False

    def clean_lead_status(self):
        return self.cleaned_data.get('lead_status') or self.instance.lead_status

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get('lead_status') == ProductRequest.LeadStatus.REJECTED
            and not cleaned.get('loss_reason')
        ):
            self.add_error('loss_reason', 'Укажите причину отказа.')
        if cleaned.get('lead_status') != ProductRequest.LeadStatus.REJECTED:
            cleaned['loss_reason'] = ''
            cleaned['loss_reason_note'] = ''
        return cleaned

    class Meta:
        model = ProductRequest
        fields = (
            'lead_status', 'status', 'estimated_value', 'converted_order',
            'loss_reason', 'loss_reason_note', 'manager_comment',
        )
        widgets = {'manager_comment': forms.Textarea(attrs={'rows': 5})}


class MarketingCampaignSpendForm(forms.ModelForm):
    class Meta:
        model = MarketingCampaignSpend
        fields = (
            'period_start', 'period_end', 'source', 'campaign',
            'content', 'ad_id', 'amount',
        )
        widgets = {
            'period_start': forms.DateInput(attrs={'type': 'date'}),
            'period_end': forms.DateInput(attrs={'type': 'date'}),
            'amount': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('period_start') and cleaned.get('period_end'):
            if cleaned['period_end'] < cleaned['period_start']:
                self.add_error('period_end', 'Конец периода не может быть раньше начала.')
        return cleaned


class SlugFromNameMixin:
    slug_max_length = 80

    def clean_slug(self):
        value = self.cleaned_data.get('slug', '').strip()
        if value:
            return value
        generated = slugify(self.cleaned_data.get('name', ''))[:self.slug_max_length]
        if not generated:
            raise forms.ValidationError('Укажите код латиницей.')
        return generated


class CatalogBrandForm(SlugFromNameMixin, forms.ModelForm):
    slug = forms.SlugField(label='Код', required=False, help_text='Можно оставить пустым.')

    class Meta:
        model = CatalogBrand
        fields = ('name', 'slug', 'image', 'is_active')
        help_texts = {
            'image': 'Автоматически сохраняется в WebP, до 1600 px по большей стороне.',
        }


class CatalogColorForm(SlugFromNameMixin, forms.ModelForm):
    slug = forms.SlugField(label='Код', required=False, help_text='Можно оставить пустым.')
    group_slug = forms.ChoiceField(label='Группа цвета', choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .catalog_data import COLOR_GROUPS
        self.fields['group_slug'].choices = [(slug, name) for slug, name, _, _ in COLOR_GROUPS]

    class Meta:
        model = CatalogColor
        fields = ('name', 'slug', 'group_slug', 'swatch')
        widgets = {'swatch': forms.TextInput(attrs={'type': 'color'})}


class CatalogMaterialForm(SlugFromNameMixin, forms.ModelForm):
    slug_max_length = 100
    slug = forms.SlugField(label='Код', required=False, help_text='Можно оставить пустым.')

    class Meta:
        model = CatalogMaterial
        fields = ('name', 'slug')


class CatalogCategoryForm(forms.ModelForm):
    class Meta:
        model = CatalogCategory
        fields = ('name', 'key', 'department_slug', 'department_name')
        labels = {
            'key': 'Ключ категории', 'department_slug': 'Код раздела',
            'department_name': 'Название раздела',
        }
        widgets = {
            'key': forms.TextInput(attrs={'placeholder': 'women/bags'}),
            'department_slug': forms.TextInput(attrs={'placeholder': 'women'}),
            'department_name': forms.TextInput(attrs={'placeholder': 'Женщины'}),
        }

    def save(self, commit=True):
        category = super().save(commit=False)
        category.path = [{
            'slug': category.key.rsplit('/', 1)[-1], 'name': category.name,
            'level': max(2, len([part for part in category.key.split('/') if part])),
            'key': category.key,
            'parent_key': category.key.rsplit('/', 1)[0] if '/' in category.key else category.department_slug,
        }]
        if commit:
            category.save()
        return category


class SizeProfileForm(SlugFromNameMixin, forms.ModelForm):
    slug_max_length = 100
    slug = forms.SlugField(label='Код', required=False, help_text='Можно оставить пустым.')
    sizes = forms.CharField(
        label='Размеры', help_text='Перечислите через запятую, например: XS, S, M, L',
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    class Meta:
        model = SizeProfile
        fields = ('name', 'slug', 'product_type', 'gender')

    def save(self, commit=True):
        profile = super().save(commit=commit)
        if commit:
            labels = [value.strip() for value in self.cleaned_data['sizes'].split(',') if value.strip()]
            for index, label in enumerate(dict.fromkeys(labels)):
                CatalogSize.objects.get_or_create(
                    profile=profile, label=label, defaults={'position': index}
                )
        return profile


class CatalogSizeForm(forms.ModelForm):
    class Meta:
        model = CatalogSize
        fields = ('profile', 'label')
        labels = {'label': 'Новый размер'}