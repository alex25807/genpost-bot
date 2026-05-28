import base64
import uuid
from pathlib import Path

import requests

from app.config import BASE_DIR

GENERATED_IMAGES_DIR = BASE_DIR / "generated_images"
LOCAL_IMAGE_PREFIX = "local://"
_MAX_STORED_IMAGES = 200


def _cleanup_old_images() -> None:
    if not GENERATED_IMAGES_DIR.exists():
        return
    files = sorted(
        GENERATED_IMAGES_DIR.glob("*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale_file in files[_MAX_STORED_IMAGES:]:
        try:
            stale_file.unlink(missing_ok=True)
        except OSError:
            pass


def save_image_bytes(image_bytes: bytes, suffix: str = ".png") -> str:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{suffix}"
    path = GENERATED_IMAGES_DIR / filename
    path.write_bytes(image_bytes)
    _cleanup_old_images()
    return f"{LOCAL_IMAGE_PREFIX}{path.relative_to(BASE_DIR).as_posix()}"


def save_image_from_data_uri(data_uri: str) -> str:
    _, encoded = data_uri.split(",", 1)
    return save_image_bytes(base64.b64decode(encoded))


def resolve_local_image_path(image_ref: str) -> Path | None:
    ref = (image_ref or "").strip()
    if ref.startswith(LOCAL_IMAGE_PREFIX):
        return BASE_DIR / ref[len(LOCAL_IMAGE_PREFIX) :]
    return None


def load_image_bytes(image_ref: str) -> bytes:
    ref = (image_ref or "").strip()
    if not ref:
        raise ValueError("Пустая ссылка на изображение.")

    local_path = resolve_local_image_path(ref)
    if local_path is not None:
        if not local_path.is_file():
            raise RuntimeError("Файл изображения не найден.")
        return local_path.read_bytes()

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
