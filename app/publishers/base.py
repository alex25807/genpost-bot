from abc import ABC, abstractmethod


class BasePublisher(ABC):
    platform_name: str

    @abstractmethod
    def publish(self, content: str, image_url: str | None = None) -> dict:
        raise NotImplementedError
