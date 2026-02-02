import telebot
from telebot import types
import sqlite3
import requests
import time
import threading
from datetime import datetime
import os

# ================= НАСТРОЙКИ =================
# Вставь сюда свой токен прямо в кавычки, если не используешь переменные окружения
BOT_TOKEN = 'ВАШ_ТОКЕН_ЗДЕСЬ'

# Если токена нет в коде, пробуем взять из системы (для продвинутых)
if BOT_TOKEN == 'ВАШ_ТОКЕН_ЗДЕСЬ':
    env_token = os.getenv('BOT_TOKEN')
    if env_token:
        BOT_TOKEN = env_token

bot = telebot.TeleBot(BOT_TOKEN)

# ================= БАЗА ДАННЫХ =================
def db_query(query, args=(), fetch=False, commit=True):
    with sqlite3.connect('crypto_bot_v2.db', check_same_thread=False) as conn:
        c = conn.cursor()
        res = c.execute(query, args)
        if fetch:
            return res.fetchall()
        if commit:
            conn.commit()
        return c.lastrowid

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                coin_id TEXT,
                coin_symbol TEXT,
                days_interval INTEGER,
                notify_time TEXT,
                last_check_date TEXT,
                last_price REAL
            )''')

# ================= API COINGECKO =================

# Словарь для точного определения популярных монет
# Это решает проблему с XRP (ripple), TON (the-open-network) и другими
MANUAL_MAPPING = {
    'xrp': 'ripple',
    'btc': 'bitcoin',
    'eth': 'ethereum',
    'ton': 'the-open-network',
    'sol': 'solana',
    'bnb': 'binancecoin',
    'doge': 'dogecoin',
    'ada': 'cardano',
    'trx': 'tron',
    'ltc': 'litecoin',
    'dot': 'polkadot',
    'avax': 'avalanche-2',
    'matic': 'matic-network',
    'shib': 'shiba-inu',
    'usdt': 'tether'
}

def resolve_coins(text):
    """Превращает 'btc, xrp' в список ID и цен"""
    found_coins = []
    # Разбиваем текст по запятым и убираем пробелы
    symbols = [s.strip().lower() for s in text.split(',')]
    
    for sym in symbols:
        api_id = None
        symbol = sym.upper()

        # 1. Сначала проверяем наш ручной список (самый надежный способ)
        if sym in MANUAL_MAPPING:
            api_id = MANUAL_MAPPING[sym]
        
        # 2. Если в списке нет, ищем через поиск API
        if not api_id:
            try:
                search_url = f"https://api.coingecko.com/api/v3/search?query={sym}"
                search_res = requests.get(search_url, timeout=5).json()
                
                if search_res.get('coins'):
                    # Берем первый результат
                    top_result = search_res['coins'][0]
                    api_id = top_result['id']
                    symbol = top_result['symbol']
            except Exception as e:
                print(f"Ошибка поиска {sym}: {e}")

        # 3. Если ID найден (в словаре или поиске), узнаем цену
        if api_id:
            try:
                price_url = f"https://api.coingecko.com/api/v3/simple/price?ids={api_id}&vs_currencies=usd"
                price_res = requests.get(price_url, timeout=5).json()
                
                if api_id in price_res:
                    found_coins.append({
                        'id': api_id,
                        'symbol': symbol.upper(),
                        'price': price_res[api_id]['usd']
                    })
            except Exception as e:
                print(f"Ошибка получения цены для {api_id}: {e}")
            
    return found_coins

def get_prices_batch(coin_ids):
    """Получает цены для списка ID одним запросом"""
    if not coin_ids:
        return {}
    try:
        # Убираем дубликаты ID для запроса
        unique_ids = list(set(coin_ids))
        ids_str = ",".join(unique_ids)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=usd"
        return requests.get(url, timeout=10).json()
    except Exception as e:
        print(f"Ошибка batch update: {e}")
        return {}

# ================= КЛАВИАТУРЫ =================
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton("➕ Добавить")
    btn2 = types.KeyboardButton("📋 Мои подписки")
    btn3 = types.KeyboardButton("🗑 Удалить")
    markup.add(btn1, btn2, btn3)
    return markup

# ================= ЛОГИКА БОТА =================
user_states = {}

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "Привет! Я слежу за курсом крипты.\nИспользуй меню внизу.", reply_markup=main_menu())

# --- Обработка кнопок меню ---
@bot.message_handler(func=lambda m: m.text == "➕ Добавить")
def add_start(m):
    msg = bot.send_message(m.chat.id, "Введите монеты через запятую:\nНапример: <code>BTC, XRP, TON</code>", parse_mode='HTML')
    bot.register_next_step_handler(msg, step_coins)

@bot.message_handler(func=lambda m: m.text == "📋 Мои подписки")
def list_alerts(m):
    rows = db_query("SELECT coin_symbol, days_interval, notify_time, last_price FROM alerts WHERE user_id=?", (m.chat.id,), fetch=True)
    if not rows:
        bot.send_message(m.chat.id, "Список пуст.", reply_markup=main_menu())
        return
    
    text = "<b>Ваши подписки:</b>\n\n"
    for r in rows:
        text += f"🔹 <b>{r[0]}</b> | Раз в {r[1]} дн. в {r[2]} | База: ${r[3]}\n"
    bot.send_message(m.chat.id, text, parse_mode='HTML', reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "🗑 Удалить")
def delete_menu(m):
    rows = db_query("SELECT id, coin_symbol, notify_time FROM alerts WHERE user_id=?", (m.chat.id,), fetch=True)
    if not rows:
        bot.send_message(m.chat.id, "Удалять нечего.", reply_markup=main_menu())
        return
    
    markup = types.InlineKeyboardMarkup()
    for r in rows:
        markup.add(types.InlineKeyboardButton(f"❌ {r[1]} ({r[2]})", callback_data=f"del_{r[0]}"))
    
    bot.send_message(m.chat.id, "Нажми на то, что хочешь удалить:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def callback_delete(call):
    alert_id = call.data.split('_')[1]
    db_query("DELETE FROM alerts WHERE id=?", (alert_id,))
    bot.answer_callback_query(call.id, "Удалено!")
    # Обновляем список кнопок
    delete_menu(call.message)

# --- Шаги добавления ---
def step_coins(m):
    bot.send_chat_action(m.chat.id, 'typing')
    coins = resolve_coins(m.text)
    
    if not coins:
        bot.send_message(m.chat.id, "Не удалось найти указанные монеты. Попробуй проверить написание.", reply_markup=main_menu())
        return

    user_states[m.chat.id] = {'coins': coins}
    names = ", ".join([c['symbol'] for c in coins])
    
    msg = bot.send_message(m.chat.id, f"Нашел: {names}\n\nКак часто присылать отчет? (введите число ДНЕЙ, например 1):")
    bot.register_next_step_handler(msg, step_interval)

def step_interval(m):
    try:
        days = int(m.text)
        user_states[m.chat.id]['days'] = days
        msg = bot.send_message(m.chat.id, "В какое время присылать? (МСК)\nФормат ЧЧ:ММ (например 09:00):")
        bot.register_next_step_handler(msg, step_time)
    except:
        bot.send_message(m.chat.id, "Нужно ввести целое число. Попробуй добавить заново.", reply_markup=main_menu())

def step_time(m):
    try:
        t_str = m.text.strip()
        time.strptime(t_str, '%H:%M') # Валидация
        
        data = user_states[m.chat.id]
        today = datetime.now().strftime("%Y-%m-%d")
        
        added_count = 0
        for coin in data['coins']:
            db_query("INSERT INTO alerts (user_id, coin_id, coin_symbol, days_interval, notify_time, last_check_date, last_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (m.chat.id, coin['id'], coin['symbol'], data['days'], t_str, today, coin['price']))
            added_count += 1
            
        bot.send_message(m.chat.id, f"✅ Добавлено монет: {added_count}. Жди уведомлений в {t_str}.", reply_markup=main_menu())
    except Exception as e:
        bot.send_message(m.chat.id, f"Ошибка времени или базы данных. {e}", reply_markup=main_menu())

# ================= ПЛАНИРОВЩИК (СВОДНЫЕ ОТЧЕТЫ) =================
def background_worker():
    print("Планировщик запущен")
    while True:
        try:
            now_time = datetime.now().strftime("%H:%M")
            today_date = datetime.now().strftime("%Y-%m-%d")
            
            # Получаем все подписки
            all_alerts = db_query("SELECT * FROM alerts", fetch=True)
            
            tasks = {}
            
            for row in all_alerts:
                # row: 0=id, 1=uid, 2=cid, 3=sym, 4=int, 5=time, 6=date, 7=price
                if row[5] == now_time: # Если время совпало
                    last_dt = datetime.strptime(row[6], "%Y-%m-%d")
                    delta = (datetime.now() - last_dt).days
                    
                    if delta >= row[4]: # Если интервал прошел
                        if row[1] not in tasks:
                            tasks[row[1]] = []
                        tasks[row[1]].append(row)
            
            # Обрабатываем каждого пользователя
            for uid, user_alerts in tasks.items():
                message_lines = []
                ids_to_check = [a[2] for a in user_alerts]
                
                # Запрашиваем актуальные цены разом
                current_prices = get_prices_batch(ids_to_check)
                
                if not current_prices:
                    continue
                
                message_header = f"📊 <b>Отчет за {today_date}</b>\n\n"
                has_updates = False
                
                for alert in user_alerts:
                    aid, _, coin_id, symbol, _, _, _, old_price = alert
                    
                    if coin_id in current_prices:
                        new_price = current_prices[coin_id]['usd']
                        
                        # Расчет процента
                        if old_price == 0: change_pct = 0
                        else: change_pct = ((new_price - old_price) / old_price) * 100
                        
                        # Эмодзи
                        if change_pct > 0:
                            emoji = "🟢 ⬆️"
                        elif change_pct < 0:
                            emoji = "🔴 ⬇️"
                        else:
                            emoji = "⚪️"
                            
                        line = (f"<b>{symbol}</b>: ${new_price}\n"
                                f"{emoji} {change_pct:+.2f}%\n")
                        message_lines.append(line)
                        
                        # Обновляем БД: ставим сегодняшнюю дату и новую цену
                        db_query("UPDATE alerts SET last_check_date=?, last_price=? WHERE id=?", 
                                 (today_date, new_price, aid))
                        has_updates = True
                
                if has_updates:
                    full_msg = message_header + "\n".join(message_lines)
                    try:
                        bot.send_message(uid, full_msg, parse_mode='HTML')
                    except Exception as e:
                        print(f"Не удалось отправить пользователю {uid}: {e}")

            time.sleep(60) # Ждем минуту
            
        except Exception as e:
            print(f"Ошибка цикла: {e}")
            time.sleep(60)

# ================= ЗАПУСК =================
if __name__ == '__main__':
    init_db()
    
    # Запуск планировщика в фоне
    t = threading.Thread(target=background_worker)
    t.start()
    
    print("Бот запущен v2.1 (Fix XRP)...")
    bot.infinity_polling()
