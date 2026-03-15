import asyncio
import logging
import random
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice,
    Message, CallbackQuery, PreCheckoutQuery, ReplyKeyboardMarkup,
    KeyboardButton, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosqlite import connect

# ---------- Настройки ----------
BOT_TOKEN = "8653942101:AAFgg1wOQz6E3_GypM9BrUXLYwZSUqA7Ukg"  # Замените на токен вашего бота
DB_PATH = "anon_chat.db"

# ---------- Инициализация бота и диспетчера ----------
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ---------- База данных ----------
async def init_db():
    async with connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                gender TEXT,
                age INTEGER,
                balance INTEGER DEFAULT 0,
                unlimited BOOLEAN DEFAULT 0,
                referrer_id INTEGER,
                referrer_claimed BOOLEAN DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Таблица рефералов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                awarded BOOLEAN DEFAULT 0,
                UNIQUE(referred_id)
            )
        """)
        # Таблица покупок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                package TEXT,
                stars INTEGER,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Таблица активных чатов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER UNIQUE,
                user2_id INTEGER UNIQUE,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# Вспомогательные функции для работы с БД
async def get_user(user_id: int) -> Optional[dict]:
    async with connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

async def create_user(user_id: int, username: str, gender: str, age: int, referrer_id: int = None):
    async with connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, gender, age, referrer_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, gender, age, referrer_id)
        )
        await db.commit()

async def update_profile(user_id: int, gender: str, age: int):
    async with connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET gender = ?, age = ? WHERE user_id = ?",
            (gender, age, user_id)
        )
        await db.commit()

async def add_balance(user_id: int, amount: int):
    async with connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

async def set_unlimited(user_id: int):
    async with connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET unlimited = 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def use_paid_chat(user_id: int) -> bool:
    """Списать один платный чат. Возвращает True, если успешно."""
    user = await get_user(user_id)
    if not user:
        return False
    if user['unlimited']:
        return True
    if user['balance'] > 0:
        async with connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET balance = balance - 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
        return True
    return False

async def add_referral(referrer_id: int, referred_id: int):
    """Добавить запись о реферале и начислить бонус рефереру."""
    async with connect(DB_PATH) as db:
        # Проверяем, не было ли уже награды за этого реферала
        cursor = await db.execute(
            "SELECT awarded FROM referrals WHERE referred_id = ?", (referred_id,)
        )
        row = await cursor.fetchone()
        if row:
            return  # уже есть запись, награда выдана или нет

        await db.execute(
            "INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
            (referrer_id, referred_id)
        )
        # Начисляем бонус рефереру (2 чата), если он не безлимитный
        referrer = await get_user(referrer_id)
        if referrer and not referrer['unlimited']:
            await db.execute(
                "UPDATE users SET balance = balance + 2 WHERE user_id = ?",
                (referrer_id,)
            )
        await db.execute(
            "UPDATE referrals SET awarded = 1 WHERE referred_id = ?",
            (referred_id,)
        )
        await db.commit()

async def get_active_chat(user_id: int) -> Optional[int]:
    """Вернуть ID собеседника, если пользователь в чате, иначе None."""
    async with connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        cursor = await db.execute(
            "SELECT user1_id, user2_id FROM active_chats WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id)
        )
        row = await cursor.fetchone()
        if row:
            return row['user2_id'] if row['user1_id'] == user_id else row['user1_id']
        return None

async def create_chat(user1_id: int, user2_id: int):
    """Создать активный чат между двумя пользователями."""
    async with connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO active_chats (user1_id, user2_id) VALUES (?, ?)",
            (user1_id, user2_id)
        )
        await db.commit()

async def end_chat(user_id: int):
    """Завершить чат, удалить запись."""
    async with connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM active_chats WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id)
        )
        await db.commit()

async def count_users_with_filters(gender: str = None, age_min: int = None, age_max: int = None) -> int:
    """Количество пользователей, подходящих под фильтры (исключая самого себя)."""
    async with connect(DB_PATH) as db:
        query = "SELECT COUNT(*) FROM users WHERE 1=1"
        params = []
        if gender:
            query += " AND gender = ?"
            params.append(gender)
        if age_min is not None:
            query += " AND age >= ?"
            params.append(age_min)
        if age_max is not None:
            query += " AND age <= ?"
            params.append(age_max)
        cursor = await db.execute(query, params)
        count = await cursor.fetchone()
        return count[0] if count else 0

async def find_random_user(exclude_user_id: int, gender: str = None, age_min: int = None, age_max: int = None) -> Optional[int]:
    """Найти случайного пользователя, исключая exclude_user_id, с опциональными фильтрами."""
    async with connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        query = "SELECT user_id FROM users WHERE user_id != ?"
        params = [exclude_user_id]
        if gender:
            query += " AND gender = ?"
            params.append(gender)
        if age_min is not None:
            query += " AND age >= ?"
            params.append(age_min)
        if age_max is not None:
            query += " AND age <= ?"
            params.append(age_max)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        if not rows:
            return None
        return random.choice(rows)['user_id']

# ---------- Клавиатуры ----------
def main_menu():
    kb = [
        [KeyboardButton(text="🔍 Найти собеседника")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="⭐ Купить чаты")],
        [KeyboardButton(text="🔗 Реферальная система"), KeyboardButton(text="❌ Завершить чат")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def gender_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="👨 Мужчина", callback_data="gender_male"))
    builder.add(InlineKeyboardButton(text="👩 Женщина", callback_data="gender_female"))
    return builder.as_markup()

def search_type_keyboard():
    kb = [
        [InlineKeyboardButton(text="🆓 Бесплатный поиск", callback_data="search_free")],
        [InlineKeyboardButton(text="⭐ Платный поиск (с фильтрами)", callback_data="search_paid")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def paid_search_gender_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="👨 Мужчина", callback_data="paid_gender_male"))
    builder.add(InlineKeyboardButton(text="👩 Женщина", callback_data="paid_gender_female"))
    builder.add(InlineKeyboardButton(text="🤷 Любой", callback_data="paid_gender_any"))
    return builder.as_markup()

def buy_packages_keyboard():
    kb = [
        [InlineKeyboardButton(text="1 чат — 2 ⭐", callback_data="buy_1")],
        [InlineKeyboardButton(text="10 чатов — 15 ⭐", callback_data="buy_10")],
        [InlineKeyboardButton(text="Навсегда (безлимит) — 35 ⭐", callback_data="buy_unlimited")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ---------- Машины состояний ----------
class Registration(StatesGroup):
    gender = State()
    age = State()

class EditProfile(StatesGroup):
    gender = State()
    age = State()

class PaidSearch(StatesGroup):
    gender = State()
    age_min = State()
    age_max = State()

# ---------- Обработчики команд ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1][4:])
        except:
            pass

    # Проверяем, зарегистрирован ли пользователь
    user = await get_user(user_id)
    if user:
        await message.answer(
            f"С возвращением, {message.from_user.full_name}!",
            reply_markup=main_menu()
        )
    else:
        # Новый пользователь: запоминаем реферера, если есть
        if referrer_id and referrer_id != user_id:
            # Проверим, что реферер существует
            referrer = await get_user(referrer_id)
            if referrer:
                # Сохраним реферера во временное состояние для дальнейшего начисления
                await state.update_data(referrer_id=referrer_id)
        await state.set_state(Registration.gender)
        await message.answer(
            "👋 Добро пожаловать в анонимный чат!\n"
            "Для начала укажите ваш пол:",
            reply_markup=gender_keyboard()
        )

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    gender = "Мужчина" if user['gender'] == 'male' else "Женщина"
    unlimited = "Да (безлимит)" if user['unlimited'] else "Нет"
    text = (
        f"👤 Ваш профиль:\n"
        f"Пол: {gender}\n"
        f"Возраст: {user['age']}\n"
        f"Платные чаты: {'∞' if user['unlimited'] else user['balance']}\n"
        f"Безлимит: {unlimited}"
    )
    # Кнопка для редактирования
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать профиль", callback_data="edit_profile")]
    ])
    await message.answer(text, reply_markup=kb)

@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    await show_buy_menu(message)

async def show_buy_menu(message: Message):
    await message.answer(
        "💎 Выберите пакет платных чатов:\n"
        "Платный поиск позволяет выбрать пол и возраст собеседника.",
        reply_markup=buy_packages_keyboard()
    )

@dp.message(Command("referral"))
async def cmd_referral(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйтесь.")
        return
    # Считаем количество приведённых друзей
    async with connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,)
        )
        count = await cursor.fetchone()
        count = count[0] if count else 0
    link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}"
    text = (
        f"🔗 Ваша реферальная ссылка:\n{link}\n\n"
        f"Приведено друзей: {count}\n"
        f"За каждого нового друга вы получаете 2 платных чата (после его регистрации)."
    )
    await message.answer(text)

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    user_id = message.from_user.id
    partner = await get_active_chat(user_id)
    if not partner:
        await message.answer("❌ Вы не находитесь в чате.")
        return
    await end_chat(user_id)
    await message.answer("✅ Чат завершён.")
    try:
        await bot.send_message(partner, "Собеседник покинул чат. Чат завершён.")
    except:
        pass

# ---------- Обработчики регистрации ----------
@dp.callback_query(Registration.gender, F.data.startswith("gender_"))
async def reg_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]  # male или female
    await state.update_data(gender=gender)
    await state.set_state(Registration.age)
    await callback.message.edit_text("Сколько вам лет? (введите число)")
    await callback.answer()

@dp.message(Registration.age)
async def reg_age(message: Message, state: FSMContext):
    try:
        age = int(message.text)
        if age < 5 or age > 120:
            raise ValueError
    except:
        await message.answer("Пожалуйста, введите корректный возраст (от 5 до 120).")
        return
    data = await state.get_data()
    gender = data['gender']
    user_id = message.from_user.id
    username = message.from_user.username or ""

    # Создаём пользователя
    await create_user(user_id, username, gender, age, referrer_id=data.get('referrer_id'))

    # Если есть реферер, начисляем бонус
    if data.get('referrer_id'):
        await add_referral(data['referrer_id'], user_id)

    await state.clear()
    await message.answer(
        "✅ Регистрация завершена! Теперь вы можете искать собеседников.",
        reply_markup=main_menu()
    )

# ---------- Редактирование профиля ----------
@dp.callback_query(F.data == "edit_profile")
async def edit_profile_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EditProfile.gender)
    await callback.message.edit_text("Выберите новый пол:", reply_markup=gender_keyboard())
    await callback.answer()

@dp.callback_query(EditProfile.gender, F.data.startswith("gender_"))
async def edit_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    await state.set_state(EditProfile.age)
    await callback.message.edit_text("Введите новый возраст (число):")
    await callback.answer()

@dp.message(EditProfile.age)
async def edit_age(message: Message, state: FSMContext):
    try:
        age = int(message.text)
        if age < 5 or age > 120:
            raise ValueError
    except:
        await message.answer("Некорректный возраст. Попробуйте снова.")
        return
    data = await state.get_data()
    gender = data['gender']
    await update_profile(message.from_user.id, gender, age)
    await state.clear()
    await message.answer("✅ Профиль обновлён!", reply_markup=main_menu())

# ---------- Поиск собеседника ----------
@dp.message(F.text == "🔍 Найти собеседника")
async def search_menu(message: Message):
    # Проверим, не в чате ли уже
    if await get_active_chat(message.from_user.id):
        await message.answer("⚠️ Вы уже находитесь в чате. Завершите текущий чат командой /stop.")
        return
    await message.answer("Выберите тип поиска:", reply_markup=search_type_keyboard())

@dp.callback_query(F.data == "search_free")
async def free_search(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await find_random_user(exclude_user_id=user_id)
    if not partner_id:
        await callback.message.edit_text("😕 К сожалению, сейчас нет свободных собеседников. Попробуйте позже.")
        await callback.answer()
        return
    # Создаём чат
    await create_chat(user_id, partner_id)
    await callback.message.edit_text("✅ Собеседник найден! Можете начинать общение.\nЧтобы завершить чат, используйте /stop.")
    await callback.answer()
    # Уведомляем собеседника
    try:
        await bot.send_message(partner_id, "✅ С вами хочет пообщаться случайный собеседник. Чат начат!\nЧтобы завершить, используйте /stop.")
    except:
        pass

@dp.callback_query(F.data == "search_paid")
async def paid_search_start(callback: CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.message.edit_text("Сначала зарегистрируйтесь.")
        await callback.answer()
        return
    # Проверяем, есть ли платные чаты
    if not user['unlimited'] and user['balance'] <= 0:
        await callback.message.edit_text(
            "❌ У вас нет платных чатов. Купите пакет в разделе ⭐ Купить чаты.",
            reply_markup=buy_packages_keyboard()
        )
        await callback.answer()
        return
    await state.set_state(PaidSearch.gender)
    await callback.message.edit_text(
        "Выберите пол собеседника:",
        reply_markup=paid_search_gender_keyboard()
    )
    await callback.answer()

@dp.callback_query(PaidSearch.gender, F.data.startswith("paid_gender_"))
async def paid_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[2]  # male, female, any
    await state.update_data(gender=gender if gender != "any" else None)
    await state.set_state(PaidSearch.age_min)
    await callback.message.edit_text(
        "Введите минимальный возраст собеседника (или 0, если не важно):"
    )
    await callback.answer()

@dp.message(PaidSearch.age_min)
async def paid_age_min(message: Message, state: FSMContext):
    try:
        age_min = int(message.text)
        if age_min < 0:
            raise ValueError
    except:
        await message.answer("Введите целое число (0, если не важно).")
        return
    await state.update_data(age_min=age_min if age_min > 0 else None)
    await state.set_state(PaidSearch.age_max)
    await message.answer("Введите максимальный возраст собеседника (или 0, если не важно):")

@dp.message(PaidSearch.age_max)
async def paid_age_max(message: Message, state: FSMContext):
    try:
        age_max = int(message.text)
        if age_max < 0:
            raise ValueError
    except:
        await message.answer("Введите целое число (0, если не важно).")
        return
    data = await state.get_data()
    age_min = data.get('age_min')
    if age_min and age_max and age_max < age_min:
        await message.answer("Максимальный возраст не может быть меньше минимального. Попробуйте снова.")
        return
    # Сохраняем возраст
    await state.update_data(age_max=age_max if age_max > 0 else None)
    # Спишем чат
    user_id = message.from_user.id
    success = await use_paid_chat(user_id)
    if not success:
        await message.answer("❌ Не удалось списать чат. Возможно, у вас закончились платные чаты.")
        await state.clear()
        return
    # Ищем собеседника
    partner_id = await find_random_user(
        exclude_user_id=user_id,
        gender=data['gender'],
        age_min=age_min,
        age_max=age_max
    )
    if not partner_id:
        # Вернём списанный чат, если никого не нашли
        await add_balance(user_id, 1)  # возвращаем один чат
        await message.answer("😕 Подходящих собеседников не найдено. Попробуйте изменить фильтры.")
        await state.clear()
        return
    # Создаём чат
    await create_chat(user_id, partner_id)
    await state.clear()
    await message.answer("✅ Собеседник найден! Можете начинать общение.\nЧтобы завершить чат, используйте /stop.")
    try:
        await bot.send_message(partner_id, "✅ С вами хочет пообщаться собеседник (платный поиск). Чат начат!\nЧтобы завершить, используйте /stop.")
    except:
        pass

# ---------- Покупка пакетов (Telegram Stars) ----------
@dp.message(F.text == "⭐ Купить чаты")
async def buy_menu(message: Message):
    await show_buy_menu(message)

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery):
    user_id = callback.from_user.id
    package = callback.data.split("_")[1]  # "1", "10", "unlimited"
    if package == "1":
        title = "1 платный чат"
        description = "Один поиск с фильтрами по полу и возрасту"
        stars = 2
        payload = "package_1"
    elif package == "10":
        title = "10 платных чатов"
        description = "Десять поисков с фильтрами"
        stars = 15
        payload = "package_10"
    elif package == "unlimited":
        title = "Безлимитные платные чаты"
        description = "Навсегда (без ограничений) поиск с фильтрами"
        stars = 35
        payload = "package_unlimited"
    else:
        await callback.answer("Неверный пакет")
        return

    # Отправляем инвойс
    await bot.send_invoice(
        chat_id=user_id,
        title=title,
        description=description,
        payload=payload,
        currency="XTR",  # Telegram Stars
        prices=[LabeledPrice(label=title, amount=stars)],
        provider_token=None,  # Для звёзд не нужен
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    # Всегда подтверждаем
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    stars = message.successful_payment.total_amount  # Количество звёзд
    package = payload.split("_")[1]  # "1", "10", "unlimited"

    # Записываем покупку
    async with connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO purchases (user_id, package, stars) VALUES (?, ?, ?)",
            (user_id, package, stars)
        )
        await db.commit()

    if package == "1":
        await add_balance(user_id, 1)
        text = "✅ Вам начислен 1 платный чат!"
    elif package == "10":
        await add_balance(user_id, 10)
        text = "✅ Вам начислено 10 платных чатов!"
    elif package == "unlimited":
        await set_unlimited(user_id)
        text = "✅ Теперь у вас безлимитный доступ ко всем платным функциям!"
    else:
        text = "❌ Ошибка: неизвестный пакет."

    await message.answer(text, reply_markup=main_menu())

# ---------- Пересылка сообщений в чате ----------
@dp.message()
async def forward_message(message: Message):
    user_id = message.from_user.id
    partner_id = await get_active_chat(user_id)
    if not partner_id:
        # Не в чате – игнорируем
        return
    # Пересылаем сообщение собеседнику
    try:
        if message.text:
            await bot.send_message(partner_id, f"<b>Собеседник:</b> {message.text}")
        elif message.caption:
            await bot.send_message(partner_id, f"<b>Собеседник (подпись):</b> {message.caption}")
        elif message.photo:
            await bot.send_photo(partner_id, message.photo[-1].file_id, caption=message.caption)
        elif message.video:
            await bot.send_video(partner_id, message.video.file_id, caption=message.caption)
        elif message.sticker:
            await bot.send_sticker(partner_id, message.sticker.file_id)
        elif message.voice:
            await bot.send_voice(partner_id, message.voice.file_id)
        elif message.audio:
            await bot.send_audio(partner_id, message.audio.file_id)
        elif message.document:
            await bot.send_document(partner_id, message.document.file_id, caption=message.caption)
        # и т.д. для других типов
        else:
            await bot.send_message(partner_id, "Собеседник отправил неподдерживаемый тип сообщения.")
    except Exception as e:
        logging.error(f"Ошибка пересылки сообщения: {e}")
        # Если не удалось отправить (пользователь заблокировал бота и т.п.), завершаем чат
        await end_chat(user_id)
        await message.answer("❌ Не удалось доставить сообщение. Возможно, собеседник покинул чат. Чат завершён.")
        try:
            await bot.send_message(partner_id, "Чат завершён из-за ошибки доставки.")
        except:
            pass

# ---------- Запуск бота ----------
async def main():
    await init_db()
    # Удаляем вебхук, используем long polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())