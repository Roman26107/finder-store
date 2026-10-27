"""Автономные брендированные ответы для пользовательских HTTP-ошибок."""

from django.conf import settings
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string


ERROR_TITLES = {
    400: 'Некорректный запрос',
    403: 'Доступ ограничен',
    404: 'Страница не найдена',
    429: 'Слишком много запросов',
    500: 'Что-то пошло не так',
}


def render_error_page(status_code, *, retry_after=None, retry_url=None):
    """Рендерит страницу без RequestContext и запросов context processors к БД."""
    context = {
        'status_code': status_code,
        'error_title': ERROR_TITLES[status_code],
        'retry_after': retry_after,
        'retry_url': retry_url or '/',
    }
    content = render_to_string(f'{status_code}.html', context)
    response = HttpResponse(content, status=status_code, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    if retry_after is not None:
        response['Retry-After'] = str(retry_after)
    return response


def bad_request(request, exception=None):
    return render_error_page(400)


def permission_denied(request, exception=None):
    return render_error_page(403)


def csrf_failure(request, reason=''):
    return render_error_page(403)


def page_not_found(request, exception=None):
    return render_error_page(404)


def too_many_requests(request, retry_after=60):
    return render_error_page(429, retry_after=retry_after)


def server_error(request):
    retry_url = request.get_full_path() if request is not None else '/'
    return render_error_page(500, retry_url=retry_url)


def preview(request, status_code):
    """Локальный просмотр страниц; маршрут подключается только при DEBUG=True."""
    if not settings.DEBUG or status_code not in ERROR_TITLES:
        raise Http404
    if status_code == 429:
        return too_many_requests(request, retry_after=60)
    if status_code == 500:
        return server_error(request)
    return render_error_page(status_code)