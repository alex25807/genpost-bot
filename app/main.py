from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import (
    GenerateImageRequest,
    GenerateImageResponse,
    GenerateRequest,
    GenerateResponse,
    PublishRequest,
    PublishResponse,
    PublishResult,
    ServiceStatus,
    SubscriptionWebhookRequest,
    SubscriptionWebhookResponse,
    TopicSuggestionRequest,
    TopicSuggestionResponse,
)
from app.publishers.telegram import TelegramPublisher
from app.publishers.vk import VKPublisher
from app.services import ContentGeneratorService, build_image_prompt_hint


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ANALYTICS_FILE = BASE_DIR / "genpost_bot_analytics.json"

app = FastAPI(title="Unified VK/TG Post Generator", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def get_publisher(platform: str):
    if platform == "vk":
        return VKPublisher()
    if platform == "telegram":
        return TelegramPublisher()
    raise ValueError(f"Unsupported platform: {platform}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_analytics() -> dict:
    if not ANALYTICS_FILE.exists():
        return {"events": [], "subscriptions": {}, "payment_events": []}
    try:
        data = json.loads(ANALYTICS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"events": [], "subscriptions": {}, "payment_events": []}
    if not isinstance(data, dict):
        return {"events": [], "subscriptions": {}, "payment_events": []}
    return {
        "events": data.get("events", []) if isinstance(data.get("events"), list) else [],
        "subscriptions": data.get("subscriptions", {}) if isinstance(data.get("subscriptions"), dict) else {},
        "payment_events": data.get("payment_events", []) if isinstance(data.get("payment_events"), list) else [],
    }


def _save_analytics(payload: dict) -> None:
    payload["events"] = payload.get("events", [])[-5000:]
    payload["payment_events"] = payload.get("payment_events", [])[-2000:]
    ANALYTICS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_paid_subscription(
    provider: str,
    event_id: str,
    chat_id: str,
    amount_rub: float,
    period_days: int,
) -> SubscriptionWebhookResponse:
    settings.reload()
    analytics = _load_analytics()
    payment_events = analytics.get("payment_events", [])
    if event_id in payment_events:
        current_expiry_raw = analytics.get("subscriptions", {}).get(chat_id)
        if not current_expiry_raw:
            raise HTTPException(status_code=409, detail="Duplicate payment event without subscription state.")
        return SubscriptionWebhookResponse(
            ok=True,
            chat_id=chat_id,
            expires_at=current_expiry_raw,
            period_days=period_days,
        )

    now = _utc_now()
    current_expiry_raw = analytics.get("subscriptions", {}).get(chat_id)
    current_expiry = None
    if current_expiry_raw:
        try:
            current_expiry = datetime.fromisoformat(current_expiry_raw)
        except ValueError:
            current_expiry = None

    actual_days = period_days or settings.subscription_default_days
    start_dt = current_expiry if current_expiry and current_expiry > now else now
    expires_at = start_dt + timedelta(days=actual_days)

    analytics.setdefault("subscriptions", {})[chat_id] = expires_at.isoformat()
    payment_events.append(event_id)
    analytics["payment_events"] = payment_events
    analytics.setdefault("events", []).append(
        {
            "ts": now.isoformat(),
            "chat_id": chat_id,
            "action": "payment_webhook_paid",
            "provider": provider,
            "event_id": event_id,
            "amount_rub": amount_rub,
            "period_days": actual_days,
            "expires_at": expires_at.isoformat(),
        }
    )
    _save_analytics(analytics)

    return SubscriptionWebhookResponse(
        ok=True,
        chat_id=chat_id,
        expires_at=expires_at.isoformat(),
        period_days=actual_days,
    )


@app.get("/", include_in_schema=False)
def read_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def healthcheck():
    return {"status": "ok"}


@app.get("/api/status", response_model=ServiceStatus)
def service_status():
    return ServiceStatus(
        openai_ready=ContentGeneratorService.check_status(),
        vk_ready=VKPublisher.check_status(),
        telegram_ready=TelegramPublisher.check_status(),
    )


@app.get("/api/image-prompt-hint")
def image_prompt_hint(topic: str = ""):
    return {"topic": topic, "hint": build_image_prompt_hint(topic)}


@app.post("/api/payments/subscription-webhook", response_model=SubscriptionWebhookResponse)
def payment_subscription_webhook(
    payload: SubscriptionWebhookRequest,
    x_webhook_secret: str | None = Header(default=None),
):
    settings.reload()
    if not settings.monetization_enabled:
        raise HTTPException(status_code=503, detail="Monetization is temporarily disabled (MVP mode).")
    if not settings.payment_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook is not configured.")
    if x_webhook_secret != settings.payment_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret.")
    if payload.status.lower() != "paid":
        raise HTTPException(status_code=400, detail="Only paid events are accepted.")

    return _apply_paid_subscription(
        provider=payload.provider,
        event_id=payload.event_id,
        chat_id=payload.chat_id,
        amount_rub=payload.amount_rub,
        period_days=payload.period_days,
    )


@app.post("/api/payments/yookassa-webhook", response_model=SubscriptionWebhookResponse)
def yookassa_subscription_webhook(
    payload: dict[str, Any],
    x_yookassa_token: str | None = Header(default=None),
):
    settings.reload()
    if not settings.monetization_enabled:
        raise HTTPException(status_code=503, detail="Monetization is temporarily disabled (MVP mode).")
    if not settings.yookassa_webhook_token:
        raise HTTPException(status_code=503, detail="YooKassa webhook is not configured.")
    if x_yookassa_token != settings.yookassa_webhook_token:
        raise HTTPException(status_code=401, detail="Invalid YooKassa webhook token.")

    event = str(payload.get("event", "")).strip().lower()
    obj = payload.get("object")
    if not isinstance(obj, dict):
        raise HTTPException(status_code=400, detail="Invalid YooKassa payload: object is required.")
    status = str(obj.get("status", "")).strip().lower()
    if event != "payment.succeeded" or status != "succeeded":
        raise HTTPException(status_code=400, detail="Only payment.succeeded events are accepted.")

    payment_id = str(obj.get("id", "")).strip()
    if not payment_id:
        raise HTTPException(status_code=400, detail="Invalid YooKassa payload: payment id is required.")

    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="Invalid YooKassa payload: metadata is required.")
    chat_id = str(metadata.get("chat_id", "")).strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="metadata.chat_id is required.")

    amount_block = obj.get("amount")
    amount_rub = 0.0
    if isinstance(amount_block, dict):
        raw_value = str(amount_block.get("value", "0")).strip()
        try:
            amount_rub = float(raw_value)
        except ValueError:
            amount_rub = 0.0

    raw_days = metadata.get("period_days", settings.subscription_default_days)
    try:
        period_days = int(str(raw_days))
    except ValueError:
        period_days = settings.subscription_default_days

    event_id = f"yookassa:{payment_id}"
    return _apply_paid_subscription(
        provider="yookassa",
        event_id=event_id,
        chat_id=chat_id,
        amount_rub=amount_rub,
        period_days=period_days,
    )


@app.post("/api/generate", response_model=GenerateResponse)
def generate_post(payload: GenerateRequest):
    try:
        generator = ContentGeneratorService()
        content = generator.generate_post(payload.topic, payload.tone)
        image_prompt = None
        image_url = None

        if payload.include_image:
            image_prompt = generator.generate_image_prompt(
                payload.topic,
                payload.tone,
                content,
                payload.image_preferences,
            )
            image_url = generator.generate_image(image_prompt)

        return GenerateResponse(
            topic=payload.topic,
            tone=payload.tone,
            platforms=payload.platforms,
            content=content,
            image_preferences=payload.image_preferences,
            image_prompt=image_prompt,
            image_url=image_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Не удалось подготовить контент. Повторите попытку позже.",
        ) from exc


@app.post("/api/generate-image", response_model=GenerateImageResponse)
def generate_image_only(payload: GenerateImageRequest):
    try:
        generator = ContentGeneratorService()
        image_prompt = generator.generate_image_prompt(
            payload.topic,
            payload.tone,
            payload.content,
            payload.image_preferences,
        )
        image_url = generator.generate_image(image_prompt)
        return GenerateImageResponse(
            topic=payload.topic,
            tone=payload.tone,
            image_preferences=payload.image_preferences,
            image_prompt=image_prompt,
            image_url=image_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Не удалось подготовить изображение. Повторите попытку позже.",
        ) from exc


@app.post("/api/topic-suggestions", response_model=TopicSuggestionResponse)
def topic_suggestions(payload: TopicSuggestionRequest):
    try:
        generator = ContentGeneratorService()
        consultant_message, topics = generator.suggest_topics(
            business_context=payload.business_context,
            goal=payload.goal,
            tone=payload.tone,
            count=payload.count,
        )
        return TopicSuggestionResponse(
            business_context=payload.business_context,
            goal=payload.goal,
            tone=payload.tone,
            consultant_message=consultant_message,
            topics=topics,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Не удалось подобрать темы для клиента. Повторите попытку позже.",
        ) from exc


@app.post("/api/publish", response_model=PublishResponse)
def publish_post(payload: PublishRequest):
    results: list[PublishResult] = []

    for platform in payload.platforms:
        try:
            publisher = get_publisher(platform)
            details = publisher.publish(payload.content, payload.image_url)
            results.append(PublishResult(platform=platform, success=True, details=details))
        except Exception as exc:
            results.append(
                PublishResult(
                    platform=platform,
                    success=False,
                    details={"error": str(exc)},
                )
            )

    return PublishResponse(results=results)
