import os
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.emailer import send_access_email
from app.lava_client import LavaApiError, create_invoice_v3
from app.storage import (
    find_order_by_contract_id,
    get_order,
    mark_email_sent,
    mark_paid,
    new_order,
    update_order,
    create_order,
)


load_dotenv(override=True)

app = FastAPI()

PUBLIC_DIR = Path(os.getcwd()) / "public"


class CheckoutCreateRequest(BaseModel):
    orderId: str
    phone: str
    email: str


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"MISSING_ENV_{name}")
    return value


def _product_config() -> dict:
    name = os.getenv("PRODUCT_NAME") or "Метод: Формула Блога"
    price = float(os.getenv("PRODUCT_PRICE") or "0")
    currency = (os.getenv("PRODUCT_CURRENCY") or "RUB").upper()
    return {"name": name, "price": price, "currency": currency}


def _is_valid_order_id(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{16,64}", value or ""))


def _is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value or ""))


def _is_valid_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value or "")
    return 10 <= len(digits) <= 15


def _demo_enabled() -> bool:
    v = (os.getenv("PAYMENT_MODE") or "").strip().lower()
    return v in ("demo", "mock")


def _is_email_ready() -> bool:
    required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM", "ACCESS_URL")
    return all(bool(os.getenv(k)) for k in required)


@app.get("/api/checkout/new")
async def checkout_new():
    return {"orderId": str(uuid.uuid4()), "product": _product_config()}


@app.post("/api/checkout/create")
async def checkout_create(payload: CheckoutCreateRequest):
    order_id = payload.orderId.strip()
    phone = payload.phone.strip()
    email = payload.email.strip().lower()

    if not _is_valid_order_id(order_id):
        raise HTTPException(status_code=400, detail="Ошибка заказа. Обновите страницу и попробуйте ещё раз.")
    if not _is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="Заполните поле номера телефона корректно.")
    if not _is_valid_email(email):
        raise HTTPException(status_code=400, detail="Заполните поле e-mail корректно.")

    product = _product_config()
    offer_id = os.getenv("LAVA_OFFER_ID") or "DEMO_OFFER"

    try:
        await create_order(
            new_order(
                order_id=order_id,
                phone=phone,
                email=email,
                product_name=product["name"],
                price=product["price"],
                currency=product["currency"],
                offer_id=offer_id,
            )
        )
    except ValueError as e:
        if str(e) == "ORDER_ID_ALREADY_EXISTS":
            existing = await get_order(order_id)
            if existing and existing.payment_url:
                return {"orderId": existing.id, "paymentUrl": existing.payment_url}
            raise HTTPException(status_code=409, detail="Этот заказ уже создан. Обновите страницу и попробуйте ещё раз.") from e
        raise

    if _demo_enabled():
        contract_id = f"demo:{order_id}"
        payment_url = f"/demo-pay.html?orderId={order_id}"
        await update_order(
            order_id,
            lambda o: o.__class__(
                **{
                    **o.to_dict(),
                    "contract_id": contract_id,
                    "payment_url": payment_url,
                }
            ),
        )
        return {"orderId": order_id, "paymentUrl": payment_url}

    try:
        _require_env("LAVA_OFFER_ID")
        invoice = await create_invoice_v3(email=email, offer_id=offer_id, currency=product["currency"])
    except LavaApiError as e:
        code = str(e.args[0] or "")
        if code == "LAVA_API_ERROR":
            raise HTTPException(status_code=502, detail="Сервис оплаты временно недоступен. Попробуйте позже.") from e
        if code == "LAVA_NETWORK_ERROR":
            raise HTTPException(status_code=502, detail="Нет соединения с сервисом оплаты. Попробуйте позже.") from e
        if code == "LAVA_INVALID_JSON":
            raise HTTPException(status_code=502, detail="Ошибка ответа сервиса оплаты. Попробуйте позже.") from e
        if code == "MISSING_ENV_LAVA_API_KEY":
            raise HTTPException(status_code=500, detail="Оплата не настроена. Обратитесь в поддержку.") from e
        raise HTTPException(status_code=502, detail="Сервис оплаты временно недоступен. Попробуйте позже.") from e
    except RuntimeError as e:
        if str(e) == "MISSING_ENV_LAVA_OFFER_ID":
            raise HTTPException(
                status_code=500,
                detail="Оплата не настроена. Не указан идентификатор товара (LAVA_OFFER_ID).",
            ) from e
        raise HTTPException(status_code=500, detail="Оплата временно недоступна. Обратитесь в поддержку.") from e
    contract_id = str(invoice.get("id") or "")
    payment_url = invoice.get("paymentUrl")
    if not contract_id or not payment_url:
        raise HTTPException(status_code=502, detail="Не удалось получить ссылку на оплату. Попробуйте позже.")

    await update_order(
        order_id,
        lambda o: o.__class__(
            **{
                **o.to_dict(),
                "contract_id": contract_id,
                "payment_url": payment_url,
            }
        ),
    )

    return {"orderId": order_id, "paymentUrl": payment_url}


@app.post("/api/demo/confirm")
async def demo_confirm(request: Request):
    if not _demo_enabled():
        raise HTTPException(status_code=404)

    payload = await request.json()
    order_id = str(payload.get("orderId") or "").strip()
    if not _is_valid_order_id(order_id):
        raise HTTPException(status_code=400, detail="Ошибка заказа. Обновите страницу и попробуйте ещё раз.")

    order = await get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден. Обновите страницу и попробуйте ещё раз.")

    updated_paid = await update_order(order.id, lambda o: mark_paid(o))
    if not updated_paid:
        raise HTTPException(status_code=500, detail="Не удалось обновить заказ. Попробуйте ещё раз.")

    emailed = False
    if updated_paid.status == "paid" and _is_email_ready():
        try:
            access_url = _require_env("ACCESS_URL")
            send_access_email(
                to_email=updated_paid.email,
                order_id=updated_paid.id,
                product_name=updated_paid.product_name,
                access_url=access_url,
            )
            await update_order(updated_paid.id, lambda o: mark_email_sent(o))
            emailed = True
        except Exception:
            emailed = False

    return JSONResponse({"ok": True, "emailed": emailed})


@app.get("/api/order/{order_id}")
async def order_status(order_id: str):
    if not _is_valid_order_id(order_id):
        raise HTTPException(status_code=400, detail="INVALID_ORDER_ID")
    order = await get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="ORDER_NOT_FOUND")
    return {
        "id": order.id,
        "status": order.status,
        "email": order.email,
        "phone": order.phone,
        "productName": order.product_name,
        "price": order.price,
        "currency": order.currency,
        "createdAt": order.created_at,
        "paidAt": order.paid_at,
        "emailSentAt": order.email_sent_at,
    }


@app.post("/api/webhook/lava")
async def lava_webhook(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    expected = os.getenv("LAVA_WEBHOOK_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")

    payload = await request.json()
    event_type = str(payload.get("eventType") or "")
    status = str(payload.get("status") or "")
    contract_id = str(payload.get("contractId") or "")

    if event_type != "payment.success":
        return JSONResponse({"ok": True})
    if status != "completed":
        return JSONResponse({"ok": True})
    if not contract_id:
        return JSONResponse({"ok": True})

    order = await find_order_by_contract_id(contract_id)
    if not order:
        return JSONResponse({"ok": True})
    if order.status == "email_sent":
        return JSONResponse({"ok": True})

    access_url = _require_env("ACCESS_URL")

    updated_paid = await update_order(order.id, lambda o: mark_paid(o))
    if not updated_paid:
        return JSONResponse({"ok": True})
    if updated_paid.status != "paid":
        return JSONResponse({"ok": True})

    send_access_email(
        to_email=updated_paid.email,
        order_id=updated_paid.id,
        product_name=updated_paid.product_name,
        access_url=access_url,
    )

    await update_order(updated_paid.id, lambda o: mark_email_sent(o))
    return JSONResponse({"ok": True})


@app.get("/")
async def index():
    fp = PUBLIC_DIR / "index.html"
    if not fp.exists():
        raise HTTPException(status_code=404)
    return FileResponse(fp)


@app.get("/{path:path}")
async def static_files(path: str):
    candidate = (PUBLIC_DIR / path).resolve()
    public_root = PUBLIC_DIR.resolve()
    if not str(candidate).startswith(str(public_root)):
        raise HTTPException(status_code=404)
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(candidate)
