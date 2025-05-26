import sqlite3

conn = sqlite3.connect("products.db")
cursor = conn.cursor()

# Eski tabloyu sil (varsa)
cursor.execute("DROP TABLE IF EXISTS products")

# Yeni tabloyu oluştur (id otomatik artacak)
cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    price REAL,
    url TEXT
)
""")

conn.commit()
conn.close()

print("Veritabanı sıfırlandı ve yeni tablo oluşturuldu.")