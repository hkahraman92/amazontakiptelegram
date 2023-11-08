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

#PARAMS
SLEEP_TIME=0.5 #between attemps to fetch the price
RUN_EVERY=30 #seconds = 0.5 minutes
PRODUCTS_FILE= 'products.ini'
CONFIG_FILE = 'config.ini'
PRICE_DIFFERENCE=1 #1 dollar, min price difference to notify
MAX_PRICE_RETRIES=30

# Log yapılandırması
logging.basicConfig(
    filename='amazon_price_alert.log',
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


# Read params from config file
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

TELEGRAM_TOKEN = config.get('TELEGRAM', 'TELEGRAM_TOKEN')
CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
apiURL = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'

def get_name(soup, url):
    try:
        title = ""
        if "suarezclothing.com" in url:
            title = soup.find("h1", attrs={"class":'vtex-store-components-3-x-productNameContainer mv0 t-heading-4'})
            title = title.find("span", attrs={"class":'vtex-store-components-3-x-productBrand'}).text
        if "amazon.com" in url:
            title = soup.find("span", attrs={"id":'productTitle'})
        if "cyclewear.com.co" in url or "bikeexchange.com.co" in url:
            title = soup.find("h1", attrs={"class":'h3 CProductHeader-title t-productHeaderHeading'})
        if "bikehouse.co" in url:
            title = soup.find("h1", attrs={"class":'product_title entry-title'})

        title = title.string
        title = title.strip().replace(",", " ")

    except AttributeError:
        title = ""

    return title

# get price and name of item (title)
def get_price_name(name, url):
    price = "-1"
    #print(url)
    logging.info(f"Fetching price for: {url}")

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        #print(f"Bağlantı hatası: {e}")
        logging.error(f"Connection error: {e}")
        return "-1", name

    soup = BeautifulSoup(response.content, "lxml")

    # İsim boşsa al
    if not name:
        name = get_name(soup, url)
        #print(name)
        logging.info(f"Product Name: {name}")

    if "amazon.com.tr" in url:
        title = soup.find("span", class_="a-size-medium a-color-success")
        if title and "şu anda mevcut değil" in title.text.lower():
            #print("Currently unavailable")
            logging.info("Currently unavailable")
            return "-2", name

        #print("Available!")
        logging.info("Available!")

        # En doğru fiyat: a-offscreen (ilk dolu olanı al)

        price_span = soup.find("span",
                               attrs={"class": "a-price aok-align-center reinventPricePriceToPayMargin priceToPay"})

        if price_span is None:
            return "-1", name

        price_whole = price_span.find("span", class_="a-price-whole")
        price_fraction = price_span.find("span", class_="a-price-fraction")

        if price_whole is None or price_fraction is None:
            return "-1", name

        # Fiyatı birleştir
        price = price_whole.text.strip() + "." + price_fraction.text.strip()

        # Binlik ayırıcıları temizle, ondalık ayırıcıyı dönüştür
        price = price.replace('.', '').replace(',', '.')

    if "suarezclothing.com" in url:
        script_tag = soup.find('script', type='application/ld+json')
        if script_tag is not None:
            json_data = json.loads(script_tag.string)
            price = str(json_data['offers']['lowPrice']).replace('.','')
    if "cyclewear.com.co" in url or "bikeexchange.com.co" in url:
        div_element = soup.find('div', class_='yotpo-main-widget')
        # Extract the 'data-price' attribute value
        price = div_element.get('data-price')
    if "bikehouse.co" in url:
       price1 = soup.find('span', class_='price_varies')
       if price1 is not None:
           price = price1.find('ins').find('span', class_='money').text
       else:
           price = soup.find('span', class_='money').text
       price = price.replace('.','')
    # Remove currency symbols and convert to float
    price = price.replace('£', '').replace('$', '').replace(',', '')

    return price, name

async def send_telegram_notification(item, previous_price, current_price, url):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    if -2.0 == current_price:
        message = f"Item {item} is no longer available!\n"
        message += f"URL: {url}"
    else:
        message = f"Price has changed for <b> {item} </b>\n"
        message += f"Previous price: {previous_price}\n"
        message += f"Current price: {current_price}\n"
        message += f"URL: {url}"

    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
    except Exception as e:
        #print("Hata oluştu:", e)
        logging.error(f"Error occurred while sending notification: {e}")
        traceback.print_exc()

async def check_price_change(id, name, previous_price, url):
    products_file = configparser.RawConfigParser()
    products_file.read(PRODUCTS_FILE)

    try:
        current_price, name_new = get_price_name(name, url)

        if current_price.strip() != "" and not current_price.isspace():
                current_price = float(current_price)
                if current_price == -1:
                    #print("Error with price")
                    logging.error("Error with price")
                    return False
                if len(name)==0:
                    #print("Name updated from ", name, "to: ", name_new)
                    logging.info(f"Name updated from {name} to: {name_new}")
                    products_file.set('PRODUCTS', id,f'{name_new},${current_price},{url}')
                    with open(PRODUCTS_FILE, 'w') as productsFile:
                        products_file.write(productsFile)

                if current_price != previous_price:
                    if abs(current_price - previous_price) >= PRICE_DIFFERENCE:
                        #print("Price has changed for", name_new)
                        #print("Previous price: ", previous_price)
                        #print("Current price: ", current_price)
                        logging.info(f"Price has changed for {name_new}")
                        logging.info(f"Previous price: {previous_price}")
                        logging.info(f"Current price: {current_price}")

                        # Send notification to Telegram
                        await send_telegram_notification(name_new, previous_price, current_price, url)
                    # Update the price in the config file
                    #print("Price changed but not more than ", PRICE_DIFFERENCE)
                    logging.info(f"Price changed but not more than {PRICE_DIFFERENCE}")
                    products_file.set('PRODUCTS', id,f'{name_new},${current_price},{url}')
                    with open(PRODUCTS_FILE, 'w') as productsFile:
                        products_file.write(productsFile)

                else:
                    #print("Price has not changed. Still ", current_price)
                    logging.info(f"Price has not changed. Still {current_price}")


                return True
        else:
            #print("Current price is empty or whitespace.")
            logging.warning(f"Current price is empty or whitespace for {name}")
            return False
    except ValueError as exc:
        #print("Invalid current price format.", exc)
        logging.error(f"Invalid current price format: {exc}")
    except requests.exceptions.HTTPError as err:
        #print("Error occurred during the request:", err)
        logging.error(f"Error occurred during the request: {err}")
        return False

async def main():

    products_file = configparser.RawConfigParser()

    while True:
        # Read product information from config file
        products_file.read(PRODUCTS_FILE)
        products = products_file.items('PRODUCTS')

        for id, info in products:
            name, price, url = info.split(',')
            price=float(price.replace('$', ''))

            #while True:
            # Yeni: başarısız denemelerde sadece 1-2 kere dene
            retry_limit = MAX_PRICE_RETRIES if "amazon.com.tr" not in url else 2
            for _ in range(retry_limit):
                status = await check_price_change(id, name, price, url)
                if status:
                    break
                #print(".")
                logging.info("Retrying...")
                time.sleep(SLEEP_TIME)
        #print("Waiting ", RUN_EVERY)
        logging.info(f"Waiting for {RUN_EVERY} seconds before checking again.")
        time.sleep(RUN_EVERY)

# Run the main function
asyncio.run(main())
