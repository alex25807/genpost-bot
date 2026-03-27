from generators.text_gen import PostGenerator
from generators.image_gen import ImageGenerator
from social_publishers.vk_publisher import VKPublisher
import config as conf

post_gen = PostGenerator(conf.openai_key, tone="строгий и академический ",
                         topic="Виртуальный ассистент, всегда готовый прийти на помощь ")
content = post_gen.generate_post()
img_desc = post_gen.generate_post_image_description()

img_gen = ImageGenerator(conf.openai_key, img_desc)  # передали prompt
image_url = img_gen.generate_image()

vk_pub = VKPublisher(conf.vk_api_key, conf.vk_group_id)
vk_pub.publish_post(content, image_url)

print(content)
print(image_url)