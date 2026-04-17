import os
import sqlite3
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Настройка логов для Bothost
logging.basicConfig(level=logging.INFO)

# Переменные
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW and ADMIN_ID_RAW.isdigit() else None

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

def db_query(query, params=(), fetchone=False, fetchall=False):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    if fetchone: res = cursor.fetchone()
    elif fetchall: res = cursor.fetchall()
    else: res = None
    conn.commit()
    conn.close()
    return res

# --- ОБЫЧНОЕ МЕНЮ (КНОПКИ ВНИЗУ) ---
def get_main_kb(user_id):
    buttons = [
        [KeyboardButton(text="🔗 Моя ссылка"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🌐 Язык"), KeyboardButton(text="💡 Идея")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="📢 Рассылка")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- ОБРАБОТЧИКИ КОМАНД ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    db_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("user"):
        target_id = int(args[1].replace("user", ""))
        if target_id == user_id:
            return await message.answer("Нельзя писать самому себе!")
        
        db_query("UPDATE users SET clicks = clicks + 1 WHERE user_id = ?", (target_id,))
        await state.update_data(target_id=target_id)
        await state.set_state(States.writing_msg)
        return await message.answer("🤫 Напиши анонимное сообщение для пользователя:", reply_markup=types.ReplyKeyboardRemove())

    bot_info = await bot.get_me()
    link = f"t.me/{bot_info.username}?start=user{user_id}"
    await message.answer(f"🚀 <b>Твоя ссылка:</b>\n<code>{link}</code>", parse_mode="HTML", reply_markup=get_main_kb(user_id))

@dp.message(F.text == "📊 Статистика")
@dp.message(Command("mystats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    res = db_query("SELECT clicks, received FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    clicks, received = res if res else (0, 0)
    
    # Расчет места в рейтинге
    rank_res = db_query("SELECT COUNT(*) + 1 FROM users WHERE received > (SELECT received FROM users WHERE user_id = ?)", (user_id,), fetchone=True)
    rank = rank_res[0] if rank_res else 1

    text = (
        "📌 <b>Статистика</b>\n\n"
        f"💬 Сообщений получено: {received}\n"
        f"👀 Переходов по ссылке: {clicks}\n"
        f"⭐ Место в рейтинге: {rank}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🔗 Моя ссылка")
@dp.message(Command("url"))
async def cmd_url(message: types.Message):
    bot_info = await bot.get_me()
    link = f"t.me/{bot_info.username}?start=user{message.from_user.id}"
    await message.answer(f"Твоя ссылка для анонимных сообщений:\n<code>{link}</code>", parse_mode="HTML")

@dp.message(F.text == "🌐 Язык")
@dp.message(Command("lang"))
async def cmd_lang(message: types.Message):
    await message.answer("🇷🇺 Текущий язык: <b>Русский</b>", parse_mode="HTML")

@dp.message(F.text == "💡 Идея")
@dp.message(Command("issue"))
async def cmd_issue(message: types.Message):
    await message.answer("Если у вас есть идеи или проблемы, пишите админу: @ваш_ник")

# --- ОТПРАВКА И УДАЛЕНИЕ ---
@dp.message(States.writing_msg)
async def process_anon_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    t_id = data['target_id']
    s_id = message.from_user.id
    
    try:
        # Отправка получателю
        r_msg = await bot.send_message(t_id, f"🎁 <b>Новое анонимное сообщение:</b>\n\n{message.text}", parse_mode="HTML")
        
        # Кнопка удаления для отправителя
        msg_key = f"del_{r_msg.message_id}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Удалить сообщение", callback_data=msg_key)]])
        
        s_msg = await message.answer("✅ Отправлено!", reply_markup=kb)
        
        # Сохраняем для удаления
        db_query("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", (msg_key, s_id, t_id, s_msg.message_id, r_msg.message_id))
        db_query("UPDATE users SET received = received + 1 WHERE user_id = ?", (t_id,))
        
        # Лог админу
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, f"🕵️ <b>ЛОГ:</b>\nОт: {s_id}\nКому: {t_id}\nТекст: {message.text}")
            
    except:
        await message.answer("❌ Ошибка отправки.")
    
    await state.clear()
    await cmd_start(message, state) # Возвращаем меню

@dp.callback_query(F.data.startswith("del_"))
async def on_delete(call: types.CallbackQuery):
    data = db_query("SELECT * FROM messages WHERE msg_key = ?", (call.data,), fetchone=True)
    if data:
        try:
            await bot.delete_message(data[2], data[4]) # у получателя
            await bot.delete_message(data[1], data[3]) # сообщение "Отправлено"
            await call.answer("Сообщение удалено.")
        except:
            await call.answer("Ошибка при удалении.")
        db_query("DELETE FROM messages WHERE msg_key = ?", (call.data,))
    else:
        await call.answer("Уже удалено.")

# --- РАССЫЛКА ДЛЯ АДМИНА ---
@dp.message(F.text == "📢 Рассылка")
async def start_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Введите текст рассылки:")
    await state.set_state(States.broadcasting)

@dp.message(States.broadcasting)
async def do_broadcast(message: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    await message.answer(f"Рассылка на {len(users)} пользователей...")
    for user in users:
        try:
            await bot.send_message(user[0], message.text)
            await asyncio.sleep(0.05)
        except: pass
    await message.answer("✅ Готово!")
    await state.clear()

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
