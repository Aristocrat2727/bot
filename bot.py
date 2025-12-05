import os
import random
import sqlite3
import telebot
import time
import threading
import uuid
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta
import atexit
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_IDS = os.environ.get('ADMIN_IDS', '')
ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_IDS.split(',') if admin_id.strip()] if ADMIN_IDS else []

MIN_BET = 5
MINES_MIN_BET = 5
MINES_COUNT = 5
GRID_SIZE = 5
DAILY_BONUS = 2500
ROULETTE_COOLDOWN = 10

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
conn = None

def init_db():
    global conn
    try:
        conn = sqlite3.connect('casino_mega.db', check_same_thread=False, timeout=10)
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
        
        conn.commit()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

def get_user(user_id):
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        
        if not user:
            c.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 0))
            conn.commit()
            c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
        
        return user
    except sqlite3.Error as e:
        logger.error(f"Ошибка get_user для {user_id}: {e}")
        init_db()
        return None

def update_user_info(user_id, username, first_name, last_name):
    try:
        c = conn.cursor()
        c.execute("UPDATE users SET username = ?, first_name = ?, last_name = ? WHERE user_id = ?",
                 (username, first_name, last_name, user_id))
        conn.commit()
        return True
    except:
        return False

def update_balance(user_id, amount):
    try:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (int(amount), user_id))
        conn.commit()
        return get_user_balance(user_id)
    except:
        conn.rollback()
        return 0

def get_user_balance(user_id):
    try:
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        return result[0] if result else 0
    except:
        return 0

def add_transaction(from_user, to_user, amount, trans_type):
    try:
        c = conn.cursor()
        c.execute("INSERT INTO transactions (from_user, to_user, amount, type) VALUES (?, ?, ?, ?)",
                 (from_user, to_user, amount, trans_type))
        conn.commit()
        return True
    except:
        return False

def update_last_bonus(user_id):
    try:
        c = conn.cursor()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (current_time, user_id))
        conn.commit()
        return True
    except:
        return False

def get_last_bonus(user_id):
    try:
        c = conn.cursor()
        c.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        return result[0] if result else None
    except:
        return None

def close_db():
    global conn
    if conn:
        conn.close()
        logger.info("Соединение с БД закрыто")

atexit.register(close_db)
init_db()

mines_games = {}
roulette_bets = {}
roulette_timers = {}
user_last_bonus_check = {}

# ПЕРЕВОД БАЛАНСА
def process_payment_command(message):
    text = message.text.strip()
    
    if not text.lower().startswith('п '):
        return False
    
    user_id = message.from_user.id
    parts = text.split()
    
    if message.reply_to_message:
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: `п [сумма]` (ответом на сообщение)")
            return True
        
        try:
            amount = int(parts[1])
        except:
            bot.reply_to(message, "❌ Сумма должна быть числом")
            return True
        
        if amount <= 0:
            bot.reply_to(message, "❌ Сумма должна быть больше 0")
            return True
        
        target_id = message.reply_to_message.from_user.id
        
        if target_id == bot.get_me().id:
            bot.reply_to(message, "❌ Нельзя переводить боту")
            return True
        
        if target_id == user_id:
            bot.reply_to(message, "❌ Нельзя переводить самому себе")
            return True
    
    elif len(parts) == 3:
        try:
            target_id = int(parts[1])
            amount = int(parts[2])
        except:
            bot.reply_to(message, "❌ Формат: `п [ID] [сумма]`")
            return True
        
        if amount <= 0:
            bot.reply_to(message, "❌ Сумма должна быть больше 0")
            return True
    
    else:
        bot.reply_to(message, "❌ Форматы:\n• `п [сумма]` (ответом на сообщение)\n• `п [ID] [сумма]`")
        return True
    
    sender_balance = get_user_balance(user_id)
    if sender_balance < amount:
        bot.reply_to(message, f"❌ Недостаточно средств!\nВаш баланс: {sender_balance} GRAM")
        return True
    
    target_user = get_user(target_id)
    if not target_user:
        bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
        return True
    
    try:
        user_info = bot.get_chat(target_id)
        if not (user_info.username or user_info.first_name):
            bot.reply_to(message, "❌ Нельзя переводить ботам")
            return True
    except:
        pass
    
    update_balance(user_id, -amount)
    update_balance(target_id, amount)
    add_transaction(user_id, target_id, amount, "payment")
    
    sender_name = message.from_user.first_name or f"ID: {user_id}"
    target_name = target_user[3] or target_user[2] or f"ID: {target_id}"
    
    bot.reply_to(message, f"""
✅ *ПЕРЕВОД ВЫПОЛНЕН*

👤 Отправитель: *{sender_name}*
👤 Получатель: *{target_name}*
💰 Сумма: *{amount}* GRAM
💳 Ваш новый баланс: *{get_user_balance(user_id)}* GRAM
""", parse_mode='Markdown')
    
    try:
        bot.send_message(target_id, f"""
💰 *ВАМ ПЕРЕВЕЛИ СРЕДСТВА*

👤 Отправитель: *{sender_name}*
💰 Сумма: *{amount}* GRAM
💳 Ваш новый баланс: *{get_user_balance(target_id)}* GRAM
""", parse_mode='Markdown')
    except:
        pass
    
    return True

# БАЛАНС И БОНУС
@bot.message_handler(func=lambda m: m.text.lower() in ['б', 'баланс'])
def show_balance(message):
    user_id = message.from_user.id
    balance = get_user_balance(user_id)
    
    update_user_info(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    last_bonus = get_last_bonus(user_id)
    show_bonus_button = False
    
    if last_bonus:
        last_time = datetime.strptime(last_bonus, '%Y-%m-%d %H:%M:%S')
        hours_passed = (datetime.now() - last_time).total_seconds() / 3600
        show_bonus_button = hours_passed >= 24
    else:
        show_bonus_button = True
    
    if show_bonus_button and user_id not in user_last_bonus_check:
        user_last_bonus_check[user_id] = True
        keyboard = [[InlineKeyboardButton("🎁 Бонус", callback_data="daily_bonus")]]
        markup = InlineKeyboardMarkup(keyboard)
    else:
        markup = None
    
    balance_text = f"""
💰 *ВАШ БАЛАНС*

👤 ID: `{user_id}`
💳 Баланс: *{balance}* GRAM
"""
    bot.reply_to(message, balance_text, parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(func=lambda m: m.text.lower() == 'бонус')
def bonus_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user:
        bot.reply_to(message, "❌ Ошибка")
        return
    
    last_bonus = get_last_bonus(user_id)
    now = datetime.now()
    
    if last_bonus:
        last_time = datetime.strptime(last_bonus, '%Y-%m-%d %H:%M:%S')
        hours_passed = (now - last_time).total_seconds() / 3600
        
        if hours_passed < 24:
            next_bonus = last_time + timedelta(hours=24)
            wait_time = next_bonus - now
            hours_left = int(wait_time.total_seconds() // 3600)
            minutes_left = int((wait_time.total_seconds() % 3600) // 60)
            
            bot.reply_to(message, f"⏳ Следующий бонус через {hours_left}ч {minutes_left}мин")
            return
    
    new_balance = update_balance(user_id, DAILY_BONUS)
    update_last_bonus(user_id)
    add_transaction(0, user_id, DAILY_BONUS, "daily_bonus")
    
    if user_id in user_last_bonus_check:
        del user_last_bonus_check[user_id]
    
    bot.reply_to(message, f"""
🎁 *БОНУС 2500 GRAM ПОЛУЧЕН!*

💰 +{DAILY_BONUS} GRAM
💳 Новый баланс: {new_balance} GRAM

Следующий бонус через 24 часа!
""", parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "daily_bonus")
def daily_bonus_callback(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    
    last_bonus = get_last_bonus(user_id)
    now = datetime.now()
    
    if last_bonus:
        last_time = datetime.strptime(last_bonus, '%Y-%m-%d %H:%M:%S')
        hours_passed = (now - last_time).total_seconds() / 3600
        
        if hours_passed < 24:
            next_bonus = last_time + timedelta(hours=24)
            wait_time = next_bonus - now
            hours_left = int(wait_time.total_seconds() // 3600)
            minutes_left = int((wait_time.total_seconds() % 3600) // 60)
            
            bot.answer_callback_query(call.id, f"⏳ Бонус через {hours_left}ч {minutes_left}мин")
            return
    
    new_balance = update_balance(user_id, DAILY_BONUS)
    update_last_bonus(user_id)
    add_transaction(0, user_id, DAILY_BONUS, "daily_bonus")
    
    if user_id in user_last_bonus_check:
        del user_last_bonus_check[user_id]
    
    bot.answer_callback_query(call.id, "🎁 Бонус получен!")
    bot.edit_message_text(
        f"""
🎁 *БОНУС 2500 GRAM ПОЛУЧЕН!*

💰 +{DAILY_BONUS} GRAM
💳 Новый баланс: {new_balance} GRAM

Следующий бонус через 24 часа!
""",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode='Markdown'
    )

# РУЛЕТКА
def parse_roulette_bet(bet_value):
    bet_value = bet_value.lower().strip()
    
    if bet_value in ['к', 'красное', 'red']:
        return 'color', 'red'
    elif bet_value in ['ч', 'черное', 'black']:
        return 'color', 'black'
    elif bet_value in ['чет', 'четное', 'even']:
        return 'evenodd', 'even'
    elif bet_value in ['нечет', 'нечетное', 'odd']:
        return 'evenodd', 'odd'
    elif '-' in bet_value:
        try:
            parts = bet_value.split('-')
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            if 0 <= start <= 36 and 0 <= end <= 36 and start < end:
                return 'range', f"{start}-{end}"
        except:
            pass
    else:
        try:
            num = int(bet_value)
            if 0 <= num <= 36:
                return 'number', str(num)
        except:
            pass
    
    return None, None

def add_roulette_bet(user_id, amount, bet_type, bet_value):
    if user_id not in roulette_bets:
        roulette_bets[user_id] = []
    
    roulette_bets[user_id].append({
        'amount': amount,
        'type': bet_type,
        'value': bet_value,
        'time': time.time()
    })

def get_user_roulette_bets(user_id):
    return roulette_bets.get(user_id, [])

def clear_user_roulette_bets(user_id):
    if user_id in roulette_bets:
        del roulette_bets[user_id]

@bot.message_handler(func=lambda m: True)
def handle_all_messages(message):
    text = message.text.strip()
    user_id = message.from_user.id
    
    if process_payment_command(message):
        return
    
    if text.lower() in ['ставки']:
        bets = get_user_roulette_bets(user_id)
        
        if not bets:
            bot.reply_to(message, "📭 У вас нет активных ставок")
            return
        
        total_amount = sum(bet['amount'] for bet in bets)
        
        bets_text = "📋 *ВАШИ СТАВКИ:*\n\n"
        for i, bet in enumerate(bets, 1):
            bet_type = bet['type']
            bet_value = bet['value']
            amount = bet['amount']
            
            if bet_type == 'number':
                bet_desc = f"Число {bet_value}"
            elif bet_type == 'color':
                color = "🔴 Красное" if bet_value == 'red' else "⚫ Черное"
                bet_desc = color
            elif bet_type == 'evenodd':
                parity = "Четное" if bet_value == 'even' else "Нечетное"
                bet_desc = parity
            elif bet_type == 'range':
                bet_desc = f"Диапазон {bet_value}"
            else:
                bet_desc = bet_value
            
            bets_text += f"{i}. {bet_desc} — *{amount}* GRAM\n"
        
        bets_text += f"\n💰 *Общая сумма:* {total_amount} GRAM"
        bot.reply_to(message, bets_text, parse_mode='Markdown')
        return
    
    if text.lower() == 'отмена':
        bets = get_user_roulette_bets(user_id)
        
        if not bets:
            bot.reply_to(message, "📭 Нет ставок для отмены")
            return
        
        total_amount = sum(bet['amount'] for bet in bets)
        
        update_balance(user_id, total_amount)
        clear_user_roulette_bets(user_id)
        
        bot.reply_to(message, f"""
❌ *СТАВКИ ОТМЕНЕНЫ*

💰 Возвращено: *{total_amount}* GRAM
💳 Новый баланс: *{get_user_balance(user_id)}* GRAM
""", parse_mode='Markdown')
        return
    
    if text.lower() == 'го':
        bets = get_user_roulette_bets(user_id)
        
        if not bets:
            bot.reply_to(message, "❌ Нет активных ставок")
            return
        
        if user_id in roulette_timers:
            time_left = roulette_timers[user_id] - time.time()
            if time_left > 0:
                bot.reply_to(message, f"⏳ Раунд можно начать через {int(time_left)} сек.")
                return
        
        roulette_number = random.randint(0, 36)
        is_red = roulette_number in [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
        is_black = roulette_number in [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35]
        is_even = roulette_number % 2 == 0 and roulette_number != 0
        
        total_win = 0
        winning_bets = []
        
        for bet in bets:
            amount = bet['amount']
            bet_type = bet['type']
            bet_value = bet['value']
            win = False
            multiplier = 0
            
            if bet_type == 'number':
                if int(bet_value) == roulette_number:
                    win = True
                    multiplier = 36
            elif bet_type == 'color':
                if bet_value == 'red' and is_red:
                    win = True
                    multiplier = 2
                elif bet_value == 'black' and is_black:
                    win = True
                    multiplier = 2
            elif bet_type == 'evenodd':
                if bet_value == 'even' and is_even:
                    win = True
                    multiplier = 2
                elif bet_value == 'odd' and not is_even and roulette_number != 0:
                    win = True
                    multiplier = 2
            elif bet_type == 'range':
                start, end = map(int, bet_value.split('-'))
                if start <= roulette_number <= end:
                    win = True
                    range_size = end - start + 1
                    multiplier = 36 / range_size
            
            if win:
                win_amount = int(amount * multiplier)
                total_win += win_amount
                winning_bets.append((bet, win_amount))
        
        if total_win > 0:
            update_balance(user_id, total_win)
            add_transaction(0, user_id, total_win, "roulette_win")
        
        total_bet = sum(bet['amount'] for bet in bets)
        color = "🟢 ZERO" if roulette_number == 0 else "🔴 RED" if is_red else "⚫ BLACK"
        
        result_text = f"""
🎰 *РУЛЕТКА РАУНД*

🎯 Выпало: *{roulette_number}* ({color})
💰 Всего ставок: *{len(bets)}*
💸 Общая ставка: *{total_bet}* GRAM
"""
        
        if total_win > 0:
            result_text += f"""
🏆 *ВЫИГРЫШ!*
💰 Выигрыш: *{total_win}* GRAM
💎 Прибыль: *{total_win - total_bet}* GRAM
"""
        else:
            result_text += "\n💸 *ПРОИГРЫШ*"
        
        result_text += f"\n💳 Новый баланс: *{get_user_balance(user_id)}* GRAM"
        
        clear_user_roulette_bets(user_id)
        roulette_timers[user_id] = time.time() + ROULETTE_COOLDOWN
        
        bot.reply_to(message, result_text, parse_mode='Markdown')
        return
    
    if text.lower().startswith('мины '):
        parts = text.split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: `мины [сумма]`\nПример: `мины 50`")
            return
        
        try:
            amount = int(parts[1])
        except:
            bot.reply_to(message, "❌ Сумма должна быть числом")
            return
        
        if amount < MINES_MIN_BET:
            bot.reply_to(message, f"❌ Минимальная ставка: {MINES_MIN_BET} GRAM")
            return
        
        balance = get_user_balance(user_id)
        
        if balance < amount:
            bot.reply_to(message, f"❌ Недостаточно средств!\nВаш баланс: {balance} GRAM")
            return
        
        grid_size = GRID_SIZE
        total_cells = grid_size * grid_size
        
        mine_positions = random.sample(range(total_cells), MINES_COUNT)
        game_id = str(uuid.uuid4())[:8]
        
        mines_games[game_id] = {
            'user_id': user_id,
            'bet_amount': amount,
            'mines_count': MINES_COUNT,
            'grid_size': grid_size,
            'mine_positions': mine_positions,
            'revealed_cells': [],
            'current_payout': amount,
            'created_at': datetime.now()
        }
        
        update_balance(user_id, -amount)
        add_transaction(user_id, 0, amount, "mines_bet")
        
        keyboard = []
        for row in range(grid_size):
            row_buttons = []
            for col in range(grid_size):
                cell_index = row * grid_size + col
                row_buttons.append(InlineKeyboardButton("🟦", callback_data=f"mines_{game_id}_{cell_index}"))
            keyboard.append(row_buttons)
        
        keyboard.append([
            InlineKeyboardButton("💰 Забрать", callback_data=f"mines_{game_id}_cashout"),
            InlineKeyboardButton("❌ Закончить", callback_data=f"mines_{game_id}_end")
        ])
        
        markup = InlineKeyboardMarkup(keyboard)
        
        game_text = f"""
💣 *МИНЫ | ИГРА НАЧАТА*

💰 Ставка: *{amount}* GRAM
💣 Мин на поле: *{MINES_COUNT}*
🎯 Размер поля: *{grid_size}×{grid_size}*
🏆 Множитель: *1.00x*

💎 Выигрыш: *{amount}* GRAM

⚠️ Выберите клетку.
"""
        bot.reply_to(message, game_text, parse_mode='Markdown', reply_markup=markup)
        return
    
    parts = text.split()
    if len(parts) >= 2:
        try:
            amount = int(parts[0])
        except:
            return
        
        if amount < MIN_BET:
            bot.reply_to(message, f"❌ Минимальная ставка: {MIN_BET} GRAM")
            return
        
        balance = get_user_balance(user_id)
        
        total_bet = amount * (len(parts) - 1)
        
        if balance < total_bet:
            bot.reply_to(message, f"❌ Недостаточно средств!\nНужно: {total_bet} GRAM\nВаш баланс: {balance} GRAM")
            return
        
        bets_added = 0
        for bet_value in parts[1:]:
            bet_type, parsed_value = parse_roulette_bet(bet_value)
            
            if bet_type and parsed_value:
                add_roulette_bet(user_id, amount, bet_type, parsed_value)
                bets_added += 1
        
        if bets_added > 0:
            update_balance(user_id, -total_bet)
            
            bot.reply_to(message, f"""
✅ *СТАВКА ПРИНЯТА*

💰 Общая сумма: *{total_bet}* GRAM ({amount} × {bets_added})
🎯 Количество ставок: *{bets_added}*
💳 Новый баланс: *{get_user_balance(user_id)}* GRAM

📋 Используй команды:
`ставки` - мои ставки
`го` - начать раунд
`отмена` - отменить все ставки
""", parse_mode='Markdown')
            
            if user_id not in roulette_timers or roulette_timers[user_id] < time.time():
                roulette_timers[user_id] = time.time() + ROULETTE_COOLDOWN
        else:
            bot.reply_to(message, "❌ Некорректные ставки\nДоступно: числа 0-36, к/ч, чет/нечет, диапазон (1-18)")
        return

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    
    if data == "daily_bonus":
        daily_bonus_callback(call)
        return
    
    if data.startswith('mines_'):
        parts = data.split('_', 2)
        
        if len(parts) != 3:
            bot.answer_callback_query(call.id, "❌ Ошибка")
            return
        
        prefix, game_id, action = parts
        
        if game_id not in mines_games:
            bot.answer_callback_query(call.id, "❌ Игра завершена")
            return
        
        game = mines_games[game_id]
        
        if game['user_id'] != call.from_user.id:
            bot.answer_callback_query(call.id, "❌ Это не ваша игра!")
            return
        
        if action == 'cashout':
            win_amount = game['current_payout'] - game['bet_amount']
            update_balance(call.from_user.id, game['current_payout'])
            add_transaction(0, call.from_user.id, win_amount, "mines_win")
            
            multiplier = game['current_payout'] / game['bet_amount']
            
            bot.edit_message_text(
                f"""
💰 *МИНЫ | ВЫВОД*

🏆 Вы успешно вывели средства!
💰 Ставка: *{game['bet_amount']}* GRAM
📈 Множитель: *{multiplier:.2f}x*
🎯 Выигрыш: *{game['current_payout']}* GRAM
💎 Прибыль: *{win_amount}* GRAM

💳 Баланс: *{get_user_balance(call.from_user.id)}* GRAM
""",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='Markdown'
            )
            
            del mines_games[game_id]
            
        elif action == 'end':
            del mines_games[game_id]
            bot.edit_message_text(
                "❌ Игра отменена",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
            
        else:
            try:
                cell_index = int(action)
                
                if cell_index in game['revealed_cells']:
                    bot.answer_callback_query(call.id, "❌ Уже открыта")
                    return
                
                if cell_index in game['mine_positions']:
                    keyboard = []
                    grid_size = game['grid_size']
                    
                    for row in range(grid_size):
                        row_buttons = []
                        for col in range(grid_size):
                            cell_idx = row * grid_size + col
                            if cell_idx in game['mine_positions']:
                                row_buttons.append(InlineKeyboardButton("💣", callback_data="noop"))
                            elif cell_idx == cell_index:
                                row_buttons.append(InlineKeyboardButton("💥", callback_data="noop"))
                            elif cell_idx in game['revealed_cells']:
                                row_buttons.append(InlineKeyboardButton("💎", callback_data="noop"))
                            else:
                                row_buttons.append(InlineKeyboardButton("🟦", callback_data="noop"))
                        keyboard.append(row_buttons)
                    
                    markup = InlineKeyboardMarkup(keyboard)
                    
                    bot.edit_message_text(
                        f"""
💣 *МИНЫ | ПРОИГРЫШ*

💰 Ставка: *{game['bet_amount']}* GRAM
💣 Мин на поле: *{game['mines_count']}*
💸 Потеряно: *{game['bet_amount']}* GRAM

😔 Вы наткнулись на мину!
""",
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='Markdown',
                        reply_markup=markup
                    )
                    
                    del mines_games[game_id]
                    
                else:
                    game['revealed_cells'].append(cell_index)
                    opened_safe = len(game['revealed_cells'])
                    multiplier = get_mines_multiplier(opened_safe)
                    new_payout = int(game['bet_amount'] * multiplier)
                    game['current_payout'] = new_payout
                    
                    keyboard = []
                    grid_size = game['grid_size']
                    
                    for row in range(grid_size):
                        row_buttons = []
                        for col in range(grid_size):
                            cell_idx = row * grid_size + col
                            if cell_idx in game['revealed_cells']:
                                row_buttons.append(InlineKeyboardButton("💎", callback_data="noop"))
                            else:
                                row_buttons.append(InlineKeyboardButton("🟦", callback_data=f"mines_{game_id}_{cell_idx}"))
                        keyboard.append(row_buttons)
                    
                    keyboard.append([
                        InlineKeyboardButton("💰 Забрать", callback_data=f"mines_{game_id}_cashout"),
                        InlineKeyboardButton("❌ Закончить", callback_data=f"mines_{game_id}_end")
                    ])
                    
                    markup = InlineKeyboardMarkup(keyboard)
                    
                    game_text = f"""
💣 *МИНЫ | ИГРА*

💰 Ставка: *{game['bet_amount']}* GRAM
💣 Мин на поле: *{game['mines_count']}*
🎯 Открыто клеток: *{opened_safe}*
🏆 Множитель: *{multiplier:.2f}x*

💎 Выигрыш: *{new_payout}* GRAM
💎 Прибыль: *{new_payout - game['bet_amount']}* GRAM

⚠️ Выберите следующую клетку.
"""
                    bot.edit_message_text(
                        game_text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='Markdown',
                        reply_markup=markup
                    )
                    
            except:
                bot.answer_callback_query(call.id, "❌ Ошибка")
    else:
        bot.answer_callback_query(call.id)

def get_mines_multiplier(opened_cells):
    multipliers = [1.00, 1.28, 1.65, 2.10, 2.65, 3.30, 4.05, 5.00, 6.15, 7.50,
                   9.15, 11.10, 13.45, 16.30, 19.75, 23.90, 29.00, 35.20, 42.70, 51.80, 62.90]
    return multipliers[opened_cells] if opened_cells < len(multipliers) else multipliers[-1]

def main():
    logger.info("🚀 Бот запущен!")
    bot.polling(none_stop=True)

if __name__ == "__main__":
    main()
