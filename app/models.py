from typing import Literal

from pydantic import BaseModel, Field


PlatformName = Literal["vk", "telegram"]


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, description="Topic for the generated post")
    tone: str = Field(default="экспертный и дружелюбный", min_length=2)
    include_image: bool = True
    image_preferences: str | None = None
    platforms: list[PlatformName] = Field(default_factory=lambda: ["vk"])


class GenerateResponse(BaseModel):
    topic: str
    tone: str
    platforms: list[PlatformName]
    content: str
    image_preferences: str | None = None
    image_prompt: str | None = None
    image_url: str | None = None


class GenerateImageRequest(BaseModel):
    topic: str = Field(..., min_length=3)
    tone: str = Field(default="экспертный и дружелюбный", min_length=2)
    content: str = Field(..., min_length=10)
    image_preferences: str | None = None


class GenerateImageResponse(BaseModel):
    topic: str
    tone: str
    image_preferences: str | None = None
    image_prompt: str
    image_url: str


class TopicSuggestionRequest(BaseModel):
    business_context: str = Field(..., min_length=3, description="Business niche, product or audience")
    goal: str = Field(
        default="получить идеи для полезных и продающих постов",
        min_length=3,
        description="What the client wants to achieve with the post",
    )
    tone: str = Field(default="экспертный и дружелюбный", min_length=2)
    count: int = Field(default=5, ge=3, le=7)


class TopicSuggestionResponse(BaseModel):
    business_context: str
    goal: str
    tone: str
    consultant_message: str
    topics: list[str]


class PublishRequest(BaseModel):
    content: str = Field(..., min_length=3)
    platforms: list[PlatformName] = Field(..., min_length=1)
    image_url: str | None = None


class PublishResult(BaseModel):
    platform: PlatformName
    success: bool
    details: dict


class PublishResponse(BaseModel):
    results: list[PublishResult]


class ServiceStatus(BaseModel):
    openai_ready: bool
    vk_ready: bool
    telegram_ready: bool


class SubscriptionWebhookRequest(BaseModel):
    event_id: str = Field(..., min_length=3)
    provider: str = Field(default="manual", min_length=2)
    status: str = Field(..., min_length=2, description="Expected: paid")
    chat_id: str = Field(..., min_length=3)
    amount_rub: float = Field(default=0.0, ge=0)
    period_days: int = Field(default=30, ge=1, le=366)


class SubscriptionWebhookResponse(BaseModel):
    ok: bool
    chat_id: str
    expires_at: str
    period_days: int
