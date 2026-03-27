from openai import OpenAI

class ImageGenerator:
    def __init__(self, openai_key: str, prompt: str):
        self.client = OpenAI(api_key=openai_key)  # исправили регистр
        self.prompt = prompt

    def generate_image(self) -> str:
        resp = self.client.images.generate(
            model="dall-e-3",   # можно "dall-e-3", но актуальнее так
            prompt=self.prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        return resp.data[0].url
