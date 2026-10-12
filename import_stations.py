# ============================================================
#  ИМПОРТ ЗАПРАВОК ИЗ stations.json В БАЗУ БОТА (fuel.db)
# ============================================================

import json
import sqlite3
import os


# Пути к файлам
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIONS_FILE = os.path.join(APP_DIR, "stations.json")
# Если на хостинге (есть папка /data) — используем её
if os.path.exists("/data"):
    DB_FILE = "/data/fuel.db"
else:
    DB_FILE = os.path.join(APP_DIR, "fuel.db")


def import_stations():
    """Загружает заправки из stations.json в базу данных."""
    
    # Проверяем, что файл существует
    if not os.path.isfile(STATIONS_FILE):
        print(f"❌ Файл {STATIONS_FILE} не найден!")
        return
    
    # Читаем JSON
    try:
        with open(STATIONS_FILE, "r", encoding="utf-8") as f:
            stations = json.load(f)
    except Exception as e:
        print(f"❌ Ошибка чтения JSON: {e}")
        return
    
    print(f"📂 Найдено в файле: {len(stations)} заправок")
    
    # Подключаемся к базе
    if not os.path.isfile(DB_FILE):
        print(f"❌ База данных {DB_FILE} не найдена! Запусти bot.py хотя бы раз.")
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Проверяем, что таблица stations существует
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stations'")
    if not cursor.fetchone():
        print("❌ Таблица 'stations' не найдена! Запусти bot.py хотя бы раз.")
        conn.close()
        return
    
    # Считаем, сколько заправок уже в базе
    cursor.execute("SELECT COUNT(*) FROM stations")
    before_count = cursor.fetchone()[0]
    
    # Загружаем заправки
    added = 0
    skipped = 0
    
    for station in stations:
        name = station.get("name", "")
        address = station.get("address", "")
        city = station.get("city", "")
        lat = station.get("lat")
        lon = station.get("lon")
        services = station.get("services", "")
        
        # Проверяем обязательные поля
        if not name or lat is None or lon is None:
            print(f"⚠️ Пропускаю заправку без обязательных полей: {station}")
            skipped += 1
            continue
        
        # Проверяем, есть ли уже такая заправка (по координатам)
        cursor.execute("""
            SELECT id FROM stations
            WHERE ABS(lat - ?) < 0.0001 AND ABS(lon - ?) < 0.0001
        """, (lat, lon))
        
        if cursor.fetchone():
            skipped += 1
            continue
        
        # Добавляем
        cursor.execute("""
            INSERT INTO stations (name, address, city, lat, lon, services, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (name, address, city, lat, lon, services, None))
        
        added += 1
    
    conn.commit()
    
    # Считаем итог
    cursor.execute("SELECT COUNT(*) FROM stations")
    after_count = cursor.fetchone()[0]
    
    conn.close()
    
    print()
    print("=" * 60)
    print(f"  ✅ Добавлено: {added}")
    print(f"  ⚠️ Пропущено (дубликаты): {skipped}")
    print(f"  📊 Было в базе: {before_count}")
    print(f"  📊 Стало в базе: {after_count}")
    print("=" * 60)


if __name__ == "__main__":
    import_stations()