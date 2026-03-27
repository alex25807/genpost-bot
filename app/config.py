import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH, override=True)


def _safe_int(raw_value: str | None, default: int) -> int:
    try:
        value = int((raw_value or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _safe_float(raw_value: str | None, default: float) -> float:
    try:
        value = float((raw_value or "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _safe_bool(raw_value: str | None, default: bool) -> bool:
    normalized = (raw_value or "").strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return default


class Settings:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        load_dotenv(ENV_PATH, override=True)
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.vk_api_key = os.getenv("VK_API_KEY", "").strip()
        self.vk_group_id = os.getenv("VK_GROUP_ID", "").strip()
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.monetization_enabled = _safe_bool(os.getenv("MONETIZATION_ENABLED"), False)
        self.openai_text_model = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o").strip()
        self.openai_image_model = os.getenv("OPENAI_IMAGE_MODEL", "dall-e-3").strip()
        self.default_image_size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024").strip()
        self.admin_chat_id = os.getenv("ADMIN_CHAT_ID", "").strip()
        self.trial_free_posts = _safe_int(os.getenv("TRIAL_FREE_POSTS"), 3)
        self.subscription_price_rub = _safe_int(os.getenv("SUBSCRIPTION_PRICE_RUB"), 990)
        self.subscription_payment_url = os.getenv("SUBSCRIPTION_PAYMENT_URL", "").strip()
        self.subscription_support_contact = os.getenv("SUBSCRIPTION_SUPPORT_CONTACT", "").strip()
        self.payment_webhook_secret = os.getenv("PAYMENT_WEBHOOK_SECRET", "").strip()
        self.yookassa_webhook_token = os.getenv("YOOKASSA_WEBHOOK_TOKEN", "").strip()
        self.yookassa_shop_id = os.getenv("YOOKASSA_SHOP_ID", "").strip()
        self.yookassa_secret_key = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
        self.subscription_return_url = os.getenv("SUBSCRIPTION_RETURN_URL", "https://t.me").strip()
        self.subscription_payment_description = os.getenv(
            "SUBSCRIPTION_PAYMENT_DESCRIPTION", "Подписка на ГенПост"
        ).strip()
        self.subscription_default_days = _safe_int(os.getenv("SUBSCRIPTION_DEFAULT_DAYS"), 30)
        self.openai_input_usd_per_1k = _safe_float(os.getenv("OPENAI_INPUT_USD_PER_1K"), 0.005)
        self.openai_output_usd_per_1k = _safe_float(os.getenv("OPENAI_OUTPUT_USD_PER_1K"), 0.015)
        self.openai_image_usd_per_1 = _safe_float(os.getenv("OPENAI_IMAGE_USD_PER_1"), 0.04)
        self.usd_to_rub = _safe_float(os.getenv("USD_TO_RUB"), 95.0)

    def has_openai(self) -> bool:
        self.reload()
        return bool(self.openai_api_key)

    def has_vk(self) -> bool:
        self.reload()
        return bool(self.vk_api_key and self.vk_group_id)

    def has_telegram(self) -> bool:
        self.reload()
        return bool(self.telegram_bot_token and self.telegram_chat_id)


settings = Settings()
