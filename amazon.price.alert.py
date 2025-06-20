import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import configparser
import telegram
import asyncio
import json
import traceback
import logging
import os
import random
import sqlite3
from logging.handlers import TimedRotatingFileHandler
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import json # JSON işlemek için
import re

#PARAMS
DEBUG_SAVE_HTML = False
SLEEP_TIME=random.uniform(10, 15) #between attemps to fetch the price
RUN_EVERY=random.uniform(500 , 600) #seconds = 0.5 minutes
# PRODUCTS_FILE kaldırıldı, artık kullanılmıyor
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
if not os.path.exists(CONFIG_FILE):
    logging.error(f"Configuration file '{CONFIG_FILE}' not found.")
    raise FileNotFoundError(f"Configuration file '{CONFIG_FILE}' not found.")
config.read(CONFIG_FILE)

try:
    TELEGRAM_TOKEN = config.get('TELEGRAM', 'TELEGRAM_TOKEN')
    CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
except (configparser.NoSectionError, configparser.NoOptionError) as e:
    logging.error("Missing 'TELEGRAM' section or required options in config file.")
    raise KeyError("Missing 'TELEGRAM' section or required options in config file.")

apiURL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'


# --- DATABASE İŞLEMLERİ ---
def init_db():
    """Veritabanını başlatır ve ürünler tablosunu oluşturur/günceller."""
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL DEFAULT 0,
                lowest_price REAL DEFAULT 0,
                url TEXT NOT NULL,
                etag TEXT,
                last_modified_date TEXT
            )
        ''')
        conn.commit()

        # Mevcut tabloya sütun eklemek için (eğer tablo zaten varsa ve sütunlar yoksa)
        # Bu kısım, daha önce çalışan bir veritabanınız varsa ve yeni sütunları eklemek istiyorsanız gereklidir.
        # İlk çalıştırmada tablo yeni oluşturuluyorsa sorun olmaz.
        try:
            cursor.execute("ALTER TABLE products ADD COLUMN etag TEXT")
            logging.info("Column 'etag' added to 'products' table.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logging.info("'etag' column already exists.")
            else:
                logging.error(f"Error adding 'etag' column: {e}")

        try:
            cursor.execute("ALTER TABLE products ADD COLUMN last_modified_date TEXT")
            logging.info("Column 'last_modified_date' added to 'products' table.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                logging.info("'last_modified_date' column already exists.")
            else:
                logging.error(f"Error adding 'last_modified_date' column: {e}")

        conn.commit()
        conn.close()
        logging.info("Database initialized and table 'products' checked/created/updated successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

def get_all_products():
    """Veritabanındaki tüm ürünleri çeker (ETag ve Last-Modified ile birlikte)."""
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, price, lowest_price, url, etag, last_modified_date FROM products')
    products = cursor.fetchall()
    conn.close()
    return products


def get_hepsiburada_price_with_selenium(url: str) -> str:
    """
    Hepsiburada için nihai ve kanıta dayalı en sağlam yöntem:
    1. 'buyboxOrder: 1' ile kazanan satıcıyı bulur.
    2. Fiyatı ve indirimi doğrudan bu satıcının objesinden okur.
    """
    driver = None
    try:
        logging.info(f"SELENIUM-STEALTH: Hepsiburada URL'si için fiyat alınıyor: {url}")

        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        stealth(driver,
                languages=["tr-TR", "tr"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
                )

        driver.get(url)

        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, 'reduxStore')))
        logging.info("SELENIUM: 'reduxStore' script etiketi yüklendi.")

        final_price_str = "-1"

        # Verinin tam yüklenmesi için bekleme döngüsü
        for i in range(10):
            soup = BeautifulSoup(driver.page_source, "html.parser")
            redux_script = soup.find("script", {"id": "reduxStore"})
            if not redux_script:
                time.sleep(0.5)
                continue

            data = json.loads(redux_script.string)
            product_state = data.get("productState", {})
            if not product_state:
                logging.info(f"SELENIUM: {i + 1}. deneme - 'productState' henüz yüklenmedi.")
                time.sleep(0.5)
                continue

            # Ana fiyatı ve kampanya detayını al
            product_info = product_state.get("product", {})
            base_price = product_info.get("prices", [{}])[0].get("value")

            campaign_detail = product_state.get("campaignDetail", {})
            winner_campaign_name = campaign_detail.get("winnerCampaignName")

            if base_price is not None:
                logging.info(f"SELENIUM: {i + 1}. denemede baz fiyat bulundu: {base_price}")
                final_price = float(base_price)

                # Kampanya metni varsa indirimi uygula ve döngüden çık
                if winner_campaign_name:
                    logging.info(f"SELENIUM: 'winnerCampaignName' bulundu: '{winner_campaign_name}'")
                    match = re.search(r'%\s*(\d+\.?\d*)', winner_campaign_name)  # Ondalıklı indirimleri de yakalar
                    if match:
                        try:
                            discount_value = float(match.group(1))
                            if discount_value > 0:
                                final_price = float(base_price) * (1 - (discount_value / 100.0))
                                logging.info(
                                    f"SELENIUM: 'winnerCampaignName' üzerinden %{discount_value} indirim uygulandı.")
                        except (ValueError, TypeError):
                            logging.warning(
                                f"SELENIUM: 'winnerCampaignName' içinde geçersiz indirim değeri: {match.group(1)}")
                    else:
                        logging.info("SELENIUM: 'winnerCampaignName' içinde yüzde formatında indirim bulunamadı.")

                else:
                    logging.info(
                        "SELENIUM: 'campaignDetail' veya 'winnerCampaignName' alanı henüz boş, indirim aranamıyor.")

                # Fiyatı bulduğumuz için artık döngüden çıkabiliriz, kampanya olmasa bile.
                final_price_str = f"{final_price:.2f}"
                break

            logging.info(f"SELENIUM: {i + 1}. denemede baz fiyat henüz bulunamadı, bekleniyor...")
            time.sleep(0.5)

        if final_price_str == "-1":
            logging.error("SELENIUM: Bekleme süresi sonunda baz fiyat dahi bulunamadı.")

        logging.info(f"SELENIUM: Nihai hesaplanan fiyat: {final_price_str}")
        return final_price_str

    except Exception as e:
        logging.error(f"SELENIUM: Beklenmedik bir hata oluştu: {e}", exc_info=True)
        return "-1"
    finally:
        if driver:
            driver.quit()


def update_product(id: int, name: str, price: float, url: str, lowest_price_to_set: float,
                   etag: str = None, last_modified_date: str = None):
    """Veritabanındaki bir ürünü günceller veya ekler (ID çakışması durumunda), ETag ve Last-Modified dahil."""
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()

        # INSERT OR REPLACE into products
        cursor.execute('''
            INSERT INTO products (id, name, price, lowest_price, url, etag, last_modified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = ?,
                price = ?,
                lowest_price = ?,
                url = ?,
                etag = ?,
                last_modified_date = ?
        ''', (id, name, price, lowest_price_to_set, url, etag, last_modified_date, # VALUES
              name, price, lowest_price_to_set, url, etag, last_modified_date))    # ON CONFLICT DO UPDATE

        conn.commit()
        conn.close()
        logging.info(f"Product updated/inserted: ID={id}, Name='{name}', Price={price}₺, Lowest={lowest_price_to_set}₺, ETag={etag}, LastMod={last_modified_date}")
    except Exception as e:
        logging.error(f"Error updating product (ID={id}): {e}")
        traceback.print_exc()

# --- WEB SCRAPING VE FİYAT ALMA ---
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
        elif "bikehouse.co" in url:
            title_element = soup.find("h1", attrs={"class":'product_title entry-title'})

        if title_element:
            title_text = title_element.text.strip().replace(",", " ")
    except Exception as e:
        logging.error(f"Error in get_name for URL {url}: {e}")
        title_text = ""
    return title_text


def get_price_name(product_id: int,name: str, url: str, previous_etag: str = None, previous_last_modified: str = None):
    """
    Verilen URL'den ürün fiyatını, adını ve HTTP ETag/Last-Modified başlıklarını çeker.
    Eğer içerik değişmediyse (304 Not Modified), özel bir kod (-3) döndürür.
    """
    price_str = "-1"  # Varsayılan hata kodu
    new_etag = None
    new_last_modified = None
    scraped_name = name  # Eğer isim başta boşsa, scrape'den gelen kullanılır

    logging.info(f"Fetching data for: {url} with ETag: {previous_etag}, Last-Modified: {previous_last_modified}")

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/125.0",
    ]
    request_headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',  # Do Not Track
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://www.google.com/'  # Nereden geldiğinizi belirtmek bot olmadığınız hissi verir
    }

    # Koşullu GET başlıklarını ekle
    if previous_etag:
        request_headers['If-None-Match'] = previous_etag
    if previous_last_modified:
        request_headers['If-Modified-Since'] = previous_last_modified

    try:
        session = requests.Session()
        response = requests.get(url, headers=request_headers, timeout=15)
        #response = session.get(url, headers=request_headers, timeout=15)
        if DEBUG_SAVE_HTML:
            # 'debug_html' adında bir klasör yoksa oluştur.
            debug_folder = 'debug_html'
            os.makedirs(debug_folder, exist_ok=True)

            # Dosya adını zaman damgası ve ürün ID'si ile oluştur.
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = "".join([c for c in name if c.isalpha() or c.isdigit() or c.isspace()]).rstrip()
            filename = os.path.join(debug_folder, f"{timestamp}_ID_{product_id}_{safe_name}.html")

            # Gelen HTML içeriğini dosyaya yaz.
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(response.text)

            logging.info(f"HTML content for product ID {product_id} saved to {filename}")

        # logging.info(f"HTML content for product ID {product_id} saved to {filename}")
        if response.status_code == 304:
            logging.info(f"Content not modified for {url} (304).")
            return "-3", scraped_name, previous_etag, previous_last_modified

        response.raise_for_status()

        new_etag = response.headers.get('ETag')
        new_last_modified = response.headers.get('Last-Modified')

        logging.debug(f"Extracted ETag for {url}: {new_etag}")
        logging.debug(f"Extracted Last-Modified for {url}: {new_last_modified}")

        #soup = BeautifulSoup(response.content, "lxml")
        soup = BeautifulSoup(response.content, "html.parser")
        if not scraped_name:
            scraped_name = get_name(soup, url)
            logging.info(f"Product Name Scraped: {scraped_name}")

        if "amazon.com" in url or "amzn" in url:
            logging.info("Amazon URL detected. Applying Amazon-specific logic.")

            # Stok kontrolü mantığı aynı kalıyor
            # ... (stok kontrol kodları burada yer almalı) ...
            unavailable_span = soup.find("span", class_="a-size-medium a-color-success")
            no_offer_span = soup.find("span", id="fod-cx-message-with-learn-more")

            is_unavailable = (unavailable_span and "şu anda mevcut değil" in unavailable_span.text.lower())
            has_no_offer = (no_offer_span and "öne çıkan teklif yok" in no_offer_span.get_text(strip=True).lower())

            if is_unavailable or has_no_offer:
                logging.info(f"Product {scraped_name or 'Unknown'} is currently unavailable on Amazon.")
                return "-2", scraped_name, response.headers.get('ETag'), response.headers.get('Last-Modified')

            # --- YENİ VE EN GÜVENİLİR FİYAT ALMA MANTIĞI ---
            raw_price = ""

            # 1. Adım: Ana fiyat bloğunu "priceToPay" class'ı ile bul.
            price_container = soup.find('span', class_='priceToPay')

            if price_container:
                logging.info("Main price container with class 'priceToPay' found.")
                # 2. Adım: SADECE bu bloğun içinde fiyatın parçalarını ara.
                price_whole_span = price_container.find('span', class_='a-price-whole')
                price_fraction_span = price_container.find('span', class_='a-price-fraction')

                if price_whole_span and price_fraction_span:
                    raw_price = price_whole_span.get_text(strip=True) + price_fraction_span.get_text(strip=True)
                    logging.info(f"Found price inside 'priceToPay' container: {raw_price}")

            # --- YEDEK PLAN (FALLBACK) ---
            # Eğer yukarıdaki en güvenilir yöntem işe yaramazsa, son çare olarak tüm sayfada ara.
            if not raw_price:
                logging.warning(
                    "'priceToPay' container method failed. Trying fallback: searching for 'a-offscreen' in the whole document.")
                # find_all ile tüm adayları bul ve içinde sayı olan ilkini al
                all_offscreen_spans = soup.find_all('span', class_=('a-offscreen', 'aok-offscreen'))
                for span in all_offscreen_spans:
                    price_text = span.get_text(strip=True)
                    # İçinde en az bir rakam olan ve boş olmayan ilk etiketi kullan
                    if price_text and any(char.isdigit() for char in price_text):
                        raw_price = price_text
                        logging.info(f"Found a valid price in 'a-offscreen' with fallback method: {raw_price}")
                        break  # İlk uygun fiyatı bulunca döngüden çık

            # Fiyat temizleme ve doğrulama mantığı
            if raw_price:
                cleaned_price = raw_price.replace('₺', '').replace('TL', '').replace(' ', '').replace('.', '').replace(
                    ',', '.').strip()
                try:
                    if cleaned_price.count('.') > 1:
                        parts = cleaned_price.split('.')
                        cleaned_price = "".join(parts[:-1]) + "." + parts[-1]

                    float(cleaned_price)
                    price_str = cleaned_price
                    logging.info(f"Successfully cleaned and validated Amazon price: {price_str}")
                except ValueError:
                    logging.warning(
                        f"Could not convert cleaned price '{cleaned_price}' to float. Raw price was '{raw_price}'.")
            else:
                logging.warning(f"No price elements found for Amazon URL: {url}")

        elif "trendyol.com" in url:  # <-- TÜM TRENDYOL MANTIĞI BU BLOKTA
            logging.info("Trendyol URL detected. Applying Trendyol-specific logic.")
            price_span = soup.find("span", class_="prc-dsc")
            if price_span:
                price_str = price_span.text.strip().replace('TL', '').replace(' ', '').replace('.', '').replace(',',
                                                                                                                '.').strip()
                logging.info(f"Found price for {scraped_name or 'Unknown'} on Trendyol: {price_str}")
            else:
                logging.warning(f"Price span 'prc-dsc' not found for Trendyol URL: {url}")

        elif "suarezclothing.com" in url:
            # Bu kısım değişmedi
            script_tag = soup.find('script', type='application/ld+json')
            if script_tag:
                json_data = json.loads(script_tag.string)
                if 'offers' in json_data and 'lowPrice' in json_data['offers']:
                    price_str = str(json_data['offers']['lowPrice']).replace('.', '')
                elif 'offers' in json_data and 'price' in json_data['offers']:
                    price_str = str(json_data['offers']['price']).replace('.', '')
                else:
                    logging.warning(f"Price not found in JSON-LD for SuarezClothing: {url}")
            else:
                logging.warning(f"JSON-LD script tag not found for SuarezClothing: {url}")

        elif "hepsiburada.com" in url:
            logging.info("Hepsiburada URL detected. Applying Hepsiburada-specific logic.")

            # 1. Adım: Güvenilir test kimliğini kullanarak ana fiyat konteynerını bul.
            # "attrs" kullanarak standart olmayan öznitelikleri arayabiliriz.
            price_container = soup.find('div', attrs={'data-test-id': 'checkout-price'})

            if price_container:
                # 2. Adım: Konteynerin içindeki tüm metni al.
                # Örnek: "Sepete özel fiyat 615,58 TL"
                # Bu metnin içinden sadece fiyatı içeren bölümü bulmaya çalışacağız.
                # Genellikle fiyat, konteyner içindeki son metin parçası olur.

                # Fiyatın bulunduğu div'i bulmak için daha spesifik bir arama yapalım.
                # Sizin örneğinizde fiyat "bWwoI8vknB6COlRVbpRj" class'ına sahip div'de.
                # Bu class güvenilmez olduğu için, metnin kendisinden yola çıkacağız.
                price_div = price_container.find('div', string=lambda text: 'TL' in text if text else False)

                if price_div:
                    raw_price = price_div.get_text(strip=True) # Örnek: "615,58 TL"
                    logging.info(f"Found raw price string in Hepsiburada container: '{raw_price}'")

                    # 3. Adım: Fiyatı temizle (mevcut kodunuzdaki temizleme mantığını kullanıyoruz)
                    cleaned_price = raw_price.replace('₺', '').replace('TL', '').replace(' ', '').replace('.', '').replace(',', '.').strip()
                    try:
                        float(cleaned_price)
                        price_str = cleaned_price
                        logging.info(f"Successfully cleaned Hepsiburada price: {price_str}")
                    except ValueError:
                        logging.warning(f"Could not convert Hepsiburada price '{cleaned_price}' to float.")
                        price_str = "-1"
                else:
                    logging.warning("Price text (div containing 'TL') not found inside the Hepsiburada container.")
                    price_str = "-1"
            else:
                logging.warning("Hepsiburada price container with data-test-id 'checkout-price' not found.")
                price_str = "-1"

        elif "bikehouse.co" in url:
            # Bu kısım değişmedi
            price_element = soup.find('span', class_='price_varies')  # Eğer indirim varsa
            if price_element:
                money_span = price_element.find('ins')
                if money_span:  # İndirimli fiyat varsa
                    money_span = money_span.find('span', class_='money')
                else:  # Normal fiyat (indirim yoksa)
                    money_span = price_element.find('span', class_='money')

                if money_span:
                    price_str = money_span.text.replace('.', '').replace('$', '').replace(',', '.').strip()
                else:
                    logging.warning(f"Money span not found within price_varies for Bikehouse: {url}")
            else:  # Direct price (no variations)
                money_span = soup.find('p', class_='price').find('span', class_='woocommerce-Price-amount amount')
                if money_span:
                    price_str = money_span.text.replace('.', '').replace('$', '').replace(',', '.').strip()
                else:
                    logging.warning(f"Money span not found for Bikehouse: {url}")
            price_str = price_str.replace('COP', '').strip()  # Para birimi sembolünü temizle

        # Nihai fiyat temizliği ve geçerlilik kontrolü. Bu blok artık Amazon'dan gelen temizlenmiş fiyatı da işleyebilir.
        if price_str not in ["-1", "-2", "-3"]:
            try:
                if ',' in price_str and '.' in price_str and price_str.rfind(',') > price_str.rfind('.'):
                    price_str = price_str.replace('.', '').replace(',', '.')
                elif ',' in price_str:
                    price_str = price_str.replace(',', '.')

                price_str = price_str.replace(' ', '')
                float(price_str)
            except ValueError:
                logging.error(
                    f"Final price string '{price_str}' could not be converted to float for {url}. Resetting to -1.")
                price_str = "-1"
    except requests.exceptions.Timeout:
        logging.error(f"Timeout occurred for {url}")
        return "-1", scraped_name, previous_etag, previous_last_modified
    except requests.exceptions.RequestException as e:
        logging.error(f"Connection error for {url}: {e}")
        return "-1", scraped_name, previous_etag, previous_last_modified
    except Exception as e:
        logging.error(f"An unexpected error occurred in get_price_name for {url}: {e}")
        traceback.print_exc()
        return "-1", scraped_name, previous_etag, previous_last_modified

    return price_str, scraped_name, new_etag, new_last_modified

# --- TELEGRAM BİLDİRİMLERİ ---
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

# --- ANA KONTROL FONKSİYONU ---
async def check_price_change(product_id: int, name: str, previous_price_db: float, url: str, db_etag: str, db_last_modified: str):
    """Ürünün fiyat değişimini kontrol eder, veritabanını günceller ve ETag/Last-Modified yönetir."""
    try:
        current_price_str, name_new, new_etag, new_last_modified = get_price_name(product_id, name, url, db_etag, db_last_modified)

        # Durum 1: İçerik değişmemiş (HTTP 304 yanıtı)
        if current_price_str == "-3":
            logging.info(f"Content not modified for {name_new or name} (ID: {product_id}). Using cached data. ETag: {db_etag}, Last-Modified: {db_last_modified}")
            # Bu durumda DB'deki etag ve last_modified zaten doğru ve günceldir.
            # Fiyat ve en düşük fiyat da değişmemiştir. Sadece logla ve çık.
            return True # Başarılı işlem (değişiklik olmasa da)

        if current_price_str == "-1" and "hepsiburada.com" in url:
            logging.warning("Requests failed for Hepsiburada. Trying fallback with Selenium...")
            # Not: Selenium senkronize çalıştığı için, asyncio döngüsünü bloklamamak adına
            # onu ayrı bir thread'de çalıştırmak en iyi yöntemdir.
            loop = asyncio.get_running_loop()
            price_from_selenium = await loop.run_in_executor(
                None, get_hepsiburada_price_with_selenium, url
            )
            current_price_str = str(price_from_selenium)
        # Eğer name_new boşsa, db'deki eski ismi kullanmaya devam et
        if not name_new:
            name_new = name

        # Durum 2: Fiyat alınamadı (-1) veya stokta yok (-2) veya geçersiz format
        if current_price_str.strip() == "" or current_price_str.isspace() or current_price_str == "-1":
            logging.warning(f"Invalid or empty price string ('{current_price_str}') for {name_new} (ID: {product_id}). Skipping price update logic.")
            # Fiyat bulunamadıysa bile etag ve last_modified güncellenebilir, çünkü sayfa içeriği değişmiş olabilir.
            update_product(product_id, name_new, previous_price_db, url, previous_price_db, new_etag, new_last_modified) # Fiyatı değiştirme, başlıkları güncelle
            return False # Fiyat alınamadığı için başarısız sayılabilir

        try:
            current_price = float(current_price_str)
        except ValueError:
            logging.error(f"Invalid current price format '{current_price_str}' for {name_new} (ID: {product_id}).")
            update_product(product_id, name_new, previous_price_db, url, previous_price_db, new_etag, new_last_modified) # Başlıkları güncelle
            return False

        # En düşük fiyatı belirle
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('SELECT lowest_price FROM products WHERE id = ?', (product_id,))
        row = cursor.fetchone()
        conn.close()
        stored_lowest_price_db = row[0] if row and row[0] is not None else 0.0

        calculated_new_lowest_price = stored_lowest_price_db
        if current_price > 0: # Sadece geçerli (pozitif) fiyatlar için lowest_price'ı güncelle
            if stored_lowest_price_db <= 0.0 or current_price < stored_lowest_price_db:
                calculated_new_lowest_price = current_price
                logging.info(f"New lowest price for {name_new} (ID: {product_id}): {calculated_new_lowest_price}₺")
            else:
                logging.info(f"Lowest price remains unchanged for {name_new} (ID: {product_id}): {calculated_new_lowest_price}₺")
        else:
            logging.info(f"Current price ({current_price}₺) is not positive. Lowest price will not be updated for {name_new} (ID: {product_id}).")


        # --- Bildirim ve Güncelleme Mantığı ---
        notification_sent = False

        if previous_price_db == -2.0 and current_price > 0: # Stokta yoktu, stoğa geldi
            logging.info(f"Product {name_new} (ID: {product_id}) is back in stock! Price: {current_price}₺.")
            await send_telegram_notification(name_new, "STOKTA YOKTU", current_price, calculated_new_lowest_price, url)
            notification_sent = True
        elif current_price == -2: # Stok dışı kaldı
            if previous_price_db > 0 : # Daha önce stokta ise bildir
                 logging.info(f"Product {name_new} (ID: {product_id}) is now unavailable.")
                 await send_telegram_notification(name_new, previous_price_db, current_price, calculated_new_lowest_price, url)
                 notification_sent = True
            # Eğer zaten -2.0 idi ve yine -2.0 ise bildirim göndermeye gerek yok.
        elif current_price > 0 and previous_price_db > 0 and current_price != previous_price_db: # Normal fiyat değişimi
            if abs(current_price - previous_price_db) >= PRICE_DIFFERENCE:
                logging.info(f"Price changed for {name_new}: {previous_price_db}₺ -> {current_price}₺.")
                await send_telegram_notification(name_new, previous_price_db, current_price, calculated_new_lowest_price, url)
                notification_sent = True
            else:
                logging.info(f"Price changed for {name_new} but below threshold. {previous_price_db}₺ -> {current_price}₺.")
        elif current_price > 0 and previous_price_db <= 0 and previous_price_db != -2.0: # İlk defa geçerli fiyat bulundu (ve stokta yoktu durumu değilse)
            logging.info(f"Found initial valid price for {name_new}: {current_price}₺. No previous valid price recorded.")
            # İsteğe bağlı: İlk geçerli fiyat bulunduğunda bildirim göndermek isterseniz burayı aktifleştirin.
            # await send_telegram_notification(name_new, "İlk Kez", current_price, calculated_new_lowest_price, url)
            # notification_sent = True
        else:
            logging.info(f"Price for {name_new} is {current_price}₺. No significant change or special status detected.")


        # Her durumda (fiyat değişse de değişmese de, 304 hariç, -1 hariç) DB'yi güncelle.
        # -1'i ve -2'yi de güncelleyebiliriz, çünkü bu durumlar da bir "durum" bilgisidir.
        update_product(product_id, name_new, current_price, url, calculated_new_lowest_price, new_etag, new_last_modified)

        return True # İşlem başarılı

    except requests.exceptions.RequestException as exc:
        logging.error(f"Network or request error for {name_new or name} (ID: {product_id}): {exc}")
        return False
    except Exception as exc:
        logging.exception(f"An unexpected error occurred in check_price_change for {name_new or name} (ID: {product_id}): {exc}")
        return False

# --- ANA ÇALIŞMA DÖNGÜSÜ ---
async def main():
    init_db()  # DB'yi başlat ve sütunları kontrol et/ekle

    while True:
        products = get_all_products()  # id, name, price, lowest_price, url, etag, last_modified_date

        for product_data in products:
            id_val, name_val, price_val, lowest_price_val, url_val, etag_val, last_modified_val = product_data

            # Amazon/Trendyol için retry_limit'i özelleştirme
            retry_limit = 2 if any(domain in url_val for domain in ["amazon.com", "amzn", "trendyol.com"]) else 2

            success_flag = False
            for attempt in range(retry_limit):
                logging.info(f"Attempt {attempt + 1}/{retry_limit} for product ID {id_val} ({url_val})")
                # check_price_change'e ETag ve Last-Modified'i yolla
                status = await check_price_change(id_val, name_val, price_val, url_val, etag_val, last_modified_val)
                if status:  # status True ise işlem başarılı (304 dahil veya fiyat güncellendi)
                    success_flag = True
                    break
                logging.warning(f"Attempt {attempt + 1} failed for product ID {id_val}. Retrying after {SLEEP_TIME:.2f}s...")
                await asyncio.sleep(SLEEP_TIME)

            if not success_flag:
                logging.error(f"All {retry_limit} retries failed for product ID {id_val} ({url_val}).")

            await asyncio.sleep(random.uniform(0.5, 1.5))  # Her ürün arasında kısa bir bekleme

        logging.info(f"All products checked. Waiting for {RUN_EVERY:.2f} seconds before the next cycle.")
        await asyncio.sleep(RUN_EVERY)


# Run the main function
if __name__ == "__main__":
    asyncio.run(main())