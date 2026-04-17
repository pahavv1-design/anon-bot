import os
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# Настройки Bothost
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()

class States(StatesGroup):
    writing_msg = State()
    broadcasting = State()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    # Храним пользователей
    cursor.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, clicks INTEGER DEFAULT 0, received INTEGER DEFAULT 0)')
    # Храним ключи сообщений для удаления (sender_msg_id - это ID сообщения с кнопкой "Удалить")
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

# --- ЛОГИКА РЕАЛЬНОГО РЕЙТИНГА ---
def get_user_rank(user_id):
    # Считаем место: сколько людей имеют сообщений больше, чем этот юзер + 1
    res = db_query('''
        SELECT COUNT(*) + 1 FROM users 
        WHERE received > (SELECT received FROM users WHERE user_id = ?)
    ''', (user_id,), fetchone=True)
    return res[0] if res else 1

# --- ПРОВЕРКА ПОДПИСКИ ---
async def check_sub(user_id):
    if not CHANNEL_ID: return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return True

# --- ГЛАВНОЕ МЕНЮ ---
def main_kb(user_id):
    buttons = [
        [KeyboardButton(text="🔗 Моя ссылка"), KeyboardButton(text="📊 Статистика")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="📢 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    db_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("user"):
        target_id = int(args[1].replace("user", ""))
        
        if not await check_sub(user_id):
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="Подписаться", url=CHANNEL_URL))
            return await message.answer(f"❌ Чтобы отправить сообщение, подпишись на канал!", reply_markup=kb.as_markup())
            
        if target_id == user_id:
            return await message.answer("Вы не можете писать самому себе.")
        
        # Увеличиваем счетчик переходов получателю
        db_query("UPDATE users SET clicks = clicks + 1 WHERE user_id = ?", (target_id,))
        
        await state.update_data(target_id=target_id)
        await state.set_state(States.writing_msg)
        return await message.answer("🤫 Напиши анонимное сообщение:")

    bot_info = await bot.get_me()
    link = f"t.me/{bot_info.username}?start=user{user_id}"
    await message.answer(f"<b>Твоя ссылка для сообщений:</b>\n<code>{link}</code>", parse_mode="HTML", reply_markup=main_kb(user_id))

@dp.message(F.text == "📊 Статистика")
@dp.message(Command("mystats"))
async def stats(message: types.Message):
    user_id = message.from_user.id
    res = db_query("SELECT clicks, received FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    clicks, received = res if res else (0, 0)
    
    rank = get_user_rank(user_id) # Реальное место без лимитов
    
    text = (
        "📌 <b>Статистика профиля</b>\n\n"
        f"💬 Получено сообщений: {received}\n"
        f"👀 Переходов по ссылке: {clicks}\n"
        f"⭐ Твое место в рейтинге: {rank}\n\n"
        "Распространяй ссылку, чтобы подняться в топе! 🚀"
    )
    await message.answer(text, parse_mode="HTML")

# --- ОТПРАВКА И УДАЛЕНИЕ ---
@dp.message(States.writing_msg)
async def process_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    t_id = data['target_id']
    s_id = message.from_user.id
    
    try:
        # Отправляем получателю
        r_msg = await bot.send_message(t_id, f"🎁 <b>Новое анонимное сообщение:</b>\n\n{message.text}", parse_mode="HTML")
        
        # Лог админу (ты видишь отправителя)
        await bot.send_message(ADMIN_ID, f"🕵️ <b>ЛОГ:</b>\nОт: @{message.from_user.username} (ID: {s_id})\nКому: {t_id}\nТекст: {message.text}")

        # Кнопка удаления для отправителя
        msg_key = f"del_{r_msg.message_id}" # уникальный ключ
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🗑 Удалить у всех", callback_data=msg_key))
        
        s_msg = await message.answer("✅ Сообщение доставлено!", reply_markup=kb.as_markup())
        
        # Сохраняем ID обоих сообщений в базу
        db_query("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", (msg_key, s_id, t_id, s_msg.message_id, r_msg.message_id))
        db_query("UPDATE users SET received = received + 1 WHERE user_id = ?", (t_id,))
    except:
        await message.answer("❌ Не удалось отправить (бот в блоке у юзера).")
    
    await state.clear()

@dp.callback_query(F.data.startswith("del_"))
async def delete_sync(call: types.CallbackQuery):
    data = db_query("SELECT * FROM messages WHERE msg_key = ?", (call.data,), fetchone=True)
    if data:
        msg_key, s_id, r_id, s_mid, r_mid = data
        # Удаляем у получателя
        try: await bot.delete_message(r_id, r_mid)
        except: pass
        # Удаляем подтверждение у отправителя
        try: await bot.delete_message(s_id, s_mid)
        except: pass
        
        await call.answer("Сообщение удалено у всех.")
        db_query("DELETE FROM messages WHERE msg_key = ?", (msg_key,))
    else:
        await call.answer("Сообщение уже удалено.")

# --- ОСТАЛЬНЫЕ КОМАНДЫ ---
@dp.message(Command("url"))
async def cmd_url(message: types.Message):
    await start(message, None)

@dp.message(F.text == "🔗 Моя ссылка")
async def cmd_url_btn(message: types.Message):
    await start(message, None)

@dp.message(F.text == "📢 Админ-панель")
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 Начать рассылку", callback_data="start_broadcast"))
    await message.answer("Меню администратора:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "start_broadcast")
async def broadcast_step1(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(States.broadcasting)
    await call.message.answer("Введите текст рассылки для всех пользователей:")

@dp.message(States.broadcasting)
async def broadcast_step2(message: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    count = 0
    await message.answer("📢 Рассылка запущена...")
    for user in users:
        try:
            await bot.send_message(user[0], message.text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Готово! Сообщение получили {count} человек.")
    await state.clear()

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
