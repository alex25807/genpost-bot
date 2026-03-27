import os
from openai import OpenAI

class PostGenerator:
    def __init__(self, api_key: str, tone: str, topic: str):
        # ВАЖНО: api_key должен быть реальным ASCII-ключом вида "sk-..."
        self.client = OpenAI(api_key=api_key)
        self.tone = tone
        self.topic = topic

    def generate_post(self) -> str:
        resp = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system",
                 "content": "Ты высококвалифицированный SMM-специалист и помогаешь генерировать тексты постов."},
                {"role": "user",
                 "content": f"Сгенерируй пост для соцсетей на тему «{self.topic}», используя тон: «{self.tone}»."}
            ],
        )
        return resp.choices[0].message.content

    def generate_post_image_description(self) -> str:
        response = self.client.chat.completions.create(model="gpt-4o",messages=[
            {"role": "system", "content": "Ты ассистент, который составит промпт для нейросети, которая будет генерировать изображения. Ты должен составлять промпт на заданную тематику."},
            {"role": "user", "content": f"Сгенерируй изображение для соцсетей с темой {self.topic}"}
          ]
        )
        return response.choices[0].message.content

