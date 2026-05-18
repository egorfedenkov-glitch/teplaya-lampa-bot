import sqlite3
import csv
import sys
import os

# Путь к базе данных (на Render она будет в той же папке)
DB_PATH = 'nostalgia.db'

def add_content_from_csv(csv_file):
    """Добавляет контент из CSV-файла в базу данных."""
    if not os.path.exists(csv_file):
        print(f"Ошибка: файл {csv_file} не найден.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Убедимся, что таблица существует
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT,
            media_url TEXT,
            caption TEXT,
            era TEXT,
            author_id INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
    ''')

    added = 0
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Ожидаем колонки: media_type, media_url, caption, era, status
            media_type = row.get('media_type', 'text')
            media_url = row.get('media_url', '')
            caption = row.get('caption', '')
            era = row.get('era', 'unknown')
            status = row.get('status', 'approved')  # по умолчанию approved

            # Пропускаем пустые строки
            if not caption and not media_url:
                continue

            cursor.execute('''
                INSERT INTO content (media_type, media_url, caption, era, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (media_type, media_url, caption, era, status))
            added += 1

    conn.commit()
    conn.close()
    print(f"✅ Добавлено записей: {added} из файла {csv_file}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Использование: python add_content.py <файл.csv>")
        sys.exit(1)
    add_content_from_csv(sys.argv[1])