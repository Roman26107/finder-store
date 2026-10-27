STATUS_ICONS = {
    'new': '🆕',
    'processing': '⚙️',
    'confirmation': '🕐',
    'purchased': '💳',
    'shipping': '🚚',
    'offer_ready': '🎁',
    'completed': '✅',
    'cancelled': '❌',
    'contacted': '📞',
    'qualified': '⭐',
    'unqualified': '⛔',
    'thinking': '🤔',
    'offer': '📨',
    'order': '🛍',
    'rejected': '❌',
}


def status_icon(value):
    return STATUS_ICONS.get(str(value or ''), '•')