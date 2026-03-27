import os
import smtplib
import ssl
from email.message import EmailMessage


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"MISSING_ENV_{name}")
    return value


def send_access_email(*, to_email: str, order_id: str, product_name: str, access_url: str) -> None:
    host = _require_env("SMTP_HOST")
    port = int(_require_env("SMTP_PORT"))
    user = _require_env("SMTP_USER")
    password = _require_env("SMTP_PASS")
    mail_from = _require_env("SMTP_FROM")

    subject = f"Доступ к покупке: {product_name}"

    text = "\n".join(
        [
            "Спасибо за покупку.",
            "",
            f"Номер заказа: {order_id}",
            f"Товар: {product_name}",
            "",
            "Ссылка для входа в канал:",
            access_url,
        ]
    )

    html = (
        "<p>Спасибо за покупку.</p>"
        f"<p><b>Номер заказа:</b> {escape_html(order_id)}</p>"
        f"<p><b>Товар:</b> {escape_html(product_name)}</p>"
        f'<p><b>Ссылка для входа в канал:</b><br/><a href="{escape_attr(access_url)}">{escape_html(access_url)}</a></p>'
    )

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host=host, port=port, context=context) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(host=host, port=port) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)


def escape_html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def escape_attr(value: str) -> str:
    return escape_html(value)

