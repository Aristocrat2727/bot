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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== КОНФИГУРАЦИЯ ==========
# Получение токена из переменных окружения Railway
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8097920907:AAG2USuFxLLMap_AyzIwcYwjwOPHvqlHUtI')

# Админы (можно задать через переменные окружения)
ADMIN_IDS = os.environ.get('ADMIN_IDS', '5504715265,8386411290')
ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_IDS.split(',')]

# Настройки
MIN_BET = 5
MINES_MIN_BET = 5
MINES_MAX_BET = 100000
MINES_DEFAULT_MINES = 5
MINES_DEFAULT_SIZE = 5
MINES_MAX_MINES = 24

# Проверка наличия токена
if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
    logger.error("❌ BOT_TOKEN не установлен! Установите переменную окружения BOT_TOKEN в Railway.")
    exit(1)

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)

# Глобальное соединение с БД
conn = None

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
def init_db():
    global conn
    try:
        # Используем абсолютный путь для Railway
        db_path = os.environ.get('DATABASE_URL', 'casino_mega.db')
        
        # Если Railway предоставляет PostgreSQL URL, используем SQLite как fallback
        if db_path.startswith('postgresql://'):
            logger.warning("PostgreSQL URL обнаружен, но используется SQLite. Для PostgreSQL нужны дополнительные настройки.")
            db_path = 'casino_mega.db'
        
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        
        c = conn.cursor()
        
        # Создание таблиц
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
        
        # Создание индексов для производительности
        c.execute('''CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(from_user)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_roulette_user ON roulette_bets(user_id)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_mines_user ON mines_games(user_id)''')
        
        conn.commit()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

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
    """Проверка здоровья приложения для Railway"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bot": "running",
        "database": "connected" if conn else "disconnected"
    }

# Закрытие соединения при выходе
def close_db():
    global conn
    if conn:
        conn.close()
        logger.info("Соединение с БД закрыто")

atexit.register(close_db)

# Инициализация при запуске
init_db()

# Хранилище в памяти (для текущих игр)
user_sessions = {}
roulette_bets = {}
roulette_timers = {}
roulette_game_active = {}
roulette_countdowns = {}
mines_games = {}

# ========== ФУНКЦИИ РАБОТЫ С БД (остаются без изменений) ==========
def get_user(user_id):
    """Получение пользователя с автоматическим созданием если не существует"""
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        
        if not user:
            # Создаем нового пользователя
            c.execute("INSERT INTO users (user_id, balance, username, first_name, last_name) VALUES (?, ?, ?, ?, ?)", 
                     (user_id, 0, None, None, None))
            conn.commit()
            c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
            logger.info(f"Создан новый пользователь: {user_id}")
        
        return user
    except sqlite3.Error as e:
        logger.error(f"Ошибка get_user для {user_id}: {e}")
        init_db()  # Пытаемся восстановить соединение
        return None

def update_balance(user_id, amount):
    """Обновление баланса пользователя"""
    try:
        c = conn.cursor()
        c.execute("""UPDATE users SET balance = balance + ? WHERE user_id = ? RETURNING balance""", (int(amount), user_id))
        
        result = c.fetchone()
        if result:
            new_balance = result[0]
            conn.commit()
            logger.info(f"Баланс обновлен: user_id={user_id}, изменение={amount}, новый баланс={new_balance}")
            return new_balance
        else:
            logger.warning(f"Пользователь {user_id} не найден при обновлении баланса")
            return 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка update_balance для {user_id}: {e}")
        conn.rollback()
        return 0

# ... (остальные функции БД остаются без изменений) ...

# ========== КОМАНДЫ БОТА ==========
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Приветственное сообщение"""
    user_id = message.from_user.id
    user_info = get_user(user_id)
    
    welcome_text = """
🎰 *Добро пожаловать в Casino Mega!*

*Доступные команды:*
/start - Начало работы
/balance - Проверить баланс
/bonus - Получить ежедневный бонус
/top - Топ игроков
/deposit - Пополнить баланс
/withdraw - Вывести средства

*Игры:*
/roulette [ставка] [ставки] - Рулетка
/mines [ставка] [мины] - Игра в мины

*Для админов:*
/allusers - Список всех пользователей
/addbalance [id] [сумма] - Пополнить баланс

💰 *Минимальная ставка:* {} GRAM
🎁 *Ежедневный бонус:* до 1000 GRAM
    """.format(MIN_BET)
    
    # Обновляем информацию о пользователе
    update_user_info(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['balance'])
def show_balance(message):
    """Показать баланс"""
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if user:
        balance = user[1]
        username = user[2] or user[3] or "Игрок"
        
        balance_text = f"""
👤 *{username}*
💰 *Баланс:* {balance} GRAM
🆔 *ID:* {user_id}

💳 Пополнить: /deposit
📤 Вывести: /withdraw
🎁 Бонус: /bonus
        """
        bot.reply_to(message, balance_text, parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Ошибка получения баланса")

# ... (остальные обработчики команд) ...

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    logger.info("🚀 Запуск Casino Mega Bot...")
    logger.info(f"🤖 Токен бота: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Админы: {ADMINS}")
    
    # Проверяем наличие вебхука для Railway
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        setup_webhook()
        logger.info("🌐 Режим: Webhook (Railway)")
        
        # Создаем простой HTTP сервер для health checks
        from flask import Flask, jsonify
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
        
        # Запускаем Flask в отдельном потоке
        port = int(os.environ.get('PORT', 5000))
        threading.Thread(
            target=app.run,
            kwargs={'host': '0.0.0.0', 'port': port, 'debug': False, 'use_reloader': False}
        ).start()
        
        # Основной поток обрабатывает бота
        bot.infinity_polling()
    else:
        logger.info("🖥️ Режим: Polling (локальный)")
        bot.infinity_polling()
