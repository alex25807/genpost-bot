import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import uuid

import requests

from app.config import settings
from app.publishers.telegram import TelegramPublisher
from app.publishers.vk import VKPublisher
from app.services import ContentGeneratorService, build_image_prompt_hint


TONE_OPTIONS = {
    "expert": "экспертный и дружелюбный",
    "friendly": "живой и дружелюбный",
    "sales": "уверенный и продающий",
    "calm": "спокойный и заботливый",
}

PLATFORM_OPTIONS = {
    "telegram": ["telegram"],
    "vk": ["vk"],
    "both": ["telegram", "vk"],
}

STATE_FILE = Path(__file__).resolve().with_name("genpost_bot_state.json")
RUNTIME_FILE = Path(__file__).resolve().with_name("genpost_bot_runtime.json")
LOCK_FILE = Path(__file__).resolve().with_name("genpost_bot.lock")
ANALYTICS_FILE = Path(__file__).resolve().with_name("genpost_bot_analytics.json")


@dataclass
class ChatSession:
    stage: str = "await_context"
    business_context: str = ""
    goal: str = ""
    tone: str = TONE_OPTIONS["expert"]
    topics: list[str] = field(default_factory=list)
    selected_topic: str = ""
    include_image: bool = False
    platforms: list[str] = field(default_factory=lambda: ["telegram"])
    generated_content: str = ""
    image_url: str | None = None
    image_prompt: str | None = None
    image_preferences: str = ""
    trial_posts_used: int = 0
    published_topics: list[str] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)

    def reset_draft(self) -> None:
        self.topics = []
        self.selected_topic = ""
        self.include_image = False
        self.platforms = ["telegram"]
        self.generated_content = ""
        self.image_url = None
        self.image_prompt = None
        self.image_preferences = ""

    def remember_topic(self, topic: str) -> None:
        cleaned = topic.strip()
        if not cleaned:
            return
        self.recent_topics = [item for item in self.recent_topics if item != cleaned]
        self.recent_topics.insert(0, cleaned)
        self.recent_topics = self.recent_topics[:10]

    def remember_published_topic(self, topic: str) -> None:
        cleaned = topic.strip()
        if not cleaned:
            return
        self.published_topics = [item for item in self.published_topics if item != cleaned]
        self.published_topics.insert(0, cleaned)
        self.published_topics = self.published_topics[:20]


class TelegramBotClient:
    def __init__(self) -> None:
        if not settings.telegram_bot_token:
            raise ValueError("Для режима бота нужен TELEGRAM_BOT_TOKEN в .env.")
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    def _request(self, method: str, data: dict | None = None) -> dict:
        response = requests.post(f"{self.base_url}/{method}", data=data or {}, timeout=60)
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram bot API error"))
        return payload["result"]

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        response = requests.get(
            f"{self.base_url}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 10,
        )
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "Telegram getUpdates error"))
        return payload["result"]

    def send_message(self, chat_id: int, text: str, keyboard: list[list[dict]] | None = None) -> dict:
        data = {
            "chat_id": str(chat_id),
            "text": text,
        }
        if keyboard:
            data["reply_markup"] = json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)
        return self._request("sendMessage", data)

    def answer_callback(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        data = {"callback_query_id": callback_query_id}
        if text:
            data["text"] = text
        if show_alert:
            data["show_alert"] = True
        self._request("answerCallbackQuery", data)

    def set_commands(self, commands: list[dict[str, str]]) -> None:
        self._request(
            "setMyCommands",
            {"commands": json.dumps(commands, ensure_ascii=False)},
        )


class GenPostDialogBot:
    def __init__(self) -> None:
        self.bot = TelegramBotClient()
        self.sessions: dict[int, ChatSession] = self._load_sessions()
        self.offset: int | None = self._load_offset()
        self.recent_update_ids: list[int] = []
        self.analytics: dict = self._load_analytics()

    def _load_analytics(self) -> dict:
        if not ANALYTICS_FILE.exists():
            return {"events": [], "subscriptions": {}}
        try:
            data = json.loads(ANALYTICS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"events": [], "subscriptions": {}}
        if not isinstance(data, dict):
            return {"events": [], "subscriptions": {}}
        events = data.get("events")
        subscriptions = data.get("subscriptions")
        return {
            "events": events if isinstance(events, list) else [],
            "subscriptions": subscriptions if isinstance(subscriptions, dict) else {},
        }

    def _save_analytics(self) -> None:
        payload = {
            "events": self.analytics.get("events", [])[-5000:],
            "subscriptions": self.analytics.get("subscriptions", {}),
        }
        ANALYTICS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_offset(self) -> int | None:
        if not RUNTIME_FILE.exists():
            return None
        try:
            data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
            offset = data.get("offset")
            return int(offset) if offset is not None else None
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return None

    def _save_runtime(self) -> None:
        payload = {"offset": self.offset}
        RUNTIME_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _acquire_lock(self) -> None:
        if LOCK_FILE.exists():
            try:
                pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = None

            if pid and self._pid_exists(pid):
                raise RuntimeError(
                    f"GenPost bot уже запущен в другом процессе (PID {pid}). Остановите старый экземпляр перед новым запуском."
                )

        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False

        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                encoding="cp866",
                errors="ignore",
                check=False,
            )
            return str(pid) in result.stdout

        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _release_lock(self) -> None:
        try:
            if LOCK_FILE.exists():
                lock_pid = LOCK_FILE.read_text(encoding="utf-8").strip()
                if lock_pid == str(os.getpid()):
                    LOCK_FILE.unlink()
        except OSError:
            pass

    def _load_sessions(self) -> dict[int, ChatSession]:
        if not STATE_FILE.exists():
            return {}

        try:
            raw_data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

        sessions: dict[int, ChatSession] = {}
        for chat_id, payload in raw_data.items():
            try:
                sessions[int(chat_id)] = ChatSession(**payload)
            except (TypeError, ValueError):
                continue
        return sessions

    def _save_sessions(self) -> None:
        serialized = {str(chat_id): asdict(session) for chat_id, session in self.sessions.items()}
        STATE_FILE.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(self) -> None:
        self._acquire_lock()
        try:
            self._register_commands()
        except Exception as exc:
            print(f"Warning: could not register bot commands: {exc}")
        print("GenPost bot polling started.")
        try:
            while True:
                try:
                    updates = self.bot.get_updates(offset=self.offset, timeout=25)
                    for update in updates:
                        update_id = update["update_id"]
                        if update_id in self.recent_update_ids:
                            continue

                        self.recent_update_ids.append(update_id)
                        self.recent_update_ids = self.recent_update_ids[-50:]
                        self.offset = update_id + 1
                        self._save_runtime()
                        self.handle_update(update)
                except KeyboardInterrupt:
                    print("GenPost bot stopped.")
                    break
                except Exception as exc:
                    print(f"Bot loop error: {exc}")
                    time.sleep(3)
        finally:
            self._save_runtime()
            self._release_lock()

    def _register_commands(self) -> None:
        settings.reload()
        commands = [
            {"command": "start", "description": "Начать работу: клиент, тема, пост"},
            {"command": "help", "description": "Что умеет бот и как работать"},
            {"command": "new", "description": "Новая тема для текущего клиента"},
            {"command": "reset", "description": "Полный сброс и новый клиент"},
            {"command": "plan", "description": "Статус trial-режима"},
            {"command": "weekly_report", "description": "Недельный отчет (админ)"},
        ]
        if settings.monetization_enabled:
            commands.extend(
                [
                    {"command": "grant_sub", "description": "Выдать подписку (админ)"},
                    {"command": "invoice", "description": "Создать ссылку оплаты (админ)"},
                ]
            )
        self.bot.set_commands(commands)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _is_admin(self, chat_id: int) -> bool:
        settings.reload()
        return bool(settings.admin_chat_id) and str(chat_id) == str(settings.admin_chat_id)

    def _subscription_expires_at(self, chat_id: int) -> datetime | None:
        raw = self.analytics.get("subscriptions", {}).get(str(chat_id))
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _has_active_subscription(self, chat_id: int) -> bool:
        settings.reload()
        if not settings.monetization_enabled:
            return False
        expires_at = self._subscription_expires_at(chat_id)
        return bool(expires_at and expires_at > self._utc_now())

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        cleaned = (text or "").strip()
        if not cleaned:
            return 0
        return max(1, math.ceil(len(cleaned) / 4))

    def _record_usage(
        self,
        chat_id: int,
        action: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        images: int = 0,
    ) -> None:
        settings.reload()
        usd_cost = (
            (input_tokens / 1000.0) * settings.openai_input_usd_per_1k
            + (output_tokens / 1000.0) * settings.openai_output_usd_per_1k
            + images * settings.openai_image_usd_per_1
        )
        event = {
            "ts": self._utc_now().isoformat(),
            "chat_id": str(chat_id),
            "action": action,
            "input_tokens_est": int(input_tokens),
            "output_tokens_est": int(output_tokens),
            "images": int(images),
            "cost_usd_est": round(usd_cost, 6),
            "cost_rub_est": round(usd_cost * settings.usd_to_rub, 2),
        }
        self.analytics.setdefault("events", []).append(event)
        self._save_analytics()

    def _remaining_trial_posts(self, session: ChatSession, chat_id: int) -> int:
        if self._has_active_subscription(chat_id):
            return 999999
        settings.reload()
        return max(0, settings.trial_free_posts - int(session.trial_posts_used))

    def _send_paywall(self, chat_id: int, session: ChatSession) -> None:
        settings.reload()
        if not settings.monetization_enabled:
            self.bot.send_message(
                chat_id,
                (
                    "🔒 Демо-лимит исчерпан.\n\n"
                    f"✅ Вы использовали {session.trial_posts_used} из {settings.trial_free_posts} бесплатных генераций.\n"
                    "Сейчас бот работает в MVP-режиме для портфолио.\n"
                    "Если хотите полный доступ раньше релиза, напишите владельцу бота."
                ),
                keyboard=self._with_navigation([], include_main_menu=True),
            )
            return

        text = (
            "🔒 Бесплатный лимит исчерпан.\n\n"
            f"✅ Вы использовали {session.trial_posts_used} из {settings.trial_free_posts} бесплатных генераций.\n"
            "Чтобы продолжить генерацию постов и картинок без ограничений, активируйте подписку.\n\n"
            f"💳 Стоимость: {settings.subscription_price_rub} ₽ / мес."
        )
        keyboard: list[list[dict]] = []
        if settings.subscription_payment_url:
            keyboard.append([{"text": "💳 Оформить подписку", "url": settings.subscription_payment_url}])
        if settings.subscription_support_contact:
            keyboard.append(
                [{"text": "✉️ Написать менеджеру", "url": f"https://t.me/{settings.subscription_support_contact.lstrip('@')}"}]
            )
        keyboard = self._with_navigation(keyboard, include_main_menu=True)
        self.bot.send_message(chat_id, text, keyboard=keyboard)

    def _send_plan_status(self, chat_id: int) -> None:
        session = self.session_for(chat_id)
        settings.reload()
        if not settings.monetization_enabled:
            remaining = self._remaining_trial_posts(session, chat_id)
            self.bot.send_message(
                chat_id,
                (
                    "🧾 Режим: MVP demo\n"
                    f"✅ Бесплатных генераций осталось: {remaining} из {settings.trial_free_posts}\n"
                    "💡 Монетизация временно отключена, бот в режиме портфолио."
                ),
            )
            return
        if self._has_active_subscription(chat_id):
            expires_at = self._subscription_expires_at(chat_id)
            expires_label = expires_at.astimezone().strftime("%d.%m.%Y %H:%M") if expires_at else "не ограничено"
            self.bot.send_message(
                chat_id,
                f"✅ Подписка активна до {expires_label}.\nГенерация постов доступна без триал-лимита.",
            )
            return
        remaining = self._remaining_trial_posts(session, chat_id)
        self.bot.send_message(
            chat_id,
            (
                "🧾 Ваш тариф: Trial\n"
                f"✅ Бесплатных генераций осталось: {remaining} из {settings.trial_free_posts}\n"
                f"💳 Подписка: {settings.subscription_price_rub} ₽ / мес"
            ),
        )

    def _grant_subscription(self, target_chat_id: str, days: int = 30) -> str:
        expires_at = self._utc_now() + timedelta(days=max(1, days))
        self.analytics.setdefault("subscriptions", {})[str(target_chat_id)] = expires_at.isoformat()
        self._save_analytics()
        return expires_at.astimezone().strftime("%d.%m.%Y %H:%M")

    def _weekly_report_text(self) -> str:
        now = self._utc_now()
        since = now - timedelta(days=7)
        events = self.analytics.get("events", [])
        week_events = []
        for event in events:
            try:
                ts = datetime.fromisoformat(event.get("ts", ""))
            except ValueError:
                continue
            if ts >= since:
                week_events.append(event)

        clients = {event.get("chat_id", "") for event in week_events if event.get("chat_id")}
        total_input = sum(int(event.get("input_tokens_est", 0)) for event in week_events)
        total_output = sum(int(event.get("output_tokens_est", 0)) for event in week_events)
        total_images = sum(int(event.get("images", 0)) for event in week_events)
        total_usd = sum(float(event.get("cost_usd_est", 0.0)) for event in week_events)
        total_rub = sum(float(event.get("cost_rub_est", 0.0)) for event in week_events)

        return (
            "📊 Еженедельный отчет (оценка)\n\n"
            f"✅ Период: {since.astimezone().strftime('%d.%m %H:%M')} — {now.astimezone().strftime('%d.%m %H:%M')}\n"
            f"✅ Активных клиентов: {len(clients)}\n"
            f"✅ Событий генерации: {len(week_events)}\n"
            f"✅ Входные токены (оценка): {total_input}\n"
            f"✅ Выходные токены (оценка): {total_output}\n"
            f"✅ Картинки: {total_images}\n"
            f"✅ Себестоимость: ~${total_usd:.2f} / ~{total_rub:.0f} ₽"
        )

    def _create_yookassa_invoice(
        self,
        target_chat_id: str,
        period_days: int | None = None,
        amount_rub: int | None = None,
    ) -> tuple[str, str]:
        settings.reload()
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            raise RuntimeError("Не настроены YOOKASSA_SHOP_ID и/или YOOKASSA_SECRET_KEY в .env")

        actual_days = period_days if period_days and period_days > 0 else settings.subscription_default_days
        actual_amount = amount_rub if amount_rub and amount_rub > 0 else settings.subscription_price_rub
        return_url = settings.subscription_return_url or "https://t.me"
        description = settings.subscription_payment_description or "Подписка на ГенПост"

        payload = {
            "amount": {"value": f"{float(actual_amount):.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description,
            "metadata": {
                "chat_id": str(target_chat_id),
                "period_days": str(actual_days),
            },
        }
        response = requests.post(
            "https://api.yookassa.ru/v3/payments",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            headers={
                "Idempotence-Key": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        payment_id = str(data.get("id", "")).strip()
        confirmation_url = str((data.get("confirmation") or {}).get("confirmation_url", "")).strip()
        if not payment_id or not confirmation_url:
            raise RuntimeError("YooKassa не вернула payment id или confirmation_url.")

        self._record_usage(
            chat_id=int(target_chat_id) if str(target_chat_id).isdigit() else 0,
            action="invoice_created",
            input_tokens=0,
            output_tokens=0,
            images=0,
        )
        return payment_id, confirmation_url

    def session_for(self, chat_id: int) -> ChatSession:
        session = self.sessions.setdefault(chat_id, ChatSession())
        self._save_sessions()
        return session

    def reset_session(self, chat_id: int) -> ChatSession:
        self.sessions[chat_id] = ChatSession()
        self._save_sessions()
        return self.sessions[chat_id]

    def restart_topic_flow(self, chat_id: int) -> None:
        session = self.session_for(chat_id)
        session.reset_draft()
        session.stage = "await_tone"
        self._save_sessions()
        self.bot.send_message(
            chat_id,
            "Продолжаем с той же нишей. Выбери тон нового поста:",
            keyboard=self._tone_keyboard(),
        )

    def start_fresh_dialog(self, chat_id: int, from_menu: bool = False) -> None:
        self.reset_session(chat_id)
        if from_menu:
            text = (
                "🆕 Запускаем сценарий для нового клиента.\n\n"
                "Чтобы начать быстро и без ошибок, отправьте одним сообщением:\n"
                "1️⃣ нишу\n"
                "2️⃣ продукт или услугу\n"
                "3️⃣ для кого это (аудиторию)\n\n"
                "Пример: «Онлайн-школа английского для взрослых специалистов, цель — доверие и заявки»."
            )
        else:
            text = (
                "✨ Здравствуйте! Очень рады видеть Вас.\n\n"
                "Я помогу пройти путь от идеи до публикации в Telegram и VK.\n\n"
                "🚀 Как начать:\n"
                "1️⃣ Нажмите «Новый клиент» или сразу напишите нишу.\n"
                "2️⃣ Укажите цель поста.\n"
                "3️⃣ Выберите тему, тон и картинку.\n"
                "4️⃣ Получите готовый черновик и опубликуйте.\n\n"
                "🧭 При нажатии кнопки меню:\n"
                "✅ Возврат в безопасную стартовую точку\n"
                "✅ Показ функций сервиса\n"
                "✅ Быстрый перезапуск сценария, если запутались\n\n"
                "💡 Пример ниши: стоматология, онлайн-школа английского, агентство недвижимости."
            )
        self.bot.send_message(
            chat_id,
            text,
            keyboard=self._main_menu_keyboard(has_client=False),
        )

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self.handle_callback(update["callback_query"])
            return

        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()
        if not chat or not text:
            return

        chat_id = chat["id"]
        command, _, command_args = text.partition(" ")
        command = command.strip().lower()
        command_args = command_args.strip()

        if command == "/start":
            self.start_dialog(chat_id)
            return
        if command == "/reset":
            self.start_dialog(chat_id)
            return
        if command == "/help":
            self.send_main_menu(chat_id)
            return
        if command == "/new":
            self.restart_topic_flow(chat_id)
            return
        if command == "/plan":
            self._send_plan_status(chat_id)
            return
        if command == "/weekly_report":
            if not self._is_admin(chat_id):
                self.bot.send_message(chat_id, "Эта команда доступна только администратору.")
                return
            self.bot.send_message(chat_id, self._weekly_report_text())
            return
        if command == "/grant_sub":
            if not self._is_admin(chat_id):
                self.bot.send_message(chat_id, "Эта команда доступна только администратору.")
                return
            settings.reload()
            if not settings.monetization_enabled:
                self.bot.send_message(chat_id, "Монетизация временно отключена (MVP mode).")
                return
            parts = command_args.split()
            if not parts:
                self.bot.send_message(chat_id, "Формат: /grant_sub <chat_id> [days]")
                return
            target_chat_id = parts[0]
            try:
                days = int(parts[1]) if len(parts) > 1 else 30
            except ValueError:
                self.bot.send_message(chat_id, "Количество дней должно быть числом. Пример: /grant_sub 123456789 30")
                return
            expires_label = self._grant_subscription(target_chat_id, days=days)
            self.bot.send_message(chat_id, f"✅ Подписка активирована для {target_chat_id} до {expires_label}")
            return
        if command == "/invoice":
            if not self._is_admin(chat_id):
                self.bot.send_message(chat_id, "Эта команда доступна только администратору.")
                return
            settings.reload()
            if not settings.monetization_enabled:
                self.bot.send_message(chat_id, "Монетизация временно отключена (MVP mode).")
                return
            parts = command_args.split()
            if not parts:
                self.bot.send_message(
                    chat_id,
                    "Формат: /invoice <chat_id> [days] [amount_rub]\nПример: /invoice 123456789 30 990",
                )
                return
            target_chat_id = parts[0]
            try:
                days = int(parts[1]) if len(parts) > 1 else None
            except ValueError:
                self.bot.send_message(chat_id, "days должно быть числом.")
                return
            try:
                amount = int(parts[2]) if len(parts) > 2 else None
            except ValueError:
                self.bot.send_message(chat_id, "amount_rub должно быть числом.")
                return
            try:
                payment_id, confirmation_url = self._create_yookassa_invoice(
                    target_chat_id=target_chat_id,
                    period_days=days,
                    amount_rub=amount,
                )
                self.bot.send_message(
                    chat_id,
                    (
                        "✅ Ссылка на оплату создана.\n\n"
                        f"✅ payment_id: {payment_id}\n"
                        f"✅ chat_id: {target_chat_id}\n"
                        f"✅ ссылка: {confirmation_url}"
                    ),
                )
            except Exception as exc:
                self.bot.send_message(chat_id, f"Не удалось создать счет YooKassa: {exc}")
            return

        self.handle_text(chat_id, text)

    def start_dialog(self, chat_id: int) -> None:
        existing = self.sessions.get(chat_id)
        if existing and existing.business_context:
            existing.stage = "await_resume_choice"
            self._save_sessions()
            remembered_goal = existing.goal or "цель еще не зафиксирована"
            remembered_topics = ", ".join(existing.published_topics[:3]) if existing.published_topics else "пока нет опубликованных тем"
            self.bot.send_message(
                chat_id,
                (
                    "Извините, могу неточно помнить прошлый диалог.\n\n"
                    "Нашел последние сохраненные данные. Проверьте, это тот же клиент?\n"
                    f"✅ Ниша: {existing.business_context}\n"
                    f"✅ Цель: {remembered_goal}\n"
                    f"✅ Недавние темы: {remembered_topics}\n\n"
                    "Хотите продолжить с этими данными или начать заново?"
                ),
                keyboard=[
                    [{"text": "Да, продолжить", "callback_data": "resume:yes"}],
                    [{"text": "Новый клиент", "callback_data": "resume:no"}],
                ],
            )
            return

        self.start_fresh_dialog(chat_id)

    def _tone_keyboard(self) -> list[list[dict]]:
        return [
            [
                {"text": "Экспертный", "callback_data": "tone:expert"},
                {"text": "Дружелюбный", "callback_data": "tone:friendly"},
            ],
            [
                {"text": "Продающий", "callback_data": "tone:sales"},
                {"text": "Спокойный", "callback_data": "tone:calm"},
            ],
        ]

    def _platform_keyboard(self) -> list[list[dict]]:
        return [
            [
                {"text": "Только Telegram", "callback_data": "platform:telegram"},
                {"text": "Только VK", "callback_data": "platform:vk"},
            ],
            [
                {"text": "Telegram + VK", "callback_data": "platform:both"},
            ],
        ]

    @staticmethod
    def _platforms_label(platforms: list[str]) -> str:
        if platforms == ["telegram"]:
            return "Telegram"
        if platforms == ["vk"]:
            return "VK"
        return "Telegram и VK"

    def _with_navigation(
        self,
        keyboard: list[list[dict]],
        back_callback: str | None = None,
        include_main_menu: bool = True,
    ) -> list[list[dict]]:
        result = [row[:] for row in keyboard]
        nav_row: list[dict] = []
        if back_callback:
            nav_row.append({"text": "⬅️ Назад", "callback_data": back_callback})
        if include_main_menu:
            nav_row.append({"text": "🏠 Главное меню", "callback_data": "menu:main"})
        if nav_row:
            result.append(nav_row)
        return result

    def _main_menu_keyboard(self, has_client: bool) -> list[list[dict]]:
        keyboard = [
            [{"text": "✨ Что умеет сервис", "callback_data": "menu:about"}],
            [{"text": "🆕 Новый клиент", "callback_data": "context:new"}],
        ]
        if has_client:
            keyboard.insert(0, [{"text": "▶️ Продолжить текущего клиента", "callback_data": "resume:yes"}])
            keyboard.insert(1, [{"text": "📝 Новая тема в этой нише", "callback_data": "draft:new"}])
        return keyboard

    def send_main_menu(self, chat_id: int) -> None:
        session = self.sessions.get(chat_id)
        has_client = bool(session and session.business_context)
        trial_block = ""
        if session and not self._has_active_subscription(chat_id):
            remaining = self._remaining_trial_posts(session, chat_id)
            trial_block = f"\n\n🧾 Trial: осталось бесплатных генераций {remaining}. Команда: /plan"
        text = (
            "Главное меню ГенПост.\n\n"
            "🚀 Как начать работу:\n"
            "1️⃣ Нажмите «Новый клиент» и опишите нишу\n"
            "2️⃣ Задайте цель поста (доверие, продажи, охваты)\n"
            "3️⃣ Выберите тему и получите готовый черновик\n\n"
            "✨ Что умеет сервис:\n"
            "✅ Подбор тем под вашу нишу\n"
            "✅ Генерация текста и изображения\n"
            "✅ Публикация в Telegram, VK или сразу в обе площадки"
            f"{trial_block}"
        )
        self.bot.send_message(chat_id, text, keyboard=self._main_menu_keyboard(has_client=has_client))

    def send_goal_prompt(self, chat_id: int) -> None:
        self.bot.send_message(
            chat_id,
            "Напиши, какую цель должен решать пост: прогрев, продажи, доверие, охваты, польза или что-то другое.",
            keyboard=self._with_navigation([], back_callback="back:context"),
        )

    def send_tone_prompt(self, chat_id: int, text: str = "Выбери тон будущего поста:") -> None:
        self.bot.send_message(
            chat_id,
            text,
            keyboard=self._with_navigation(self._tone_keyboard(), back_callback="back:goal"),
        )

    def send_platform_prompt(self, chat_id: int, topic: str) -> None:
        self.bot.send_message(
            chat_id,
            (
                f"Выбрана тема:\n\n{topic}\n\n"
                "Теперь выбери, куда готовить публикацию."
            ),
            keyboard=self._with_navigation(self._platform_keyboard(), back_callback="back:topics"),
        )

    def send_image_prompt(self, chat_id: int, platforms: list[str]) -> None:
        self.bot.send_message(
            chat_id,
            (
                f"Площадки: {self._platforms_label(platforms)}.\n\n"
                "Добавить изображение к посту?"
            ),
            keyboard=self._with_navigation(
                [
                    [
                        {"text": "Да, с картинкой", "callback_data": "image:yes"},
                        {"text": "Нет, только текст", "callback_data": "image:no"},
                    ]
                ],
                back_callback="back:platforms",
            ),
        )

    def send_image_preferences_prompt(self, chat_id: int) -> None:
        session = self.session_for(chat_id)
        self.bot.send_message(
            chat_id,
            (
                "Напиши своими словами, какой должна быть картинка.\n\n"
                "Опиши кто в кадре, что делает, какой свет, интерьер, настроение, ракурс и важные детали. "
                "Если не знаешь, с чего начать, нажми кнопку с примером по теме поста."
            ),
            keyboard=self._with_navigation(
                [
                    [{"text": "💡 Показать пример по теме", "callback_data": "imagehint:show"}],
                    [{"text": "Сгенерировать без своего описания", "callback_data": "imageprefs:skip"}],
                ],
                back_callback="back:image" if not session.generated_content else "back:imageprefs",
            ),
        )

    def handle_text(self, chat_id: int, text: str) -> None:
        session = self.session_for(chat_id)

        if session.stage == "await_context":
            session.business_context = text
            session.stage = "await_goal"
            self._save_sessions()
            self.send_goal_prompt(chat_id)
            return

        if session.stage == "await_goal":
            session.goal = text
            session.stage = "await_tone"
            self._save_sessions()
            self.send_tone_prompt(chat_id)
            return

        if session.stage == "await_image_preferences_text":
            session.image_preferences = text
            self._save_sessions()
            try:
                self.generate_draft(chat_id)
            except Exception as exc:
                self.bot.send_message(chat_id, f"Не удалось подготовить черновик: {exc}")
            return

        self.bot.send_message(
            chat_id,
            "Сейчас я жду выбор кнопкой. Можно использовать /reset для полного сброса или /new для нового поста в той же нише.",
        )

    def handle_callback(self, callback_query: dict) -> None:
        callback_id = callback_query["id"]
        message = callback_query.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        data = callback_query.get("data", "")
        if not chat_id or not data:
            return

        if data == "imagehint:show":
            session = self.session_for(chat_id)
            hint = build_image_prompt_hint(session.selected_topic)
            self.bot.answer_callback(callback_id, text="Показываю пример промпта")
            self.bot.send_message(
                chat_id,
                (
                    "💡 Пример промпта по теме:\n\n"
                    f"{hint}\n\n"
                    "Можно отправить свой вариант текстом, или нажать кнопку ниже."
                ),
                keyboard=self._with_navigation(
                    [[{"text": "✅ Использовать пример и сгенерировать", "callback_data": "imagehint:use"}]],
                    back_callback="back:image" if not session.generated_content else "back:imageprefs",
                ),
            )
            return

        self.bot.answer_callback(callback_id)

        if data.startswith("tone:"):
            self.handle_tone_choice(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("topic:"):
            self.handle_topic_choice(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("image:"):
            self.handle_image_choice(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("platform:"):
            self.handle_platform_choice(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("publish:"):
            self.handle_publish_choice(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("resume:"):
            self.handle_resume_choice(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("back:"):
            self.handle_back(chat_id, data.split(":", 1)[1])
            return
        if data.startswith("menu:"):
            self.handle_menu(chat_id, data.split(":", 1)[1])
            return
        if data == "topics:refresh":
            self.send_topic_choices(chat_id, refresh=True)
            return
        if data == "tone:change":
            self.session_for(chat_id).stage = "await_tone"
            self._save_sessions()
            self.send_tone_prompt(chat_id, text="Выбери новый тон поста:")
            return
        if data == "draft:regenerate":
            try:
                self.generate_draft(chat_id)
            except Exception as exc:
                self.bot.send_message(chat_id, f"Не удалось перегенерировать черновик: {exc}")
            return
        if data == "image:regenerate":
            try:
                self.regenerate_image(chat_id)
            except Exception as exc:
                self.bot.send_message(chat_id, f"Не удалось перегенерировать картинку: {exc}")
            return
        if data == "image:change":
            session = self.session_for(chat_id)
            session.stage = "await_image_preferences_text"
            self._save_sessions()
            self.send_image_preferences_prompt(chat_id)
            return
        if data == "imageprefs:skip":
            session = self.session_for(chat_id)
            session.image_preferences = ""
            self._save_sessions()
            try:
                if session.generated_content:
                    self.regenerate_image(chat_id)
                else:
                    self.generate_draft(chat_id)
            except Exception as exc:
                self.bot.send_message(chat_id, f"Не удалось подготовить изображение: {exc}")
            return
        if data == "imagehint:use":
            session = self.session_for(chat_id)
            session.image_preferences = build_image_prompt_hint(session.selected_topic)
            self._save_sessions()
            try:
                if session.generated_content:
                    self.regenerate_image(chat_id)
                else:
                    self.generate_draft(chat_id)
            except Exception as exc:
                self.bot.send_message(chat_id, f"Не удалось подготовить изображение: {exc}")
            return
        if data == "draft:new":
            self.restart_topic_flow(chat_id)
            return
        if data == "context:new":
            self.start_fresh_dialog(chat_id, from_menu=True)

    def handle_menu(self, chat_id: int, action: str) -> None:
        if action == "main":
            self.send_main_menu(chat_id)
            return
        if action == "about":
            self.bot.send_message(
                chat_id,
                (
                    "✨ ГенПост умеет:\n\n"
                    "1️⃣ Подобрать 5 сильных тем под вашу нишу\n"
                    "2️⃣ Дать выбрать тон поста\n"
                    "3️⃣ Сгенерировать текст и картинку\n"
                    "4️⃣ Перегенерировать текст/картинку в один клик\n"
                    "5️⃣ Опубликовать в Telegram, VK или сразу в обе площадки\n\n"
                    "🧭 Если не знаете, что нажимать дальше, жмите «🏠 Главное меню»."
                ),
                keyboard=self._main_menu_keyboard(
                    has_client=bool(self.sessions.get(chat_id) and self.sessions[chat_id].business_context)
                ),
            )

    def handle_back(self, chat_id: int, target: str) -> None:
        session = self.session_for(chat_id)

        if target == "context":
            session.stage = "await_context"
            self._save_sessions()
            self.bot.send_message(
                chat_id,
                "Вернулись к шагу выбора ниши. Опиши нишу, продукт или аудиторию клиента заново.",
                keyboard=self._with_navigation([], include_main_menu=True),
            )
            return

        if target == "goal":
            session.stage = "await_goal"
            self._save_sessions()
            self.send_goal_prompt(chat_id)
            return

        if target == "topics":
            session.stage = "await_topic"
            self._save_sessions()
            self.send_existing_topic_choices(chat_id)
            return

        if target == "platforms":
            session.stage = "await_platform_choice"
            self._save_sessions()
            self.send_platform_prompt(chat_id, session.selected_topic)
            return

        if target == "image":
            session.stage = "await_image_choice"
            self._save_sessions()
            self.send_image_prompt(chat_id, session.platforms)
            return

        if target == "imageprefs":
            session.stage = "await_image_preferences_text"
            self._save_sessions()
            self.send_image_preferences_prompt(chat_id)
            return

    def handle_resume_choice(self, chat_id: int, decision: str) -> None:
        if decision == "yes":
            self.restart_topic_flow(chat_id)
            return
        self.start_fresh_dialog(chat_id)

    def handle_tone_choice(self, chat_id: int, tone_key: str) -> None:
        session = self.session_for(chat_id)
        session.tone = TONE_OPTIONS.get(tone_key, TONE_OPTIONS["expert"])
        self._save_sessions()
        try:
            self.send_topic_choices(chat_id, refresh=False)
        except Exception as exc:
            self.bot.send_message(chat_id, f"Не удалось подобрать темы: {exc}")

    def send_existing_topic_choices(self, chat_id: int) -> None:
        session = self.session_for(chat_id)
        if not session.topics:
            self.send_topic_choices(chat_id, refresh=False)
            return

        keyboard = [
            [{"text": topic[:60], "callback_data": f"topic:{index}"}]
            for index, topic in enumerate(session.topics)
        ]
        keyboard.append([{"text": "Предложить другие темы", "callback_data": "topics:refresh"}])
        keyboard.append([{"text": "Изменить тон", "callback_data": "tone:change"}])
        keyboard = self._with_navigation(keyboard, back_callback="back:goal")

        self.bot.send_message(chat_id, "Вернулись к выбору темы. Выбери один из вариантов:", keyboard=keyboard)

    def send_topic_choices(self, chat_id: int, refresh: bool) -> None:
        session = self.session_for(chat_id)
        generator = ContentGeneratorService()

        self.bot.send_message(chat_id, "Подбираю темы для клиента, это займет несколько секунд...")
        consultant_message, topics = generator.suggest_topics(
            business_context=session.business_context,
            goal=session.goal or "получить идеи для полезных и продающих постов",
            tone=session.tone,
            count=5,
            excluded_topics=session.published_topics + session.recent_topics,
        )
        session.topics = topics
        session.selected_topic = ""
        session.stage = "await_topic"
        self._save_sessions()

        keyboard = [
            [{"text": topic[:60], "callback_data": f"topic:{index}"}]
            for index, topic in enumerate(topics)
        ]
        keyboard.append([{"text": "Предложить другие темы", "callback_data": "topics:refresh"}])
        keyboard.append([{"text": "Изменить тон", "callback_data": "tone:change"}])
        keyboard = self._with_navigation(keyboard, back_callback="back:goal")

        intro = "Подобрал новые темы." if refresh else "Вот темы, которые можно взять в работу."
        self.bot.send_message(
            chat_id,
            f"{consultant_message}\n\n{intro}\nВыбери одну тему:",
            keyboard=keyboard,
        )

    def handle_topic_choice(self, chat_id: int, index_value: str) -> None:
        session = self.session_for(chat_id)
        try:
            topic = session.topics[int(index_value)]
        except (ValueError, IndexError):
            self.bot.send_message(chat_id, "Не удалось определить выбранную тему. Попробуй выбрать ее снова.")
            return

        session.selected_topic = topic
        session.remember_topic(topic)
        session.stage = "await_platform_choice"
        self._save_sessions()
        self.send_platform_prompt(chat_id, topic)

    def handle_platform_choice(self, chat_id: int, platform_key: str) -> None:
        session = self.session_for(chat_id)
        session.platforms = PLATFORM_OPTIONS.get(platform_key, ["telegram"])
        session.stage = "await_image_choice"
        self._save_sessions()
        self.send_image_prompt(chat_id, session.platforms)

    def handle_image_choice(self, chat_id: int, image_choice: str) -> None:
        session = self.session_for(chat_id)
        session.include_image = image_choice == "yes"
        self._save_sessions()
        if session.include_image:
            session.stage = "await_image_preferences_text"
            self._save_sessions()
            self.send_image_preferences_prompt(chat_id)
            return
        try:
            self.generate_draft(chat_id)
        except Exception as exc:
            self.bot.send_message(chat_id, f"Не удалось подготовить черновик: {exc}")

    def generate_draft(self, chat_id: int) -> None:
        session = self.session_for(chat_id)
        if not self._has_active_subscription(chat_id):
            remaining = self._remaining_trial_posts(session, chat_id)
            if remaining <= 0:
                self._send_paywall(chat_id, session)
                return

        generator = ContentGeneratorService()

        self.bot.send_message(chat_id, "Генерирую черновик поста...")
        session.generated_content = generator.generate_post(session.selected_topic, session.tone)
        session.image_prompt = None
        session.image_url = None

        input_tokens = self._estimate_tokens(
            f"{session.selected_topic}\n{session.tone}\n{session.business_context}\n{session.goal}"
        )
        output_tokens = self._estimate_tokens(session.generated_content)
        image_count = 0

        if session.include_image:
            self.bot.send_message(chat_id, "Подбираю изображение к посту...")
            session.image_prompt = generator.generate_image_prompt(
                session.selected_topic,
                session.tone,
                session.generated_content,
                session.image_preferences,
            )
            session.image_url = generator.generate_image(session.image_prompt)
            input_tokens += self._estimate_tokens(session.image_preferences) + self._estimate_tokens(session.generated_content)
            output_tokens += self._estimate_tokens(session.image_prompt)
            image_count = 1

        if not self._has_active_subscription(chat_id):
            session.trial_posts_used += 1

        session.stage = "await_publish_choice"
        self._save_sessions()
        self._record_usage(
            chat_id=chat_id,
            action="generate_draft",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            images=image_count,
        )
        preview = session.generated_content[:3500]
        if len(session.generated_content) > 3500:
            preview += "\n\n🔹 Черновик сокращен в превью, но при публикации уйдет полный текст."

        keyboard = [
            [
                {"text": "Опубликовать сейчас", "callback_data": "publish:selected"},
                {"text": "Перегенерировать текст", "callback_data": "draft:regenerate"},
            ],
        ]
        if session.include_image:
            keyboard.append(
                [
                    {"text": "Перегенерировать картинку", "callback_data": "image:regenerate"},
                    {"text": "Параметры картинки", "callback_data": "image:change"},
                ]
            )
        keyboard.append(
            [
                {"text": "Новая тема в этой нише", "callback_data": "draft:new"},
                {"text": "Новый клиент", "callback_data": "context:new"},
            ]
        )
        keyboard = self._with_navigation(keyboard, back_callback="back:image")

        if session.image_url:
            preview += f"\n\n🔹 Изображение готово: {session.image_url}"
        if session.image_preferences:
            preview += f"\n🔹 Параметры картинки: {session.image_preferences}"
        if not self._has_active_subscription(chat_id):
            remaining = self._remaining_trial_posts(session, chat_id)
            preview += f"\n\n🧾 Осталось бесплатных генераций: {remaining}"

        self.bot.send_message(
            chat_id,
            (
                f"Черновик готов для площадок: {self._platforms_label(session.platforms)}.\n\n"
                f"{preview}"
            ),
            keyboard=keyboard,
        )

    def regenerate_image(self, chat_id: int) -> None:
        session = self.session_for(chat_id)
        if not session.include_image:
            self.bot.send_message(chat_id, "Для этого черновика изображение отключено. Сначала выбери вариант с картинкой.")
            return
        if not session.generated_content:
            self.bot.send_message(chat_id, "Сначала нужно подготовить текст поста.")
            return

        generator = ContentGeneratorService()
        self.bot.send_message(chat_id, "Перегенерирую изображение по текущим параметрам...")
        session.image_prompt = generator.generate_image_prompt(
            session.selected_topic,
            session.tone,
            session.generated_content,
            session.image_preferences,
        )
        session.image_url = generator.generate_image(session.image_prompt)
        self._save_sessions()
        self._record_usage(
            chat_id=chat_id,
            action="regenerate_image",
            input_tokens=self._estimate_tokens(session.generated_content) + self._estimate_tokens(session.image_preferences),
            output_tokens=self._estimate_tokens(session.image_prompt or ""),
            images=1,
        )

        keyboard = self._with_navigation(
            [
                [
                    {"text": "Опубликовать сейчас", "callback_data": "publish:selected"},
                    {"text": "Перегенерировать картинку", "callback_data": "image:regenerate"},
                ],
                [
                    {"text": "Параметры картинки", "callback_data": "image:change"},
                    {"text": "Перегенерировать текст", "callback_data": "draft:regenerate"},
                ],
            ],
            back_callback="back:image",
        )
        self.bot.send_message(
            chat_id,
            (
                "Картинка обновлена.\n\n"
                f"🔹 Параметры: {session.image_preferences or 'базовый фотореалистичный сценарий'}\n"
                f"🔹 Ссылка: {session.image_url}"
            ),
            keyboard=keyboard,
        )

    def handle_publish_choice(self, chat_id: int, target: str) -> None:
        session = self.session_for(chat_id)
        if not session.generated_content:
            self.bot.send_message(chat_id, "Сначала нужно подготовить черновик. Отправь /start.")
            return

        results: list[str] = []
        target_platforms = session.platforms if target == "selected" else PLATFORM_OPTIONS.get(target, session.platforms)
        successful_platforms: list[str] = []

        if "telegram" in target_platforms:
            try:
                TelegramPublisher(chat_id=str(chat_id)).publish(
                    session.generated_content,
                    session.image_url,
                )
                results.append("Telegram: успешно")
                successful_platforms.append("telegram")
            except Exception as exc:
                results.append(f"Telegram: ошибка - {exc}")

        if "vk" in target_platforms:
            try:
                VKPublisher().publish(session.generated_content, session.image_url)
                results.append("VK: успешно")
                successful_platforms.append("vk")
            except Exception as exc:
                results.append(f"VK: ошибка - {exc}")

        if successful_platforms:
            session.remember_published_topic(session.selected_topic)
            self._save_sessions()

        self.bot.send_message(
            chat_id,
            (
                "Результат публикации:\n\n"
                + "\n".join(f"✅ {item}" for item in results)
                + (
                    f"\n\n🔹 Я запомнил тему '{session.selected_topic}' и постараюсь не предлагать ее повторно."
                    if successful_platforms
                    else ""
                )
            ),
            keyboard=[
                [{"text": "Новая тема в этой нише", "callback_data": "draft:new"}],
                [{"text": "Новый клиент", "callback_data": "context:new"}],
            ],
        )


if __name__ == "__main__":
    GenPostDialogBot().run()
