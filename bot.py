import asyncio
import os
import random
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ChatType

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН_СЮДА")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

games: dict[int, dict] = {}

TYPE_LABEL = {
    "photo": "[фото]",
    "video": "[видео]",
    "video_note": "[видео-кружок]",
    "voice": "[голосовое]",
    "document": "[документ]",
}


def new_game(chat_id: int) -> dict:
    return {
        "chat_id": chat_id,
        "state": "waiting",   # waiting | starting | question | answering | reveal | confirming | lastwords | elimination_after | finished
        "groom_id": None,
        "next_number": 1,
        "brides": {},         # uid -> {"name","number","alive"}
        "question": None,     # dict content
        "answers": {},        # uid -> dict content
        "round": 0,
        "shuffled": [],       # [(number, uid)]
        "pending_lastwords": None,
        "seen_media_groups": set(),
    }


def alive_brides(g):
    return [u for u, d in g["brides"].items() if d["alive"]]


def extract_content(msg: Message) -> dict | None:
    if msg.photo:
        return {"type": "photo", "file_id": msg.photo[-1].file_id, "text": msg.caption}
    if msg.video:
        return {"type": "video", "file_id": msg.video.file_id, "text": msg.caption}
    if msg.video_note:
        return {"type": "video_note", "file_id": msg.video_note.file_id, "text": None}
    if msg.voice:
        return {"type": "voice", "file_id": msg.voice.file_id, "text": msg.caption}
    if msg.document:
        return {"type": "document", "file_id": msg.document.file_id, "text": msg.caption}
    if msg.text:
        return {"type": "text", "file_id": None, "text": msg.text}
    return None


def content_preview(content: dict) -> str:
    """Короткое текстовое представление контента для сводки."""
    t = content["type"]
    txt = (content["text"] or "").strip()
    if t == "text":
        return txt
    label = TYPE_LABEL.get(t, "[материал]")
    if txt:
        return f"{label} {txt}"
    return f"{label} (прикрепила материал)"


async def send_media_only(chat_id: int, content: dict, caption_prefix: str):
    """Отправляет только медиа (для отдельного сообщения после сводки)."""
    t = content["type"]
    fid = content["file_id"]
    txt = (content["text"] or "").strip()
    caption = caption_prefix + ("\n" + txt if txt else "")

    if t == "photo":
        await bot.send_photo(chat_id, fid, caption=caption)
    elif t == "video":
        await bot.send_video(chat_id, fid, caption=caption)
    elif t == "video_note":
        await bot.send_message(chat_id, caption)
        await bot.send_video_note(chat_id, fid)
    elif t == "voice":
        await bot.send_message(chat_id, caption)
        await bot.send_voice(chat_id, fid)
    elif t == "document":
        await bot.send_document(chat_id, fid, caption=caption)


# ---------- Команды в общем чате ----------

@dp.message(Command("startgame"))
async def cmd_startgame(msg: Message):
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await msg.answer("Эту команду нужно вызывать в группе.")
        return
    chat_id = msg.chat.id
    if chat_id in games and games[chat_id]["state"] != "finished":
        await msg.answer("Игра уже идёт. Дождитесь окончания.")
        return
    games[chat_id] = new_game(chat_id)
    await msg.answer(
        "🎮 Игра «Невесты» создана!\n"
        "Участники, напишите /join чтобы войти.\n"
        "Когда все зашли — /begin."
    )


@dp.message(Command("join"))
async def cmd_join(msg: Message):
    chat_id = msg.chat.id
    g = games.get(chat_id)
    if not g:
        await msg.answer("Сначала создайте игру: /startgame")
        return
    if g["state"] != "waiting":
        await msg.answer("Игра уже началась, присоединиться нельзя.")
        return
    uid = msg.from_user.id
    if uid in g["brides"]:
        await msg.answer("Ты уже в игре.")
        return
    number = g["next_number"]
    g["next_number"] += 1
    g["brides"][uid] = {"name": msg.from_user.full_name, "number": number, "alive": True}
    await msg.answer(
        f"✅ {msg.from_user.full_name} в игре под номером {number}. "
        f"(всего: {len(g['brides'])})"
    )


@dp.message(Command("begin"))
async def cmd_begin(msg: Message):
    chat_id = msg.chat.id
    g = games.get(chat_id)
    if not g or g["state"] != "waiting":
        await msg.answer("Сейчас нельзя начать (игра уже идёт или нет игры).")
        return
    if len(g["brides"]) < 2:
        await msg.answer("Нужно минимум 2 участника.")
        return

    g["state"] = "starting"  # защита от двойного /begin

    all_uids = list(g["brides"].keys())
    groom_id = random.choice(all_uids)
    remaining = [u for u in all_uids if u != groom_id]
    for i, uid in enumerate(remaining, start=1):
        g["brides"][uid]["number"] = i
    g["next_number"] = len(remaining) + 1

    g["groom_id"] = groom_id
    del g["brides"][groom_id]

    try:
        await bot.send_message(
            groom_id,
            "💍 Ты — Жених!\n"
            "Присылай сюда вопросы по одному (текст, фото, видео, голос, кружок, документ).\n"
            "После публикации ответов выбери кнопкой в чате, кто выбывает."
        )
    except Exception:
        g["state"] = "waiting"
        g["groom_id"] = None
        await msg.answer(
            "⚠️ Не смог написать жениху в ЛС. Пусть он нажмёт /start у бота и повторите /begin."
        )
        return

    g["state"] = "question"
    g["round"] = 1

    await msg.answer(
        f"💍 Жених выбран (анонимно)!\n"
        f"Невест: {len(g['brides'])}.\n\n"
        f"Жених, пришли мне в ЛИЧКУ первый вопрос."
    )


# ---------- ЛС ----------

@dp.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_private_start(msg: Message):
    await msg.answer(
        "Привет! Я бот игры «Невесты».\n"
        "Жених — присылай сюда вопросы.\n"
        "Невеста — присылай сюда ответы (текст, фото, видео, голос, кружок, документ)."
    )


@dp.message(F.chat.type == ChatType.PRIVATE)
async def private_any(msg: Message):
    uid = msg.from_user.id

    # Альбомы: обрабатываем только первое сообщение группы
    if msg.media_group_id:
        for g in games.values():
            key = (msg.chat.id, msg.media_group_id)
            if key in g["seen_media_groups"]:
                return
            g["seen_media_groups"].add(key)

    content = extract_content(msg)
    if not content:
        await msg.answer(
            "Поддерживаются: текст, фото, видео, кружок, голосовое, документ."
        )
        return

    for chat_id, g in games.items():

        # --- Жених присылает вопрос ---
        if g["groom_id"] == uid and g["state"] == "question":
            g["question"] = content
            g["answers"] = {}
            g["state"] = "answering"

            # 1) Сводка
            if content["type"] == "text":
                await bot.send_message(
                    chat_id,
                    f"❓ Вопрос от Жениха (раунд {g['round']}):\n\n{content['text']}",
                )
            else:
                await bot.send_message(
                    chat_id,
                    f"❓ Вопрос от Жениха (раунд {g['round']}):\n\n"
                    f"{content_preview(content)}",
                )
                # 2) Отдельное медиа
                await send_media_only(
                    chat_id, content, "Жених прикрепил материал:"
                )

            await bot.send_message(chat_id, "Невесты, отвечайте мне в ЛС. Ждём всех.")
            await msg.answer("Вопрос отправлен в чат.")
            return

        # --- Невеста отвечает ---
        if g["state"] == "answering" and uid in g["brides"] and g["brides"][uid]["alive"]:
            if uid in g["answers"]:
                await msg.answer("Ты уже ответила на этот вопрос.")
                return
            g["answers"][uid] = content
            alive = alive_brides(g)
            await msg.answer(f"Ответ принят ({len(g['answers'])}/{len(alive)}).")
            if all(u in g["answers"] for u in alive):
                await publish_answers(chat_id, g)
            return

        # --- Последние слова ---
        if g["state"] == "lastwords" and g["pending_lastwords"] == uid:
            number = g["brides"][uid]["number"]
            g["pending_lastwords"] = None
            g["state"] = "elimination_after"

            if content["type"] == "text":
                await bot.send_message(
                    chat_id,
                    f"💬 Последние слова Невесты {number}:\n\n{content['text']}",
                )
            else:
                await bot.send_message(
                    chat_id,
                    f"💬 Последние слова Невесты {number}:\n\n"
                    f"{content_preview(content)}",
                )
                await send_media_only(
                    chat_id, content, f"Невеста {number} прикрепила материал:"
                )

            await msg.answer("Последние слова опубликованы.")
            await continue_after_elimination(chat_id, g)
            return


# ---------- Публикация ответов ----------

async def publish_answers(chat_id: int, g: dict):
    items = sorted(
        g["answers"].items(),
        key=lambda kv: g["brides"][kv[0]]["number"],
    )
    g["shuffled"] = [(g["brides"][u]["number"], u) for u, _ in items]

    # 1) Сводное сообщение
    lines = [f"📨 Ответы невест (раунд {g['round']}):\n"]
    for num, uid in g["shuffled"]:
        lines.append(f"Невеста {num}: {content_preview(g['answers'][uid])}")
    await bot.send_message(chat_id, "\n\n".join(lines))

    # 2) Отдельные медиа-сообщения
    for num, uid in g["shuffled"]:
        content = g["answers"][uid]
        if content["type"] != "text":
            await send_media_only(
                chat_id, content, f"Невеста {num} прикрепила материал:"
            )

    # 3) Кнопки выбывания
    g["state"] = "reveal"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Выгнать Невесту {n}", callback_data=f"elim:{n}")]
        for n, _ in g["shuffled"]
    ])
    await bot.send_message(
        chat_id,
        "Жених, выбери, кто выбывает:",
        reply_markup=kb,
    )


# ---------- Выбывание: 2 шага ----------

@dp.callback_query(F.data.startswith("elim:"))
async def cb_eliminate(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    g = games.get(chat_id)
    if not g or g["state"] != "reveal":
        await cb.answer("Сейчас нельзя.")
        return
    if cb.from_user.id != g["groom_id"]:
        await cb.answer("Только Жених выбирает!")
        return

    num = int(cb.data.split(":")[1])
    if num not in dict(g["shuffled"]):
        await cb.answer("Нет такой невесты.")
        return

    g["state"] = "confirming"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{num}"),
            InlineKeyboardButton(text="↩️ Отмена", callback_data="cancel"),
        ]
    ])
    await cb.message.edit_text(
        f"Вы уверены, что выгоняете Невесту {num}?",
        reply_markup=kb,
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    g = games.get(chat_id)
    if not g or g["state"] != "confirming":
        await cb.answer("Уже неактуально.")
        return
    if cb.from_user.id != g["groom_id"]:
        await cb.answer("Только Жених!")
        return

    num = int(cb.data.split(":")[1])
    uid = dict(g["shuffled"]).get(num)
    if not uid:
        await cb.answer("Ошибка.")
        return

    g["brides"][uid]["alive"] = False
    await cb.message.edit_text(f"❌ Невеста {num} покидает игру.")
    await cb.answer()

    g["state"] = "lastwords"
    g["pending_lastwords"] = uid
    try:
        await bot.send_message(
            uid,
            f"Тебя выгнали 😢 Напиши сюда последние слова "
            f"(текст, фото, видео, голос) — опубликую в чате от Невесты {num}."
        )
    except Exception:
        await bot.send_message(chat_id, f"(Невеста {num} не смогла написать последние слова.)")
        g["pending_lastwords"] = None
        g["state"] = "elimination_after"
        await continue_after_elimination(chat_id, g)


@dp.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery):
    chat_id = cb.message.chat.id
    g = games.get(chat_id)
    if not g or g["state"] != "confirming":
        await cb.answer("Уже неактуально.")
        return
    if cb.from_user.id != g["groom_id"]:
        await cb.answer("Только Жених!")
        return

    g["state"] = "reveal"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Выгнать Невесту {n}", callback_data=f"elim:{n}")]
        for n, _ in g["shuffled"]
    ])
    await cb.message.edit_text(
        "Жених, выбери, кто выбывает:",
        reply_markup=kb,
    )
    await cb.answer()


async def continue_after_elimination(chat_id: int, g: dict):
    alive = alive_brides(g)
    if len(alive) == 1:
        await finish_game(chat_id, g)
        return
    if len(alive) == 0:
        await bot.send_message(chat_id, "Все невесты выбыли. Игра окончена.")
        games.pop(chat_id, None)
        return

    g["round"] += 1
    g["state"] = "question"
    g["answers"] = {}
    g["question"] = None
    await bot.send_message(chat_id, "Жених, пришли следующий вопрос мне в ЛС.")
    if g["groom_id"]:
        try:
            await bot.send_message(g["groom_id"], "Жду следующий вопрос.")
        except Exception:
            pass


# ---------- Финал ----------

async def finish_game(chat_id: int, g: dict):
    winner_uid = alive_brides(g)[0]
    winner = g["brides"][winner_uid]

    lines = ["🎉 Игра окончена!", ""]
    lines.append(f"👑 Победительница: Невеста {winner['number']} — {winner['name']}")
    lines.append("")
    lines.append("Раскрытие участниц:")
    ordered = sorted(g["brides"].items(), key=lambda kv: kv[1]["number"])
    for uid, d in ordered:
        status = "победила 👑" if d["alive"] else "выбыла"
        lines.append(f"— Невеста {d['number']}: {d['name']} ({status})")

    groom_name = "неизвестно"
    try:
        member = await bot.get_chat_member(chat_id, g["groom_id"])
        groom_name = member.user.full_name
    except Exception:
        pass
    lines.append("")
    lines.append(f"💍 Жених: {groom_name}")

    g["state"] = "finished"
    await bot.send_message(chat_id, "\n".join(lines))
    games.pop(chat_id, None)


# ---------- Прочее ----------

@dp.message(Command("cancel"))
async def cmd_cancel(msg: Message):
    chat_id = msg.chat.id
    g = games.get(chat_id)
    if not g:
        await msg.answer("Нет активной игры.")
        return
    games.pop(chat_id, None)
    await msg.answer("Игра отменена, все данные удалены.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())