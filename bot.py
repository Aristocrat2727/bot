# ... (предыдущий код остается без изменений до функций админ-команд) ...

# ========== АДМИН КОМАНДЫ ==========
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
        # Получаем статистику из БД
        c = conn.cursor()
        
        # Общее количество пользователей
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        # Пользователи с балансом > 0
        c.execute("SELECT COUNT(*) FROM users WHERE balance > 0")
        users_with_balance = c.fetchone()[0]
        
        # Общая сумма балансов
        c.execute("SELECT SUM(balance) FROM users")
        total_balance = c.fetchone()[0] or 0
        
        # Количество транзакций
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
        
        # Добавляем в список админов
        ADMINS.append(new_admin_id)
        
        # Обновляем переменную окружения (в памяти, для Railway нужно отдельно)
        admin_ids_str = ','.join(str(admin) for admin in ADMINS)
        
        # Сохраняем информацию о пользователе
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
        
        # Удаляем из списка админов
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
        
        # Проверяем, существует ли пользователь
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        # Выдаем баланс
        new_balance = update_balance(target_id, amount)
        add_transaction(0, target_id, amount, "admin_give")
        
        # Получаем имя пользователя
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
        
        # Проверяем, существует ли пользователь
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        current_balance = get_user_balance(target_id)
        
        if current_balance < amount:
            bot.reply_to(message, f"❌ У пользователя только {current_balance} GRAM, нельзя забрать {amount} GRAM")
            return
        
        # Забираем баланс
        new_balance = update_balance(target_id, -amount)
        add_transaction(target_id, 0, amount, "admin_take")
        
        # Получаем имя пользователя
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
        
        # Проверяем, существует ли пользователь
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        # Получаем текущий баланс
        current_balance = get_user_balance(target_id)
        
        # Рассчитываем разницу
        difference = amount - current_balance
        
        # Устанавливаем новый баланс
        if difference != 0:
            update_balance(target_id, difference)
            
            if difference > 0:
                add_transaction(0, target_id, difference, "admin_set_add")
            else:
                add_transaction(target_id, 0, -difference, "admin_set_sub")
        
        # Получаем имя пользователя
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
        
        # Ищем пользователя
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ Пользователь `{target_id}` не найден")
            return
        
        user_id_db, balance, username, first_name, last_name, last_bonus, created_at = user
        
        # Получаем статистику транзакций
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
        # Получаем текст рассылки
        text = message.text.strip()
        if len(text.split()) < 2:
            bot.reply_to(message, "❌ Формат: `/broadcast [текст рассылки]`")
            return
        
        # Убираем команду из текста
        broadcast_text = text.replace('/broadcast', '', 1).strip()
        
        # Подтверждение
        confirm_text = f"""
📢 *ПОДТВЕРЖДЕНИЕ РАССЫЛКИ*

Текст рассылки:
{broadcast_text}

⚠️ *Внимание:* Рассылка будет отправлена ВСЕМ пользователям бота.

Для подтверждения отправьте: `/confirm_broadcast`
Для отмены: `/cancel_broadcast`
"""
        
        # Сохраняем текст рассылки во временное хранилище
        user_sessions[user_id] = {
            'broadcast_text': broadcast_text,
            'action': 'broadcast'
        }
        
        bot.reply_to(message, confirm_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка broadcast: {e}")
        bot.reply_to(message, "❌ Ошибка подготовки рассылки")

# ... (остальной код остается без изменений, включая запуск бота) ...
