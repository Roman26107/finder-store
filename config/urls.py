"""Маршруты витрины, панели управления и служебных страниц."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from shop import views as shop_views
from shop import error_views

admin.site.site_header = 'FINDER — управление магазином'
admin.site.site_title = 'FINDER Admin'
admin.site.index_title = 'Управление каталогом'

urlpatterns = [
    path('healthz/', shop_views.healthcheck, name='healthcheck'),
    path('admin/login/', shop_views.admin_login_redirect, name='admin_login_redirect'),
    path('admin/', admin.site.urls),
    path('', include('shop.urls')),
]

if settings.DEBUG:
    urlpatterns += [
        path('__errors__/<int:status_code>/', error_views.preview, name='error_preview'),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


handler400 = error_views.bad_request
handler403 = error_views.permission_denied
handler404 = error_views.page_not_found
handler500 = error_views.server_error
