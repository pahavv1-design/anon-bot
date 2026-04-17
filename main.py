import os
import sqlite3
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# Настройки
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")  # ID канала для проверки подписки
CHANNEL_URL = os.getenv("CHANNEL_URL") # Ссылка на канал

bot = Bot(token=TOKEN)
dp = Dispatcher()

class States(StatesGroup):
    writing_msg = State()
    broadcasting = State()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, clicks INTEGER DEFAULT 0, received INTEGER DEFAULT 0)')
    cursor.execute('CREATE TABLE IF NOT EXISTS messages (msg_key TEXT PRIMARY KEY, s_id INTEGER, r_id INTEGER, s_mid INTEGER, r_mid INTEGER)')
    conn.commit()
    conn.close()

def db_query(query, params=(), fetchone=False):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = cursor.fetchone() if fetchone else None
    conn.commit()
    conn.close()
    return res

# --- ПРОВЕРКА ПОДПИСКИ ---
async def check_sub(user_id):
    if not CHANNEL_ID: return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return True

# --- КЛАВИАТУРА ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔗 Моя ссылка"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📢 Админ-панель")] if True else [] # Кнопка будет у всех, но сработает только у админа
    ], resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    db_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("user"):
        target_id = int(args[1].replace("user", ""))
        
        # Проверка подписки перед тем как написать
        if not await check_sub(user_id):
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="Подписаться", url=CHANNEL_URL))
            return await message.answer(f"❌ Чтобы отправить сообщение, подпишись на наш канал!", reply_markup=kb.as_markup())

        if target_id == user_id:
            return await message.answer("Нельзя писать самому себе.")
        
        await state.update_data(target_id=target_id)
        await state.set_state(States.writing_msg)
        return await message.answer("🤫 Напиши анонимное сообщение для этого пользователя:")

    bot_info = await bot.get_me()
    link = f"t.me/{bot_info.username}?start=user{user_id}"
    await message.answer(f"<b>Твоя ссылка для сообщений:</b>\n<code>{link}</code>", parse_mode="HTML", reply_markup=main_kb())

@dp.message(F.text == "🔗 Моя ссылка")
async def my_link(message: types.Message):
    await start(message, None)

@dp.message(F.text == "📊 Статистика")
async def stats(message: types.Message):
    res = db_query("SELECT clicks, received FROM users WHERE user_id = ?", (message.from_user.id,), fetchone=True)
    clicks, received = res if res else (0, 0)
    text = f"📌 <b>Статистика</b>\n\n💬 Получено: {received}\n👀 Переходов: {clicks}\n⭐ Популярность: 1000+ место"
    await message.answer(text, parse_mode="HTML")

# --- ОТПРАВКА СООБЩЕНИЯ ---
@dp.message(States.writing_msg)
async def process_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    t_id = data['target_id']
    
    try:
        # Отправка получателю
        sent = await bot.send_message(t_id, f"🎁 <b>Новое анонимное сообщение:</b>\n\n{message.text}", parse_mode="HTML")
        
        # Лог админу
        await bot.send_message(ADMIN_ID, f"🕵️ <b>ЛОГ:</b>\nОт: @{message.from_user.username} ({message.from_user.id})\nКому: {t_id}\nТекст: {message.text}")

        # Кнопка удаления для отправителя
        msg_key = f"del_{sent.message_id}"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🗑 Удалить у всех", callback_data=msg_key))
        
        confirm = await message.answer("✅ Отправлено!", reply_markup=kb.as_markup())
        
        db_query("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", (msg_key, message.from_user.id, t_id, confirm.message_id, sent.message_id))
        db_query("UPDATE users SET received = received + 1 WHERE user_id = ?", (t_id,))
    except:
        await message.answer("❌ Ошибка отправки (возможно, бот в блоке).")
    await state.clear()

# --- УДАЛЕНИЕ ---
@dp.callback_query(F.data.startswith("del_"))
async def delete_sync(call: types.CallbackQuery):
    data = db_query("SELECT * FROM messages WHERE msg_key = ?", (call.data,), fetchone=True)
    if data:
        try:
            await bot.delete_message(data[2], data[4]) # у получателя
            await bot.delete_message(data[1], data[3]) # подтверждение у отправителя
        except: pass
        db_query("DELETE FROM messages WHERE msg_key = ?", (call.data,))

# --- АДМИН ПАНЕЛЬ ---
@dp.message(F.text == "📢 Админ-панель")
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 Начать рассылку", callback_data="start_broadcast"))
    await message.answer("Добро пожаловать в админку!", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "start_broadcast")
async def broadcast_step1(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(States.broadcasting)
    await call.message.answer("Введите текст рассылки:")

@dp.message(States.broadcasting)
async def broadcast_step2(message: types.Message, state: FSMContext):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    users = cursor.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    
    count = 0
    await message.answer("📢 Рассылка запущена...")
    for user in users:
        try:
            await bot.send_message(user[0], message.text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    
    await message.answer(f"✅ Рассылка завершена. Получили {count} пользователей.")
    await state.clear()

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
