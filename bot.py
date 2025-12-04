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
# Получение токена из переменных окружения Railway
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# Админы (можно задать через переменные окружения)
ADMIN_IDS = os.environ.get('ADMIN_IDS')
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

# ========== ФУНКЦИИ РАБОТЫ С БД ==========
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

def update_user_info(user_id, username, first_name, last_name):
    """Обновление информации о пользователе"""
    try:
        c = conn.cursor()
        c.execute("""UPDATE users SET username = ?, first_name = ?, last_name = ? 
                     WHERE user_id = ?""", 
                     (username, first_name, last_name, user_id))
        conn.commit()
        logger.info(f"Информация обновлена для user_id={user_id}")
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
            logger.info(f"Баланс обновлен: user_id={user_id}, изменение={amount}, новый баланс={new_balance}")
            return new_balance
        else:
            logger.warning(f"Пользователь {user_id} не найден при обновлении баланса")
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

def get_top_users(limit=10):
    """Получение топа пользователей по балансу"""
    try:
        c = conn.cursor()
        c.execute("""SELECT user_id, username, first_name, balance 
                     FROM users ORDER BY balance DESC LIMIT ?""", (limit,))
        return c.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Ошибка get_top_users: {e}")
        return []

def update_last_bonus(user_id):
    """Обновление времени последнего бонуса"""
    try:
        c = conn.cursor()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (current_time, user_id))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Ошибка update_last_bonus: {e}")
        return False

def get_last_bonus(user_id):
    """Получение времени последнего бонуса"""
    try:
        c = conn.cursor()
        c.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Ошибка get_last_bonus: {e}")
        return None

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
mines_games = {}

# ========== ФУНКЦИИ ИГР ==========
def process_roulette_bet(message):
    """Обработка ставки на рулетку в формате: 50 1 4 1-7"""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        parts = text.split()
        
        if len(parts) < 3:
            bot.reply_to(message, "❌ Формат: [сумма] [тип ставки] [значения]\nПример: `50 1 4` или `100 2 red`")
            return
        
        # Парсим сумму
        try:
            amount = int(parts[0])
        except ValueError:
            bot.reply_to(message, "❌ Сумма должна быть числом")
            return
        
        # Проверяем баланс
        balance = get_user_balance(user_id)
        if balance < amount:
            bot.reply_to(message, f"❌ Недостаточно средств!\n💰 Ваш баланс: {balance} GRAM")
            return
        
        if amount < MIN_BET:
            bot.reply_to(message, f"❌ Минимальная ставка: {MIN_BET} GRAM")
            return
        
        # Парсим тип ставки
        bet_type = parts[1].lower()
        
        # Играем в рулетку
        roulette_number = random.randint(0, 36)
        is_red = roulette_number in [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
        is_black = roulette_number in [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35]
        
        win = False
        multiplier = 0
        win_amount = 0
        
        # Проверяем ставки
        if bet_type == '1':  # На число
            if len(parts) < 3:
                bot.reply_to(message, "❌ Укажите число для ставки\nПример: `50 1 7`")
                return
            
            try:
                bet_number = int(parts[2])
                if bet_number < 0 or bet_number > 36:
                    bot.reply_to(message, "❌ Число должно быть от 0 до 36")
                    return
                
                if bet_number == roulette_number:
                    win = True
                    multiplier = 36
                    win_amount = amount * multiplier
                
            except ValueError:
                bot.reply_to(message, "❌ Некорректное число")
                return
                
        elif bet_type == '2':  # На цвет
            if len(parts) < 3:
                bot.reply_to(message, "❌ Укажите цвет (red/black/zero)\nПример: `50 2 red`")
                return
            
            color = parts[2].lower()
            
            if color == 'red' and is_red:
                win = True
                multiplier = 2
                win_amount = amount * multiplier
            elif color == 'black' and is_black:
                win = True
                multiplier = 2
                win_amount = amount * multiplier
            elif color == 'zero' and roulette_number == 0:
                win = True
                multiplier = 14
                win_amount = amount * multiplier
            elif color == 'zero':
                win = False  # Ставка на зеро проиграна
                
        elif bet_type == '3':  На диапазон (1-7, 8-14 и т.д.)
            if len(parts) < 3:
                bot.reply_to(message, "❌ Укажите диапазон\nПример: `50 3 1-7`")
                return
            
            try:
                range_str = parts[2]
                if '-' not in range_str:
                    bot.reply_to(message, "❌ Диапазон должен быть в формате X-Y")
                    return
                
                start, end = map(int, range_str.split('-'))
                if start < 0 or end > 36 or start > end:
                    bot.reply_to(message, "❌ Некорректный диапазон")
                    return
                
                if start <= roulette_number <= end:
                    win = True
                    # Коэффициент зависит от размера диапазона
                    range_size = end - start + 1
                    multiplier = 36 / range_size
                    win_amount = int(amount * multiplier)
                
            except ValueError:
                bot.reply_to(message, "❌ Некорректный диапазон")
                return
        else:
            bot.reply_to(message, "❌ Неизвестный тип ставки. Доступные: 1 (число), 2 (цвет), 3 (диапазон)")
            return
        
        # Обрабатываем результат
        if win:
            # Выигрыш
            update_balance(user_id, win_amount - amount)  # +выигрыш, -ставка
            add_transaction(0, user_id, win_amount - amount, "roulette_win")
            
            result_text = f"""
🎰 *РУЛЕТКА | ВЫИГРЫШ!*

🎯 Выпало: *{roulette_number}*
💰 Ставка: *{amount}* GRAM
📈 Коэффициент: *x{multiplier}*
🏆 Выигрыш: *{win_amount}* GRAM
💎 Чистая прибыль: *{win_amount - amount}* GRAM

💳 Баланс: *{get_user_balance(user_id)}* GRAM
"""
            bot.reply_to(message, result_text, parse_mode='Markdown')
        else:
            # Проигрыш
            update_balance(user_id, -amount)
            add_transaction(user_id, 0, amount, "roulette_loss")
            
            # Определяем цвет выпавшего числа
            color = "🟢 ZERO" if roulette_number == 0 else "🔴 RED" if is_red else "⚫ BLACK"
            
            result_text = f"""
🎰 *РУЛЕТКА | ПРОИГРЫШ*

🎯 Выпало: *{roulette_number}* ({color})
💰 Ставка: *{amount}* GRAM
💸 Потеряно: *{amount}* GRAM

💳 Баланс: *{get_user_balance(user_id)}* GRAM

💪 Удачи в следующий раз!
"""
            bot.reply_to(message, result_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Ошибка в рулетке: {e}")
        bot.reply_to(message, "❌ Ошибка обработки ставки")

def process_mines_bet(message):
    """Обработка ставки на мины в формате: б мины 50 5"""
    try:
        user_id = message.from_user.id
        text = message.text.strip().lower()
        
        # Парсим "б мины 50 5"
        parts = text.split()
        if len(parts) < 4 or parts[0] != 'б' or parts[1] != 'мины':
            return False
        
        try:
            amount = int(parts[2])
            mines_count = int(parts[3])
        except ValueError:
            bot.reply_to(message, "❌ Формат: `б мины [сумма] [количество мин]`\nПример: `б мины 50 5`")
            return True
        
        # Проверяем баланс
        balance = get_user_balance(user_id)
        if balance < amount:
            bot.reply_to(message, f"❌ Недостаточно средств!\n💰 Ваш баланс: {balance} GRAM")
            return True
        
        if amount < MINES_MIN_BET:
            bot.reply_to(message, f"❌ Минимальная ставка: {MINES_MIN_BET} GRAM")
            return True
        
        if mines_count < 1 or mines_count > MINES_MAX_MINES:
            bot.reply_to(message, f"❌ Количество мин: 1-{MINES_MAX_MINES}")
            return True
        
        # Создаем игру
        grid_size = MINES_DEFAULT_SIZE
        total_cells = grid_size * grid_size
        
        # Генерируем мины
        mine_positions = random.sample(range(total_cells), mines_count)
        
        # Сохраняем игру
        game_id = f"{user_id}_{int(time.time())}"
        mines_games[game_id] = {
            'user_id': user_id,
            'chat_id': message.chat.id,
            'bet_amount': amount,
            'mines_count': mines_count,
            'grid_size': grid_size,
            'mine_positions': mine_positions,
            'revealed_cells': [],
            'current_payout': amount,
            'game_state': 'active',
            'created_at': datetime.now()
        }
        
        # Списываем ставку
        update_balance(user_id, -amount)
        add_transaction(user_id, 0, amount, "mines_bet")
        
        # Создаем клавиатуру
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
        
        # Кнопки вывода
        keyboard.append([
            InlineKeyboardButton(text="💰 Забрать", callback_data=f"mines_cashout_{game_id}"),
            InlineKeyboardButton(text="❌ Закончить", callback_data=f"mines_end_{game_id}")
        ])
        
        markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем сообщение с игрой
        game_text = f"""
💣 *МИНЫ | ИГРА НАЧАТА*

💰 Ставка: *{amount}* GRAM
💣 Мин на поле: *{mines_count}*
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
        
        # Проверяем, не мина ли это
        if cell_index in game['mine_positions']:
            # ИГРА ПРОИГРАНА
            game['game_state'] = 'lost'
            game['revealed_cells'].append(cell_index)
            
            # Создаем новую клавиатуру с открытыми минами
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
            
            # Обновляем сообщение
            lose_text = f"""
💣 *МИНЫ | ПРОИГРЫШ*

💰 Ставка: *{game['bet_amount']}* GRAM
💣 Открыто мин: *{len([c for c in game['revealed_cells'] if c in game['mine_positions']])}*
🎯 Всего мин: *{game['mines_count']}*
💸 Потеряно: *{game['bet_amount']}* GRAM

😔 Вы наткнулись на мину!

🔄 Новая игра: `б мины [сумма] [мины]`
"""
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=lose_text,
                parse_mode='Markdown',
                reply_markup=markup
            )
            
            # Удаляем игру из памяти
            del mines_games[game_id]
            
        else:
            # Клетка безопасна
            game['revealed_cells'].append(cell_index)
            opened_safe = len(game['revealed_cells'])
            total_safe = (game['grid_size'] * game['grid_size']) - game['mines_count']
            
            # Рассчитываем множитель
            multiplier = 1.0
            if opened_safe > 0:
                # Формула множителя (можно настроить)
                risk_factor = game['mines_count'] / (game['grid_size'] * game['grid_size'])
                multiplier = 1 + (opened_safe * 0.5 * (1 + risk_factor * 2))
            
            game['current_payout'] = int(game['bet_amount'] * multiplier)
            
            # Обновляем клавиатуру
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
            
            # Кнопки вывода
            keyboard.append([
                InlineKeyboardButton(text=f"💰 Забрать {multiplier:.2f}x", callback_data=f"mines_cashout_{game_id}"),
                InlineKeyboardButton(text="❌ Закончить", callback_data=f"mines_end_{game_id}")
            ])
            
            markup = InlineKeyboardMarkup(keyboard)
            
            # Обновляем сообщение
            game_text = f"""
💣 *МИНЫ | ИГРА ПРОДОЛЖАЕТСЯ*

💰 Ставка: *{game['bet_amount']}* GRAM
💣 Открыто клеток: *{opened_safe}*
🎯 Всего безопасных: *{total_safe}*
🏆 Текущий множитель: *{multiplier:.2f}x*

💎 Текущий выигрыш: *{game['current_payout']}* GRAM

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
        
        # Зачисляем выигрыш
        win_amount = game['current_payout'] - game['bet_amount']
        update_balance(call.from_user.id, game['current_payout'])
        add_transaction(0, call.from_user.id, win_amount, "mines_win")
        
        # Показываем результат
        cashout_text = f"""
💰 *МИНЫ | ВЫВОД СРЕДСТВ*

🏆 Вы успешно вывели средства!
💰 Ставка: *{game['bet_amount']}* GRAM
📈 Множитель: *{(game['current_payout'] / game['bet_amount']):.2f}x*
🎯 Выигрыш: *{game['current_payout']}* GRAM
💎 Чистая прибыль: *{win_amount}* GRAM

💳 Баланс: *{get_user_balance(call.from_user.id)}* GRAM

🔄 Новая игра: `б мины [сумма] [мины]`
"""
        
        # Удаляем клавиатуру
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=cashout_text,
            parse_mode='Markdown'
        )
        
        # Удаляем игру из памяти
        del mines_games[game_id]
        
    except Exception as e:
        logger.error(f"Ошибка вывода в минах: {e}")

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
    
    welcome_text = """
🎰 *Добро пожаловать в Casino Mega Bot!*

*Доступные игры:*

🎯 *РУЛЕТКА*
Формат: `[сумма] [тип] [значение]`
Примеры:
• `50 1 7` - 50 на число 7
• `100 2 red` - 100 на красное
• `200 3 1-7` - 200 на диапазон 1-7

💣 *МИНЫ*
Формат: `б мины [сумма] [мины]`
Пример: `б мины 50 5`

💰 Минимальная ставка: 5 GRAM
🎁 Для получения баланса: /balance
"""
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['balance'])
def show_balance(message):
    """Показать баланс"""
    user_id = message.from_user.id
    balance = get_user_balance(user_id)
    
    balance_text = f"""
💰 *ВАШ БАЛАНС*

💳 Баланс: *{balance}* GRAM
🆔 ID: `{user_id}`

🎮 *Игры:*
🎯 Рулетка: `[сумма] [тип] [значение]`
💣 Мины: `б мины [сумма] [мины]`
"""
    bot.reply_to(message, balance_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Обработка всех сообщений"""
    user_id = message.from_user.id
    
    # Сначала пробуем обработать как "б мины"
    if message.text.lower().startswith('б мины'):
        if process_mines_bet(message):
            return
    
    # Пробуем обработать как ставку на рулетку (формат: 50 1 7)
    try:
        parts = message.text.strip().split()
        if len(parts) >= 3 and parts[0].isdigit():
            # Проверяем, что это может быть ставка
            amount = int(parts[0])
            bet_type = parts[1]
            
            if bet_type in ['1', '2', '3']:
                process_roulette_bet(message)
                return
    except:
        pass
    
    # Если не распознано - показываем помощь
    help_text = """
❓ *Неизвестная команда*

*Доступные форматы:*

🎯 *РУЛЕТКА*
`50 1 7` - 50 на число 7
`100 2 red` - 100 на красное
`200 3 1-7` - 200 на диапазон 1-7

💣 *МИНЫ*
`б мины 50 5` - ставка 50, 5 мин

💰 *БАЛАНС*
/balance - показать баланс

🎮 *ПОМОЩЬ*
/start - показать справку
"""
    bot.reply_to(message, help_text, parse_mode='Markdown')

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
                    # Завершение игры без вывода
                    if game_id in mines_games:
                        del mines_games[game_id]
                    bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text="❌ Игра отменена",
                        parse_mode='Markdown'
                    )
                else:
                    # Клик по клетке
                    if len(parts) >= 4:
                        cell_index = int(parts[3])
                        process_mines_click(call, game_id, cell_index)
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    logger.info("🚀 Запуск Casino Mega Bot...")
    logger.info(f"🤖 Токен бота: {BOT_TOKEN[:10]}...")
    logger.info(f"👑 Админы: {ADMINS}")
    
    # Проверяем наличие вебхука для Railway
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        setup_webhook()
        logger.info("🌐 Режим: Webhook (Railway)")
        
        # Создаем Flask сервер для health checks
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
