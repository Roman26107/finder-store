import logging
import re
import traceback


SECRET_PATTERN = re.compile(
    r'(?i)(password|passwd|token|secret|authorization|cookie|code)(\s*[=:]\s*)([^\s,;]+)'
)


def _sanitize(value, limit):
    text = SECRET_PATTERN.sub(r'\1\2[REDACTED]', str(value or ''))
    return text[:limit]


class DatabaseErrorHandler(logging.Handler):
    """Best-effort запись ERROR/CRITICAL без рекурсии и секретных значений."""

    def emit(self, record):
        if record.name.startswith('shop.logging_handlers'):
            return
        try:
            from .models import ErrorLog

            trace = ''
            if record.exc_info:
                trace = ''.join(traceback.format_exception(*record.exc_info))
            ErrorLog.objects.create(
                level=record.levelname[:16],
                logger_name=record.name[:255],
                message=_sanitize(record.getMessage(), 8000),
                traceback=_sanitize(trace, 30000),
            )
        except Exception:
            # Logging никогда не должен ломать исходный запрос или создавать рекурсию.
            pass