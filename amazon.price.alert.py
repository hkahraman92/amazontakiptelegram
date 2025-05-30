import requests
from bs4 import BeautifulSoup
import time
import configparser
import telegram
import asyncio
import json
import requests
import traceback
import logging
import os
import random
from filelock import FileLock
import sqlite3
from logging.handlers import TimedRotatingFileHandler

#PARAMS
SLEEP_TIME=random.uniform(1, 2.5) #between attemps to fetch the price
RUN_EVERY=random.uniform(45, 60) #seconds = 0.5 minutes
PRODUCTS_FILE= 'C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\products.ini'
CONFIG_FILE = 'C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\config.ini'
PRICE_DIFFERENCE=1 #1 dollar, min price difference to notify
MAX_PRICE_RETRIES=30

# Günlük dönen log ayarı (her gün yenilenir, 30 gün saklanır)
log_file_path = 'C:\\Users\\Harun\\PycharmProjects\\amazonpricealertTelegramBot\\logs\\amazon_price_alert.log'
os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

handler = TimedRotatingFileHandler(log_file_path, when='midnight', interval=1, backupCount=30, encoding='utf-8')
handler.suffix = "%Y-%m-%d"  # Dosya ismine tarih eklensin
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)


# Read params from config file
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

TELEGRAM_TOKEN = config.get('TELEGRAM', 'TELEGRAM_TOKEN')
CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
apiURL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'

def init_db():
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, -- Değişiklik burada
                name TEXT,
                price REAL DEFAULT 0,
                lowest_price REAL DEFAULT 0,
                url TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logging.info("Database initialized and table 'products' checked/created successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")


def get_name(soup, url):
    """Verilen URL'den ürün adını çeker."""
    title_text = ""
    try:
        title_element = None

        if "suarezclothing.com" in url:
            container = soup.find("h1", attrs={"class":'vtex-store-components-3-x-productNameContainer mv0 t-heading-4'})
            if container:
                span_element = container.find("span", attrs={"class":'vtex-store-components-3-x-productBrand'})
                if span_element:
                    title_text = span_element.text
        elif "amazon.com" in url or "amzn" in url:
            title_element = soup.find("span", attrs={"id":'productTitle'})
        elif "trendyol.com" in url:
            title_element = soup.find("h1", attrs={"class": "pr-new-br"})
        # cyclewear.com.co ve bikeexchange.com.co kaldırıldı
        elif "bikehouse.co" in url:
            title_element = soup.find("h1", attrs={"class":'product_title entry-title'})

        if title_element:
            title_text = title_element.text.strip().replace(",", " ")

    except AttributeError:
        logging.warning(f"AttributeError in get_name for URL: {url}. Title element not found or structure changed.")
        title_text = ""
    except Exception as e:
        logging.error(f"Unexpected error in get_name for URL {url}: {e}")
        title_text = ""

    return title_text

# get price and name of item (title)
def get_price_name(name, url):
    """Verilen URL'den ürün fiyatını ve adını (boşsa) çeker."""
    price = "-1"
    logging.info(f"Fetching price for: {url}")

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/125.0",
    ]
    HEADERS = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Connection error for {url}: {e}")
        return "-1", name

    soup = BeautifulSoup(response.content, "lxml")

    if not name:
        name = get_name(soup, url)
        logging.info(f"Product Name: {name}")

    if "amazon" in url or "amzn" in url:
        unavailable_span = soup.find("span", class_="a-size-medium a-color-success")
        if unavailable_span and "şu anda mevcut değil" in unavailable_span.text.lower():
            logging.info(f"Product {name or 'Unknown'} is currently unavailable on Amazon.")
            return "-2", name

        # Önce a-offscreen sınıfını dene (genellikle en doğru fiyat buradadır)
        price_offscreen = soup.find("span", class_="a-offscreen")
        if price_offscreen:
            raw_price = price_offscreen.get_text(strip=True)
            cleaned_price = raw_price.replace('£', '').replace('$', '').replace('€', '').replace(',', '.').strip()
            try:
                float(cleaned_price)
                logging.info(f"Found price for {name or 'Unknown'} using a-offscreen: {cleaned_price}")
                return cleaned_price, name
            except ValueError:
                logging.warning(f"Could not convert price '{cleaned_price}' from a-offscreen to float for {url}. Trying alternative method.")

        # Eğer a-offscreen başarısız olursa, mevcut a-price span mantığını kullan
        price_span = soup.find("span", attrs={"class": "a-price aok-align-center reinventPricePriceToPayMargin priceToPay"})

        if price_span:
            price_whole = price_span.find("span", class_="a-price-whole")
            price_fraction = price_span.find("span", class_="a-price-fraction")

            if price_whole and price_fraction:
                price = price_whole.text.strip() + "." + price_fraction.text.strip()
                price = price.replace('.', '').replace(',', '.') # Binlik ayırıcıları temizle, ondalık ayırıcıyı dönüştür
                logging.info(f"Found price for {name or 'Unknown'} using a-price span: {price}")
                return price, name
            else:
                logging.warning(f"Price whole or fraction not found within a-price span for {url}.")
        else:
            logging.warning(f"a-price span not found for {url}.")

        logging.error(f"Failed to find any price for Amazon URL: {url}")
        return "-1", name

    elif "trendyol.com" in url:
        price_span = soup.find("span", class_="prc-dsc")
        if price_span:
            price = price_span.text.strip()
            # "TL" gibi metinleri ve binlik ayırıcıları temizle, ondalık nokta için virgülü dönüştür
            price = price.replace('TL', '').replace(' ', '').replace('.', '').replace(',', '.').strip()
            logging.info(f"Found price for {name or 'Unknown'} on Trendyol: {price}")
        else:
            logging.warning(f"Price span with class 'prc-dsc' not found for Trendyol URL: {url}")
            price = "-1"
        return price, name

    elif "suarezclothing.com" in url:
        script_tag = soup.find('script', type='application/ld+json')
        if script_tag:
            json_data = json.loads(script_tag.string)
            if 'offers' in json_data and 'lowPrice' in json_data['offers']:
                price = str(json_data['offers']['lowPrice']).replace('.','')
            elif 'offers' in json_data and 'price' in json_data['offers']:
                 price = str(json_data['offers']['price']).replace('.','')
            else:
                logging.warning(f"Price not found in JSON-LD for SuarezClothing: {url}")
                price = "-1"
        else:
            logging.warning(f"JSON-LD script tag not found for SuarezClothing: {url}")
            price = "-1"
    # cyclewear.com.co ve bikeexchange.com.co kaldırıldı
    elif "bikehouse.co" in url:
       price1 = soup.find('span', class_='price_varies')
       if price1:
           money_span = price1.find('ins').find('span', class_='money')
           if money_span:
               price = money_span.text
           else:
               logging.warning(f"Money span not found within price_varies for Bikehouse: {url}")
               price = "-1"
       else:
           money_span = soup.find('span', class_='money')
           if money_span:
               price = money_span.text
           else:
               logging.warning(f"Money span not found for Bikehouse: {url}")
               price = "-1"
       price = price.replace('.','')

    # Tüm siteler için son fiyat temizleme ve float'a dönüştürme
    # Trendyol için fiyat zaten yukarıda temizlendiği için tekrar işlem yapmıyoruz
    if price != "-1" and price != "-2":
        try:
            if not "trendyol.com" in url: # Trendyol için zaten temizlendi
                 price = price.replace('£', '').replace('$', '').replace('€', '').replace(',', '.').strip()
            price = float(price) # Son olarak float'a dönüştür
        except ValueError:
            logging.error(f"Final price conversion failed for {url}. Raw price: {price}")
            price = "-1" # Geçersiz fiyat formatı

    return str(price), name # Fiyatı string olarak döndürüyoruz

async def send_telegram_notification(item, previous_price, current_price, lowest_price, url):
    """Telegram'a fiyat değişimi, stok dışı veya stokta tekrar bildirimi gönderir."""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    message = ""
    if -2.0 == current_price: # Ürün stok dışına çıktı
        message = f"❌ <b>{item}</b> artık stokta yok!\n"
        message += f"🔗 <a href='{url}'>Ürünü Görüntüle</a>"
    elif previous_price == "STOKTA YOKTU": # Ürün tekrar stokta (özel durum)
        message = f"✅ <b>{item}</b> tekrar stokta!\n"
        message += f"💰 Güncel Fiyat: <b>{current_price}₺</b>\n"
        message += f"🏷️ En Düşük Fiyat: <b>{lowest_price}₺</b>\n"
        message += f"🔗 <a href='{url}'>Ürünü Görüntüle</a>"
    else: # Normal fiyat değişimi veya stokta olup fiyatı değişmeyen durum
        message = f"💸 <b>{item}</b> için fiyat değişti!\n"
        message += f"📉 Önceki Fiyat: <b>{previous_price}₺</b>\n"
        message += f"💰 Yeni Fiyat: <b>{current_price}₺</b>\n"
        message += f"🏷️ En Düşük Fiyat: <b>{lowest_price}₺</b>\n"
        message += f"🔗 <a href='{url}'>Ürünü Görüntüle</a>"

    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML', disable_web_page_preview=False)
    except Exception as e:
        logging.error(f"Error occurred while sending notification: {e}")
        traceback.print_exc()

async def check_price_change(id: int, name: str, previous_price: float, url: str):
    """Ürünün fiyat değişimini kontrol eder ve veritabanını günceller."""
    try:
        current_price_str, name_new = get_price_name(name, url)

        if current_price_str.strip() == "" or current_price_str.isspace():
            logging.warning(f"Current price is empty or whitespace for {name or name_new} (ID: {id}). Skipping update.")
            return False

        try:
            current_price = float(current_price_str)
        except ValueError:
            logging.error(f"Invalid current price format '{current_price_str}' for {name or name_new} (ID: {id}). Skipping update.")
            return False

        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('SELECT lowest_price FROM products WHERE id = ?', (id,))
        row = cursor.fetchone()
        conn.close()

        stored_lowest_price = row[0] if row and row[0] is not None else 0.0

        new_lowest_price = stored_lowest_price
        if current_price > 0: # Sadece geçerli (pozitif) fiyatlar için lowest_price'ı güncelle
            if stored_lowest_price == 0.0 or current_price < stored_lowest_price:
                new_lowest_price = current_price
                logging.info(f"New lowest price for {name_new or name} (ID: {id}): {new_lowest_price}₺")
            else:
                logging.info(f"Lowest price remains unchanged for {name_new or name} (ID: {id}): {new_lowest_price}₺")
        else: # Eğer current_price -1 veya -2 ise (stok yok/hata), lowest_price'ı değiştirmeyiz
            logging.info(f"Current price ({current_price}₺) is not positive. Lowest price will not be updated for {name_new or name} (ID: {id}).")

        # --- YENİ MANTIK: Ürün tekrar stokta mı? ---
        # Eğer önceki fiyat -2.0 (stokta yok) ve şimdiki fiyat pozitifse (stokta)
        if previous_price == -2.0 and current_price > 0:
            logging.info(f"Product {name_new} (ID: {id}) is back in stock! Current price: {current_price}₺.")
            await send_telegram_notification(name_new, "STOKTA YOKTU", current_price, new_lowest_price, url)
            update_product(id, name_new, current_price, url, new_lowest_price) # Veritabanını güncelleyelim
            return True # Bildirim gönderildi ve işlem tamamlandı

        # Stokta yok veya hata durumu bildirimi (sadece -2.0 durumunda, -1 için bildirim yok)
        if current_price == -1: # Fiyat çekmede genel hata
            logging.error(f"Error fetching price for {name or name_new} (ID: {id}). No price update.")
            return False
        elif current_price == -2: # Ürün stok dışına çıktı
            logging.info(f"Product {name or name_new} (ID: {id}) is currently unavailable.")
            if previous_price > 0: # Eğer daha önce stokta ve fiyatı biliniyorsa bildir
                await send_telegram_notification(name_new, previous_price, current_price, new_lowest_price, url)
            update_product(id, name_new, current_price, url, new_lowest_price) # Veritabanını -2.0 ile güncelleyelim
            return True

        # Fiyat değişimi kontrolü ve bildirim (sadece geçerli fiyatlar için)
        if current_price != previous_price:
            if abs(current_price - previous_price) >= PRICE_DIFFERENCE:
                logging.info(f"Price has changed for {name_new} from {previous_price}₺ to {current_price}₺. Sending notification.")
                await send_telegram_notification(name_new, previous_price, current_price, new_lowest_price, url)
            else:
                logging.info(f"Price changed for {name_new}, but below notification threshold ({PRICE_DIFFERENCE}₺). Previous: {previous_price}₺, Current: {current_price}₺")
            update_product(id, name_new, current_price, url, new_lowest_price)
        else:
            logging.info(f"Price has not changed for {name_new}. Still {current_price}₺.")
            update_product(id, name_new, current_price, url, new_lowest_price) # Fiyat değişmese bile en düşük fiyat ve isim güncel olsun

        return True

    except requests.exceptions.RequestException as exc:
        logging.error(f"Network or request error for {name or name_new} (ID: {id}): {exc}")
        return False
    except Exception as exc:
        logging.exception(f"An unexpected error occurred in check_price_change for {name or name_new} (ID: {id}): {exc}")
        return False
def get_all_products():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, price, lowest_price, url FROM products')
    products = cursor.fetchall()
    conn.close()
    return products

def update_product(id: int, name: str, price: float, url: str, lowest_price_from_checker: float):
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()

        # lowest_price_from_checker parametresi zaten check_price_change'den doğru hesaplanmış olarak gelmelidir.
        # Bu yüzden burada tekrar hesaplamaya gerek yoktur.
        # Eğer yine de emin değilseniz ve bu fonksiyonun kendi başına da tutarlı olmasını isterseniz,
        # aşağıdaki blok tekrar lowest_price'ı hesaplayabilir, ancak bu durumda check_price_change'deki
        # hesaplamayı kaldırmak tutarlılık açısından daha iyi olur.
        # Basitlik ve sorumluluk ayrımı için, bu fonksiyonun sadece kendisine verilen değerleri yazmasını öneririm.

        cursor.execute('''
            INSERT INTO products (id, name, price, lowest_price, url)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = ?,          -- name = excluded.name yerine doğrudan parametreyi kullan
                price = ?,         -- price = excluded.price yerine doğrudan parametreyi kullan
                lowest_price = ?,  -- BURADA CRUCIAL DEĞİŞİKLİK: lowest_price_from_checker'ı kullan
                url = ?            -- url = excluded.url yerine doğrudan parametreyi kullan
        ''', (id, name, price, lowest_price_from_checker, url, # VALUES kısmı
              name, price, lowest_price_from_checker, url)) # ON CONFLICT DO UPDATE SET kısmı için parametreler

        conn.commit()
        conn.close()
        logging.info(f"Product updated/inserted: ID={id}, Name='{name}', Current Price={price}₺, Lowest Price={lowest_price_from_checker}₺, URL={url}")
    except Exception as e:
        logging.error(f"Error updating product (ID={id}): {e}")



async def main():
    init_db()

    while True:
        products = get_all_products()

        for id, name, price, lowest_price, url in products:
            retry_limit = MAX_PRICE_RETRIES if "amazon" not in url or "trendyol.com" in url else 2
            for _ in range(retry_limit):
                status = await check_price_change(id, name, price, url)
                if status:
                    break
                logging.info("Retrying...")
                await asyncio.sleep(SLEEP_TIME)

        logging.info(f"Waiting for {RUN_EVERY:.2f} seconds before checking again.")
        await asyncio.sleep(RUN_EVERY)

# Run the main function

asyncio.run(main())
