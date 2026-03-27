import re

from openai import OpenAI

from app.config import settings


CLIENT_ASSISTANT_SYSTEM_PROMPT = (
    "Ты AI-консультант по контенту для бизнеса. Твоя задача — не бросаться сразу писать пост, "
    "а сначала помочь клиенту выбрать сильную тему. Если тема не определена, предложи несколько "
    "понятных тем на выбор с опорой на нишу, цель и аудиторию клиента. Формулируй идеи простым "
    "языком, без воды и без канцелярита. Темы должны быть пригодны для публикации в Telegram и VK."
)

DEFAULT_IMAGE_PREFERENCES = (
    "Photorealistic social media scene. Show one man or one woman, or two people together, "
    "engaged in a creative process: drawing, crafting, brainstorming, designing, or working at a PC/laptop "
    "with something interesting visible on screen. Natural emotions, real environment, cinematic light, "
    "detailed hands, realistic face, modern clothes. No logos, no posters, no captions, no letters, no UI text, no watermark."
)


def _short_topic(topic: str) -> str:
    cleaned = re.sub(r"\s+", " ", (topic or "").strip())
    return cleaned or "творческий процесс для соцсетей"


def build_image_prompt_hint(topic: str) -> str:
    concise_topic = _short_topic(topic)
    return (
        f"Пример для темы «{concise_topic}»:\n"
        "фотореалистичная сцена, которая однозначно показывает эту тему: "
        f"«{concise_topic}»; главный объект в кадре напрямую связан с темой, без абстрактных офисов и случайных людей. "
        "Живые эмоции, естественный свет, понятный контекст (рабочее место, сервис, продукт или клиент), "
        "аккуратные детали в кадре, без текста, букв, логотипов и водяных знаков"
    )


def _format_openai_error(exc: Exception, action: str) -> str:
    message = str(exc).strip()
    lowered_message = message.lower()
    error_name = exc.__class__.__name__.lower()

    if "auth" in error_name or "api key" in lowered_message or "invalid_api_key" in lowered_message:
        return "OpenAI недоступен: проверьте корректность OPENAI_API_KEY."
    if "rate" in error_name or "rate limit" in lowered_message:
        return "OpenAI временно ограничил запросы. Повторите попытку позже."
    if "timeout" in error_name or "timed out" in lowered_message:
        return f"OpenAI не ответил вовремя при попытке {action}."
    if "connection" in error_name or "connect" in lowered_message:
        return "Не удалось подключиться к OpenAI."
    if message:
        return f"OpenAI не смог {action}: {message}"
    return f"OpenAI не смог {action}."


def _normalize_generated_post(content: str) -> str:
    text = (content or "").replace("\r\n", "\n").strip()
    replacements = [
        (r"(?m)^\s*#{1,6}\s*", "🔷 "),
        (r"\*\*(.*?)\*\*", r"\1"),
        (r"__(.*?)__", r"\1"),
        (r"(?m)^\s*[-*•]\s+", "🔹 "),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_topic_suggestions(content: str) -> list[str]:
    topics: list[str] = []

    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        line = re.sub(r"^\d+[.)]\s*", "", line)
        line = re.sub(r"^[-*•🔹🔷✅]+\s*", "", line)
        line = line.strip(" \"'.,;:-")
        if len(line) >= 6:
            topics.append(line)

    unique_topics: list[str] = []
    for topic in topics:
        if topic not in unique_topics:
            unique_topics.append(topic)

    return unique_topics


class ContentGeneratorService:
    def __init__(self) -> None:
        if not settings.has_openai():
            raise ValueError("OpenAI не настроен: задайте OPENAI_API_KEY в .env.")
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=60.0)

    @staticmethod
    def check_status() -> bool:
        if not settings.has_openai():
            return False

        try:
            client = OpenAI(api_key=settings.openai_api_key, timeout=10.0)
            client.models.list()
            return True
        except Exception:
            return False

    def generate_post(self, topic: str, tone: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=settings.openai_text_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты сильный SMM-специалист. Пиши посты на русском языке для соцсетей. "
                            "Текст должен быть структурным, живым и пригодным для публикации без доработок. "
                            "Не используй Markdown-разметку: не ставь #, ##, **, *, списки с дефисами и служебные символы. "
                            "Вместо этого используй обычный текст, короткие абзацы и при необходимости аккуратные эмодзи-маркеры "
                            "вроде 🔷, 🔹 или ✅. Не добавляй хэштеги, если пользователь отдельно их не просил. "
                            "Сделай текст красивым для Telegram и VK."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Сгенерируй пост для соцсетей на тему '{topic}'. "
                            f"Нужный тон: '{tone}'. Добавь короткий выразительный заголовок, 2-4 абзаца "
                            "и финальный призыв к действию. Общая длина текста должна быть комфортной для чтения в мессенджере."
                        ),
                    },
                ],
            )
            return _normalize_generated_post(response.choices[0].message.content or "")
        except Exception as exc:
            raise RuntimeError(_format_openai_error(exc, "сгенерировать текст")) from exc

    def suggest_topics(
        self,
        business_context: str,
        goal: str,
        tone: str,
        count: int = 5,
        excluded_topics: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        try:
            exclusions = [topic.strip() for topic in (excluded_topics or []) if topic.strip()]
            exclusion_block = ""
            if exclusions:
                joined_topics = "\n".join(f"- {topic}" for topic in exclusions[:10])
                exclusion_block = (
                    "\n\nНе предлагай темы, которые слишком похожи на следующие уже использованные или опубликованные идеи:\n"
                    f"{joined_topics}"
                )

            response = self.client.chat.completions.create(
                model=settings.openai_text_model,
                messages=[
                    {
                        "role": "system",
                        "content": CLIENT_ASSISTANT_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Ниша или контекст клиента: {business_context}\n"
                            f"Цель клиента: {goal}\n"
                            f"Желаемый тон: {tone}\n\n"
                            f"Сначала напиши 1 короткую фразу-консультацию для клиента на русском языке. "
                            f"Затем с новой строки выдай ровно {count} тем для постов, каждую на новой строке. "
                            "Не используй markdown-решетки, не используй двойные звездочки. "
                            "Темы должны быть конкретными, разными и подходящими для выбора клиентом."
                            f"{exclusion_block}"
                        ),
                    },
                ],
            )
            raw_content = (response.choices[0].message.content or "").strip()
            lines = [line.strip() for line in raw_content.splitlines() if line.strip()]
            consultant_message = lines[0] if lines else "Вот несколько тем, которые можно взять в работу."
            topics = _parse_topic_suggestions("\n".join(lines[1:] if len(lines) > 1 else lines))

            if len(topics) < count:
                raise RuntimeError("OpenAI вернул слишком мало тем для выбора.")

            return consultant_message, topics[:count]
        except Exception as exc:
            raise RuntimeError(_format_openai_error(exc, "предложить темы для клиента")) from exc

    def generate_image_prompt(
        self,
        topic: str,
        tone: str,
        content: str,
        image_preferences: str | None = None,
    ) -> str:
        try:
            visual_brief = (image_preferences or "").strip() or DEFAULT_IMAGE_PREFERENCES
            response = self.client.chat.completions.create(
                model=settings.openai_text_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты создаешь промпты для генерации изображений. "
                            "Верни один подробный промпт на английском языке для качественной AI-генерации. "
                            "Промпт должен вести к фотореалистичному изображению для соцсетей. "
                            "Главный сюжет изображения обязан явно и однозначно отражать тему поста; "
                            "нельзя рисовать абстрактную творческую сцену, если тема про конкретный бизнес или сервис. "
                            "Запрещай текст, буквы, логотипы, водяные знаки, постеры и любые надписи в кадре."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Тема поста: {topic}\n"
                            f"Тон: {tone}\n"
                            f"Текст поста: {content}\n\n"
                            f"Пожелания клиента к изображению: {visual_brief}\n\n"
                            "Сделай промпт для квадратного изображения для соцсетей. "
                            "Сюжет должен быть жизненным, эмоциональным, фотореалистичным, без графического логотипного стиля. "
                            "Убедись, что по картинке сразу понятно, что она про эту тему, а не про что-то общее."
                        ),
                    },
                ],
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            raise RuntimeError(_format_openai_error(exc, "подготовить промпт изображения")) from exc

    def generate_image(self, prompt: str) -> str:
        try:
            response = self.client.images.generate(
                model=settings.openai_image_model,
                prompt=prompt,
                size=settings.default_image_size,
                quality="standard",
                n=1,
            )
            return response.data[0].url
        except Exception as exc:
            raise RuntimeError(_format_openai_error(exc, "сгенерировать изображение")) from exc
