import telebot
import sqlite3
import requests
import time
import threading
from datetime import datetime

# ================= НАСТРОЙКИ =================
BOT_TOKEN = 'ВАШ_ТОКЕН_ЗДЕСЬ'
bot = telebot.TeleBot(BOT_TOKEN)

# ================= БАЗА ДАННЫХ =================
def db_query(query, args=(), fetch=False):
    """Универсальная функция для работы с БД"""
    with sqlite3.connect('my_bot.db') as conn:
        c = conn.cursor()
        res = c.execute(query, args)
        if fetch:
            return res.fetchall()
        conn.commit()

def init_db():
    """Создает таблицу при первом запуске"""
    db_query('''CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                coin TEXT,
                days_interval INTEGER,
                notify_time TEXT,
                last_check_date TEXT,
                last_price REAL
            )''')

# ================= ФУНКЦИИ =================
def get_price(coin_id):
    """Получает цену крипты с CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        r = requests.get(url, timeout=5).json()
        return r[coin_id]['usd']
    except:
        return None

# ================= ФОНОВАЯ ЗАДАЧА (ПРОВЕРКА ВРЕМЕНИ) =================
def background_worker():
    """Бесконечный цикл проверки времени для уведомлений"""
    while True:
        try:
            now_time = datetime.now().strftime("%H:%M")
            today_date = datetime.now().strftime("%Y-%m-%d")
            
            # Берем все подписки
            alerts = db_query("SELECT * FROM alerts", fetch=True)
            
            for row in alerts:
                # row: 0=id, 1=user_id, 2=coin, 3=interval, 4=time, 5=last_date, 6=last_price
                aid, uid, coin, interval, target_time, last_date, old_price = row
                
                # Если время совпадает (проверяем только часы и минуты)
                if target_time == now_time:
                    # Проверяем, сколько дней прошло
                    last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                    delta = (datetime.now() - last_dt).days
                    
                    if delta >= interval:
                        new_price = get_price(coin)
                        if new_price:
                            # Считаем %
                            percent = ((new_price - old_price) / old_price) * 100
                            sign = "+" if percent >= 0 else ""
                            
                            msg = (f"🔔 <b>{coin.upper()}</b>\n"
                                   f"📅 Прошло дней: {delta}\n"
                                   f"💰 Цена: ${new_price}\n"
                                   f"📊 Изменение: <b>{sign}{percent:.2f}%</b>")
                            
                            bot.send_message(uid, msg, parse_mode='HTML')
                            
                            # Обновляем дату и цену в БД
                            db_query("UPDATE alerts SET last_check_date=?, last_price=? WHERE id=?", 
                                     (today_date, new_price, aid))
            
            # Ждем 60 секунд до следующей проверки
            time.sleep(60)
            
        except Exception as e:
            print(f"Ошибка в планировщике: {e}")
            time.sleep(60)

# ================= ОБРАБОТЧИКИ TELEGRAM =================
user_states = {} # Временная память для диалогов

@bot.message_handler(commands=['start'])
def send_welcome(m):
    bot.send_message(m.chat.id, "Привет! \n/add - добавить отслеживание\n/list - список моих подписок\n/delete ID - удалить подписку")

@bot.message_handler(commands=['add'])
def add_start(m):
    msg = bot.send_message(m.chat.id, "Введи ID монеты (например: bitcoin, toncoin, ethereum):")
    bot.register_next_step_handler(msg, step_coin)

def step_coin(m):
    coin = m.text.lower().strip()
    price = get_price(coin)
    if not price:
        bot.send_message(m.chat.id, "Не нашел такую монету. Попробуй снова /add")
        return
    
    user_states[m.chat.id] = {'coin': coin, 'price': price}
    msg = bot.send_message(m.chat.id, f"Цена: ${price}. Раз в сколько ДНЕЙ уведомлять? (например 1):")
    bot.register_next_step_handler(msg, step_interval)

def step_interval(m):
    try:
        user_states[m.chat.id]['days'] = int(m.text)
        msg = bot.send_message(m.chat.id, "В какое время уведомлять? (МСК/Серверное)\nФормат ЧЧ:ММ (например 09:00):")
        bot.register_next_step_handler(msg, step_time)
    except:
        bot.send_message(m.chat.id, "Нужно число. Заново /add")

def step_time(m):
    try:
        t_str = m.text.strip()
        time.strptime(t_str, '%H:%M') # Проверка формата
        data = user_states[m.chat.id]
        
        # Сохраняем в БД
        db_query("INSERT INTO alerts (user_id, coin, days_interval, notify_time, last_check_date, last_price) VALUES (?, ?, ?, ?, ?, ?)",
                 (m.chat.id, data['coin'], data['days'], t_str, datetime.now().strftime("%Y-%m-%d"), data['price']))
        
        bot.send_message(m.chat.id, f"✅ Готово! Слежу за {data['coin']} раз в {data['days']} дн. в {t_str}")
    except:
        bot.send_message(m.chat.id, "Ошибка времени. Формат 14:30. Заново /add")

@bot.message_handler(commands=['list'])
def list_alerts(m):
    rows = db_query("SELECT id, coin, days_interval, notify_time, last_price FROM alerts WHERE user_id=?", (m.chat.id,), fetch=True)
    if not rows:
        bot.send_message(m.chat.id, "Пусто.")
        return
    text = "\n".join([f"ID:{r[0]} | {r[1]} | Раз в {r[2]} дн. в {r[3]} | База: ${r[4]}" for r in rows])
    bot.send_message(m.chat.id, f"Ваши подписки:\n{text}\n\nУдалить: /delete ID")

@bot.message_handler(commands=['delete'])
def delete_alert(m):
    try:
        aid = m.text.split()[1]
        db_query("DELETE FROM alerts WHERE id=? AND user_id=?", (aid, m.chat.id))
        bot.send_message(m.chat.id, "Удалено.")
    except:
        bot.send_message(m.chat.id, "Пиши: /delete ID")

# ================= ЗАПУСК =================
if __name__ == '__main__':
    init_db()
    # Запускаем проверку времени в отдельном потоке
    t = threading.Thread(target=background_worker)
    t.start()
    
    print("Бот работает...")
    bot.infinity_polling()
