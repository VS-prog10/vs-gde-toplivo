# ============================================================
#  АВТОБЭКАП БАЗЫ ДАННЫХ
#  Каждый день в 10:00 — копия fuel.db в backup/
# ============================================================

import os
import shutil
import time
from datetime import datetime


# Пути
if os.path.exists("/data"):
    DB_PATH = "/data/fuel.db"
    BACKUP_DIR = "/data/backup"
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(APP_DIR, "fuel.db")
    BACKUP_DIR = os.path.join(APP_DIR, "backup")


MAX_BACKUPS = 7  # хранить последние 7 копий


def do_backup():
    """Создаёт копию базы данных."""
    if not os.path.isfile(DB_PATH):
        print(f"⚠️ База не найдена: {DB_PATH}")
        return

    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    # Имя файла: fuel_2026-10-11_10-00.db
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_name = f"fuel_{ts}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    try:
        shutil.copy2(DB_PATH, backup_path)
        print(f"✅ Бэкап создан: {backup_name}")

        # Удаляем старые (оставляем 7 последних)
        backups = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith("fuel_") and f.endswith(".db")],
            reverse=True
        )
        for old in backups[MAX_BACKUPS:]:
            os.remove(os.path.join(BACKUP_DIR, old))
            print(f"🗑 Удалён старый бэкап: {old}")

    except Exception as e:
        print(f"❌ Ошибка бэкапа: {e}")


def run_scheduler():
    """Запускает планировщик: бэкап каждый день в 10:00."""
    print("⏰ Планировщик бэкапа запущен. Бэкап в 10:00 каждый день.")

    while True:
        try:
            now = datetime.now()
            # Если сейчас 10:00 (час = 10, минута = 0)
            if now.hour == 10 and now.minute == 0:
                print(f"⏰ Время бэкапа: {now.strftime('%d.%m.%Y %H:%M')}")
                do_backup()
                # Спим 60 секунд, чтобы не сработало несколько раз в одну минуту
                time.sleep(60)
            else:
                # Проверяем каждые 30 секунд
                time.sleep(30)
        except Exception as e:
            print(f"❌ Ошибка планировщика: {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_scheduler()