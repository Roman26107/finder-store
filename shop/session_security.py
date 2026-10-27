from importlib import import_module

from django.conf import settings


def invalidate_user_sessions(user_id):
    """Удаляет активные сессии пользователя после изменения его привилегий."""
    engine = import_module(settings.SESSION_ENGINE)
    for session in engine.SessionStore.get_model_class().objects.all().iterator():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get('_auth_user_id')) == str(user_id):
            session.delete()