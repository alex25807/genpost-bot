from app.config import settings


def __getattr__(name: str):
    settings.reload()

    if name == "openai_key":
        return settings.openai_api_key
    if name == "vk_api_key":
        return settings.vk_api_key
    if name == "vk_group_id":
        return settings.vk_group_id

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
