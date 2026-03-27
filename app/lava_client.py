import os
from typing import Any

import httpx


class LavaApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"MISSING_ENV_{name}")
    return value


def _base_url() -> str:
    return os.getenv("LAVA_BASE_URL") or "https://gate.lava.top"


async def create_invoice_v3(*, email: str, offer_id: str, currency: str) -> dict[str, Any]:
    try:
        api_key = _require_env("LAVA_API_KEY")
    except RuntimeError as e:
        raise LavaApiError(str(e)) from e
    url = f"{_base_url().rstrip('/')}/api/v3/invoice"

    payload: dict[str, Any] = {
        "email": email,
        "offerId": offer_id,
        "currency": currency.upper(),
        "buyerLanguage": (os.getenv("LAVA_BUYER_LANGUAGE") or "RU").upper(),
    }

    payment_provider = os.getenv("LAVA_PAYMENT_PROVIDER")
    if payment_provider:
        payload["paymentProvider"] = payment_provider

    payment_method = os.getenv("LAVA_PAYMENT_METHOD")
    if payment_method:
        payload["paymentMethod"] = payment_method

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(url, json=payload, headers={"X-Api-Key": api_key})
        except httpx.RequestError as e:
            raise LavaApiError("LAVA_NETWORK_ERROR") from e

        if r.status_code < 200 or r.status_code >= 300:
            raise LavaApiError("LAVA_API_ERROR", status_code=r.status_code, body=r.text)

        try:
            return r.json()
        except ValueError as e:
            raise LavaApiError("LAVA_INVALID_JSON") from e
