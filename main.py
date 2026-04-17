import os
import sqlite3
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Переменные из Bothost
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

bot = Bot(token=TOKEN)
dp = Dispatcher()

class AnonState(StatesGroup):
    writing_message = State()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, clicks INTEGER DEFAULT 0, received INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS messages 
                      (msg_key TEXT PRIMARY KEY, sender_id INTEGER, recipient_id INTEGER, 
                       sender_msg_id INTEGER, recipient_msg_id INTEGER)''')
    conn.commit()
    conn.close()

def execute_query(query, params=()):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def fetch_one(query, params=()):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = cursor.fetchone()
    conn.close()
    return res

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    args = message.text.split()
    
    execute_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

    # Если перешли по ссылке
    if len(args) > 1 and args[1].startswith("user"):
        target_id = args[1].replace("user", "")
        if target_id.isdigit():
            target_id = int(target_id)
            if target_id == user_id:
                await message.answer("❌ Нельзя писать самому себе!")
                return
            
            execute_query("UPDATE users SET clicks = clicks + 1 WHERE user_id = ?", (target_id,))
            await state.update_data(target_id=target_id)
            await state.set_state(AnonState.writing_message)
            await message.answer("🤫 <b>Напиши анонимное сообщение:</b>\n\nЕго увидит только получатель.", parse_mode="HTML")
            return

    # Главное меню
    bot_user = await bot.get_me()
    link = f"t.me/{bot_user.username}?start=user{user_id}"
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔗 Поделиться ссылкой", url=f"https://t.me/share/url?url={link}&text=Напиши мне анонимно!"))
    
    text = (
        f"<b>Начни получать анонимные сообщения прямо сейчас 🚀</b>\n\n"
        f"Твоя ссылка 👇\n<code>{link}</code>\n\n"
        f"Размести эту ссылку 👆 в профиле, чтобы начать получать сообщения 💬"
    )
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")

@dp.message(Command("mystats"))
async def cmd_stats(message: types.Message):
    data = fetch_one("SELECT clicks, received FROM users WHERE user_id = ?", (message.from_user.id,))
    clicks, received = data if data else (0, 0)
    
    text = (
        "📌 <b>Статистика профиля</b>\n\n"
        "—— Сегодня:\n"
        f"💬 Сообщений: {received}\n"
        f"👀 Переходов по ссылке: {clicks}\n"
        f"⭐ Популярность: 1000+ место\n\n"
        "—— За всё время:\n"
        f"💬 Сообщений: {received}\n"
        f"👀 Переходов по ссылке: {clicks}\n"
        f"⭐ Популярность: 1000+ место"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(AnonState.writing_message)
async def process_anon_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data['target_id']
    
    try:
        # Отправка получателю
        sent = await bot.send_message(target_id, f"🎁 <b>Новое анонимное сообщение:</b>\n\n{message.text}", parse_mode="HTML")
        
        # Лог для админа (ты увидишь КТО отправил)
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"🕵️ <b>LOG:</b>\nОт: @{message.from_user.username} (ID: {message.from_user.id})\nКому: ID {target_id}\nТекст: {message.text}")
            except: pass

        # Кнопка удаления для отправителя
        msg_key = f"del_{sent.message_id}"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🗑 Удалить сообщение", callback_data=msg_key))
        
        confirm = await message.answer("✅ Сообщение отправлено!", reply_markup=kb.as_markup())

        execute_query("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", 
                      (msg_key, message.from_user.id, target_id, confirm.message_id, sent.message_id))
        execute_query("UPDATE users SET received = received + 1 WHERE user_id = ?", (target_id,))
        
    except Exception as e:
        await message.answer("❌ Ошибка отправки. Бот заблокирован пользователем.")
    
    await state.clear()

@dp.callback_query(F.data.startswith("del_"))
async def on_delete(callback: types.CallbackQuery):
    data = fetch_one("SELECT * FROM messages WHERE msg_key = ?", (callback.data,))
    if data:
        _, sender_id, recip_id, s_msg_id, r_msg_id = data
        try:
            await bot.delete_message(recip_id, r_msg_id) # Удаляем у получателя
            await bot.delete_message(sender_id, s_msg_id) # Удаляем подтверждение у отправителя
            await callback.answer("Удалено везде")
        except:
            await callback.answer("Ошибка или уже удалено", show_alert=True)
        execute_query("DELETE FROM messages WHERE msg_key = ?", (callback.data,))

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
