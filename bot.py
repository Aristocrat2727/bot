import os
import random
import sqlite3
import telebot
import time
import threading
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta
import atexit
import logging
import json
from flask import Flask, jsonify, request

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_IDS = os.environ.get('ADMIN_IDS', '')
ADMINS = []
if ADMIN_IDS:
    ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_IDS.split(',') if admin_id.strip()]

# Настройки
MIN_BET = 5
MINES_MIN_BET = 5
MINES_MAX_BET = 100000
MINES_COUNT = 5
GRID_SIZE = 5

# Проверка наличия токена
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    exit(1)

# Инициализация бота - ВАЖНО: ДОЛЖНО БЫТЬ ПЕРЕД ВСЕМИ @bot.message_handler
bot = telebot.TeleBot(BOT_TOKEN)

# Глобальное соединение с БД
conn = None

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
def init_db():
    global conn
    try:
        db_path = os.environ.get('DATABASE_URL', 'casino_mega.db')
        
        if db_path.startswith('postgresql://'):
            logger.warning("PostgreSQL URL обнаружен, но используется SQLite.")
            db_path = 'casino_mega.db'
        
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY, 
                     balance INTEGER DEFAULT 0,
                     username TEXT,
                     first_name TEXT,
                     last_name TEXT,
                     last_bonus TEXT,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS transactions
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     from_user INTEGER,
                     to_user INTEGER,
                     amount INTEGER,
                     type TEXT,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS roulette_bets
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     amount INTEGER,
                     bet_type TEXT,
                     bet_value TEXT,
                     multiplier REAL,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS mines_games
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     chat_id INTEGER,
                     bet_amount INTEGER,
                     mines_count INTEGER,
                     grid_size INTEGER DEFAULT 5,
                     revealed_cells TEXT,
                     mine_positions TEXT,
                     current_payout REAL,
                     game_state TEXT DEFAULT 'active',
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     finished_at TIMESTAMP)''')
        
        c.execute('''CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(from_user)''')
        
        conn.commit()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

# ========== ФУНКЦИИ РАБОТЫ С БД ==========
def get_user(user_id):
    """Получение пользователя"""
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        
        if not user:
            c.execute("INSERT INTO users (user_id, balance, username, first_name, last_name) VALUES (?, ?, ?, ?, ?)", 
                     (user_id, 0, None, None, None))
            conn.commit()
            c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
            logger.info(f"Создан новый пользователь: {user_id}")
        
        return user
    except sqlite3.Error as e:
        logger.error(f"Ошибка get_user для {user_id}: {e}")
        init_db()
        return None

def update_user_info(user_id, username, first_name, last_name):
    """Обновление информации о пользователе"""
    try:
        c = conn.cursor()
        c.execute("""UPDATE users SET username = ?, first_name = ?, last_name = ? 
                     WHERE user_id = ?""", 
                     (username, first_name, last_name, user_id))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка update_user_info для {user_id}: {e}")
        return False

def update_balance(user_id, amount):
    """Обновление баланса пользователя"""
    try:
        c = conn.cursor()
        c.execute("""UPDATE users SET balance = balance + ? WHERE user_id = ? RETURNING balance""", (int(amount), user_id))
        
        result = c.fetchone()
        if result:
            new_balance = result[0]
            conn.commit()
            return new_balance
        else:
            return 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка update_balance для {user_id}: {e}")
        conn.rollback()
        return 0

def get_user_balance(user_id):
    """Получение баланса пользователя"""
    try:
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        return result[0] if result else 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка get_user_balance для {user_id}: {e}")
        return 0

def add_transaction(from_user, to_user, amount, trans_type):
    """Добавление транзакции"""
    try:
        c = conn.cursor()
        c.execute("""INSERT INTO transactions (from_user, to_user, amount, type) 
                     VALUES (?, ?, ?, ?)""", (from_user, to_user, amount, trans_type))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка add_transaction: {e}")
        return False

# ========== ФУНКЦИИ ДЛЯ RAILWAY ==========
def setup_webhook():
    """Настройка вебхука для Railway"""
    webhook_url = os.environ.get('RAILWAY_WEBHOOK_URL')
    if webhook_url:
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=f"{webhook_url}/{BOT_TOKEN}")
            logger.info(f"✅ Вебхук установлен: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка установки вебхука: {e}")

def health_check():
    """Проверка здоровья приложения"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bot": "running",
        "database": "connected" if conn else "disconnected"
    }

def close_db():
    global conn
    if conn:
        conn.close()
        logger.info("Соединение с БД закрыто")

atexit.register(close_db)

# Инициализация при запуске
init_db()

# Хранилище в памяти
mines_games = {}

# ========== АДМИНСКИЕ КОМАНДЫ ==========
@bot.message_handler(commands=['ahelp'])
def admin_help(message):
    """Показать админские команды"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    help_text = """
🛠️ *АДМИН КОМАНДЫ:*

📊 *СТАТИСТИКА*
`/status` - статус бота
`/admon` - показать всех админов

👤 *УПРАВЛЕНИЕ АДМИНАМИ*
`/addadmin [user_id]` - добавить админа
`/deladmin [user_id]` - удалить админа

💰 *УПРАВЛЕНИЕ БАЛАНСАМИ*
`/give [user_id] [amount]` - выдать баланс
`/take [user_id] [amount]` - забрать баланс
`/setbalance [user_id] [amount]` - установить баланс

👥 *ПОЛЬЗОВАТЕЛИ*
`/allusers` - список всех пользователей
`/top20` - топ 20 пользователей
`/finduser [user_id]` - найти пользователя

📢 *РАССЫЛКА*
`/broadcast [текст]` - рассылка всем пользователям
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['admon'])
def show_admins(message):
    """Показать всех админов"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    admins_text = "👑 *СПИСОК АДМИНОВ:*\n\n"
    for i, admin_id in enumerate(ADMINS, 1):
        try:
            user = get_user(admin_id)
            if user:
                username = user[2] or user[3] or f"ID: {admin_id}"
                admins_text += f"{i}. {username} (`{admin_id}`)\n"
            else:
                admins_text += f"{i}. ID: `{admin_id}`\n"
        except:
            admins_text += f"{i}. ID: `{admin_id}`\n"
    
    admins_text += f"\nВсего админов: *{len(ADMINS)}*"
    bot.reply_to(message, admins_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def bot_status(message):
    """Статус бота"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE balance > 0")
        users_with_balance = c.fetchone()[0]
        
        c.execute("SELECT SUM(balance) FROM users")
        total_balance = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM transactions")
        total_transactions = c.fetchone()[0]
        
        status_text = f"""
📊 *СТАТУС БОТА*

🤖 *Бот:* Работает ✅
👥 *Пользователей:* {total_users}
💰 *С балансом > 0:* {users_with_balance}
🏦 *Общий баланс:* {total_balance} GRAM
📈 *Транзакций:* {total_transactions}
👑 *Админов:* {len(ADMINS)}

💾 *База данных:* Подключена ✅
🌐 *Режим:* {'Webhook (Railway)' if os.environ.get('RAILWAY_ENVIRONMENT') else 'Polling'}
"""
        bot.reply_to(message, status_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка статуса: {e}")
        bot.reply_to(message, "❌ Ошибка получения статуса")

@bot.message_handler(commands=['addadmin'])
def add_admin(message):
    """Добавить админа"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: `/addadmin [user_id]`")
            return
        
        new_admin_id = int(parts[1])
        
        if new_admin_id in ADMINS:
            bot.reply_to(message, f"❌ Пользователь `{new_admin_id}` уже админ")
            return
        
        ADMINS.append(new_admin_id)
        admin_ids_str = ','.join(str(admin) for admin in ADMINS)
        
        get_user(new_admin_id)
        
        bot.reply_to(message, f"✅ Пользователь `{new_admin_id}` добавлен в админы\n👑 Всего админов: {len(ADMINS)}")
        
    except ValueError:
        bot.reply_to(message, "❌ user_id должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка добавления админа: {e}")
        bot.reply_to(message, "❌ Ошибка добавления админа")

@bot.message_handler(commands=['deladmin'])
def delete_admin(message):
    """Удалить админа"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: `/deladmin [user_id]`")
            return
        
        admin_to_remove = int(parts[1])
        
        if admin_to_remove == user_id:
            bot.reply_to(message, "❌ Нельзя удалить самого себя")
            return
        
        if admin_to_remove not in ADMINS:
            bot.reply_to(message, f"❌ Пользователь `{admin_to_remove}` не является админом")
            return
        
        ADMINS.remove(admin_to_remove)
        
        bot.reply_to(message, f"✅ Пользователь `{admin_to_remove}` удален из админов\n👑 Всего админов: {len(ADMINS)}")
        
    except ValueError:
        bot.reply_to(message, "❌ user_id должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка удаления админа: {e}")
        bot.reply_to(message, "❌ Ошибка удаления админа")

@bot.message_handler(commands=['give'])
def give_balance(message):
    """Выдать баланс пользователю"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "❌ Формат: `/give [user_id] [amount]`")
            return
        
        target_id = int(parts[1])
        amount = int(parts[2])
        
        if amount <= 0:
            bot.reply_to(message, "❌ Сумма должна быть больше 0")
            return
        
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        new_balance = update_balance(target_id, amount)
        add_transaction(0, target_id, amount, "admin_give")
        
        username = target_user[2] or target_user[3] or f"ID: {target_id}"
        
        bot.reply_to(message, f"""
✅ *БАЛАНС ВЫДАН*

👤 Пользователь: *{username}*
🆔 ID: `{target_id}`
💰 Выдано: *+{amount}* GRAM
💳 Новый баланс: *{new_balance}* GRAM
""")
        
    except ValueError:
        bot.reply_to(message, "❌ ID и сумма должны быть числами")
    except Exception as e:
        logger.error(f"Ошибка выдачи баланса: {e}")
        bot.reply_to(message, "❌ Ошибка выдачи баланса")

@bot.message_handler(commands=['take'])
def take_balance(message):
    """Забрать баланс у пользователя"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "❌ Формат: `/take [user_id] [amount]`")
            return
        
        target_id = int(parts[1])
        amount = int(parts[2])
        
        if amount <= 0:
            bot.reply_to(message, "❌ Сумма должна быть больше 0")
            return
        
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        current_balance = get_user_balance(target_id)
        
        if current_balance < amount:
            bot.reply_to(message, f"❌ У пользователя только {current_balance} GRAM, нельзя забрать {amount} GRAM")
            return
        
        new_balance = update_balance(target_id, -amount)
        add_transaction(target_id, 0, amount, "admin_take")
        
        username = target_user[2] or target_user[3] or f"ID: {target_id}"
        
        bot.reply_to(message, f"""
✅ *БАЛАНС ЗАБРАН*

👤 Пользователь: *{username}*
🆔 ID: `{target_id}`
💰 Забрано: *-{amount}* GRAM
💳 Новый баланс: *{new_balance}* GRAM
""")
        
    except ValueError:
        bot.reply_to(message, "❌ ID и сумма должны быть числами")
    except Exception as e:
        logger.error(f"Ошибка забора баланса: {e}")
        bot.reply_to(message, "❌ Ошибка забора баланса")

@bot.message_handler(commands=['setbalance'])
def set_balance(message):
    """Установить баланс пользователю"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "❌ Формат: `/setbalance [user_id] [amount]`")
            return
        
        target_id = int(parts[1])
        amount = int(parts[2])
        
        if amount < 0:
            bot.reply_to(message, "❌ Сумма не может быть отрицательной")
            return
        
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        current_balance = get_user_balance(target_id)
        difference = amount - current_balance
        
        if difference != 0:
            update_balance(target_id, difference)
            
            if difference > 0:
                add_transaction(0, target_id, difference, "admin_set_add")
            else:
                add_transaction(target_id, 0, -difference, "admin_set_sub")
        
        username = target_user[2] or target_user[3] or f"ID: {target_id}"
        
        bot.reply_to(message, f"""
✅ *БАЛАНС УСТАНОВЛЕН*

👤 Пользователь: *{username}*
🆔 ID: `{target_id}`
📊 Старый баланс: *{current_balance}* GRAM
💰 Новый баланс: *{amount}* GRAM
📈 Изменение: *{difference:+}* GRAM
""")
        
    except ValueError:
        bot.reply_to(message, "❌ ID и сумма должны быть числами")
    except Exception as e:
        logger.error(f"Ошибка установки баланса: {e}")
        bot.reply_to(message, "❌ Ошибка установки баланса")

@bot.message_handler(commands=['allusers'])
def show_all_users(message):
    """Показать всех пользователей"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT SUM(balance) FROM users")
        total_balance = c.fetchone()[0] or 0
        
        users_text = f"""
👥 *ВСЕ ПОЛЬЗОВАТЕЛИ*

📊 Всего пользователей: *{total_users}*
🏦 Общий баланс: *{total_balance}* GRAM
💰 Средний баланс: *{total_balance // max(1, total_users)}* GRAM

💡 Используй `/finduser [id]` для поиска
📈 Используй `/top20` для топа
"""
        bot.reply_to(message, users_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка allusers: {e}")
        bot.reply_to(message, "❌ Ошибка получения данных")

@bot.message_handler(commands=['top20'])
def show_top_20(message):
    """Показать топ 20 пользователей"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        c = conn.cursor()
        c.execute("""SELECT user_id, username, first_name, balance 
                     FROM users ORDER BY balance DESC LIMIT 20""")
        top_users = c.fetchall()
        
        if not top_users:
            bot.reply_to(message, "📊 Пока нет пользователей")
            return
        
        top_text = "🏆 *ТОП 20 ПОЛЬЗОВАТЕЛЕЙ:*\n\n"
        
        for idx, user in enumerate(top_users, 1):
            user_id, username, first_name, balance = user
            name = username or first_name or f"ID: {user_id}"
            top_text += f"{idx}. {name} — *{balance}* GRAM\n"
        
        bot.reply_to(message, top_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка top20: {e}")
        bot.reply_to(message, "❌ Ошибка получения топа")

@bot.message_handler(commands=['finduser'])
def find_user_cmd(message):
    """Найти пользователя по ID"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: `/finduser [user_id]`")
            return
        
        target_id = int(parts[1])
        
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        user_id_db, balance, username, first_name, last_name, last_bonus, created_at = user
        
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM transactions WHERE from_user = ? OR to_user = ?", (target_id, target_id))
        transaction_count = c.fetchone()[0]
        
        c.execute("SELECT SUM(amount) FROM transactions WHERE to_user = ? AND amount > 0", (target_id,))
        total_received = c.fetchone()[0] or 0
        
        c.execute("SELECT SUM(amount) FROM transactions WHERE from_user = ? AND amount > 0", (target_id,))
        total_sent = c.fetchone()[0] or 0
        
        user_info = f"""
👤 *ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ*

🆔 ID: `{user_id_db}`
👤 Имя: *{first_name or 'Не указано'}*
📛 Фамилия: *{last_name or 'Не указано'}*
🔗 Юзернейм: *@{username}*` if username else 'Не указано'`

💰 Баланс: *{balance}* GRAM
📅 Зарегистрирован: *{created_at}*
🎁 Последний бонус: *{last_bonus or 'Никогда'}*

📊 *СТАТИСТИКА:*
📈 Транзакций: *{transaction_count}*
📥 Получено: *{total_received}* GRAM
📤 Отправлено: *{total_sent}* GRAM
"""
        bot.reply_to(message, user_info, parse_mode='Markdown')
        
    except ValueError:
        bot.reply_to(message, "❌ user_id должен быть числом")
    except Exception as e:
        logger.error(f"Ошибка finduser: {e}")
        bot.reply_to(message, "❌ Ошибка поиска пользователя")

@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    """Сделать рассылку всем пользователям"""
    user_id = message.from_user.id
    
    if user_id not in ADMINS:
        bot.reply_to(message, "❌ Эта команда только для админов")
        return
    
    try:
        text = message.text.strip()
        if len(text.split()) < 2:
            bot.reply_to(message, "❌ Формат: `/broadcast [текст рассылки]`")
            return
        
        broadcast_text = text.replace('/broadcast', '', 1).strip()
        
        confirm_text = f"""
📢 *ПОДТВЕРЖДЕНИЕ РАССЫЛКИ*

Текст рассылки:
{broadcast_text}

⚠️ *Внимание:* Рассылка будет отправлена ВСЕМ пользователям бота.

Для подтверждения отправьте: `/confirm_broadcast`
Для отмены: `/cancel_broadcast`
"""
        
        # Для упрощения, сразу делаем рассылку
        bot.reply_to(message, "⚠️ Рассылка в разработке. Команда будет работать в следующей версии.")
        
    except Exception as e:
        logger.error(f"Ошибка broadcast: {e}")
        bot.reply_to(message, "❌ Ошибка подготовки рассылки")

# ========== ФУНКЦИИ ИГР ==========
def process_roulette_bet(message):
    """Обработка ставки на рулетку в формате: 50 14"""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        parts = text.split()
        
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: [сумма] [ставка]\nПример: `50 14` - 50 на число 14")
            return
        
        try:
            amount = int(parts[0])
            bet_value = parts[1].lower()
        except ValueError:
            bot.reply_to(message, "❌ Сумма должна быть числом")
            return
        
        balance = get_user_balance(user_id)
        if balance < amount:
            bot.reply_to(message, f"❌ Недостаточно средств!\n💰 Ваш баланс: {balance} GRAM")
            return
        
        if amount < MIN_BET:
            bot.reply_to(message, f"❌ Минимальная ставка: {MIN_BET} GRAM")
            return
        
        roulette_number = random.randint(0, 36)
        is_red = roulette_number in [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
        is_black = roulette_number in [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35]
        
        win = False
        multiplier = 0
        win_amount = 0
        
        if bet_value == 'red':
            if is_red:
                win = True
                multiplier = 2
                win_amount = amount * multiplier
        elif bet_value == 'black':
            if is_black:
                win = True
                multiplier = 2
                win_amount = amount * multiplier
        elif bet_value == 'zero':
            if roulette_number == 0:
                win = True
                multiplier = 14
                win_amount = amount * multiplier
        else:
            try:
                bet_number = int(bet_value)
                if bet_number < 0 or bet_number > 36:
                    bot.reply_to(message, "❌ Число должно быть от 0 до 36")
                    return
                
                if bet_number == roulette_number:
                    win = True
                    multiplier = 36
                    win_amount = amount * multiplier
            except ValueError:
                bot.reply_to(message, "❌ Некорректная ставка. Доступно: число (0-36), red, black, zero")
                return
        
        if win:
            update_balance(user_id, win_amount - amount)
            add_transaction(0, user_id, win_amount - amount, "roulette_win")
            
            result_text = f"""
🎰 *РУЛЕТКА | ВЫИГРЫШ!*

🎯 Выпало: *{roulette_number}*
💰 Ставка: *{amount}* GRAM
📈 Коэффициент: *x{multiplier}*
🏆 Выигрыш: *{win_amount}* GRAM
💎 Прибыль: *{win_amount - amount}* GRAM

💳 Баланс: *{get_user_balance(user_id)}* GRAM
"""
            bot.reply_to(message, result_text, parse_mode='Markdown')
        else:
            update_balance(user_id, -amount)
            add_transaction(user_id, 0, amount, "roulette_loss")
            
            color = "🟢 ZERO" if roulette_number == 0 else "🔴 RED" if is_red else "⚫ BLACK"
            
            result_text = f"""
🎰 *РУЛЕТКА | ПРОИГРЫШ*

🎯 Выпало: *{roulette_number}* ({color})
💰 Ставка: *{amount}* GRAM
💸 Потеряно: *{amount}* GRAM

💳 Баланс: *{get_user_balance(user_id)}* GRAM
"""
            bot.reply_to(message, result_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Ошибка в рулетке: {e}")
        bot.reply_to(message, "❌ Ошибка обработки ставки")

def process_mines_bet(message):
    """Обработка ставки на мины в формате: б мины 50"""
    try:
        user_id = message.from_user.id
        text = message.text.strip().lower()
        
        parts = text.split()
        if len(parts) != 3 or parts[0] != 'б' or parts[1] != 'мины':
            return False
        
        try:
            amount = int(parts[2])
        except ValueError:
            bot.reply_to(message, "❌ Формат: `б мины [сумма]`\nПример: `б мины 50`")
            return True
        
        balance = get_user_balance(user_id)
        if balance < amount:
            bot.reply_to(message, f"❌ Недостаточно средств!\n💰 Ваш баланс: {balance} GRAM")
            return True
        
        if amount < MINES_MIN_BET:
            bot.reply_to(message, f"❌ Минимальная ставка: {MINES_MIN_BET} GRAM")
            return True
        
        grid_size = GRID_SIZE
        total_cells = grid_size * grid_size
        
        mine_positions = random.sample(range(total_cells), MINES_COUNT)
        
        game_id = f"{user_id}_{int(time.time())}"
        mines_games[game_id] = {
            'user_id': user_id,
            'chat_id': message.chat.id,
            'bet_amount': amount,
            'mines_count': MINES_COUNT,
            'grid_size': grid_size,
            'mine_positions': mine_positions,
            'revealed_cells': [],
            'current_payout': amount,
            'game_state': 'active',
            'created_at': datetime.now()
        }
        
        update_balance(user_id, -amount)
        add_transaction(user_id, 0, amount, "mines_bet")
        
        keyboard = []
        for row in range(grid_size):
            row_buttons = []
            for col in range(grid_size):
                cell_index = row * grid_size + col
                row_buttons.append(InlineKeyboardButton(
                    text="🟦", 
                    callback_data=f"mines_{game_id}_{cell_index}"
                ))
            keyboard.append(row_buttons)
        
        keyboard.append([
            InlineKeyboardButton(text="💰 Забрать", callback_data=f"mines_cashout_{game_id}"),
            InlineKeyboardButton(text="❌ Закончить", callback_data=f"mines_end_{game_id}")
        ])
        
        markup = InlineKeyboardMarkup(keyboard)
        
        game_text = f"""
💣 *МИНЫ | ИГРА НАЧАТА*

💰 Ставка: *{amount}* GRAM
💣 Мин на поле: *{MINES_COUNT}*
🎯 Размер поля: *{grid_size}×{grid_size}*
🏆 Текущий множитель: *1.00x*

💎 Текущий выигрыш: *{amount}* GRAM

⚠️ Выберите клетку для открытия.
"""
        bot.reply_to(message, game_text, parse_mode='Markdown', reply_markup=markup)
        return True
        
    except Exception as e:
        logger.error(f"Ошибка в минах: {e}")
        return False

def process_mines_click(call, game_id, cell_index):
    """Обработка клика по клетке в минах"""
    try:
        if game_id not in mines_games:
            bot.answer_callback_query(call.id, "❌ Игра не найдена или завершена")
            return
        
        game = mines_games[game_id]
        
        if game['user_id'] != call.from_user.id:
            bot.answer_callback_query(call.id, "❌ Это не ваша игра!")
            return
        
        if cell_index in game['revealed_cells']:
            bot.answer_callback_query(call.id, "❌ Эта клетка уже открыта!")
            return
        
        if cell_index in game['mine_positions']:
            game['game_state'] = 'lost'
            game['revealed_cells'].append(cell_index)
            
            keyboard = []
            grid_size = game['grid_size']
            
            for row in range(grid_size):
                row_buttons = []
                for col in range(grid_size):
                    cell_idx = row * grid_size + col
                    if cell_idx in game['mine_positions']:
                        row_buttons.append(InlineKeyboardButton(text="💣", callback_data="noop"))
                    elif cell_idx == cell_index:
                        row_buttons.append(InlineKeyboardButton(text="💥", callback_data="noop"))
                    elif cell_idx in game['revealed_cells']:
                        row_buttons.append(InlineKeyboardButton(text="💎", callback_data="noop"))
                    else:
                        row_buttons.append(InlineKeyboardButton(text="🟦", callback_data="noop"))
                keyboard.append(row_buttons)
            
            markup = InlineKeyboardMarkup(keyboard)
            
            lose_text = f"""
💣 *МИНЫ | ПРОИГРЫШ*

💰 Ставка: *{game['bet_amount']}* GRAM
💣 Мин на поле: *{game['mines_count']}*
💸 Потеряно: *{game['bet_amount']}* GRAM

😔 Вы наткнулись на мину!

🔄 Новая игра: `б мины [сумма]`
"""
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=lose_text,
                parse_mode='Markdown',
                reply_markup=markup
            )
            
            del mines_games[game_id]
            
        else:
            game['revealed_cells'].append(cell_index)
            opened_safe = len(game['revealed_cells'])
            total_safe = (game['grid_size'] * game['grid_size']) - game['mines_count']
            
            multiplier = 1.0
            if opened_safe > 0:
                risk_factor = game['mines_count'] / (game['grid_size'] * game['grid_size'])
                multiplier = 1 + (opened_safe * 0.5 * (1 + risk_factor * 2))
            
            game['current_payout'] = int(game['bet_amount'] * multiplier)
            
            keyboard = []
            grid_size = game['grid_size']
            
            for row in range(grid_size):
                row_buttons = []
                for col in range(grid_size):
                    cell_idx = row * grid_size + col
                    if cell_idx in game['revealed_cells']:
                        if cell_idx == cell_index:
                            row_buttons.append(InlineKeyboardButton(text="💎", callback_data="noop"))
                        else:
                            row_buttons.append(InlineKeyboardButton(text="🟩", callback_data="noop"))
                    else:
                        row_buttons.append(InlineKeyboardButton(
                            text="🟦", 
                            callback_data=f"mines_{game_id}_{cell_idx}"
                        ))
                keyboard.append(row_buttons)
            
            keyboard.append([
                InlineKeyboardButton(text=f"💰 Забрать {multiplier:.2f}x", callback_data=f"mines_cashout_{game_id}"),
                InlineKeyboardButton(text="❌ Закончить", callback_data=f"mines_end_{game_id}")
            ])
            
            markup = InlineKeyboardMarkup(keyboard)
            
            game_text = f"""
💣 *МИНЫ | ИГРА ПРОДОЛЖАЕТСЯ*

💰 Ставка: *{game['bet_amount']}* GRAM
💣 Открыто клеток: *{opened_safe}*
🎯 Всего безопасных: *{total_safe}*
🏆 Множитель: *{multiplier:.2f}x*

💎 Выигрыш: *{game['current_payout']}* GRAM

⚠️ Выберите следующую клетку.
"""
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=game_text,
                parse_mode='Markdown',
                reply_markup=markup
            )
        
    except Exception as e:
        logger.error(f"Ошибка обработки клика в минах: {e}")

def process_mines_cashout(call, game_id):
    """Обработка вывода в минах"""
    try:
        if game_id not in mines_games:
            bot.answer_callback_query(call.id, "❌ Игра не найдена")
            return
        
        game = mines_games[game_id]
        
        if game['user_id'] != call.from_user.id:
            bot.answer_callback_query(call.id, "❌ Это не ваша игра!")
            return
        
        win_amount = game['current_payout'] - game['bet_amount']
        update_balance(call.from_user.id, game['current_payout'])
        add_transaction(0, call.from_user.id, win_amount, "mines_win")
        
        cashout_text = f"""
💰 *МИНЫ | ВЫВОД СРЕДСТВ*

🏆 Вы успешно вывели средства!
💰 Ставка: *{game['bet_amount']}* GRAM
📈 Множитель: *{(game['current_payout'] / game['bet_amount']):.2f}x*
🎯 Выигрыш: *{game['current_payout']}* GRAM
💎 Прибыль: *{win_amount}* GRAM

💳 Баланс: *{get_user_balance(call.from_user.id)}* GRAM

🔄 Новая игра: `б мины [сумма]`
"""
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=cashout_text,
            parse_mode='Markdown'
        )
        
        del mines_games[game_id]
        
    except Exception as e:
        logger.error(f"Ошибка вывода в минах: {e}")

# ========== ФУНКЦИЯ ПРОВЕРКИ БАЛАНСА ==========
def check_balance_command(message):
    """Проверка баланса через разные команды: б, b, баланс"""
    text = message.text.strip().lower()
    
    balance_commands = ['б', 'b', 'баланс']
    
    if text in balance_commands:
        user_id = message.from_user.id
        balance = get_user_balance(user_id)
        
        update_user_info(
            user_id,
            message.from_user.username,
            message.from_user.first_name,
            message.from_user.last_name
        )
        
        balance_text = f"""
💰 *ВАШ БАЛАНС*

👤 ID: `{user_id}`
💳 Баланс: *{balance}* GRAM

📋 *Примеры команд:*
• `б мины 50` - игра в мины
• `50 14` - 50 на число 14 в рулетке
• `100 red` - 100 на красное
• `б` - баланс
"""
        bot.reply_to(message, balance_text, parse_mode='Markdown')
        return True
    
    return False

# ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========
@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Приветственное сообщение"""
    user_id = message.from_user.id
    update_user_info(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    welcome_text = "🎰 *Добро пожаловать в Casino Mega Bot!*\n\nДля помощи используй /help"
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def send_help(message):
    """Справка по командам"""
    help_text = """
🎮 *ДОСТУПНЫЕ КОМАНДЫ:*

💰 *БАЛАНС*
`б`, `баланс` - показать баланс

🎰 *РУЛЕТКА*
`[сумма] [ставка]`
Примеры:
• `50 14` - 50 на число 14
• `100 red` - 100 на красное
• `200 black` - 200 на черное
• `50 zero` - 50 на зеро

💣 *МИНЫ*
`б мины [сумма]`
Пример: `б мины 50`
• 5 мин на поле 5×5
• Открывай клетки, избегая мин
• Забирай выигрыш в любой момент

💰 Минимальная ставка: 5 GRAM
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Обработка всех сообщений"""
    
    if check_balance_command(message):
        return
    
    if message.text.lower().startswith('б мины'):
        if process_mines_bet(message):
            return
    
    try:
        parts = message.text.strip().split()
        if len(parts) == 2:
            if parts[0].isdigit():
                process_roulette_bet(message)
                return
    except:
        pass
    
    help_text = "❓ Неизвестная команда\nИспользуй /help для списка команд"
    bot.reply_to(message, help_text)

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Обработка callback-запросов"""
    try:
        data = call.data
        
        if data.startswith('mines_'):
            parts = data.split('_')
            if len(parts) >= 3:
                game_id = parts[1]
                
                if parts[2] == 'cashout':
                    process_mines_cashout(call, game_id)
                elif parts[2] == 'end':
                    if game_id in mines_games:
                        del mines_games[game_id]
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text="❌ Игра отменена",
                        parse_mode='Markdown'
                    )
                else:
                    if len(parts) >= 4:
                        cell_index = int(parts[3])
                        process_mines_click(call, game_id, cell_index)
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    logger.info("🚀 Запуск Casino Mega Bot...")
    
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        setup_webhook()
        logger.info("🌐 Режим: Webhook (Railway)")
        
        app = Flask(__name__)
        
        @app.route('/')
        def home():
            return jsonify(health_check())
        
        @app.route('/health')
        def health():
            return jsonify(health_check())
        
        @app.route(f'/{BOT_TOKEN}', methods=['POST'])
        def webhook():
            if request.headers.get('content-type') == 'application/json':
                json_string = request.get_data().decode('utf-8')
                update = telebot.types.Update.de_json(json_string)
                bot.process_new_updates([update])
                return ''
            return 'Bad Request', 400
        
        port = int(os.environ.get('PORT', 5000))
        threading.Thread(
            target=app.run,
            kwargs={'host': '0.0.0.0', 'port': port, 'debug': False, 'use_reloader': False}
        ).start()
        
        bot.infinity_polling()
    else:
        logger.info("🖥️ Режим: Polling (локальный)")
        bot.infinity_polling()
