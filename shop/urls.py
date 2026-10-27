from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import api, backoffice, views

app_name = 'shop'

urlpatterns = [
    path('api/v1/catalog/products/', api.catalog_products, name='api_catalog_products'),
    path(
        'api/v1/catalog/products/<int:product_id>/',
        api.catalog_product_detail, name='api_catalog_product_detail',
    ),
    path(
        'api/v1/catalog/products/<int:product_id>/commercial/',
        api.catalog_product_update, name='api_catalog_product_update',
    ),
    path(
        'api/v1/catalog/commercial/bulk/',
        api.catalog_bulk_update, name='api_catalog_bulk_update',
    ),
    path('api/v1/orders/', api.orders, name='api_orders'),
    path('api/v1/orders/<str:number>/', api.order_detail, name='api_order_detail'),
    path(
        'api/v1/telegram/administrators/', api.telegram_administrators,
        name='api_telegram_administrators',
    ),
    path(
        'api/v1/telegram/accounts/<int:chat_id>/', api.telegram_account,
        name='api_telegram_account',
    ),
    path(
        'api/v1/telegram/link/claim/', api.telegram_link_claim,
        name='api_telegram_link_claim',
    ),
    path(
        'api/v1/telegram/registration/claim/', api.telegram_registration_claim,
        name='api_telegram_registration_claim',
    ),
    path(
        'api/v1/telegram/admin/<int:chat_id>/challenge/',
        api.telegram_admin_challenge, name='api_telegram_admin_challenge',
    ),
    path(
        'api/v1/telegram/admin/<int:chat_id>/verify/',
        api.telegram_admin_verify, name='api_telegram_admin_verify',
    ),
    path(
        'api/v1/telegram/manager-contacts/', api.manager_contacts,
        name='api_manager_contacts',
    ),
    path(
        'api/v1/telegram/customers/<int:user_id>/', api.telegram_customer_recipient,
        name='api_telegram_customer_recipient',
    ),
    path(
        'api/v1/customer/orders/', api.customer_order_history,
        name='api_customer_order_history',
    ),
    path(
        'api/v1/customer/orders/<str:number>/', api.customer_order_detail,
        name='api_customer_order_detail',
    ),
    path(
        'api/v1/admin/dashboard/', api.admin_dashboard,
        name='api_admin_dashboard',
    ),
    path(
        'api/v1/admin/orders/', api.admin_order_history,
        name='api_admin_order_history',
    ),
    path(
        'api/v1/admin/orders/<str:number>/', api.admin_order_detail,
        name='api_admin_order_detail',
    ),
    path(
        'api/v1/admin/orders/<str:number>/status/', api.admin_order_status,
        name='api_admin_order_status',
    ),
    path(
        'api/v1/notifications/claim/',
        api.notification_claim, name='api_notification_claim',
    ),
    path(
        'api/v1/notifications/<uuid:event_id>/ack/',
        api.notification_ack, name='api_notification_ack',
    ),
    path(
        'api/v1/notifications/<uuid:event_id>/deliveries/',
        api.notification_deliveries, name='api_notification_deliveries',
    ),
    path(
        'api/v1/notifications/<uuid:event_id>/deliveries/telegram/',
        api.notification_recipient_delivered,
        name='api_notification_recipient_delivered',
    ),
    path('api/v1/broadcasts/', api.broadcast_create, name='api_broadcast_create'),
    path('control/', backoffice.dashboard, name='control_dashboard'),
    path('control/updates/', backoffice.catalog_updates, name='control_catalog_updates'),
    path('control/products/', backoffice.product_list, name='control_products'),
    path('control/products/new/', backoffice.product_create, name='control_product_create'),
    path('control/products/similar/', backoffice.product_duplicate_search, name='control_product_duplicates'),
    path('control/products/<int:pk>/', backoffice.product_edit, name='control_product_edit'),
    path('control/collection/', backoffice.collection_list, name='control_collection'),
    path(
        'control/collection/new/', backoffice.collection_product_create,
        name='control_collection_product_create',
    ),
    path(
        'control/collection/bulk/', backoffice.collection_bulk_create,
        name='control_collection_bulk',
    ),
    path(
        'control/collection/batches/<int:pk>/', backoffice.collection_batch,
        name='control_collection_batch',
    ),
    path('control/dictionaries/', backoffice.dictionaries, name='control_dictionaries'),
    path('control/brands/<int:pk>/', backoffice.brand_edit, name='control_brand_edit'),
    path('control/banners/', backoffice.banners, name='control_banners'),
    path('control/weekly-look/', backoffice.weekly_look_edit, name='control_weekly_look'),
    path('control/banners/<int:pk>/', backoffice.banner_edit, name='control_banner_edit'),
    path('control/banner-images/search/', backoffice.banner_image_search, name='control_banner_image_search'),
    path('control/users/', backoffice.user_list, name='control_users'),
    path('control/users/<int:pk>/', backoffice.user_detail, name='control_user_detail'),
    path(
        'control/contacts/', backoffice.manager_contacts,
        name='control_manager_contacts',
    ),
    path(
        'control/telegram/', backoffice.telegram_accounts,
        name='control_telegram_accounts',
    ),
    path('control/errors/', backoffice.error_logs, name='control_error_logs'),
    path(
        'control/users/<int:pk>/grant/<str:grant_type>/',
        backoffice.admin_grant_start, name='control_admin_grant_start',
    ),
    path(
        'control/admin-grant/verify/', backoffice.admin_grant_verify,
        name='control_admin_grant_verify',
    ),
    path('control/import/', backoffice.product_import, name='control_import'),
    path('control/orders/', backoffice.order_list, name='control_orders'),
    path('control/orders/<int:pk>/', backoffice.order_detail, name='control_order_detail'),
    path('control/requests/', backoffice.request_list, name='control_requests'),
    path('control/requests/<int:pk>/', backoffice.request_detail, name='control_request_detail'),
    path('control/pricing/', backoffice.pricing, name='control_pricing'),
    path('control/analytics/', backoffice.analytics_dashboard, name='control_analytics'),
    path(
        'control/analytics/marketing/', backoffice.analytics_dashboard,
        {'section': 'marketing'}, name='control_analytics_marketing',
    ),
    path(
        'control/analytics/catalog/', backoffice.analytics_dashboard,
        {'section': 'catalog'}, name='control_analytics_catalog',
    ),
    path(
        'control/analytics/behavior/', backoffice.analytics_dashboard,
        {'section': 'behavior'}, name='control_analytics_behavior',
    ),
    path(
        'control/analytics/sales/', backoffice.analytics_dashboard,
        {'section': 'sales'}, name='control_analytics_sales',
    ),
    path(
        'control/analytics/finance/', backoffice.analytics_dashboard,
        {'section': 'finance'}, name='control_analytics_finance',
    ),
    path('analytics/events/', views.analytics_event, name='analytics_event'),
    path('', views.home, name='home'),
    path('catalog/', views.catalog, name='catalog'),
    path('collection/', views.collection, name='collection'),
    path('stores/', views.stores, name='stores'),
    path('stores/hermes/', views.hermes_catalog, name='hermes_catalog'),
    path('stores/miu-miu/', views.miumiu_catalog, name='miumiu_catalog'),
    path('stores/<slug:brand_slug>/', views.brand_catalog, name='brand_catalog'),
    path('request-product/', views.product_request_create, name='product_request_create'),
    path('account/requests/', views.product_request_list, name='product_request_list'),
    path('account/requests/<int:pk>/', views.product_request_detail, name='product_request_detail'),
    path('account/orders/', views.order_list, name='orders'),
    path('account/orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('account/orders/<int:pk>/repeat/', views.order_repeat, name='order_repeat'),
    path('account/login/', views.account_login, name='login'),
    path('account/admin-verification/', views.admin_access_verify, name='admin_access_verify'),
    path(
        'account/password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='shop/account/password_reset.html',
            email_template_name='shop/account/password_reset_email.txt',
            html_email_template_name='shop/account/password_reset_email.html',
            subject_template_name='shop/account/password_reset_subject.txt',
            success_url=reverse_lazy('shop:password_reset_done'),
        ), name='password_reset',
    ),
    path(
        'account/password-reset/sent/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='shop/account/password_reset_done.html',
        ), name='password_reset_done',
    ),
    path(
        'account/password-reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='shop/account/password_reset_confirm.html',
            success_url=reverse_lazy('shop:password_reset_complete'),
        ), name='password_reset_confirm',
    ),
    path(
        'account/password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='shop/account/password_reset_complete.html',
        ), name='password_reset_complete',
    ),
    path('account/register/', views.account_register, name='register'),
    path('account/', views.account, name='account'),
    path('account/addresses/add/', views.account_address_add, name='account_address_add'),
    path('account/addresses/<int:pk>/edit/', views.account_address_edit, name='account_address_edit'),
    path('account/addresses/<int:pk>/default/', views.account_address_default, name='account_address_default'),
    path('account/addresses/<int:pk>/delete/', views.account_address_delete, name='account_address_delete'),
    path('account/telegram/connect/', views.telegram_link_start, name='telegram_link_start'),
    path('account/telegram/confirm/', views.telegram_link, name='telegram_link'),
    path('account/verify-email/', views.verify_email, name='verify_email'),
    path('account/verify-email/resend/', views.resend_email_verification, name='resend_email_verification'),
    path('account/password/', views.account_password, name='account_password'),
    path('account/logout/', views.account_logout, name='logout'),
    path('product/<int:product_id>/quick-view/', views.product_quick_view, name='product_quick_view'),
    path('product/<int:pk>/<slug:slug>/', views.product_detail, name='product_detail'),
    path('favorites/', views.favorite_detail, name='favorites'),
    path('favorites/toggle/<int:product_id>/', views.favorite_toggle, name='favorite_toggle'),
    path('your-things/', views.things_panel, name='things_panel'),
    path('cart/', views.cart_detail, name='cart'),
    path('cart/checkout/', views.checkout, name='checkout'),
    path('cart/checkout/success/<int:pk>/', views.order_success, name='order_success'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/update/<int:product_id>/', views.cart_update, name='cart_update'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
]