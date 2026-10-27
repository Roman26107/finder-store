from html import escape


def finder_code_email_html(*, heading, intro, code, notice):
    """Возвращает автономную HTML-версию транзакционного письма с кодом."""
    return f'''<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(heading)}</title>
</head>
<body style="margin:0;padding:0;background:#e3e4e1;color:#242524;font-family:Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#e3e4e1;">
        <tr>
            <td align="center" style="padding:32px 16px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#f0f1ee;border:1px solid #c3c5c2;">
                    <tr>
                        <td style="padding:30px 32px 12px;font-family:Georgia,serif;font-size:24px;letter-spacing:7px;">FINDER</td>
                    </tr>
                    <tr>
                        <td style="padding:18px 32px 32px;">
                            <h1 style="margin:0 0 16px;font-family:Georgia,serif;font-size:26px;font-weight:500;line-height:1.25;">{escape(heading)}</h1>
                            <p style="margin:0 0 24px;font-size:15px;line-height:1.6;">{escape(intro)}</p>
                            <div style="margin:0 0 24px;padding:18px;background:#242524;color:#ffffff;font-family:Arial,sans-serif;font-size:30px;font-weight:600;letter-spacing:8px;text-align:center;">{escape(code)}</div>
                            <p style="margin:0;color:#555957;font-size:13px;line-height:1.6;">{escape(notice)}</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''