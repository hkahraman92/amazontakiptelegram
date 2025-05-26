import configparser
import sqlite3
import os

PRODUCTS_FILE = "C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\products.ini"
DB_FILE = "products.db"

def migrate_ini_to_db():
    if not os.path.exists(PRODUCTS_FILE):
        print("products.ini bulunamadı.")
        return

    config = configparser.ConfigParser()
    config.read(PRODUCTS_FILE, encoding='utf-8')

    if "PRODUCTS" not in config:
        print("'PRODUCTS' bölümü bulunamadı.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Tabloyu oluşturuyoruz. ID TEXT tipinde PRIMARY KEY olarak.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            url TEXT NOT NULL
        )
    ''')

    for key, value in config["PRODUCTS"].items():
        try:
            parts = value.split(",")
            if len(parts) >= 3:
                name = parts[0].strip()
                price = float(parts[1].strip())
                url = ",".join(parts[2:]).strip()  # URL içinde virgül varsa onu koruyalım
                cursor.execute('''
                    INSERT OR REPLACE INTO products (id, name, price, url) 
                    VALUES (?, ?, ?, ?)
                ''', (key, name, price, url))
                print(f"Aktarıldı: ID={key}, Name={name}, Price={price}, URL={url}")
            else:
                print(f"Uyarı: {key} satırı eksik veya yanlış formatta: {value}")
        except Exception as e:
            print(f"Hata: {key} -> {value} aktarılırken: {e}")

    conn.commit()
    conn.close()
    print("Tüm ürünler başarıyla aktarıldı.")

if __name__ == "__main__":
    migrate_ini_to_db()