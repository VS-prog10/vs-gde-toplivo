# ============================================================
#  БОТ «VS Где Топливо» — v1.13
#  Мотивирующие тексты вместо пустых колонок
# ============================================================

import os
import time
import telebot
from telebot import types
import config
import database
import threading
import backup
import re
import sqlite3
import json
from math import radians, sin, cos, sqrt, atan2
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer


bot = telebot.TeleBot(config.BOT_TOKEN)
database.init_db()


temp_stations = {}


# ============================================================
#  ГЛОБАЛЬНЫЕ: АКТИВНОСТЬ И КЭШ
# ============================================================

_last_activity = {"ts": time.time()}
_cache = {
    "stations": {"data": None, "ts": 0},
    "fuel_grouped": {"data": None, "ts": 0},
}
CACHE_TTL = 60


def touch_activity():
    _last_activity["ts"] = time.time()


def get_cached_stations():
    now = time.time()
    if (_cache["stations"]["data"] is None
            or now - _cache["stations"]["ts"] > CACHE_TTL):
        _cache["stations"]["data"] = database.get_all_stations()
        _cache["stations"]["ts"] = now
    return _cache["stations"]["data"]


def get_cached_fuel_grouped():
    now = time.time()
    if (_cache["fuel_grouped"]["data"] is None
            or now - _cache["fuel_grouped"]["ts"] > CACHE_TTL):
        _cache["fuel_grouped"]["data"] = database.get_all_fuel_grouped()
        _cache["fuel_grouped"]["ts"] = now
    return _cache["fuel_grouped"]["data"]


def invalidate_cache():
    _cache["stations"]["ts"] = 0
    _cache["fuel_grouped"]["ts"] = 0


# ============================================================
#  БЕЗОПАСНАЯ ОТПРАВКА
# ============================================================

def safe_send_message(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print(f"⚠️ Не удалось отправить {chat_id}: {e}")
        return None


def safe_edit_message_text(text, chat_id, message_id, **kwargs):
    try:
        return bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, **kwargs)
    except Exception as e:
        print(f"⚠️ Не удалось редактировать: {e}")
        return None


def safe_answer_callback(call_id, **kwargs):
    try:
        bot.answer_callback_query(call_id, **kwargs)
    except:
        pass


# ============================================================
#  ПОСТОЯННАЯ REPLY-КЛАВИАТУРА
# ============================================================

def get_persistent_keyboard():
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True
    )
    btn_start = types.KeyboardButton("🚀 Старт")
    btn_location = types.KeyboardButton("📍 Отправить локацию", request_location=True)
    markup.row(btn_start, btn_location)
    return markup


# ============================================================
#  HEALTH-CHECK
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        silent = time.time() - _last_activity["ts"]
        if silent > 300:
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"HUNG")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        print(f"💚 Health-check на порту {port}")
        server.serve_forever()
    except Exception as e:
        print(f"⚠️ Health-check не запустился: {e}")


health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()


# ============================================================
#  WATCHDOG
# ============================================================

def run_watchdog():
    print("🐕 Watchdog запущен. Порог: 5 минут.")
    while True:
        try:
            time.sleep(60)
            silent = time.time() - _last_activity["ts"]
            if silent > 300:
                print(f"🚨 Watchdog: бот молчит {int(silent)} сек. Убиваю процесс...")
                os._exit(1)
        except Exception as e:
            print(f"⚠️ Watchdog: {e}")
            time.sleep(60)


watchdog_thread = threading.Thread(target=run_watchdog, daemon=True)
watchdog_thread.start()


# ============================================================
#  ПЛАНИРОВЩИК БЭКАПА
# ============================================================

def run_backup_scheduler():
    backup.run_scheduler()

backup_thread = threading.Thread(target=run_backup_scheduler, daemon=True)
backup_thread.start()


# ============================================================
#  ИЗБРАННОЕ
# ============================================================

def is_favorite(user_id, station_id):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM favorites WHERE user_id = ? AND station_id = ?",
                       (user_id, station_id))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except:
        return False


def add_favorite(user_id, station_id):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO favorites (user_id, station_id, added_at)
            VALUES (?, ?, ?)
        """, (user_id, station_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except:
        return False


def remove_favorite(user_id, station_id):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM favorites WHERE user_id = ? AND station_id = ?",
                       (user_id, station_id))
        conn.commit()
        conn.close()
        return True
    except:
        return False


def get_favorites(user_id):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.* FROM stations s
            JOIN favorites f ON s.id = f.station_id
            WHERE f.user_id = ?
            ORDER BY f.added_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []


# ============================================================
#  АВТОИМПОРТ
# ============================================================

def auto_import_stations():
    try:
        conn = sqlite3.connect(database.DB_PATH)
        count = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
        conn.close()

        print(f"📂 БАЗА: {database.DB_PATH}")
        print(f"📂 В базе {count} заправок")

        if count == 0:
            print("📂 База пустая — импортирую...")
            import import_stations
            import_stations.import_stations()
            print("✅ Автоимпорт завершён")
        else:
            print(f"📂 Импорт не нужен. Для обновления — /reimport")
    except Exception as e:
        print(f"⚠️ Ошибка автоимпорта: {e}")

auto_import_stations()


# ============================================================
#  РАССТОЯНИЕ
# ============================================================

def distance_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c


# ============================================================
#  ВРЕМЯ
# ============================================================

def format_time_ago(iso_time):
    if not iso_time:
        return "нет данных"
    try:
        updated = datetime.fromisoformat(iso_time)
        now = datetime.now()
        diff = now - updated
        seconds = diff.total_seconds()
        minutes = int(seconds // 60)
        hours = int(minutes // 60)
        days = int(hours // 24)

        if seconds < 60:
            return "только что"
        elif minutes < 60:
            return f"{minutes} мин назад"
        elif hours < 2:
            return f"{hours} ч назад"
        elif hours < 24:
            return f"⚠️ {hours} ч назад"
        else:
            return f"⚠️ {days} дн назад"
    except Exception:
        return "недавно"


def is_fresh(iso_time, hours=2):
    if not iso_time:
        return False
    try:
        updated = datetime.fromisoformat(iso_time)
        diff = (datetime.now() - updated).total_seconds()
        return diff < hours * 3600
    except:
        return False


# ============================================================
#  УСЛУГИ
# ============================================================

def format_services(services_str):
    if not services_str:
        return ""
    icons = {
        "car_wash": "🚿",
        "cafe": "☕",
        "toilet": "🚻",
        "shop": "🛒",
        "tire_pump": "💨",
    }
    result = []
    for s in services_str.split(","):
        s = s.strip()
        if s in icons:
            result.append(icons[s])
    return " ".join(result)


# ============================================================
#  ЦЕНА
# ============================================================

def format_price(available, price):
    if price:
        return f"{price:.2f} ₽"
    elif available:
        return "в наличии, цена неизвестна"
    else:
        return "цена не указана"


# ============================================================
#  КАРТА И МАРШРУТ
# ============================================================

def map_link(lat, lon):
    return f"https://yandex.ru/maps/?ll={lon},{lat}&z=17&pt={lon},{lat},pm2rdm"

def route_link(lat, lon):
    return f"https://yandex.ru/maps/?rtext=~{lat},{lon}&rtt=auto"


# ============================================================
#  INLINE-КНОПКИ "НАЗАД"
# ============================================================

def btn_back_to_menu():
    return types.InlineKeyboardButton("⬅️ В главное меню", callback_data="back_to_menu")

def btn_back_to_cities():
    return types.InlineKeyboardButton("⬅️ К выбору города", callback_data="choose_city")

def btn_back_to_filter():
    return types.InlineKeyboardButton("⬅️ К фильтру", callback_data="filter_fuel")

def btn_back_to_add_cities():
    return types.InlineKeyboardButton("⬅️ К выбору города", callback_data="add_data")

def btn_back_to_add_stations(city):
    return types.InlineKeyboardButton("⬅️ К заправкам", callback_data=f"addcity_{city}")

def btn_back_to_new_station_cities():
    return types.InlineKeyboardButton("⬅️ К выбору города", callback_data="add_station")


# ============================================================
#  КЛАВИАТУРА ДЛЯ ГОРОДА
# ============================================================

def _build_city_keyboard(city, user_id, stations_list):
    markup = types.InlineKeyboardMarkup(row_width=2)

    for i, station in enumerate(stations_list[:15], 1):
        lat, lon = station["lat"], station["lon"]

        btn_map = types.InlineKeyboardButton(
            f"🗺 {i}",
            url=map_link(lat, lon))

        if is_favorite(user_id, station["id"]):
            btn_fav = types.InlineKeyboardButton(
                f"🗑 {i}",
                callback_data=f"fav_del_{station['id']}")
        else:
            btn_fav = types.InlineKeyboardButton(
                f"⭐ {i}",
                callback_data=f"fav_add_{station['id']}")

        markup.row(btn_map, btn_fav)

    markup.add(btn_back_to_cities())
    markup.add(btn_back_to_menu())
    return markup


# ============================================================
#  КЛАВИАТУРА ДЛЯ GPS
# ============================================================

def _build_gps_keyboard(user_id, stations_list):
    markup = types.InlineKeyboardMarkup(row_width=2)

    for i, (station, dist) in enumerate(stations_list[:10], 1):
        lat, lon = station["lat"], station["lon"]

        btn_map = types.InlineKeyboardButton(
            f"🗺 {i}",
            url=map_link(lat, lon))

        if is_favorite(user_id, station["id"]):
            btn_fav = types.InlineKeyboardButton(
                f"🗑 {i}",
                callback_data=f"fav_del_{station['id']}")
        else:
            btn_fav = types.InlineKeyboardButton(
                f"⭐ {i}",
                callback_data=f"fav_add_{station['id']}")

        markup.row(btn_map, btn_fav)

    markup.add(btn_back_to_menu())
    return markup


# ============================================================
#  ГЛАВНОЕ МЕНЮ
# ============================================================

def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_gps = types.InlineKeyboardButton("📍 Ближайшие (GPS)", callback_data="nearby")
    btn_city = types.InlineKeyboardButton("🏙 Выбрать город", callback_data="choose_city")
    btn_filter = types.InlineKeyboardButton("🔎 Фильтр по топливу", callback_data="filter_fuel")
    btn_fav = types.InlineKeyboardButton("⭐ Мои заправки", callback_data="favorites")
    btn_add = types.InlineKeyboardButton("➕ Добавить данные", callback_data="add_data")
    btn_new = types.InlineKeyboardButton("🆕 Новая заправка", callback_data="add_station")
    btn_profile = types.InlineKeyboardButton("📊 Профиль", callback_data="profile")
    btn_share = types.InlineKeyboardButton("📢 Поделиться", callback_data="share")
    btn_about = types.InlineKeyboardButton("ℹ️ О боте", callback_data="about")
    btn_help = types.InlineKeyboardButton("❓ Помощь", callback_data="help")
    btn_report = types.InlineKeyboardButton("✉️ Сообщить о проблеме", callback_data="report")

    markup.add(btn_gps, btn_city)
    markup.add(btn_filter, btn_fav)
    markup.add(btn_add, btn_new)
    markup.add(btn_profile, btn_share)
    markup.add(btn_about, btn_help)
    markup.add(btn_report)
    return markup


# ============================================================
#  /start
# ============================================================

@bot.message_handler(commands=['start'])
def start_command(message):
    touch_activity()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    database.get_or_create_user(user_id, username)

    welcome_text = (
        f"⛽ Привет, {message.from_user.first_name}!\n\n"
        "Добро пожаловать в VS Где Топливо — бот, который помогает "
        "найти ближайшие заправки и узнать, где есть бензин.\n\n"
        "Выбери действие:"
    )
    safe_send_message(message.chat.id, welcome_text,
                      reply_markup=get_main_menu())
    safe_send_message(message.chat.id,
                      "👇 Кнопки внизу экрана всегда доступны:",
                      reply_markup=get_persistent_keyboard())


# ============================================================
#  КНОПКА «🚀 СТАРТ»
# ============================================================

@bot.message_handler(func=lambda message: message.text == "🚀 Старт")
def handle_start_button(message):
    touch_activity()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    database.get_or_create_user(user_id, username)

    welcome_text = (
        "⛽ VS Где Топливо\n\n"
        "Выбери действие:"
    )
    safe_send_message(message.chat.id, welcome_text,
                      reply_markup=get_main_menu())
    safe_send_message(message.chat.id,
                      "👇 Кнопки внизу экрана всегда доступны:",
                      reply_markup=get_persistent_keyboard())


# ============================================================
#  /help
# ============================================================

@bot.message_handler(commands=['help'])
def help_command(message):
    touch_activity()
    show_help(message.chat.id)


def show_help(chat_id):
    help_text = (
        "❓ Помощь по боту\n\n"
        "• 📍 Ближайшие заправки по GPS\n"
        "• 🏙 Заправки по городу\n"
        "• 🔎 Фильтр по топливу (с зелёными галочками)\n"
        "• ⭐ Избранные заправки\n"
        "• 🗺 Карта и маршрут\n"
        "• ➕ Добавление данных\n"
        "• 🆕 Новая заправка\n\n"
        "Как читать кнопки:\n"
        "🗺 1 — карта заправки №1\n"
        "⭐ 1 — добавить заправку №1 в избранное\n"
        "🗑 1 — убрать из избранного\n\n"
        "Обозначения в списке:\n"
        "✅ — топливо в наличии\n"
        "❌ — топлива нет\n"
        "🟢 — свежие данные (менее 2 часов)\n\n"
        "Контакты: vs_prog10@list.ru"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, help_text, reply_markup=markup)


# ============================================================
#  /about
# ============================================================

@bot.message_handler(commands=['about'])
def about_command(message):
    touch_activity()
    show_about(message.chat.id)


def show_about(chat_id):
    about_text = (
        "⛽ VS Где Топливо\n\n"
        "Надоело ездить в поисках бензина? Этот бот знает, где он есть.\n\n"
        "📍 АЗС по GPS\n"
        "💰 Цены\n"
        "✅ Зелёные галочки — где есть топливо\n"
        "⭐ Избранное\n"
        "🗺 Карта и маршрут\n\n"
        "Данные обновляют сами водители.\n"
        "Помог другим — помогли тебе.\n\n"
        "Автор: Владислав Сергеевич\n"
        "Связь: vs_prog10@list.ru\n\n"
        "Версия: 1.13"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, about_text, reply_markup=markup)


# ============================================================
#  /reimport
# ============================================================

@bot.message_handler(commands=['reimport'])
def reimport_command(message):
    touch_activity()
    ADMIN_ID = 874768370

    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "⛔ Только для админа.")
        return

    safe_send_message(message.chat.id, "🔄 Переимпорт...")

    try:
        conn = sqlite3.connect(database.DB_PATH)
        cursor = conn.cursor()
        before = cursor.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
        cursor.execute("DELETE FROM stations")
        try:
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='stations'")
        except:
            pass
        conn.commit()
        conn.close()

        import importlib
        import import_stations
        importlib.reload(import_stations)
        import_stations.import_stations()

        invalidate_cache()

        conn = sqlite3.connect(database.DB_PATH)
        after = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
        conn.close()

        safe_send_message(message.chat.id,
            f"✅ Готово!\n\n📊 Было: {before}\n📊 Стало: {after}")
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Ошибка: {e}")


# ============================================================
#  /stats
# ============================================================

@bot.message_handler(commands=['stats'])
def stats_command(message):
    touch_activity()
    ADMIN_ID = 874768370

    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "⛔ Только для админа.")
        return

    try:
        conn = database.get_connection()
        cursor = conn.cursor()

        users_total = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        stations_total = cursor.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
        updates_total = cursor.execute("SELECT COUNT(*) FROM fuel_log").fetchone()[0]
        favorites_total = cursor.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]

        cursor.execute("SELECT username, reputation FROM users ORDER BY reputation DESC LIMIT 5")
        top_users = cursor.fetchall()

        cursor.execute("SELECT city, COUNT(*) as cnt FROM stations GROUP BY city ORDER BY cnt DESC")
        cities = cursor.fetchall()

        conn.close()

        text = (
            f"📊 Статистика\n\n"
            f"👥 Пользователей: {users_total}\n"
            f"⛽ Заправок: {stations_total}\n"
            f"📝 Обновлений: {updates_total}\n"
            f"⭐ В избранном: {favorites_total}\n\n"
            f"🏆 Топ-5:\n"
        )
        for u in top_users:
            name = u["username"] or "без имени"
            text += f"  • {name}: {u['reputation']}\n"

        text += f"\n🏙 По городам:\n"
        for c in cities:
            text += f"  • {c['city']}: {c['cnt']}\n"

        safe_send_message(message.chat.id, text)
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Ошибка: {e}")


# ============================================================
#  ОБРАБОТКА INLINE-КНОПОК
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    touch_activity()
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    safe_answer_callback(call.id)

    if call.data == "back_to_menu":
        safe_edit_message_text(
            "⛽ VS Где Топливо\n\nВыбери действие:",
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=get_main_menu()
        )

    elif call.data == "help":
        show_help(chat_id)

    elif call.data == "about":
        show_about(chat_id)

    elif call.data == "profile":
        show_profile(chat_id, user_id)

    elif call.data == "share":
        show_share(chat_id)

    elif call.data == "report":
        ask_report(chat_id)

    elif call.data == "favorites":
        show_favorites(chat_id, user_id)

    elif call.data.startswith("fav_add_"):
        station_id = int(call.data.replace("fav_add_", ""))
        add_favorite(user_id, station_id)
        safe_answer_callback(call.id, text="⭐ Добавлено в избранное", show_alert=False)
        try:
            station = database.get_station_by_id(station_id)
            if station:
                city = station["city"]
                all_stations = get_cached_stations()
                city_stations = [s for s in all_stations if s["city"] == city]
                new_markup = _build_city_keyboard(city, user_id, city_stations)
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=new_markup)
        except Exception as e:
            print(f"⚠️ Не удалось обновить клавиатуру: {e}")

    elif call.data.startswith("fav_del_"):
        station_id = int(call.data.replace("fav_del_", ""))
        remove_favorite(user_id, station_id)
        safe_answer_callback(call.id, text="Удалено из избранного", show_alert=False)
        try:
            station = database.get_station_by_id(station_id)
            if station:
                city = station["city"]
                all_stations = get_cached_stations()
                city_stations = [s for s in all_stations if s["city"] == city]
                new_markup = _build_city_keyboard(city, user_id, city_stations)
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=new_markup)
        except:
            show_favorites(chat_id, user_id)

    elif call.data.startswith("map_"):
        parts = call.data.replace("map_", "").split("_")
        lat, lon = float(parts[0]), float(parts[1])
        safe_send_message(chat_id,
            f"🗺 Карта:\n{map_link(lat, lon)}\n\n"
            f"🚗 Маршрут:\n{route_link(lat, lon)}")

    elif call.data == "nearby":
        safe_send_message(chat_id,
            "📍 Нажми кнопку «📍 Отправить локацию» внизу экрана — "
            "и я найду ближайшие заправки.")

    elif call.data == "choose_city":
        markup = types.InlineKeyboardMarkup(row_width=1)
        for city in config.CITIES:
            btn = types.InlineKeyboardButton(f"🏙 {city}", callback_data=f"city_{city}")
            markup.add(btn)
        markup.add(btn_back_to_menu())
        safe_edit_message_text("🏙 Выбери город:", chat_id=chat_id,
            message_id=call.message.message_id, reply_markup=markup)

    elif call.data.startswith("city_"):
        city = call.data.replace("city_", "")
        show_stations_by_city(chat_id, city, user_id)

    elif call.data == "add_data":
        show_city_for_add(chat_id)

    elif call.data.startswith("addcity_"):
        city = call.data.replace("addcity_", "")
        show_stations_for_add(chat_id, city)

    elif call.data.startswith("addstation_"):
        station_id = int(call.data.replace("addstation_", ""))
        show_fuel_types_for_add(chat_id, station_id)

    elif call.data.startswith("addfuel_"):
        parts = call.data.replace("addfuel_", "").split("_")
        station_id = int(parts[0])
        fuel_type = "_".join(parts[1:])
        ask_price(chat_id, station_id, fuel_type)

    elif call.data.startswith("present_"):
        parts = call.data.replace("present_", "").split("_")
        station_id = int(parts[0])
        fuel_type = "_".join(parts[1:])
        process_present_unknown_price(chat_id, user_id, station_id, fuel_type)

    elif call.data.startswith("absent_"):
        parts = call.data.replace("absent_", "").split("_")
        station_id = int(parts[0])
        fuel_type = "_".join(parts[1:])
        process_absent(chat_id, user_id, station_id, fuel_type)

    elif call.data == "filter_fuel":
        show_fuel_filter_menu(chat_id)

    elif call.data.startswith("filterf_"):
        fuel = call.data.replace("filterf_", "")
        show_stations_by_fuel(chat_id, fuel, sort_by_price=False)

    elif call.data.startswith("filtersort_"):
        fuel = call.data.replace("filtersort_", "")
        show_stations_by_fuel(chat_id, fuel, sort_by_price=True)

    elif call.data == "add_station":
        start_add_station(chat_id)

    elif call.data.startswith("nscity_"):
        city = call.data.replace("nscity_", "")
        ask_name(chat_id, city)

    elif call.data.startswith("ns_svc_"):
        code = call.data.replace("ns_svc_", "")
        handle_service_checkbox(chat_id, call.message.message_id, code)

    elif call.data == "ns_done":
        finish_add_station(chat_id, call.message.message_id)

    elif call.data == "ns_skip":
        finish_add_station(chat_id, call.message.message_id, skip=True)


# ============================================================
#  ПРОФИЛЬ
# ============================================================

def show_profile(chat_id, user_id):
    user = database.get_or_create_user(user_id, "")
    reputation = user.get("reputation", 0)
    joined_at = user.get("joined_at", "")

    try:
        joined = datetime.fromisoformat(joined_at).strftime("%d.%m.%Y")
    except:
        joined = "неизвестно"

    total_updates = stations_count = fuels_count = fav_count = 0
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM fuel_log WHERE user_id = ?", (user_id,))
        total_updates = cursor.fetchone()[0]
        cursor.execute("SELECT DISTINCT station_id FROM fuel_log WHERE user_id = ?", (user_id,))
        stations_count = len(cursor.fetchall())
        cursor.execute("SELECT DISTINCT fuel_type FROM fuel_log WHERE user_id = ?", (user_id,))
        fuels_count = len(cursor.fetchall())
        cursor.execute("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (user_id,))
        fav_count = cursor.fetchone()[0]
        conn.close()
    except:
        pass

    if reputation >= 50:
        level = "🏆 Эксперт"
    elif reputation >= 20:
        level = "⭐ Опытный"
    elif reputation >= 5:
        level = "👍 Активный"
    else:
        level = "🌱 Новичок"

    # Мотивирующий текст
    if total_updates == 0:
        motivation = "💡 Ты ещё не помогал другим. Зайди на заправку и нажми «➕ Добавить данные»!"
    elif total_updates < 5:
        motivation = "👍 Хорошее начало! Продолжай помогать водителям."
    elif total_updates < 20:
        motivation = "🔥 Отлично! Ты помогаешь другим водителям."
    else:
        motivation = "🏆 Ты — легенда! Спасибо за твой вклад!"

    profile_text = (
        f"📊 Твой профиль\n\n"
        f"👤 Уровень: {level}\n"
        f"🏅 Репутация: {reputation} очков\n"
        f"📅 С нами с: {joined}\n\n"
        f"📈 Статистика:\n"
        f"• Обновлений: {total_updates}\n"
        f"• Заправок: {stations_count}\n"
        f"• Марок: {fuels_count}\n"
        f"• В избранном: {fav_count}\n\n"
        f"{motivation}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, profile_text, reply_markup=markup)


# ============================================================
#  ПОДЕЛИТЬСЯ
# ============================================================

def show_share(chat_id):
    bot_info = bot.get_me()
    bot_username = bot_info.username
    share_link = f"https://t.me/{bot_username}"

    share_text = (
        f"📢 Поделиться ботом\n\n"
        f"Отправь друзьям:\n\n{share_link}\n\n"
        f"Чем больше людей — тем точнее данные!"
    )
    markup = types.InlineKeyboardMarkup()
    share_btn = types.InlineKeyboardButton(
        "📤 Поделиться",
        url=f"https://t.me/share/url?url={share_link}&text=Найди бензин рядом!"
    )
    markup.add(share_btn)
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, share_text, reply_markup=markup)


# ============================================================
#  СООБЩИТЬ О ПРОБЛЕМЕ
# ============================================================

def ask_report(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    msg = safe_send_message(chat_id,
        "✉️ Сообщить о проблеме\n\n"
        "Напиши текст следующим сообщением:",
        reply_markup=markup)
    if msg:
        bot.register_next_step_handler(msg, process_report)


def process_report(message):
    touch_activity()
    if not message.text:
        safe_send_message(message.chat.id, "❌ Не понял.")
        return

    user = message.from_user
    username = f"@{user.username}" if user.username else user.first_name
    ADMIN_ID = 874768370

    safe_send_message(ADMIN_ID,
        f"✉️ От {username} (ID: {user.id}):\n\n{message.text}")

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(message.chat.id,
        "✅ Отправлено разработчику.",
        reply_markup=markup)


# ============================================================
#  ИЗБРАННОЕ — ПОКАЗ
# ============================================================

def show_favorites(chat_id, user_id):
    favs = get_favorites(user_id)

    markup = types.InlineKeyboardMarkup()

    if not favs:
        markup.add(btn_back_to_menu())
        safe_send_message(chat_id,
            "⭐ Мои заправки\n\n"
            "Пока пусто. Зайди в город и нажми «⭐» рядом с заправкой.",
            reply_markup=markup)
        return

    response = f"⭐ Мои заправки ({len(favs)}):\n\n"
    for i, s in enumerate(favs, 1):
        response += f"{i}. {s['name']}\n"
        response += f"   📍 {s['address']}, {s['city']}\n"

        services_icons = format_services(s.get("services", ""))
        if services_icons:
            response += f"   {services_icons}\n"

        fuel_data = database.get_fuel_for_station(s["id"])
        if fuel_data:
            for f in fuel_data[:3]:
                status = "✅" if f["available"] else "❌"
                price = format_price(f["available"], f["price"])
                time_ago = format_time_ago(f.get("updated_at"))
                fresh = " 🟢" if is_fresh(f.get("updated_at")) else ""
                response += f"   {status} {f['fuel_type']}: {price} ({time_ago}){fresh}\n"
        else:
            response += "   ⛽ Данных о топливе пока нет\n"
            response += "   💡 Помоги другим — нажми «➕ Добавить данные»\n"

        response += "\n"

    for i, s in enumerate(favs[:10], 1):
        lat, lon = s["lat"], s["lon"]
        btn_map = types.InlineKeyboardButton(
            f"🗺 {i}",
            url=map_link(lat, lon))
        btn_del = types.InlineKeyboardButton(
            f"🗑 {i}",
            callback_data=f"fav_del_{s['id']}")
        markup.row(btn_map, btn_del)

    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, response, reply_markup=markup)


# ============================================================
#  GPS-ПОИСК
# ============================================================

@bot.message_handler(content_types=['location'])
def handle_location(message):
    touch_activity()
    user_id = message.from_user.id
    user_lat = message.location.latitude
    user_lon = message.location.longitude

    all_stations = get_cached_stations()

    if not all_stations:
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(message.chat.id, "⚠️ Нет заправок в базе.",
                          reply_markup=markup)
        return

    all_fuel_grouped = get_cached_fuel_grouped()

    stations_with_distance = []
    for s in all_stations:
        dist = distance_km(user_lat, user_lon, s["lat"], s["lon"])
        if dist <= config.MAX_RADIUS_KM:
            stations_with_distance.append((s, dist))

    stations_with_distance.sort(key=lambda x: x[1])

    if not stations_with_distance:
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(message.chat.id,
            f"😔 В радиусе {config.MAX_RADIUS_KM} км ничего нет.",
            reply_markup=markup)
        return

    response = f"📍 Ближайшие (до {config.MAX_RADIUS_KM} км):\n\n"

    for i, (station, dist) in enumerate(stations_with_distance[:10], 1):
        response += f"{i}. {station['name']}\n"
        response += f"   📍 {station['address'] or 'Адрес не указан'}\n"
        response += f"   📏 {dist:.2f} км\n"

        services_icons = format_services(station.get("services", ""))
        if services_icons:
            response += f"   {services_icons}\n"

        fuel_data = all_fuel_grouped.get(station["id"], [])
        if fuel_data:
            response += "   ⛽ Топливо:\n"
            for f in fuel_data[:5]:
                status = "✅" if f["available"] else "❌"
                price = format_price(f["available"], f["price"])
                time_ago = format_time_ago(f.get("updated_at"))
                fresh = " 🟢" if is_fresh(f.get("updated_at")) else ""
                response += f"     {status} {f['fuel_type']}: {price} ({time_ago}){fresh}\n"
        else:
            response += "   ⛽ Данных о топливе пока нет\n"
            response += "   💡 Помоги другим — нажми «➕ Добавить данные»\n"
        response += "\n"

    markup = _build_gps_keyboard(user_id, stations_with_distance)
    safe_send_message(message.chat.id, response, reply_markup=markup)


# ============================================================
#  ПОИСК ПО ГОРОДУ
# ============================================================

def show_stations_by_city(chat_id, city, user_id=None):
    all_stations = get_cached_stations()
    city_stations = [s for s in all_stations if s["city"] == city]

    markup = types.InlineKeyboardMarkup()

    if not city_stations:
        markup.add(btn_back_to_cities())
        markup.add(btn_back_to_menu())
        safe_send_message(chat_id, f"😔 В городе {city} заправок нет.",
                          reply_markup=markup)
        return

    all_fuel_grouped = get_cached_fuel_grouped()

    response = f"🏙 Заправки в городе {city}:\n\n"

    for i, station in enumerate(city_stations[:15], 1):
        response += f"{i}. {station['name']}\n"
        response += f"   📍 {station['address'] or 'Адрес не указан'}\n"

        services_icons = format_services(station.get("services", ""))
        if services_icons:
            response += f"   {services_icons}\n"

        fuel_data = all_fuel_grouped.get(station["id"], [])
        if fuel_data:
            response += "   ⛽ Топливо:\n"
            for f in fuel_data[:5]:
                status = "✅" if f["available"] else "❌"
                price = format_price(f["available"], f["price"])
                time_ago = format_time_ago(f.get("updated_at"))
                fresh = " 🟢" if is_fresh(f.get("updated_at")) else ""
                response += f"     {status} {f['fuel_type']}: {price} ({time_ago}){fresh}\n"
        else:
            response += "   ⛽ Данных о топливе пока нет\n"
            response += "   💡 Помоги другим — нажми «➕ Добавить данные»\n"
        response += "\n"

    if user_id is not None:
        markup = _build_city_keyboard(city, user_id, city_stations)
    else:
        for i, station in enumerate(city_stations[:5], 1):
            lat, lon = station["lat"], station["lon"]
            btn_map = types.InlineKeyboardButton(
                f"🗺 {i}",
                url=map_link(lat, lon))
            markup.row(btn_map)
        markup.add(btn_back_to_cities())
        markup.add(btn_back_to_menu())

    safe_send_message(chat_id, response, reply_markup=markup)


# ============================================================
#  ФИЛЬТР ПО ТОПЛИВУ
# ============================================================

def show_fuel_filter_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for fuel in config.FUEL_TYPES:
        btn = types.InlineKeyboardButton(f"⛽ {fuel}", callback_data=f"filterf_{fuel}")
        markup.add(btn)
    markup.add(btn_back_to_menu())

    safe_send_message(chat_id,
        "🔎 Фильтр по топливу\n\n"
        "Выбери марку — покажу АЗС, где это топливо есть:\n\n"
        "✅ — в наличии\n"
        "❌ — нет\n"
        "🟢 — свежие данные (менее 2 часов)",
        reply_markup=markup)


def show_stations_by_fuel(chat_id, fuel_type, sort_by_price=False):
    all_stations = get_cached_stations()
    all_fuel_grouped = get_cached_fuel_grouped()

    matching = []
    for s in all_stations:
        for f in all_fuel_grouped.get(s["id"], []):
            if f["fuel_type"] == fuel_type and f["available"] == 1:
                matching.append((s, f))
                break

    markup = types.InlineKeyboardMarkup()

    if not matching:
        markup.add(btn_back_to_filter())
        markup.add(btn_back_to_menu())
        safe_send_message(chat_id,
            f"😔 Пока никто не отметил {fuel_type}\n\n"
            f"💡 Будь первым! Зайди на заправку и нажми:\n"
            f"«➕ Добавить данные» → выбери {fuel_type}\n\n"
            f"Твоя отметка появится здесь через минуту.",
            reply_markup=markup)
        return

    if sort_by_price:
        matching.sort(key=lambda x: x[1]["price"] if x[1]["price"] else 9999)

    response = f"🔎 АЗС с {fuel_type}"
    if sort_by_price:
        response += " (дешёвые сверху)"
    response += ":\n\n"

    for i, (s, f) in enumerate(matching[:20], 1):
        price = format_price(f["available"], f["price"])
        time_ago = format_time_ago(f.get("updated_at"))
        services_icons = format_services(s.get("services", ""))

        if f["available"] == 1:
            check = "✅ "
            fresh_mark = " 🟢" if is_fresh(f.get("updated_at")) else ""
        else:
            check = "❌ "
            fresh_mark = ""

        response += f"{i}. {check}{s['name']} — {price} ({time_ago}){fresh_mark}\n"
        response += f"   📍 {s['address']}, {s['city']}\n"
        if services_icons:
            response += f"   {services_icons}\n"
        response += "\n"

    response += "────────────────────\n"
    response += "✅ — топливо в наличии\n"
    response += "🟢 — свежие данные (до 2 часов)\n"

    if not sort_by_price:
        btn_sort = types.InlineKeyboardButton(
            "💰 Сначала дешёвые",
            callback_data=f"filtersort_{fuel_type}")
        markup.add(btn_sort)
    else:
        btn_sort = types.InlineKeyboardButton(
            "↩️ Обычная сортировка",
            callback_data=f"filterf_{fuel_type}")
        markup.add(btn_sort)

    for i, (s, f) in enumerate(matching[:5], 1):
        lat, lon = s["lat"], s["lon"]
        btn_map = types.InlineKeyboardButton(
            f"🗺 {i} — {s['name'][:12]}",
            url=map_link(lat, lon))
        markup.row(btn_map)

    markup.add(btn_back_to_filter())
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, response, reply_markup=markup)


# ============================================================
#  КРАУДСОРСИНГ
# ============================================================

def show_city_for_add(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for city in config.CITIES:
        btn = types.InlineKeyboardButton(f"🏙 {city}", callback_data=f"addcity_{city}")
        markup.add(btn)
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id, "➕ Добавление данных\n\nШаг 1: Выбери город:",
        reply_markup=markup)


def show_stations_for_add(chat_id, city):
    all_stations = get_cached_stations()
    city_stations = [s for s in all_stations if s["city"] == city]

    markup = types.InlineKeyboardMarkup()

    if not city_stations:
        markup.add(btn_back_to_add_cities())
        markup.add(btn_back_to_menu())
        safe_send_message(chat_id, f"😔 В городе {city} заправок нет.",
                          reply_markup=markup)
        return

    for s in city_stations[:20]:
        label = f"⛽ {s['name']} — {s['address']}"
        btn = types.InlineKeyboardButton(label, callback_data=f"addstation_{s['id']}")
        markup.add(btn)

    markup.add(btn_back_to_add_cities())
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id,
        f"➕ Шаг 2: Выбери заправку в {city}:",
        reply_markup=markup)


def show_fuel_types_for_add(chat_id, station_id):
    station = database.get_station_by_id(station_id)
    if not station:
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_add_cities())
        safe_send_message(chat_id, "❌ Заправка не найдена.", reply_markup=markup)
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    for fuel in config.FUEL_TYPES:
        btn = types.InlineKeyboardButton(f"⛽ {fuel}", callback_data=f"addfuel_{station_id}_{fuel}")
        markup.add(btn)

    markup.add(btn_back_to_add_stations(station['city']))
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id,
        f"➕ Шаг 3: {station['name']} — {station['address']}\n\nВыбери марку:",
        reply_markup=markup)


def ask_price(chat_id, station_id, fuel_type):
    station = database.get_station_by_id(station_id)
    station_name = station["name"] if station else "Неизвестно"

    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_present = types.InlineKeyboardButton(
        "✅ В наличии (цена неизвестна)",
        callback_data=f"present_{station_id}_{fuel_type}")
    btn_absent = types.InlineKeyboardButton(
        "❌ Нет в наличии",
        callback_data=f"absent_{station_id}_{fuel_type}")
    markup.add(btn_present)
    markup.add(btn_absent)
    markup.add(btn_back_to_menu())

    msg = safe_send_message(chat_id,
        f"➕ Шаг 4: {station_name}\n⛽ {fuel_type}\n\n"
        f"Что известно?\n\n"
        f"• Напиши цену (55.50)\n"
        f"• Или нажми «✅ В наличии»\n"
        f"• Или нажми «❌ Нет в наличии»",
        reply_markup=markup)
    if msg:
        bot.register_next_step_handler(msg, process_price_input, station_id, fuel_type)


def _check_limit(chat_id, user_id, station_id, fuel_type):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT updated_at FROM fuel_log
            WHERE station_id = ? AND fuel_type = ? AND user_id = ?
            ORDER BY updated_at DESC LIMIT 1
        """, (station_id, fuel_type, user_id))
        row = cursor.fetchone()
        conn.close()

        if row:
            try:
                last_update = datetime.fromisoformat(row["updated_at"])
                diff = (datetime.now() - last_update).total_seconds()
                if diff < 180:
                    wait = int(180 - diff)
                    safe_send_message(chat_id, f"⏳ Подожди {wait} сек.")
                    return True
            except:
                pass
    except:
        pass
    return False


def process_present_unknown_price(chat_id, user_id, station_id, fuel_type):
    if _check_limit(chat_id, user_id, station_id, fuel_type):
        return

    database.update_fuel(station_id, fuel_type, None, 1, user_id)
    database.increase_reputation(user_id, 1)
    invalidate_cache()

    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO fuel_log (station_id, fuel_type, user_id, updated_at)
            VALUES (?, ?, ?, ?)
        """, (station_id, fuel_type, user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except:
        pass

    station = database.get_station_by_id(station_id)
    station_name = station["name"] if station else "Неизвестно"

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id,
        f"✅ {station_name}\n⛽ {fuel_type}: в наличии, цена неизвестна",
        reply_markup=markup)


def process_absent(chat_id, user_id, station_id, fuel_type):
    if _check_limit(chat_id, user_id, station_id, fuel_type):
        return

    database.update_fuel(station_id, fuel_type, None, 0, user_id)
    database.increase_reputation(user_id, 1)
    invalidate_cache()

    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO fuel_log (station_id, fuel_type, user_id, updated_at)
            VALUES (?, ?, ?, ?)
        """, (station_id, fuel_type, user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except:
        pass

    station = database.get_station_by_id(station_id)
    station_name = station["name"] if station else "Неизвестно"

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id,
        f"✅ {station_name}\n⛽ {fuel_type}: нет в наличии",
        reply_markup=markup)


def process_price_input(message, station_id, fuel_type):
    touch_activity()
    if not message.text:
        safe_send_message(message.chat.id, "❌ Не понял.")
        return

    text = message.text.strip().lower()
    user_id = message.from_user.id

    if _check_limit(message.chat.id, user_id, station_id, fuel_type):
        return

    if text in ("нет", "no", "н"):
        database.update_fuel(station_id, fuel_type, None, 0, user_id)
        database.increase_reputation(user_id, 1)
        invalidate_cache()
        try:
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO fuel_log (station_id, fuel_type, user_id, updated_at)
                VALUES (?, ?, ?, ?)
            """, (station_id, fuel_type, user_id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except:
            pass

        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(message.chat.id, "✅ Отмечено: нет.", reply_markup=markup)
        return

    try:
        price = float(text.replace(",", "."))
    except:
        safe_send_message(message.chat.id, "❌ Не понял цену.")
        return

    if price < 10 or price > 200:
        safe_send_message(message.chat.id, "❌ Странная цена.")
        return

    database.update_fuel(station_id, fuel_type, price, 1, user_id)
    database.increase_reputation(user_id, 1)
    invalidate_cache()

    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO fuel_log (station_id, fuel_type, user_id, updated_at)
            VALUES (?, ?, ?, ?)
        """, (station_id, fuel_type, user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except:
        pass

    station = database.get_station_by_id(station_id)
    station_name = station["name"] if station else "Неизвестно"

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())
    safe_send_message(message.chat.id,
        f"✅ {station_name}\n⛽ {fuel_type}: {price:.2f} ₽",
        reply_markup=markup)


# ============================================================
#  ДОБАВЛЕНИЕ НОВОЙ ЗАПРАВКИ
# ============================================================

def start_add_station(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for city in config.CITIES_FOR_ADD:
        btn = types.InlineKeyboardButton(f"🏙 {city}", callback_data=f"nscity_{city}")
        markup.add(btn)
    markup.add(btn_back_to_menu())
    safe_send_message(chat_id,
        "🆕 Новая заправка\n\n⚠️ Только те, которых НЕТ в базе.\n\nШаг 1: Выбери город:",
        reply_markup=markup)


def ask_name(chat_id, city):
    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_new_station_cities())
    msg = safe_send_message(chat_id,
        f"🆕 {city}\n\nШаг 2: Введи название (Лукойл, Газпромнефть):",
        reply_markup=markup)
    if msg:
        bot.register_next_step_handler(msg, ask_address, city)


def ask_address(message, city):
    touch_activity()
    if not message.text or message.text.strip().lower() in ("/cancel", "отмена"):
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(message.chat.id, "❌ Отменено.", reply_markup=markup)
        return

    name = message.text.strip()
    if len(name) < 2:
        safe_send_message(message.chat.id, "❌ Слишком короткое.")
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_new_station_cities())
    msg = safe_send_message(message.chat.id,
        f"🆕 {name}\n\nШаг 3: Введи адрес (Московское шоссе, 65):",
        reply_markup=markup)
    if msg:
        bot.register_next_step_handler(msg, ask_coordinates, city, name)


def ask_coordinates(message, city, name):
    touch_activity()
    if not message.text or message.text.strip().lower() in ("/cancel", "отмена"):
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(message.chat.id, "❌ Отменено.", reply_markup=markup)
        return

    address = message.text.strip()
    if len(address) < 3:
        safe_send_message(message.chat.id, "❌ Слишком короткий адрес.")
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_new_station_cities())
    msg = safe_send_message(message.chat.id,
        f"🆕 {name}\n📍 {address}\n\n"
        f"Шаг 4: Координаты (54.9194, 37.4137):",
        reply_markup=markup)
    if msg:
        bot.register_next_step_handler(msg, ask_services, city, name, address)


def ask_services(message, city, name, address):
    touch_activity()
    if not message.text or message.text.strip().lower() in ("/cancel", "отмена"):
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(message.chat.id, "❌ Отменено.", reply_markup=markup)
        return

    text = message.text.strip()
    parts = [p for p in re.split(r"[\s,;]+", text) if p]

    if len(parts) < 2:
        safe_send_message(message.chat.id, "❌ Не понял. Формат: 54.9194, 37.4137")
        return

    try:
        lat = float(parts[0].replace(",", "."))
        lon = float(parts[1].replace(",", "."))
    except:
        safe_send_message(message.chat.id, "❌ Координаты — числа.")
        return

    if not (54.0 < lat < 56.5 and 36.0 < lon < 39.5):
        safe_send_message(message.chat.id, "❌ Координаты вне Подмосковья.")
        return

    temp_stations[message.chat.id] = {
        "name": name,
        "address": address,
        "city": city,
        "lat": lat,
        "lon": lon,
        "services": []
    }

    markup = types.InlineKeyboardMarkup(row_width=2)
    for code, label in config.SERVICES_LIST:
        btn = types.InlineKeyboardButton(f"☐ {label}", callback_data=f"ns_svc_{code}")
        markup.add(btn)
    btn_done = types.InlineKeyboardButton("✅ Готово", callback_data="ns_done")
    btn_skip = types.InlineKeyboardButton("➡️ Пропустить", callback_data="ns_skip")
    markup.add(btn_done, btn_skip)
    markup.add(btn_back_to_new_station_cities())

    safe_send_message(message.chat.id,
        f"🆕 {name}\n📍 {address}\n🏙 {city}\n📌 {lat}, {lon}\n\n"
        f"Шаг 5: Отметь услуги:",
        reply_markup=markup)


def handle_service_checkbox(chat_id, message_id, code):
    if chat_id not in temp_stations:
        return

    data = temp_stations[chat_id]
    if code in data["services"]:
        data["services"].remove(code)
    else:
        data["services"].append(code)

    markup = types.InlineKeyboardMarkup(row_width=2)
    for c, label in config.SERVICES_LIST:
        mark = "✅" if c in data["services"] else "☐"
        btn = types.InlineKeyboardButton(f"{mark} {label}", callback_data=f"ns_svc_{c}")
        markup.add(btn)
    btn_done = types.InlineKeyboardButton("✅ Готово", callback_data="ns_done")
    btn_skip = types.InlineKeyboardButton("➡️ Пропустить", callback_data="ns_skip")
    markup.add(btn_done, btn_skip)
    markup.add(btn_back_to_new_station_cities())

    try:
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except:
        pass


def finish_add_station(chat_id, message_id, skip=False):
    if chat_id not in temp_stations:
        markup = types.InlineKeyboardMarkup()
        markup.add(btn_back_to_menu())
        safe_send_message(chat_id, "❌ Что-то не так. Начни заново.",
                          reply_markup=markup)
        return

    data = temp_stations.pop(chat_id)
    services_str = ",".join(data["services"])

    station_id, status = database.add_station_from_user(
        name=data["name"],
        address=data["address"],
        city=data["city"],
        lat=data["lat"],
        lon=data["lon"],
        services=services_str,
        user_id=chat_id
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(btn_back_to_menu())

    if status == "duplicate":
        safe_send_message(chat_id,
            f"⚠️ Такая заправка уже есть:\n{data['name']}\n{data['address']}",
            reply_markup=markup)
        return

    invalidate_cache()
    database.increase_reputation(chat_id, 2)
    services_icons = format_services(services_str)

    safe_send_message(chat_id,
        f"✅ Заправка добавлена:\n\n"
        f"⛽ {data['name']}\n📍 {data['address']}\n🏙 {data['city']}\n"
        f"📌 {data['lat']}, {data['lon']}\n{services_icons}\n\n"
        f"+2 к репутации 🙏",
        reply_markup=markup)


# ============================================================
#  ОБЪЕДИНЁННЫЙ ПЛАНИРОВЩИК
# ============================================================

def send_reminders():
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT u.user_id,
                (SELECT MAX(updated_at) FROM fuel_log WHERE user_id = u.user_id) as last_update
            FROM users u
            WHERE u.updates_count > 0
        """)
        rows = cursor.fetchall()
        conn.close()

        three_days = 3 * 24 * 3600
        sent = 0

        for row in rows:
            user_id = row["user_id"]
            last = row["last_update"]
            if not last:
                continue
            try:
                last_dt = datetime.fromisoformat(last)
                diff = (datetime.now() - last_dt).total_seconds()
                if diff > three_days:
                    safe_send_message(user_id,
                        "👋 Привет! Ты давно не обновлял данные.\n\n"
                        "Если был на заправке — поделись с другими! "
                        "Это 10 секунд.\n\n"
                        "Нажми /start → «➕ Добавить данные».")
                    sent += 1
            except:
                continue

        print(f"📩 Напоминаний: {sent}")
    except Exception as e:
        print(f"⚠️ Ошибка напоминаний: {e}")


def cleanup_old_fuel_log():
    try:
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM fuel_log WHERE updated_at < ?", (cutoff,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"🧹 Удалено {deleted} старых записей")
    except Exception as e:
        print(f"⚠️ Ошибка очистки: {e}")


def checkpoint_wal():
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        print("💾 WAL checkpoint выполнен")
    except Exception as e:
        print(f"⚠️ Ошибка checkpoint: {e}")


def run_combined_scheduler():
    print("⏰ Объединённый планировщик запущен.")
    last_reminder_date = None
    last_cleanup_date = None
    last_checkpoint_hour = None

    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            current_hour = now.strftime("%Y-%m-%d %H")

            if now.hour == 19 and last_reminder_date != today:
                send_reminders()
                last_reminder_date = today

            elif now.hour == 3 and last_cleanup_date != today:
                cleanup_old_fuel_log()
                last_cleanup_date = today

            elif last_checkpoint_hour != current_hour:
                checkpoint_wal()
                last_checkpoint_hour = current_hour

            time.sleep(120)
        except Exception as e:
            print(f"⚠️ Ошибка планировщика: {e}")
            time.sleep(120)


scheduler_thread = threading.Thread(target=run_combined_scheduler, daemon=True)
scheduler_thread.start()


# ============================================================
#  GRACEFUL SHUTDOWN
# ============================================================

import signal
import sys

def graceful_shutdown(signum, frame):
    print("\n🛑 Остановка...")
    try:
        checkpoint_wal()
        print("💾 Данные сохранены")
    except:
        pass
    print("✅ Завершено.")
    sys.exit(0)

try:
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)
except:
    pass


# ============================================================
#  ЗАПУСК
# ============================================================

if __name__ == "__main__":
    print("🤖 Бот «VS Где Топливо» v1.13 запущен...")
    print(f"🐕 Watchdog: 5 минут тишины → перезапуск")
    print(f"💾 Кэш заправок: {CACHE_TTL} сек")
    print(f"⏰ Один поток: напоминания + очистка + WAL checkpoint")
    print(f"🎯 Компактные кнопки: карта + звезда в ряд")
    print(f"💡 Мотивирующие тексты вместо пустых колонок")
    bot.infinity_polling()