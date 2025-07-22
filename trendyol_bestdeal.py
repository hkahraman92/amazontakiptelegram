from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time

# --- Ayarlar ---
#url = "https://www.trendyol.com/sr?mid=968&lpd=30&os=1&sst=PRICE_BY_DESC"
#url = "https://www.trendyol.com/sr?q=sabunluk%20deterjanl%C4%B1k&qt=Sabunluk%20Deterjanl%C4%B1k&st=Sabunluk%20Deterjanl%C4%B1k&lpd=30&os=1"
DISCOUNT_THRESHOLD = 30  # İndirim eşiği
SCROLL_PAUSE_TIME = 3  # Her kaydırma sonrası yeni ürünlerin yüklenmesi için bekleme süresi
HEADLESS_MODE = False  # Tarayıcının görünmesini istemiyorsanız True yapın


# Fiyatları temizlemek için fonksiyon
def clean_price(price_text):
    if not price_text: return None
    cleaned_text = price_text.replace('TL', '').strip().replace('.', '').replace(',', '.')
    try:
        return float(cleaned_text)
    except (ValueError, TypeError):
        return None


# --- Selenium Kurulumu ---
print("Selenium ile Chrome tarayıcısı hazırlanıyor...")
chrome_options = Options()
if HEADLESS_MODE:
    chrome_options.add_argument("--headless")  # Tarayıcıyı arka planda çalıştırır
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--log-level=3")  # Terminali temiz tutar
chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=chrome_options)

# İşlenen ürünleri takip etmek için bir set (hafıza)
processed_urls = set()
deals_found_count = 0

try:
    driver.get(url)
    print(f"'{url}' adresine gidildi. Tarama başlıyor...")
    time.sleep(3)

    last_height = driver.execute_script("return document.body.scrollHeight")

    # Ana döngü: Sayfa sonuna gelene kadar devam et
    while True:
        # Mevcut HTML'i al ve işle
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        products = soup.find_all('div', class_='p-card-wrppr')

        new_items_found_this_scroll = False
        for product in products:
            product_link_element = product.find('a', class_='p-card-chldrn-cntnr')
            if not product_link_element or 'href' not in product_link_element.attrs:
                continue

            product_url = "https://www.trendyol.com" + product_link_element['href']

            # Eğer ürün daha önce işlenmediyse, şimdi işle
            if product_url not in processed_urls:
                new_items_found_this_scroll = True

                # Yeni ürünü hafızaya ekle
                processed_urls.add(product_url)

                # Ürün bilgilerini çek
                original_price_element = product.find('div', class_='price-item lowest-price-original') or product.find(
                    'div', class_='prc-box-sllng')
                discounted_price_element = product.find('div',
                                                        class_='price-item lowest-price-discounted') or product.find(
                    'div', class_='prc-box-dscntd')

                if original_price_element and discounted_price_element:
                    original_price = clean_price(original_price_element.text)
                    discounted_price = clean_price(discounted_price_element.text)

                    if original_price and discounted_price and original_price > discounted_price:
                        discount_percentage = ((original_price - discounted_price) / original_price) * 100
                    else:
                        discount_percentage = 0

                    # İndirim eşiğini karşılıyorsa yazdır
                    if discount_percentage >= DISCOUNT_THRESHOLD:
                        deals_found_count += 1
                        brand = (product.find('span', class_='prdct-desc-cntnr-ttl') or {}).get('title', 'Marka Yok')
                        name_part = (product.find('span', class_='prdct-desc-cntnr-name') or {}).get('title', '')
                        full_name = f"{brand} {name_part}".strip()

                        print("-" * 35)
                        print(f"✅ YENİ İNDİRİM ALGILANDI!")
                        print(f"  Ürün: {full_name}")
                        print(
                            f"  Fiyat: {original_price_element.text.strip()} -> {discounted_price_element.text.strip()}")
                        print(f"  İndirim Oranı: %{discount_percentage:.2f}")
                        print("-" * 35)

        if new_items_found_this_scroll:
            print(f"({len(processed_urls)} ürün işlendi) Bir sonraki grup için aşağı kaydırılıyor...")

        # Sayfanın en altına kaydırarak yeni ürünleri tetikle
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE_TIME)

        # Sayfa yüksekliğinin artıp artmadığını kontrol et
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            # Emin olmak için bir kez daha kontrol et
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print("\nSayfanın sonuna ulaşıldı. Tarama tamamlandı.")
                break
        last_height = new_height

finally:
    driver.quit()
    print("\nTarayıcı kapatıldı.")
    if deals_found_count == 0:
        print(f"İşlem sonucunda %{DISCOUNT_THRESHOLD} ve üzeri indirimli bir ürün bulunamadı.")
    else:
        print(f"Toplamda {deals_found_count} adet büyük indirimli ürün bulundu.")