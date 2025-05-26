import configparser
import os
import logging
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import __version__ as TG_VER
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
)
import aiofiles
import requests
from bs4 import BeautifulSoup
from filelock import FileLock
import sqlite3

# Loglama yapılandırması
logging.basicConfig(
    level=logging.INFO,  # Tüm log seviyelerini kaydet
    format='%(asctime)s - %(levelname)s - %(message)s',  # Zaman damgası, log seviyesi ve mesaj
    handlers=[
        logging.FileHandler("C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\bot_log.txt"),  # Logları dosyaya kaydet
        logging.StreamHandler()  # Aynı zamanda terminale de yaz
    ]
)

# Konfigürasyon dosya yolları
PRODUCTS_FILE = os.getenv("PRODUCTS_FILE", "C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\products.ini")
CONFIG_FILE = os.getenv("CONFIG_FILE", "C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\config.ini")

# Konfigürasyonu oku
config_reader = configparser.ConfigParser()
if not os.path.exists(CONFIG_FILE):
    logging.error(f"Configuration file '{CONFIG_FILE}' not found.")
    raise FileNotFoundError(f"Configuration file '{CONFIG_FILE}' not found.")
config_reader.read(CONFIG_FILE)

try:
    TELEGRAM_TOKEN = config_reader.get("TELEGRAM", "TELEGRAM_TOKEN")
    CHAT_ID = config_reader.get("TELEGRAM", "CHAT_ID")
except (configparser.NoSectionError, configparser.NoOptionError) as e:
    logging.error("Missing 'TELEGRAM' section or required options in config file.")
    raise KeyError("Missing 'TELEGRAM' section or required options in config file.")

API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# Telegram sürüm uyumluluğu kontrolü
if tuple(map(int, TG_VER.split('.'))) < (20, 0):
    logging.critical(
        f"This script is not compatible with your current PTB version {TG_VER}. Ensure you have at least PTB v20.")
    raise RuntimeError(
        f"This script is not compatible with your current PTB version {TG_VER}. Ensure you have at least PTB v20.")

# --- Yardımcı Fonksiyonlar --- #

def init_db():
    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            price REAL DEFAULT 0,
            lowest_price REAL DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

async def insert_product_to_db(name: str, url: str, price: float = 0) -> int:
    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (name, url, price, lowest_price) VALUES (?, ?, ?, ?)",
                   (name, url, price, price))
    conn.commit()
    item_id = cursor.lastrowid
    conn.close()
    logging.info(f"Product inserted: {name}, URL: {url}, ID: {item_id}, Price: {price}")
    return item_id

def is_valid_url(url: str) -> bool:
    """URL geçerliliğini kontrol eder."""
    parsed_url = urlparse(url)
    return bool(parsed_url.scheme and parsed_url.netloc)

def validate_input(input: str) -> bool:
    """/add_item komutunun geçerliliğini kontrol eder."""
    if not input or not input.startswith("/add_item"):
        logging.warning(f"Invalid input format: {input}")
        return False
    comma_index = input.find(",")
    if comma_index == -1:
        return is_valid_url(input[len("/add_item"):].strip())
    if comma_index == len(input) - 1:
        logging.warning(f"Invalid input format (trailing comma): {input}")
        return False
    return True

def read_value(input: str):
    """/add_item girişini okur ve ad ve URL'yi ayırır."""
    comma_index = input.find(",")
    if comma_index == -1:
        return "", input[len("/add_item"):].strip()
    return input[len("/add_item"):comma_index].strip(), input[comma_index + 1:].strip()

async def write_product_to_file(item_id: int, name: str, url: str) -> None:
    lock = FileLock(PRODUCTS_FILE + ".lock")
    config = configparser.ConfigParser()

    try:
        with lock:  # ✅ Doğru kullanım
            if os.path.exists(PRODUCTS_FILE):
                config.read(PRODUCTS_FILE)

            if not config.has_section("PRODUCTS"):
                config.add_section("PRODUCTS")

            config.set("PRODUCTS", str(item_id), f"{name},0,{url}")

            with open(PRODUCTS_FILE, "w") as f:
                config.write(f)

            logging.info(f"Added new product: {name}, URL: {url}")
    except Exception as e:
        logging.error(f"Error writing to file: {e}")

async def read_products() -> str:
    try:
        conn = sqlite3.connect("products.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, url, price, lowest_price FROM products")
        products = cursor.fetchall()
        conn.close()

        if not products:
            return "📭 Hiç ürün bulunamadı."

        # ID, isim, URL, son fiyat ve en düşük fiyatı göster
        return "\n\n".join([
            f"ID {pid}: {name}\nURL: {url}\nSon Fiyat: {price}₺\nEn Düşük Fiyat: {lowest_price}₺"
            for pid, name, url, price, lowest_price in products
        ])

    except Exception as e:
        logging.error(f"Error reading products from database: {e}")
        return "🚫 Ürünler okunurken bir hata oluştu."

# def get_last_item(products_file: str) -> int:
#     try:
#         config = configparser.ConfigParser()
#         config.read(products_file)
#         if not config.has_section("PRODUCTS"):
#             return 0
#         ids = [int(k) for k, _ in config.items("PRODUCTS")]
#         return max(ids) if ids else 0
#     except Exception as e:
#         logging.error(f"Error reading the last item ID: {e}")
#         return 0
# # Ana menü oluşturma fonksiyonu
def main_menu_keyboard():
    """Ana menü oluşturur."""
    keyboard = [
        [InlineKeyboardButton("🛒 Ürünleri Göster", callback_data="read_items")],
        [InlineKeyboardButton("➕ Ürün Ekle", callback_data="add_item")],
        [InlineKeyboardButton("❌ Ürün Sil", callback_data="remove_item")],
        [InlineKeyboardButton("ℹ️ Yardım", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Bot Komutları --- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot başlatıldığında hoş geldiniz mesajı gönderir ve ana menüyü gösterir."""
    await update.message.reply_text(
        "🛍️ Merhaba! Amazon fiyat takibi botuna hoş geldiniz.\n\n"
        "Fiyat takibi yapmak için ürünleri ekleyebilir, mevcut ürünlerin fiyatlarını kontrol edebilirsiniz.\n\n"
        "Başlamak için lütfen menüyü kullanın.",
        reply_markup=main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yardım mesajı gönderir."""
    help_message = """
    🌟 **Bot Komutları:**

    /start - Botu başlatır ve ana menüyü gösterir.
    /help - Bot hakkında yardım alırsınız.
    /read_items - Kayıtlı tüm ürünleri görüntüler.
    /add_item NAME,URL - Yeni bir ürün ekler.
    /remove_item ID - ID'ye sahip ürünü siler.

    🎯 **Öneriler:**
    - Ürün eklemek için "/add_item [Ürün Adı], [Ürün Linki]" komutunu kullanın.
    - Ürünleri görmek için "/read_items" komutunu kullanın.
    - Ürün silmek için "/remove_item [Ürün ID]" komutunu kullanın.
    """
    await update.message.reply_text(help_message, reply_markup=help_menu_keyboard())
    logging.info(f"Help command requested by user {update.message.from_user.id}")

# Ana menüdeki seçeneklere göre işlemler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcı menüye tıkladığında yapılan işlemler."""
    query = update.callback_query
    await query.answer()

    if query.data == "read_items":
        # Ürünleri okuma fonksiyonu
        await query.edit_message_text("Ürünler yükleniyor...")
        await read_items(update, context)

    elif query.data == "add_item":
        await query.edit_message_text("Yeni ürün eklemek için /add_item [Ad], [Link] komutunu kullanın.")

    elif query.data == "remove_item":
        await query.edit_message_text("Ürün silmek için /remove_item [ID] komutunu kullanın.")

    elif query.data == "help":
        await query.edit_message_text("Yardım menüsü", reply_markup=help_menu_keyboard())

    elif query.data == "back_to_main_menu":
        await query.edit_message_text("Ana menüye dönülüyor...", reply_markup=main_menu_keyboard())
    elif query.data == "commands":
        # Komutlar butonuna tıklanıldığında gösterilecek mesaj
        command_message = """
            🛠️ **Bot Komutları:**

            /start - Botu başlatır ve ana menüyü gösterir.
            /help - Bot hakkında yardım alırsınız.
            /read_items - Kayıtlı tüm ürünleri görüntüler.
            /add_item NAME,URL - Yeni bir ürün ekler.
            /remove_item ID - ID'ye sahip ürünü siler.

            🎯 **Öneriler:**
            - Ürün eklemek için "/add_item [Ürün Adı], [Ürün Linki]" komutunu kullanın.
            - Ürünleri görmek için "/read_items" komutunu kullanın.
            - Ürün silmek için "/remove_item [Ürün ID]" komutunu kullanın.
            """
        await query.edit_message_text(command_message)
def help_menu_keyboard():
    """Yardım menüsü oluşturur."""
    keyboard = [
        [InlineKeyboardButton("📝 Komutlar", callback_data="commands")],
        [InlineKeyboardButton("🔙 Geri", callback_data="back_to_main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def update_price(product_id: int, new_price: float):
    conn = sqlite3.connect("products.db")
    cursor = conn.cursor()

    cursor.execute("SELECT lowest_price FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return

    current_lowest = row[0]

    # Eğer mevcut en düşük fiyat 0'sa ya da yeni fiyat ondan düşükse güncelle
    if current_lowest == 0 or (new_price > 0 and new_price < current_lowest):
        lowest_price = new_price
    else:
        lowest_price = current_lowest

    cursor.execute("""
        UPDATE products SET price = ?, lowest_price = ? WHERE id = ?
    """, (new_price, lowest_price, product_id))
    conn.commit()
    conn.close()

async def read_items(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ürünleri okur ve görüntüler."""
    query = update.callback_query  # CallbackQuery nesnesini kullanıyoruz.

    # Eğer message kısmında hata olmaması için callback_query üzerinden erişiyoruz
    logging.info(f"Read items command requested by user {query.from_user.id}")

    # Ürünlerin okunması için işlemler yapılır
    items = await read_products()  # Ürünleri okuma fonksiyonu burada çağrılacak
    await query.edit_message_text(items)  # Yanıtı callback query üzerine gönderiyoruz.


async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kullanıcı bir Amazon linki ve ürün adı girerek ürünü ekler."""
    input_text = update.message.text
    logging.info(f"Add item command received from user {update.message.from_user.id}: {input_text}")

    # "/add_item" komutundan sonra gelen kısmı ayırıyoruz (item adı ve URL)
    if input_text.startswith("/add_item "):
        input_text = input_text[len("/add_item "):].strip()

    # Virgülle ayırarak item adı ve URL'yi alıyoruz
    comma_index = input_text.find(",")
    if comma_index == -1:
        await update.message.reply_text("❗ Lütfen ürün adını ve URL'yi virgülle ayırarak girin. Örnek: /add_item ITEM NAME, https://amazon.com/...")
        logging.warning(f"Invalid input format (missing comma): {input_text}")
        return

    # Ürün adını ve URL'yi al
    item_name = input_text[:comma_index].strip()
    url = input_text[comma_index + 1:].strip()

    # Amazon URL'si olup olmadığını kontrol et
    if not any(url.startswith(domain) for domain in
               ["https://www.amazon.com/", "https://amzn.eu/", "https://www.amazon.com.tr/"]):
        await update.message.reply_text("❗ Lütfen geçerli bir Amazon ürün linki gönderin.")
        logging.warning(f"Invalid URL provided by user {update.message.from_user.id}: {url}")
        return

    # URL geçerli ise, ürünü ekleyelim
    last_item_id = get_last_item(PRODUCTS_FILE)
    #new_item_id = last_item_id + 1
    new_item_id = await insert_product_to_db(item_name, url, price=0)
    await write_product_to_file(new_item_id, item_name, url)

    await update.message.reply_text(f"✅ Ürün '{item_name}' başarıyla eklendi. ID: {new_item_id}")
    logging.info(f"Product '{item_name}' added by user {update.message.from_user.id}")

def validate_input(input: str) -> bool:
    """'/add_item ITEM NAME, URL' formatında girişin geçerliliğini kontrol eder."""
    if not input or not input.startswith("/add_item"):
        logging.warning(f"Invalid input format: {input}")
        return False
    comma_index = input.find(",")
    if comma_index == -1:
        logging.warning(f"Invalid input format (missing comma): {input}")
        return False
    return True

async def remove_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    input_text = update.message.text.strip()
    try:
        item_id = int(input_text[len("/remove_item"):].strip())
    except ValueError:
        await update.message.reply_text("❗ Geçersiz ID formatı. Örnek: /remove_item 2")
        return

    try:
        conn = sqlite3.connect("products.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (item_id,))
        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows == 0:
            await update.message.reply_text("❌ Bu ID'ye sahip bir ürün bulunamadı.")
        else:
            await update.message.reply_text(f"🗑️ {item_id} numaralı ürün silindi.")
    except Exception as e:
        logging.error(f"Veritabanından silinirken hata: {e}")
        await update.message.reply_text("🛑 Veritabanı hatası.")

def main():
    """Botu başlatır ve handler'ları ekler."""
    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add_item", add_item))
    application.add_handler(CommandHandler("remove_item", remove_item))
    application.add_handler(CommandHandler("read_items", read_items))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()

if __name__ == "__main__":
    main()
