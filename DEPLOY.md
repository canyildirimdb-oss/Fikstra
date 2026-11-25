# İddaa Radar • %100 Ücretsiz 7/24 Bulut Kurulum Rehberi

Bu rehberle bilgisayarınız kapalıyken bile uygulamanızı **sıfır maliyetle** 7/24 çalışır hale getirebilirsiniz.

---

## 1. Ücretsiz PostgreSQL Veritabanı (Neon veya Supabase)
Render Free sunucusunun yerel diski yeniden başlatmalarda sıfırlandığı için kalıcı veriyi ücretsiz PostgreSQL'de tutuyoruz:
1. [Neon.tech](https://neon.tech) veya [Supabase.com](https://supabase.com) üzerinden ücretsiz hesap açın.
2. Yeni bir proje oluşturun ve **Connection String (`DATABASE_URL`)** adresini kopyalayın (ör. `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`).

---

## 2. Ücretsiz Google Gemini API Anahtarı
1. [Google AI Studio](https://aistudio.google.com/apikey) adresine gidin.
2. **Create API Key** butonuna tıklayıp ücretsiz `GEMINI_API_KEY` değerini alın.
   - *Not:* Anahtar girilmezse veya günlük ücretsiz kota dolarsa sistem otomatik olarak gerçek istatistiklerden kural tabanlı Türkçe özet üretmeye devam eder.

---

## 3. Tek Komutla Ücretsiz VAPID Anahtarları Üretme (Telefon Bildirimi)
Proje klasöründe terminalden şu tek komutu çalıştırın:

```bash
cd backend
python -c "import base64; from py_vapid import Vapid; from cryptography.hazmat.primitives import serialization; v=Vapid(); v.generate_keys(); print('VAPID_PUBLIC_KEY=' + base64.urlsafe_b64encode(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()); print('VAPID_PRIVATE_KEY=' + base64.urlsafe_b64encode(v.private_key.private_numbers().private_value.to_bytes(32,'big')).rstrip(b'=').decode())"
```

Çıkan `VAPID_PUBLIC_KEY` ve `VAPID_PRIVATE_KEY` değerlerini kopyalayın.

---

## 4. GitHub + Render Free Üzerinde Tek Tıkla Yayınlama
Bu projede `Dockerfile` hem React PWA arayüzünü derler hem de FastAPI sunucusu üzerinden tek serviste sunar (512MB RAM dostu).
1. Projeyi kendi GitHub reponuza pushlayın.
2. [Render.com](https://render.com) hesabınızla giriş yapıp **New + -> Blueprint** (veya **Web Service**) seçin ve reponuzu bağlayın (`render.yaml` otomatik algılanır).
3. Render **Environment** sekmesinde şu değişkenleri girin:
   - `DATABASE_URL`: Adım 1'deki Neon/Supabase adresi
   - `CRON_SECRET`: Güçlü rastgele bir şifre (veya Render otomatik üretir)
   - `GEMINI_API_KEY`: Adım 2'deki anahtar
   - `VAPID_PUBLIC_KEY` ve `VAPID_PRIVATE_KEY`: Adım 3'teki anahtarlar
   - `VAPID_SUBJECT`: `mailto:sizinmail@example.com`
4. Deploy tamamlandığında uygulamanız `https://iddaa-radar-xxx.onrender.com` adresinde yayına alınacaktır.

*(İsteğe bağlı: Frontend'i ayrı olarak Vercel'de barındırmak isterseniz `vercel.json` hazırdır; Vercel ortam değişkenine `VITE_API_BASE=https://iddaa-radar-xxx.onrender.com` eklemeniz yeterlidir.)*

---

## 5. `cron-job.org` ile Bilgisayar Kapalıyken 7/24 Çalıştırma
Render Free 15 dakika trafik gelmezse uyur. Bunu önlemek ve her 12 dakikada bir canlı veri çekmek için:
1. [cron-job.org](https://cron-job.org) üzerinden ücretsiz hesap açın.
2. **Create Cronjob** tıklayın:
   - **URL:** `https://iddaa-radar-xxx.onrender.com/api/cron/sync`
   - **Execution schedule:** `Every 12 minutes` (`*/12 * * * *`)
   - **Request Method:** `POST`
   - **Headers:** `x-cron-secret` = Render'daki `CRON_SECRET` değeriniz
3. Endpoint arka plan görevini başlatıp anında `202 Accepted` döndüğü için `cron-job.org` asla 30 saniye timeout'a düşmez ve sunucunuz 7/24 uyanık kalır.

---

## 6. Telefonda "Ana Ekrana Ekle" ve Bildirimleri Açma
- **Android (Chrome):** Yayın URL'sini açın -> Sağ üst `⋮` menü -> **Ana ekrana ekle / Uygulamayı yükle** -> Açtıktan sonra üst bardaki **Bildirim** zil ikonuna dokunup izin verin.
- **iOS (Safari, iOS 16.4+):** Yayın URL'sini Safari'de açın -> Alt ortadaki **Paylaş** ikonu -> **Ana Ekrana Ekle** -> Ana ekrandaki ikondan uygulamayı açın ve üst bardaki **Bildirim** zil ikonuna dokunup izin verin.
