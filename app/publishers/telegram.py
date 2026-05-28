import requests

from app.config import settings
from app.publishers.base import BasePublisher, load_image_bytes


TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


def _format_telegram_error(message: str) -> str:
    lowered_message = message.lower()

    if "chat not found" in lowered_message:
        return "Telegram не нашел указанный чат. Проверьте TELEGRAM_CHAT_ID."
    if "bot was blocked by the user" in lowered_message:
        return "Telegram-бот заблокирован получателем."
    if "forbidden" in lowered_message or "not enough rights" in lowered_message:
        return "Telegram отклонил запрос: у бота нет доступа к этому чату."
    if "unauthorized" in lowered_message or "not found" in lowered_message:
        return "Telegram отклонил запрос: проверьте TELEGRAM_BOT_TOKEN."
    if "wrong file identifier" in lowered_message or "failed to get http url content" in lowered_message:
        return "Telegram не смог получить изображение по указанной ссылке."
    if "caption is too long" in lowered_message:
        return "Подпись к изображению слишком длинная для Telegram."
    if "message is too long" in lowered_message:
        return "Текст сообщения слишком длинный для Telegram."
    return f"Telegram вернул ошибку: {message}" if message else "Telegram вернул ошибку публикации."


def _normalize_telegram_text(content: str) -> str:
    text = (content or "").replace("\r\n", "\n").strip()
    text = text.replace("### ", "🔷 ").replace("## ", "🔷 ").replace("# ", "🔷 ")
    text = text.replace("**", "")
    lines = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if line.startswith("- "):
            line = f"🔹 {line[2:].strip()}"
        elif line.startswith("* "):
            line = f"🔹 {line[2:].strip()}"
        lines.append(line)

    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _take_chunk(text: str, limit: int) -> tuple[str, str]:
    if len(text) <= limit:
        return text.strip(), ""

    cut_at = text.rfind("\n\n", 0, limit + 1)
    if cut_at == -1:
        cut_at = text.rfind("\n", 0, limit + 1)
    if cut_at == -1:
        cut_at = text.rfind(" ", 0, limit + 1)
    if cut_at == -1 or cut_at < limit // 2:
        cut_at = limit

    chunk = text[:cut_at].strip()
    remainder = text[cut_at:].lstrip()
    return chunk, remainder


class TelegramPublisher(BasePublisher):
    platform_name = "telegram"

    def __init__(self, chat_id: str | None = None) -> None:
        if not settings.has_telegram():
            raise ValueError("Telegram не настроен: задайте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env.")
        self.bot_token = settings.telegram_bot_token
        self.chat_id = str(chat_id or settings.telegram_chat_id).strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    @classmethod
    def check_status(cls) -> bool:
        if not settings.has_telegram():
            return False

        try:
            publisher = cls()
            publisher._call_api("getMe", {"chat_id": None}, timeout=10)
            publisher._call_api("getChat", {"chat_id": publisher.chat_id}, timeout=10)
            return True
        except Exception:
            return False

    def _call_api(
        self,
        method: str,
        payload: dict[str, str | None],
        timeout: int = 30,
        files: dict | None = None,
    ) -> dict:
        data = {key: value for key, value in payload.items() if value is not None}

        try:
            response = requests.post(
                f"{self.base_url}/{method}",
                data=data,
                files=files,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise RuntimeError("Telegram не ответил вовремя. Повторите попытку позже.") from exc
        except requests.RequestException as exc:
            raise RuntimeError("Не удалось подключиться к Telegram.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Telegram вернул непонятный ответ.") from exc

        if not payload.get("ok"):
            raise RuntimeError(_format_telegram_error(payload.get("description", "")))

        return payload["result"]

    def _send_text_chunks(self, content: str) -> list[dict]:
        messages: list[dict] = []
        remaining = content.strip()

        while remaining:
            chunk, remaining = _take_chunk(remaining, TELEGRAM_MESSAGE_LIMIT)
            if not chunk:
                break
            messages.append(
                self._call_api(
                    "sendMessage",
                    {
                        "chat_id": self.chat_id,
                        "text": chunk,
                    },
                )
            )

        return messages

    def publish(self, content: str, image_url: str | None = None) -> dict:
        normalized_content = _normalize_telegram_text(content)

        if image_url:
            caption, remainder = _take_chunk(normalized_content, TELEGRAM_CAPTION_LIMIT)
            photo_payload = {
                "chat_id": self.chat_id,
                "caption": caption,
            }
            photo_files = None
            if image_url.strip().startswith("data:"):
                photo_files = {"photo": ("image.png", load_image_bytes(image_url))}
            else:
                photo_payload["photo"] = image_url
            photo_message = self._call_api("sendPhoto", photo_payload, files=photo_files)
            follow_up_messages = self._send_text_chunks(remainder)
            return {
                "photo_message": photo_message,
                "follow_up_messages": follow_up_messages,
                "chunks_sent": 1 + len(follow_up_messages),
            }

        messages = self._send_text_chunks(normalized_content)
        return {
            "messages": messages,
            "chunks_sent": len(messages),
        }
