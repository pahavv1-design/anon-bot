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

# 1. Настройка логирования (чтобы видеть ошибки в логах Bothost)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. Получение переменных
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/telegram")

# Безопасная конвертация ID
try:
    ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW else None
except ValueError:
    logger.error("ОШИБКА: ADMIN_ID должен быть числом!")
    ADMIN_ID = None

try:
    CHANNEL_ID = int(CHANNEL_ID_RAW) if CHANNEL_ID_RAW else None
except ValueError:
    logger.error("ОШИБКА: CHANNEL_ID должен быть числом (начинается с -100)!")
    CHANNEL_ID = None

if not TOKEN:
    logger.error("ОШИБКА: BOT_TOKEN не найден в переменных Bothost!")

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
    logger.info("База данных инициализирована.")

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

# --- ЛОГИКА РЕЙТИНГА (БЕЗ ЛИМИТОВ) ---
def get_user_rank(user_id):
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
    except Exception as e:
        logger.warning(f"Ошибка проверки подписки: {e}")
        return True

# --- КЛАВИАТУРА ---
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
        if target_id == user_id: return await message.answer("Нельзя писать самому себе.")
        
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
    rank = get_user_rank(user_id)
    text = (
        "📌 <b>Статистика профиля</b>\n\n"
        f"💬 Получено сообщений: {received}\n"
        f"👀 Переходов по ссылке: {clicks}\n"
        f"⭐ Твое место в рейтинге: {rank}\n"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("url"))
@dp.message(F.text == "🔗 Моя ссылка")
async def cmd_url(message: types.Message):
    bot_info = await bot.get_me()
    link = f"t.me/{bot_info.username}?start=user{message.from_user.id}"
    await message.answer(f"🔗 Твоя ссылка:\n<code>{link}</code>", parse_mode="HTML")

@dp.message(Command("lang"))
async def cmd_lang(message: types.Message):
    await message.answer("🇷🇺 Язык: Русский")

@dp.message(Command("issue"))
async def cmd_issue(message: types.Message):
    await message.answer("💡 Пишите ваши предложения прямо в чат админу: @ваш_юзернейм")

# --- ОТПРАВКА И УДАЛЕНИЕ ---
@dp.message(States.writing_msg)
async def process_msg(message: types.Message, state: FSMContext):
    data = await state.get_data()
    t_id = data['target_id']
    s_id = message.from_user.id
    
    try:
        r_msg = await bot.send_message(t_id, f"🎁 <b>Новое анонимное сообщение:</b>\n\n{message.text}", parse_mode="HTML")
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, f"🕵️ <b>ЛОГ:</b>\nОт: {s_id}\nКому: {t_id}\nТекст: {message.text}")

        msg_key = f"del_{r_msg.message_id}"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🗑 Удалить у всех", callback_data=msg_key))
        s_msg = await message.answer("✅ Отправлено!", reply_markup=kb.as_markup())
        
        db_query("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", (msg_key, s_id, t_id, s_msg.message_id, r_msg.message_id))
        db_query("UPDATE users SET received = received + 1 WHERE user_id = ?", (t_id,))
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await message.answer("❌ Ошибка отправки.")
    await state.clear()

@dp.callback_query(F.data.startswith("del_"))
async def delete_sync(call: types.CallbackQuery):
    data = db_query("SELECT * FROM messages WHERE msg_key = ?", (call.data,), fetchone=True)
    if data:
        try:
            await bot.delete_message(data[2], data[4]) # у получателя
            await bot.delete_message(data[1], data[3]) # у отправителя
            await call.answer("Удалено.")
        except: pass
        db_query("DELETE FROM messages WHERE msg_key = ?", (call.data,))
    else:
        await call.answer("Уже удалено.")

@dp.message(F.text == "📢 Админ-панель")
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 Рассылка", callback_data="start_broadcast"))
    await message.answer("Админка:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "start_broadcast")
async def broadcast_step1(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(States.broadcasting)
    await call.message.answer("Введите текст рассылки:")

@dp.message(States.broadcasting)
async def broadcast_step2(message: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    await message.answer(f"🚀 Рассылка на {len(users)} чел. запущена...")
    for user in users:
        try:
            await bot.send_message(user[0], message.text)
            await asyncio.sleep(0.05)
        except: pass
    await message.answer("✅ Готово!")
    await state.clear()

async def main():
    init_db()
    logger.info("Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
