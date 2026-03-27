import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_lock = asyncio.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Order:
    id: str
    phone: str
    email: str
    product_name: str
    price: float
    currency: str
    offer_id: str
    status: str
    created_at: str
    contract_id: str | None
    payment_url: str | None
    paid_at: str | None
    email_sent_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phone": self.phone,
            "email": self.email,
            "product_name": self.product_name,
            "price": self.price,
            "currency": self.currency,
            "offer_id": self.offer_id,
            "status": self.status,
            "created_at": self.created_at,
            "contract_id": self.contract_id,
            "payment_url": self.payment_url,
            "paid_at": self.paid_at,
            "email_sent_at": self.email_sent_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Order":
        return Order(
            id=str(data.get("id") or ""),
            phone=str(data.get("phone") or ""),
            email=str(data.get("email") or ""),
            product_name=str(data.get("product_name") or ""),
            price=float(data.get("price") or 0),
            currency=str(data.get("currency") or ""),
            offer_id=str(data.get("offer_id") or ""),
            status=str(data.get("status") or ""),
            created_at=str(data.get("created_at") or ""),
            contract_id=(str(data["contract_id"]) if data.get("contract_id") else None),
            payment_url=(str(data["payment_url"]) if data.get("payment_url") else None),
            paid_at=(str(data["paid_at"]) if data.get("paid_at") else None),
            email_sent_at=(str(data["email_sent_at"]) if data.get("email_sent_at") else None),
        )


def _data_file() -> Path:
    data_dir = Path(os.getcwd()) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "orders.json"


async def _read_all() -> dict[str, Any]:
    fp = _data_file()
    if not fp.exists():
        fp.write_text(json.dumps({"orders": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    raw = fp.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"orders": []}

    if not isinstance(parsed, dict):
        return {"orders": []}
    if not isinstance(parsed.get("orders"), list):
        return {"orders": []}
    return parsed


async def _write_all(state: dict[str, Any]) -> None:
    fp = _data_file()
    tmp = fp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)


async def create_order(order: Order) -> Order:
    async with _lock:
        state = await _read_all()
        orders = [Order.from_dict(o) for o in state["orders"]]
        if any(o.id == order.id for o in orders):
            raise ValueError("ORDER_ID_ALREADY_EXISTS")

        orders.append(order)
        await _write_all({"orders": [o.to_dict() for o in orders]})
        return order


async def get_order(order_id: str) -> Order | None:
    async with _lock:
        state = await _read_all()
        for o in state["orders"]:
            order = Order.from_dict(o)
            if order.id == order_id:
                return order
        return None


async def update_order(order_id: str, updater: Callable[[Order], Order]) -> Order | None:
    async with _lock:
        state = await _read_all()
        orders = [Order.from_dict(o) for o in state["orders"]]
        idx = next((i for i, o in enumerate(orders) if o.id == order_id), None)
        if idx is None:
            return None
        orders[idx] = updater(orders[idx])
        await _write_all({"orders": [o.to_dict() for o in orders]})
        return orders[idx]


async def find_order_by_contract_id(contract_id: str) -> Order | None:
    async with _lock:
        state = await _read_all()
        for o in state["orders"]:
            order = Order.from_dict(o)
            if order.contract_id == contract_id:
                return order
        return None


def new_order(
    *,
    order_id: str,
    phone: str,
    email: str,
    product_name: str,
    price: float,
    currency: str,
    offer_id: str,
) -> Order:
    return Order(
        id=order_id,
        phone=phone,
        email=email,
        product_name=product_name,
        price=price,
        currency=currency,
        offer_id=offer_id,
        status="created",
        created_at=_utc_now_iso(),
        contract_id=None,
        payment_url=None,
        paid_at=None,
        email_sent_at=None,
    )


def mark_paid(order: Order) -> Order:
    if order.status in ("paid", "email_sent"):
        return order
    return Order(
        **{
            **order.to_dict(),
            "status": "paid",
            "paid_at": _utc_now_iso(),
        }
    )


def mark_email_sent(order: Order) -> Order:
    if order.status == "email_sent":
        return order
    return Order(
        **{
            **order.to_dict(),
            "status": "email_sent",
            "email_sent_at": _utc_now_iso(),
        }
    )

