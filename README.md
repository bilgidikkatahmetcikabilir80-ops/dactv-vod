# DACTVNET-VOD

DACTVNET YouTube kanalındaki (https://www.youtube.com/@dactvnet) herkese açık videoları otomatik bulup IPTV uygulamaları için `playlist.m3u` (VOD) dosyası üreten sistem.

- Videolar **indirilmez / saklanmaz**.
- M3U'da **geçici googlevideo adresi tutulmaz**; her kayıt `server.py` içindeki `/play/<video_id>` adresine işaret eder. Oynatıcı bu adresi açınca sunucu güncel googlevideo adresini yt-dlp ile çözer.
- Eski videolar asla silinmez; kayıtlar video ID'siyle tekilleştirilir.

## Nasıl çalışır?

```
GitHub Actions (6 saatte bir)          Sizin sunucunuz
  update.py ──► YouTube Data API v3      server.py  (/play/<id>)
     │            (uploads playlist)        ▲   yt-dlp ile güncel googlevideo adresi
     ▼                                      │
  videos.json + playlist.m3u  ◄── IPTV oynatıcısı M3U'yu raw.githubusercontent.com'dan alır
```

**Önemli:** GitHub yalnızca M3U'yu ve otomatik güncellemeyi barındırır. `server.py` (oynatma servisi) sürekli çalışan bir sunucu gerektirir (VPS, ev sunucusu/Raspberry Pi, Render, Fly.io vb.). GitHub Pages / Actions bunu barındıramaz.

## Dosyalar

| Dosya | Görev |
|---|---|
| `update.py` | API ile yeni videoları bulur, `videos.json` ve `playlist.m3u` üretir |
| `server.py` | `/play/<id>` oynatma servisi (yt-dlp, önbellek, Range, proxy/redirect) |
| `config.json` | Kanal ve sunucu ayarları |
| `videos.json` | Video veritabanı (otomatik üretilir, silinmez) |
| `playlist.m3u` | IPTV listesi (otomatik üretilir) |
| `.github/workflows/update.yml` | 6 saatlik otomasyon |
| `tests/` | Birim testleri (`python -m unittest discover -s tests`) |
| `Dockerfile` | Sunucu için isteğe bağlı kapsayıcı |

## Kurulum (adım adım)

### 1) GitHub deposu oluşturma
1. github.com → **New repository**. Ad: `DACTVNET-VOD`.
2. **Public** seçin (M3U'yu oynatıcıdan açabilmek için raw adresi herkese açık olmalı). Private depoda raw adres belirteç ister ve çoğu IPTV uygulamasında çalışmaz.
3. **Create repository**.

### 2) Proje dosyalarını yükleme
- Web arayüzü: **Add file → Upload files** ile bu klasördeki **tüm dosyaları** (`.github` klasörü ve `.gitignore` dahil) yükleyin. Klasör yapısının korunduğundan emin olun (`.github/workflows/update.yml`). `.github` gizli klasör olduğundan sürükle-bırak yerine git kullanmak daha kolaydır:
```bash
git clone https://github.com/KULLANICI/DACTVNET-VOD.git
# dosyaları klasöre kopyalayın, sonra:
cd DACTVNET-VOD
git add -A && git commit -m "İlk sürüm" && git push
```

### 3) YouTube Data API v3 etkinleştirme
1. https://console.cloud.google.com → yeni proje oluşturun.
2. **APIs & Services → Library → "YouTube Data API v3" → Enable**.
3. **Credentials → Create credentials → API key**.
4. Anahtarı kısıtlayın: **API restrictions → YouTube Data API v3**.
5. Varsayılan günlük kota 10.000 birimdir. Bu sistem çalıştırma başına yaklaşık 2–4 birim kullanır (günde ~16).

### 4) API anahtarını GitHub Secrets'e ekleme
Depo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `YOUTUBE_API_KEY`
- Secret: kopyaladığınız anahtar

Sunucu adresiniz hazırsa aynı sayfada **Variables** sekmesinden `SERVER_BASE_URL` değişkenini ekleyin (örn. `https://vod.alanadiniz.com`, sonda `/` olmadan). Alternatif: `config.json` içindeki `server_base_url` değerini düzenleyin.

### 5) GitHub Actions'ı etkinleştirme
1. Depo → **Settings → Actions → General → Workflow permissions → Read and write permissions** → Save. (Otomatik commit için gerekli.)
2. **Actions** sekmesinde iş akışlarını etkinleştirin ("I understand my workflows, go ahead and enable them").

### 6) İlk çalıştırma
**Actions → Update DACTVNET playlist → Run workflow**. İlk çalıştırmada tüm herkese açık videolar eklenir. Başarılıysa `playlist.m3u` ve `videos.json` otomatik commit edilir. Sonraki çalıştırmalar 6 saatte bir otomatik yapılır. Hata olursa çalıştırma kırmızı olur ve **update-log** adlı log dosyası Artifacts bölümüne yüklenir.

> Eski bir videonun sonradan "herkese açık" yapılması gibi durumlar için **Run workflow → full** seçeneğiyle tam tarama yapabilirsiniz (yerelde: `python update.py --full`).

### 7) M3U adresini alma
```
https://raw.githubusercontent.com/KULLANICI/DACTVNET-VOD/main/playlist.m3u
```
(`KULLANICI` yerine GitHub kullanıcı adınızı yazın; ana dal `main` değilse düzeltin.)

### 8) IPTV oynatıcısına ekleme
Oynatıcıda "Playlist ekle / M3U URL" bölümüne yukarıdaki adresi yapıştırın. Oynatma servisi (`server.py`) çalışıyor olmalıdır.

## Oynatma servisini çalıştırma

```bash
pip install -r requirements.txt
export VIDEOS_URL="https://raw.githubusercontent.com/KULLANICI/DACTVNET-VOD/main/videos.json"
gunicorn -w 1 --threads 16 -b 0.0.0.0:8080 server:app
# kontrol:  curl http://localhost:8080/health
```
Docker: `docker build -t dactvnet-vod . && docker run -p 8080:8080 -e VIDEOS_URL=... dactvnet-vod`

HTTPS için servisin önüne bir ters vekil (Caddy/nginx/Cloudflare Tunnel) koyun; birçok oynatıcı düz HTTP'yi engeller.

| Değişken | Açıklama |
|---|---|
| `STREAM_MODE` | `proxy` (varsayılan; sunucu akışı aktarır, Range destekli) veya `redirect` (302 ile googlevideo'ya yönlendirir) |
| `VIDEOS_URL` | videos.json ham adresi (yoksa yerel `videos.json`) |
| `REQUIRE_KNOWN` | `1` (varsayılan): yalnızca listedeki ID'ler oynatılır (sunucunuz açık YouTube vekiline dönüşmez) |
| `YTDLP_COOKIES` | İsteğe bağlı `cookies.txt` yolu (bot doğrulaması için) |
| `MAX_STREAMS` | Eşzamanlı akış sınırı (varsayılan 20) |

**Neden varsayılan proxy?** googlevideo adresleri genellikle çözümleyen IP'ye bağlıdır. Sunucu ile oynatıcı farklı IP'deyse `redirect` modu büyük olasılıkla 403 verir. `redirect` yalnızca sunucu ve oynatıcı aynı ağdaysa (evde) mantıklıdır ve bant genişliği tasarrufu sağlar.

## Güvenlik
- API anahtarı koda yazılmaz; yalnızca GitHub Secrets / ortam değişkeni. Hata loglarına anahtar sızmaz (test edildi).
- `video_id` sıkı doğrulanır (11 karakter); yalnızca `*.googlevideo.com` HTTPS adresleri açılır (SSRF koruması); bilinmeyen ID'ler reddedilir.
- Eşzamanlı çözümleme ve akış sayısı sınırlıdır.
- Servise kimlik doğrulama **yoktur**; internete açıyorsanız ters vekil seviyesinde IP/parola kısıtı ekleyin.

## Test sonuçları (dürüst özet)

**Yapıldı:** 13 birim testi geçti (`python -m unittest discover -s tests`): sayfalama, bilinen videoda durma, özel video atlama, yinelenen kayıt engelleme, hiçbir kaydın silinmemesi, M3U biçimi/kaçış karakterleri, süresi dolan adresin yeniden çözülmesi, 403'te yeniden çözümleme, Range iletimi, desteklenmeyen format hatası, yabancı host reddi, API anahtarı sızıntısı. Bu testler **sahte (mock) YouTube/yt-dlp yanıtlarıyla** çalışır.

**Yapılmadı (geliştirme ortamında internet erişimi yoktu):**
- Gerçek YouTube Data API çağrısı
- Gerçek yt-dlp ile googlevideo çözümlemesi ve gerçek akış
- Herhangi bir IPTV oynatıcısında oynatma testi
- GitHub Actions'ın gerçek çalıştırılması

Bunları kurulumdan sonra kendiniz doğrulayın: `curl -I http://SUNUCU/play/VIDEO_ID` ve oynatıcıda deneme.

## Uyumluluk sınırları ve riskler
- **Her oynatıcıda çalışacağı garanti edilmez.** M3U yapısı standarttır, ancak sunucu bir MP4 akışı verir; oynatıcıların bu akışı ve Range isteklerini nasıl işlediği uygulamaya bağlıdır. Oynatıcıyı denemeden varsaymayın.
- Yalnızca **görüntü+ses birlikte tek dosya (genelde 360p/720p MP4)** formatlar desteklenir; YouTube yüksek çözünürlükleri ayrı video/ses akışı olarak sunar, bunlar bu sistemde yoktur. Canlı yayın/prömiyerler desteklenmez (açık hata mesajı döner).
- **Bot doğrulaması:** YouTube, bulut/veri merkezi IP'lerini sık sık "Sign in to confirm you're not a bot" ile engeller. Çözüm: ev/konut IP'sinde çalıştırmak veya `YTDLP_COOKIES`. Çerezler hesap erişimi içerir; gizli tutun, depoya koymayın.
- YouTube sık sık değişir; **yt-dlp'yi güncel tutun** (`pip install -U yt-dlp`).
- **Kullanım koşulları:** YouTube Hizmet Şartları, videoların resmi oynatıcı dışında doğrudan medya adresleriyle sunulmasını kısıtlayabilir; yt-dlp kullanımı da bu kapsamda gri alandır. Kendi kanalınızın içerikleri olsa da bu servisi kişisel/sınırlı kullanım için tutun, herkese açık dağıtmayın ve güncel şartları kendiniz gözden geçirin. Bu belge hukuki tavsiye değildir. Resmi ve riski en düşük yol, YouTube'un kendi oynatıcısı/gömme (embed) bağlantılarını kullanmaktır; ancak çoğu IPTV oynatıcısı bunu oynatamaz.
