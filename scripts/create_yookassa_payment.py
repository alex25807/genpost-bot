import json
import os
import uuid
from urllib import request

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    shop_id = os.getenv("YOOKASSA_SHOP_ID", "").strip()
    secret_key = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
    return_url = os.getenv("SUBSCRIPTION_RETURN_URL", "https://t.me").strip()
    payment_desc = os.getenv("SUBSCRIPTION_PAYMENT_DESCRIPTION", "Подписка на ГенПост").strip()
    amount_rub = float(os.getenv("SUBSCRIPTION_PRICE_RUB", "990").strip() or "990")
    period_days = int(os.getenv("SUBSCRIPTION_DEFAULT_DAYS", "30").strip() or "30")
    chat_id = os.getenv("TARGET_CHAT_ID", "").strip()

    if not shop_id or not secret_key:
        raise RuntimeError("Set YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY in .env")
    if not chat_id:
        raise RuntimeError("Set TARGET_CHAT_ID in .env (Telegram chat id of customer)")

    payload = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "description": payment_desc,
        "metadata": {
            "chat_id": chat_id,
            "period_days": str(period_days),
        },
    }
    body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        "https://api.yookassa.ru/v3/payments",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Idempotence-Key": str(uuid.uuid4()),
        },
        method="POST",
    )
    credentials = f"{shop_id}:{secret_key}".encode("utf-8")
    import base64

    req.add_header("Authorization", "Basic " + base64.b64encode(credentials).decode("utf-8"))

    with request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    confirmation_url = (
        result.get("confirmation", {}).get("confirmation_url")
        if isinstance(result.get("confirmation"), dict)
        else None
    )
    payment_id = result.get("id")
    status = result.get("status")

    print("YooKassa payment created")
    print(f"payment_id: {payment_id}")
    print(f"status: {status}")
    print(f"confirmation_url: {confirmation_url}")


if __name__ == "__main__":
    main()

