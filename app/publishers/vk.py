import requests

from app.config import settings
from app.publishers.base import BasePublisher


def _format_vk_error(exc: Exception) -> str:
    message = str(exc).strip()
    lowered_message = message.lower()

    if isinstance(exc, requests.Timeout) or "timed out" in lowered_message:
        return "VK не ответил вовремя. Повторите попытку позже."
    if isinstance(exc, requests.RequestException):
        return "Не удалось подключиться к VK."
    if "access denied" in lowered_message or "permission" in lowered_message:
        return "VK отклонил публикацию: проверьте токен и права доступа."
    if message:
        return f"VK вернул ошибку: {message}"
    return "VK вернул ошибку публикации."


class VKPublisher(BasePublisher):
    platform_name = "vk"

    def __init__(self) -> None:
        if not settings.has_vk():
            raise ValueError("VK не настроен: задайте VK_API_KEY и VK_GROUP_ID в .env.")
        self.vk_api_key = settings.vk_api_key
        self.group_id = settings.vk_group_id
        self.api_version = "5.236"

    @classmethod
    def check_status(cls) -> bool:
        if not settings.has_vk():
            return False

        try:
            response = requests.get(
                "https://api.vk.com/method/groups.getById",
                params={
                    "access_token": settings.vk_api_key,
                    "v": "5.236",
                    # Since VK API 5.218 this method expects `group_ids`.
                    "group_ids": settings.vk_group_id,
                },
                timeout=10,
            )
            payload = response.json()
            return "error" not in payload
        except (requests.RequestException, ValueError):
            return False

    def _vk_get(self, method: str, params: dict) -> dict:
        try:
            response = requests.get(
                f"https://api.vk.com/method/{method}",
                params={"access_token": self.vk_api_key, "v": self.api_version, **params},
                timeout=30,
            )
            payload = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(_format_vk_error(exc)) from exc
        except ValueError as exc:
            raise RuntimeError("VK вернул непонятный ответ.") from exc

        if "error" in payload:
            raise RuntimeError(_format_vk_error(ValueError(payload["error"]["error_msg"])))

        return payload["response"]

    def upload_photo(self, image_url: str) -> str:
        upload_server = self._vk_get("photos.getWallUploadServer", {"group_id": self.group_id})

        try:
            image_data = requests.get(image_url, timeout=60).content
        except requests.RequestException as exc:
            raise RuntimeError("Не удалось загрузить изображение для публикации в VK.") from exc

        try:
            upload_response = requests.post(
                upload_server["upload_url"],
                files={"photo": ("image.jpg", image_data)},
                timeout=60,
            ).json()
        except requests.RequestException as exc:
            raise RuntimeError("VK не принял загрузку изображения.") from exc
        except ValueError as exc:
            raise RuntimeError("VK вернул непонятный ответ при загрузке изображения.") from exc

        if "photo" not in upload_response:
            raise RuntimeError("VK не смог обработать загруженное изображение.")

        save_response = self._vk_get(
            "photos.saveWallPhoto",
            {
                "group_id": self.group_id,
                "photo": upload_response["photo"],
                "server": upload_response["server"],
                "hash": upload_response["hash"],
            },
        )

        photo = save_response[0]
        return f"photo{photo['owner_id']}_{photo['id']}"

    def publish(self, content: str, image_url: str | None = None) -> dict:
        params = {
            "owner_id": f"-{self.group_id}",
            "from_group": 1,
            "message": content,
        }

        if image_url:
            params["attachments"] = self.upload_photo(image_url)

        try:
            response = requests.post(
                "https://api.vk.com/method/wall.post",
                params={"access_token": self.vk_api_key, "v": self.api_version, **params},
                timeout=30,
            ).json()
        except requests.RequestException as exc:
            raise RuntimeError(_format_vk_error(exc)) from exc
        except ValueError as exc:
            raise RuntimeError("VK вернул непонятный ответ при публикации.") from exc

        if "error" in response:
            raise RuntimeError(_format_vk_error(ValueError(response["error"]["error_msg"])))

        return response["response"]
