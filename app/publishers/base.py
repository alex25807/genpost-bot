import base64
from abc import ABC, abstractmethod

import requests


def load_image_bytes(image_ref: str) -> bytes:
    ref = (image_ref or "").strip()
    if not ref:
        raise ValueError("Пустая ссылка на изображение.")
    if ref.startswith("data:"):
        try:
            _, encoded = ref.split(",", 1)
            return base64.b64decode(encoded)
        except (ValueError, base64.binascii.Error) as exc:
            raise RuntimeError("Не удалось декодировать изображение.") from exc
    try:
        response = requests.get(ref, timeout=60)
        response.raise_for_status()
        return response.content
    except requests.RequestException as exc:
        raise RuntimeError("Не удалось загрузить изображение.") from exc


class BasePublisher(ABC):
    platform_name: str

    @abstractmethod
    def publish(self, content: str, image_url: str | None = None) -> dict:
        raise NotImplementedError
