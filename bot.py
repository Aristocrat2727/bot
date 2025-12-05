import os
import random
import sqlite3
import telebot
import time
import threading
import uuid
import signal
import sys
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

# Инициализация бота
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
    """Обработка ставки на мины в формате: мины 50"""
    try:
        user_id = message.from_user.id
        text = message.text.strip().lower()
        
        # Парсим "мины 50"
        parts = text.split()
        if len(parts) != 2 or parts[0] != 'мины':
            return False
        
        try:
            amount = int(parts[1])
        except ValueError:
            bot.reply_to(message, "❌ Формат: `мины [сумма]`\nПример: `мины 50`")
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
        
        # Создаем уникальный ID для игры
        game_id = str(uuid.uuid4())[:8]
        
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
            InlineKeyboardButton(text="💰 Забрать", callback_data=f"mines_{game_id}_cashout"),
            InlineKeyboardButton(text="❌ Закончить", callback_data=f"mines_{game_id}_end")
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

def get_mines_multiplier(opened_cells):
    """Получение множителя для мин в зависимости от открытых клеток"""
    # Прогрессивные множители: x1.28, x1.65, x2.10, x2.65, x3.30, x4.05, x5.00, x6.15, x7.50, x9.15, x11.10, x13.45, x16.30, x19.75, x23.90, x29.00, x35.20, x42.70, x51.80, x62.90
    multipliers = [
        1.00,  # 0 клеток
        1.28,  # 1 клетка
        1.65,  # 2 клетки
        2.10,  # 3 клетки
        2.65,  # 4 клетки
        3.30,  # 5 клеток
        4.05,  # 6 клеток
        5.00,  # 7 клеток
        6.15,  # 8 клеток
        7.50,  # 9 клеток
        9.15,  # 10 клеток
        11.10, # 11 клеток
        13.45, # 12 клеток
        16.30, # 13 клеток
        19.75, # 14 клеток
        23.90, # 15 клеток
        29.00, # 16 клеток
        35.20, # 17 клеток
        42.70, # 18 клеток
        51.80, # 19 клеток
        62.90  # 20 клеток
    ]
    
    if opened_cells >= len(multipliers):
        return multipliers[-1]
    return multipliers[opened_cells]

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

🔄 Новая игра: `мины [сумма]`
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
            
            # Используем прогрессивные множители
            multiplier = get_mines_multiplier(opened_safe)
            
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
                InlineKeyboardButton(text=f"💰 Забрать {multiplier:.2f}x", callback_data=f"mines_{game_id}_cashout"),
                InlineKeyboardButton(text="❌ Закончить", callback_data=f"mines_{game_id}_end")
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
        
        multiplier = game['current_payout'] / game['bet_amount']
        
        cashout_text = f"""
💰 *МИНЫ | ВЫВОД СРЕДСТВ*

🏆 Вы успешно вывели средства!
💰 Ставка: *{game['bet_amount']}* GRAM
📈 Множитель: *{multiplier:.2f}x*
🎯 Выигрыш: *{game['current_payout']}* GRAM
💎 Прибыль: *{win_amount}* GRAM

💳 Баланс: *{get_user_balance(call.from_user.id)}* GRAM

🔄 Новая игра: `мины [сумма]`
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
    """Проверка баланса через разные команды: б, баланс"""
    text = message.text.strip().lower()
    
    balance_commands = ['б', 'баланс']
    
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
🎰 *CASINO MEGA BOT*

💰 *БАЛАНС*
`б`, `баланс` - показать баланс

🎮 *ДОСТУПНЫЕ ИГРЫ:*

🎯 *РУЛЕТКА*
Простая ставка: `[сумма] [ставка]`
Примеры:
• `50 14` - 50 на число 14
• `100 red` - 100 на красное
• `200 black` - 200 на черное
• `50 zero` - 50 на зеро (0)

💣 *МИНЫ*
`мины [сумма]`
Пример: `мины 100`
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
    
    # Проверяем мины (мины ...)
    if message.text.lower().startswith('мины'):
        if process_mines_bet(message):
            return
    
    # Пробуем обработать как ставку на рулетку
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
            parts = data.split('_', 2)
            
            if len(parts) != 3:
                bot.answer_callback_query(call.id, "❌ Ошибка данных")
                return
            
            prefix, game_id, action = parts
            
            if action == 'cashout':
                process_mines_cashout(call, game_id)
            elif action == 'end':
                if game_id in mines_games:
                    del mines_games[game_id]
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text="❌ Игра отменена",
                    parse_mode='Markdown'
                )
            else:
                try:
                    cell_index = int(action)
                    process_mines_click(call, game_id, cell_index)
                except ValueError:
                    bot.answer_callback_query(call.id, "❌ Ошибка индекса клетки")
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

# ========== GRACEFUL SHUTDOWN ==========
def signal_handler(sig, frame):
    """Обработчик сигналов завершения"""
    logger.info("🚫 Получен сигнал завершения...")
    close_db()
    logger.info("👋 Бот завершает работу")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

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
