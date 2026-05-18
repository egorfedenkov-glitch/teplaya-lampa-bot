import asyncio
import logging
import os
import sqlite3
import datetime
import random
import aiohttp
import time

# --- Библиотеки для асинхронности, веб-сервера и бота ---
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Update
from aiogram.enums import ParseMode

# Импорт datetime с учётом временной зоны
from datetime import datetime as dt
import pytz

# --- Настройки ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # Токен из переменной окружения
WEBHOOK_PATH = "/webhook"
PORT = int(os.environ.get("PORT", 8000))

# Render передаёт публичный URL сервиса через эту переменную
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    logging.error("Переменная окружения RENDER_EXTERNAL_URL не установлена!")
    exit(1)

WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Инициализация бота и диспетчера ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Работа с базой данных ---
def init_db():
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_active TEXT,
        subscribed INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_type TEXT,
        media_url TEXT,
        caption TEXT,
        era TEXT,
        author_id INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        status TEXT DEFAULT 'pending'
    )''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def add_user(user_id, username, first_name):
    """Добавляет пользователя, если его нет, или обновляет время активности."""
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_active, subscribed)
        VALUES (?, ?, ?, ?, 1)
    ''', (user_id, username or "", first_name or "", dt.now().isoformat()))
    conn.commit()
    conn.close()

init_db()

# ========== ОСНОВНЫЕ КОМАНДЫ ==========
@dp.message(Command('start'))
async def start_cmd(message: types.Message):
    user = message.from_user
    add_user(user.id, user.username, user.first_name)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📖 Как это работает", callback_data="howto")],
        [types.InlineKeyboardButton(text="✨ Добавить своё воспоминание", callback_data="add_memory")],
        [types.InlineKeyboardButton(text="🔕 Отписаться от рассылки", callback_data="unsubscribe")]
    ])
    await message.answer(
        "🕯 *Добро пожаловать в «Тёплую лампу»!*\n\n"
        "Каждый вечер в 20:00 я буду присылать тебе один кадр из прошлого.\n"
        "Звуки модема, запах жвачки, старые интерфейсы…\n\n"
        "Ты можешь *добавить своё воспоминание* — и его увидят другие.\n"
        "А если захочешь тишины — просто отпишись в один клик.\n\n"
        "Тепло уже в пути 🕯",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

@dp.message(Command('subscribe'))
async def subscribe_cmd(message: types.Message):
    user = message.from_user
    # Убедимся, что пользователь есть в базе
    add_user(user.id, user.username, user.first_name)
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('UPDATE users SET subscribed = 1 WHERE user_id = ?', (user.id,))
    conn.commit()
    conn.close()
    await message.answer("🕯 Ты снова в рассылке! Сегодня в 20:00 придёт тепло.")

@dp.message(Command('memory'))
async def memory_cmd(message: types.Message):
    text = message.text.replace('/memory', '').strip()
    if not text:
        await message.answer("Напиши после команды текст воспоминания.\nПример: `/memory Как я ждал звонка по телефону...`", parse_mode=ParseMode.MARKDOWN)
        return
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('INSERT INTO user_memories (user_id, text, status) VALUES (?, ?, "pending")', (message.from_user.id, text))
    conn.commit()
    conn.close()
    await message.answer("Спасибо! Твоё воспоминание отправлено на проверку. Если оно попадёт в рассылку — я уведомлю тебя.")

@dp.message(Command('random'))
async def random_memory(message: types.Message):
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('SELECT media_type, media_url, caption FROM content WHERE status="approved" ORDER BY RANDOM() LIMIT 1')
    row = c.fetchone()
    conn.close()
    if not row:
        await message.answer("Пока нет воспоминаний. Добавьте первое через /add")
        return
    media_type, media_url, caption = row
    if media_type == 'photo':
        await message.answer_photo(media_url, caption=caption)
    elif media_type == 'gif':
        await message.answer_animation(media_url, caption=caption)
    else:
        await message.answer(f"🕯 *Воспоминание*\n\n{caption}", parse_mode='Markdown')

# ========== АДМИН-КОМАНДА (добавление контента) ==========
ADMIN_ID = 298207628  # Ваш Telegram ID

@dp.message(Command('add'))
async def admin_add_content(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав на эту команду.")
        return
    args = message.text.split('|')
    if len(args) < 2:
        await message.answer("❌ Неверный формат.\nИспользуйте:\n`/add text|ваш текст`\n`/add photo|URL_картинки|подпись`", parse_mode="Markdown")
        return
    media_type = args[0].replace('/add ', '').strip().lower()
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    if media_type == 'text':
        caption = args[1]
        c.execute('INSERT INTO content (media_type, caption, era, status) VALUES (?, ?, ?, ?)',
                  ('text', caption, 'admin', 'approved'))
        await message.answer(f"✅ Текст добавлен:\n`{caption[:50]}...`", parse_mode="Markdown")
    elif media_type == 'photo':
        if len(args) < 3:
            await message.answer("❌ Для фото укажите и ссылку, и подпись.")
            return
        photo_url, caption = args[1], args[2]
        c.execute('INSERT INTO content (media_type, media_url, caption, era, status) VALUES (?, ?, ?, ?, ?)',
                  ('photo', photo_url, caption, 'admin', 'approved'))
        await message.answer(f"✅ Фото добавлено:\n`{caption[:50]}...`", parse_mode="Markdown")
    else:
        await message.answer("❌ Поддерживаются только `text` и `photo`.", parse_mode="Markdown")
    conn.commit()
    conn.close()

# ========== CALLBACK-ЗАПРОСЫ ==========
@dp.callback_query(lambda c: c.data == "howto")
async def howto_callback(callback: types.CallbackQuery):
    await callback.message.answer(
        "📖 *Как это работает*\n\n"
        "1. Каждый день в 20:00 (по Москве) бот присылает случайный пост.\n"
        "2. Это может быть старое фото, гифка, стикер или просто тёплый текст.\n"
        "3. Ты можешь отправить боту команду /memory и написать своё воспоминание.\n"
        "4. Если я его одобрю — оно попадёт в общую копилку.\n\n"
        "Никакой рекламы, никакого сбора данных. Просто лампа 🕯",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "add_memory")
async def add_memory_callback(callback: types.CallbackQuery):
    await callback.message.answer(
        "✨ *Расскажи своё воспоминание*\n\n"
        "Просто напиши мне текст или пришли фото/гифку (не больше 10 МБ).\n"
        "Пример: *«В 2001 году я нашёл дискету с игрой Doom на школьном компьютере…»*\n\n"
        "Я добавлю это в очередь на модерацию. Если одобрю — твой ник появится под постом (если хочешь).",
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "unsubscribe")
async def unsubscribe_callback(callback: types.CallbackQuery):
    user = callback.from_user
    # Сначала убедимся, что пользователь есть в базе (на всякий случай)
    add_user(user.id, user.username, user.first_name)
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('UPDATE users SET subscribed = 0 WHERE user_id = ?', (user.id,))
    conn.commit()
    conn.close()
    await callback.message.answer("🔕 Ты отписался от вечерней рассылки. Если захочешь вернуться — напиши /subscribe")
    await callback.answer()

# ========== ОБРАБОТЧИК МЕДИА (пользовательские воспоминания) ==========
@dp.message(lambda msg: msg.photo or msg.animation)
async def handle_media_memory(message: types.Message):
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = 'photo'
        media_url = file_id
    elif message.animation:
        file_id = message.animation.file_id
        media_type = 'gif'
        media_url = file_id
    else:
        return
    conn = sqlite3.connect('nostalgia.db')
    c = conn.cursor()
    c.execute('INSERT INTO content (media_type, media_url, caption, era, author_id, status) VALUES (?, ?, ?, ?, ?, "pending")',
              (media_type, media_url, message.caption or "", "user", message.from_user.id))
    conn.commit()
    conn.close()
    await message.answer("Твоё медиа-воспоминание сохранено и будет проверено модератором. Спасибо за вклад в общую копилку!")

# ========== ЕЖЕДНЕВНАЯ РАССЫЛКА ==========
async def daily_mailing():
    moscow_tz = pytz.timezone('Europe/Moscow')
    while True:
        now_moscow = dt.now(moscow_tz)
        target_hour, target_min = 20, 0
        target = now_moscow.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        if now_moscow >= target:
            target += datetime.timedelta(days=1)
        sleep_seconds = (target - now_moscow).total_seconds()
        logger.info(f"Следующая рассылка через {sleep_seconds} секунд.")
        await asyncio.sleep(sleep_seconds)

        logger.info("Начинаем рассылку...")
        conn = sqlite3.connect('nostalgia.db')
        c = conn.cursor()
        c.execute('SELECT user_id FROM users WHERE subscribed = 1')
        users = [row[0] for row in c.fetchall()]
        c.execute('SELECT media_type, media_url, caption FROM content WHERE status="approved" ORDER BY RANDOM() LIMIT 1')
        content = c.fetchone()
        conn.close()

        if not content:
            logger.warning("Нет контента для рассылки!")
            continue

        media_type, media_url, caption = content
        for user_id in users:
            try:
                if media_type == 'photo':
                    await bot.send_photo(user_id, media_url, caption=caption)
                elif media_type == 'gif':
                    await bot.send_animation(user_id, media_url, caption=caption)
                else:
                    await bot.send_message(user_id, f"🕯 *Воспоминание дня*\n\n{caption}", parse_mode=ParseMode.MARKDOWN)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Не удалось отправить пользователю {user_id}: {e}")

# ========== ВЕБ-СЕРВЕР ДЛЯ ВЕБХУКОВ ==========
async def webhook_handler(request: web.Request) -> web.Response:
    try:
        update_data = await request.json()
        update = Update(**update_data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка при обработке вебхука: {e}")
        return web.Response(status=500)

async def health_check_handler(request: web.Request) -> web.Response:
    return web.Response(status=200, text="OK")

async def on_startup(app: web.Application) -> None:
    logger.info("Устанавливаем вебхук...")
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Вебхук установлен на {WEBHOOK_URL}")
    asyncio.create_task(daily_mailing())

async def on_shutdown(app: web.Application) -> None:
    logger.info("Удаляем вебхук...")
    await bot.delete_webhook()
    logger.info("Вебхук удалён.")

# ========== ТОЧКА ВХОДА ==========
if __name__ == "__main__":
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/health", health_check_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    logger.info(f"Запуск приложения на порту {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)

# --- Функция для генерации текста через GigaChat (остаётся без изменений) ---
async def generate_gigachat_text(prompt: str, api_key: str) -> str:
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "GigaChat",
        "messages": [
            {"role": "system", "content": "Ты — профессиональный копирайтер, генерирующий уютные и ностальгические посты для Telegram."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.9,
        "max_tokens": 500
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            result = await resp.json()
            if 'choices' in result:
                return result['choices'][0]['message']['content']
            else:
                return "Не удалось сгенерировать текст."

# --- НОВАЯ КОМАНДА /gen с Pollinations.ai ---
@dp.message(Command('gen'))
async def admin_gen_post(message: types.Message):
    # Проверка прав админа
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав на эту команду.")
        return

    # Получаем тему
    topic = message.text.replace('/gen', '').strip()
    if not topic:
        await message.answer("❌ Укажите тему поста.\nПример: `/gen Воспоминания о видеосалонах`", parse_mode="Markdown")
        return

    # Проверяем API-ключ GigaChat
    GIGACHAT_API_KEY = os.environ.get("GIGACHAT_API_KEY")
    if not GIGACHAT_API_KEY:
        await message.answer("⚠️ API-ключ GigaChat не настроен. Добавьте его в переменные окружения Render.")
        return

    # Уведомляем о начале работы
    status_message = await message.answer(f"🤖 Генерирую пост на тему: *{topic}*...\n\n⏳ Это может занять до 30 секунд.", parse_mode="Markdown")

    # --- 1. Генерируем текст через GigaChat ---
    text_prompt = f"""
    Ты — профессиональный копирайтер, ведущий уютный телеграм-канал о ностальгии. 
    Напиши небольшой, цепляющий пост на русском языке на тему: "{topic}".
    Пост должен быть написан от первого лица, в разговорном стиле, вызывать тёплые эмоции.
    Длина поста — от 150 до 300 символов.
    """
    generated_text = await generate_gigachat_text(text_prompt, GIGACHAT_API_KEY)
    
    if not generated_text or generated_text == "Не удалось сгенерировать текст.":
        await status_message.edit_text("❌ Не удалось сгенерировать текст. Попробуйте позже.")
        return

    # --- 2. Генерируем изображение через Pollinations.ai (без ключа) ---
    # Формируем промпт на английском для лучшего качества
    image_prompt = f"nostalgic atmosphere, warm memory style, retro vibes, {topic}, cozy, detailed, 8k resolution, no text"
    # Кодируем промпт для URL
    import urllib.parse
    encoded_prompt = urllib.parse.quote(image_prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
    
    # Скачиваем изображение по ссылке
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    image_data = io.BytesIO(await resp.read())
                    # Сохраняем в базу данных
                    conn = sqlite3.connect('nostalgia.db')
                    c = conn.cursor()
                    c.execute('INSERT INTO content (media_type, media_url, caption, status) VALUES (?, ?, ?, ?)',
                              ('photo', image_url, generated_text, 'approved'))
                    conn.commit()
                    conn.close()
                    
                    # Отправляем результат админу
                    await message.answer_photo(
                        photo=types.BufferedInputFile(image_data.getvalue(), filename="nostalgia.jpg"),
                        caption=f"✨ *Сгенерированный пост:*\n\n{generated_text}\n\n✅ Пост сохранён в БД и готов к рассылке!",
                        parse_mode="Markdown"
                    )
                    await status_message.delete()
                else:
                    # Если картинку не удалось получить, сохраняем только текст
                    conn = sqlite3.connect('nostalgia.db')
                    c = conn.cursor()
                    c.execute('INSERT INTO content (media_type, caption, status) VALUES (?, ?, ?)',
                              ('text', generated_text, 'approved'))
                    conn.commit()
                    conn.close()
                    await message.answer(f"✨ *Сгенерированный текст:*\n\n{generated_text}\n\n⚠️ Изображение не удалось загрузить, но текст сохранён в БД.", parse_mode="Markdown")
                    await status_message.delete()
        except Exception as e:
            logger.error(f"Ошибка при запросе к Pollinations: {e}")
            # В случае ошибки сохраняем только текст
            conn = sqlite3.connect('nostalgia.db')
            c = conn.cursor()
            c.execute('INSERT INTO content (media_type, caption, status) VALUES (?, ?, ?)',
                      ('text', generated_text, 'approved'))
            conn.commit()
            conn.close()
            await message.answer(f"✨ *Сгенерированный текст:*\n\n{generated_text}\n\n✅ Текст сохранён в БД (изображение не сгенерировано из-за технической ошибки).", parse_mode="Markdown")
            await status_message.delete()
