import configparser
import os
import logging
from urllib.parse import urlparse
from telegram.error import BadRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import __version__ as TG_VER
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
)
import sqlite3

# Loglama yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\bot_log.txt"),
        logging.StreamHandler()
    ]
)

# Konfigürasyon dosya yolu
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
    logging.info("Database initialized and table 'products' checked/created successfully.")

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

async def read_products() -> list[str]:
    """Ürünleri okur ve metinleri Telegram'ın limitlerine göre böler."""
    try:
        conn = sqlite3.connect("products.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, url, price, lowest_price FROM products")
        products = cursor.fetchall()
        conn.close()

        if not products:
            return ["📭 Hiç ürün bulunamadı."]

        message_chunks = []
        current_chunk = ""
        max_chunk_length = 3500 # Telegram'ın 4096 karakter limitinden biraz daha az

        for pid, name, url, price, lowest_price in products:
            product_line = (
                f"ID {pid}: {name}\n"
                f"URL: {url}\n"
                f"Son Fiyat: {price}₺\n"
                f"En Düşük Fiyat: {lowest_price}₺\n\n"
            )

            if len(current_chunk) + len(product_line) > max_chunk_length:
                message_chunks.append(current_chunk.strip()) # Önceki chunk'ı ekle
                current_chunk = product_line # Yeni chunk'ı başlat
            else:
                current_chunk += product_line

        if current_chunk: # Son chunk'ı ekle
            message_chunks.append(current_chunk.strip())

        return message_chunks

    except Exception as e:
        logging.error(f"Error reading products from database: {e}")
        return ["🚫 Ürünler okunurken bir hata oluştu."]


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
        "🛍️ Merhaba! Fiyat takip botuna hoş geldiniz.\n\n"
        "Fiyat takibi yapmak için ürünleri ekleyebilir, mevcut ürünlerin fiyatlarını kontrol edebilirsiniz.\n\n"
        "Başlamak için lütfen menüyü kullanın.",
        reply_markup=main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yardım mesajı gönderir."""
    # YENİ KOMUTUN GÜNCELLENMİŞ AÇIKLAMASI
    help_message = """
    🌟 **Bot Komutları:**

    /start - Botu başlatır ve ana menüyü gösterir.
    /help - Bot hakkında yardım alırsınız.
    /read_items - Kayıtlı tüm ürünleri görüntüler.
    /add_item [Ad],[Link] - Yeni bir ürün ekler.
    /remove_item [ID] - ID'ye sahip ürünü siler.
    /setinterval [Min Ürün] [Max Ürün] [Min Döngü] [Max Döngü] - Zamanlamayı ayarlar.

    🎯 **Zamanlama Ayarı Örneği:**
    `/setinterval 3 5 300 400`
    Bu komut:
    - Ürünler arası beklemeyi 3-5 saniye yapar.
    - Tüm ürünler bittikten sonraki ana beklemeyi (döngü) 300-400 saniye (5-6.6 dakika) yapar.
    """
    await update.message.reply_text(help_message, reply_markup=main_menu_keyboard())
    logging.info(f"Help command requested by user {update.message.from_user.id}")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcı menüye tıkladığında yapılan işlemler."""
    query = update.callback_query
    await query.answer()  # İşlemin alındığını belirtmek için

    try:
        if query.data == "read_items":
            await query.edit_message_text("Ürünler yükleniyor...")
            items_chunks = await read_products()

            if items_chunks:
                await query.edit_message_text(items_chunks[0], reply_markup=main_menu_keyboard())
                for i in range(1, len(items_chunks)):
                    await query.message.reply_text(items_chunks[i])
            else:
                await query.edit_message_text("📭 Hiç ürün bulunamadı.", reply_markup=main_menu_keyboard())

        elif query.data == "add_item":
            await query.edit_message_text(
                "Yeni ürün eklemek için /add_item [Ad], [Link] komutunu kullanın.",
                reply_markup=main_menu_keyboard()
            )

        elif query.data == "remove_item":
            await query.edit_message_text(
                "Ürün silmek için /remove_item [ID] komutunu kullanın.",
                reply_markup=main_menu_keyboard()
            )

        elif query.data == "help":
            await query.edit_message_text("Yardım menüsü", reply_markup=help_menu_keyboard())

        elif query.data == "back_to_main_menu":
            await query.edit_message_text("Ana menüye dönülüyor...", reply_markup=main_menu_keyboard())

        elif query.data == "commands":
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
            await query.edit_message_text(command_message, reply_markup=help_menu_keyboard())

    except BadRequest as e:
        if "Message is not modified" in str(e):
            # Mesaj değişmediyse bu hatayı görmezden gel, program çökmesin.
            logging.info("Ignoring 'Message is not modified' error.")
            pass
        else:
            # Eğer başka bir BadRequest hatası ise logla.
            logging.error(f"An unexpected BadRequest occurred: {e}")

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scraper'ın zamanlama ayarlarını günceller."""
    user_id = update.message.from_user.id
    logging.info(f"Set interval command received from user {user_id}: {' '.join(context.args)}")

    # Kullanım talimatını 4 parametreye göre güncelle
    usage_text = (
        "❗ Hatalı kullanım.\n"
        "Lütfen **dört sayı** girin:\n\n"
        "1. Min ürün arası bekleme (sn)\n"
        "2. Max ürün arası bekleme (sn)\n"
        "3. Min ana döngü bekleme (sn)\n"
        "4. Max ana döngü bekleme (sn)\n\n"
        "Örnek: `/setinterval 3 5 300 400`"
    )

    if len(context.args) != 4:
        await update.message.reply_text(usage_text)
        return

    try:
        # Dört parametreyi de al
        min_s, max_s, min_r, max_r = map(float, context.args)

        # Tüm süreleri kontrol et
        if min_s <= 0 or max_s <= 0 or min_r <= 0 or max_r <= 0:
            await update.message.reply_text("❗ Süreler sıfırdan büyük olmalıdır.")
            return

        if min_s > max_s or min_r > max_r:
            await update.message.reply_text("❗ Minimum süreler, ilgili maksimum sürelerden büyük olamaz.")
            return

        # Config dosyasını oku ve güncelle
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)

        if not config.has_section('TIMING'):
            config.add_section('TIMING')

        # Dört değeri de ayarla
        config.set('TIMING', 'MIN_SLEEP', str(min_s))
        config.set('TIMING', 'MAX_SLEEP', str(max_s))
        config.set('TIMING', 'MIN_RUN_EVERY', str(min_r))
        config.set('TIMING', 'MAX_RUN_EVERY', str(max_r))

        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)

        # Onay mesajını güncelle
        await update.message.reply_text(
            f"✅ Zamanlama başarıyla güncellendi!\n\n"
            f"🛍️ Her ürün arası bekleme: **{min_s:.1f} - {max_s:.1f} saniye**\n"
            f"🔄 Döngüler arası bekleme: **{min_r:.1f} - {max_r:.1f} saniye**\n\n"
            f"Bu ayarlar, scraper'ın bir sonraki döngüsünde aktif olacaktır."
        )
        logging.info(f"Interval updated by user {user_id}. SLEEP: {min_s}-{max_s}s, RUN_EVERY: {min_r}-{max_r}s.")

    except ValueError:
        await update.message.reply_text("❗ Lütfen geçerli sayılar girin.\n" + usage_text)
    except Exception as e:
        logging.error(f"Error updating config file: {e}")
        await update.message.reply_text("❌ Ayarlar güncellenirken bir hata oluştu.")


def help_menu_keyboard():
    """Yardım menüsü oluşturur."""
    keyboard = [
        [InlineKeyboardButton("📝 Komutlar", callback_data="commands")],
        [InlineKeyboardButton("🔙 Geri", callback_data="back_to_main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def read_items(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ürünleri okur ve görüntüler."""
    source = update.callback_query if update.callback_query else update.message
    user_id = source.from_user.id
    is_callback = bool(update.callback_query)

    logging.info(f"Read items command requested by user {user_id}")

    items_chunks = await read_products() # Artık bir liste dönüyor

    if is_callback:
        # Callback query ise ilk mesajı edit_message_text ile gönder
        if items_chunks:
            await source.edit_message_text(items_chunks[0], reply_markup=main_menu_keyboard())
            # Kalan mesajları reply_text ile gönder
            for i in range(1, len(items_chunks)):
                await source.message.reply_text(items_chunks[i])
        else:
            await source.edit_message_text("📭 Hiç ürün bulunamadı.", reply_markup=main_menu_keyboard())
    else:
        # Normal mesaj ise ilk mesajı reply_text ile gönder
        if items_chunks:
            await source.reply_text(items_chunks[0], reply_markup=main_menu_keyboard())
            # Kalan mesajları reply_text ile gönder
            for i in range(1, len(items_chunks)):
                await source.reply_text(items_chunks[i])
        else:
            await source.reply_text("📭 Hiç ürün bulunamadı.", reply_markup=main_menu_keyboard())

async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kullanıcı bir ürün linki ve ürün adı girerek ürünü ekler."""
    input_text = update.message.text
    logging.info(f"Add item command received from user {update.message.from_user.id}: {input_text}")

    if input_text.startswith("/add_item "):
        input_text = input_text[len("/add_item "):].strip()

    comma_index = input_text.find(",")
    if comma_index == -1:
        await update.message.reply_text("❗ Lütfen ürün adını ve URL'yi virgülle ayırarak girin. Örnek: /add_item ÜRÜN ADI, https://trendyol.com/urun-linki", reply_markup=main_menu_keyboard())
        logging.warning(f"Invalid input format (missing comma): {input_text}")
        return

    item_name = input_text[:comma_index].strip()
    url = input_text[comma_index + 1:].strip()

    # Desteklenen domain'leri içeren bir liste oluşturun
    # Amazon ve Trendyol domainlerini ekliyoruz
    supported_domains = [
        "https://www.amazon.com/", "https://amzn.eu/", "https://www.amazon.com.tr/", "https://amzn.to/",
        "https://www.trendyol.com/",
        "https://www.hepsiburada.com/",
        "https://www.mediamarkt.com.tr/"
    ]

    # Gönderilen URL'nin desteklenen domainlerden biriyle başlayıp başlamadığını kontrol edin
    if not any(url.startswith(domain) for domain in supported_domains):
        # --- DEĞİŞİKLİK 2: Hata mesajı güncellendi ---
        await update.message.reply_text(
            "❗ Lütfen geçerli bir **Amazon, Trendyol , Hepsiburada veya MediaMarkt** ürün linki gönderin.",
            reply_markup=main_menu_keyboard()
        )
        logging.warning(f"Unsupported URL provided by user {update.message.from_user.id}: {url}")
        return
    # URL geçerli ise, ürünü ekleyelim
    new_item_id = await insert_product_to_db(item_name, url, price=0)

    await update.message.reply_text(f"✅ Ürün '{item_name}' başarıyla eklendi. ID: {new_item_id}", reply_markup=main_menu_keyboard())
    logging.info(f"Product '{item_name}' added by user {update.message.from_user.id}")

async def remove_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    input_text = update.message.text.strip()
    try:
        item_id = int(input_text[len("/remove_item"):].strip())
    except ValueError:
        await update.message.reply_text("❗ Geçersiz ID formatı. Örnek: /remove_item 2", reply_markup=main_menu_keyboard())
        return

    try:
        conn = sqlite3.connect("products.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (item_id,))
        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()

        if affected_rows == 0:
            await update.message.reply_text("❌ Bu ID'ye sahip bir ürün bulunamadı.", reply_markup=main_menu_keyboard())
        else:
            await update.message.reply_text(f"🗑️ {item_id} numaralı ürün silindi.", reply_markup=main_menu_keyboard())
            logging.info(f"Product ID {item_id} removed by user {update.message.from_user.id}")
    except Exception as e:
        logging.error(f"Veritabanından silinirken hata: {e}")
        await update.message.reply_text("🛑 Veritabanı hatası.", reply_markup=main_menu_keyboard())

def main():
    """Botu başlatır ve handler'ları ekler."""
    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add_item", add_item))
    application.add_handler(CommandHandler("remove_item", remove_item))
    application.add_handler(CommandHandler("read_items", read_items))
    application.add_handler(CommandHandler("setinterval", set_interval))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()

if __name__ == "__main__":
    main()