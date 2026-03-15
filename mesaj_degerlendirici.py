"""
TELEGRAM MESAJ DEĞERLENDİRİCİ
Son gelen mesajları çeker, AI ile analiz eder,
geliştirme önerileri üretir ve loga kaydeder.
"""
import os, json, warnings
from datetime import datetime
from pathlib import Path
warnings.filterwarnings("ignore")
import requests
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
DEGERLENDIRME_LOG = "degerlendirme_log.json"


# ────────────────────────────────────────────────────────────
# TELEGRAM'DAN MESAJ ÇEK
# ────────────────────────────────────────────────────────────

def telegram_mesajlari_cek(limit: int = 10) -> list:
    """
    Bot'un gönderdiği son mesajları çeker.
    getUpdates ile chat geçmişinden alır.
    """
    if not TELEGRAM_TOKEN:
        print("  HATA: TELEGRAM_BOT_TOKEN yok")
        return []

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        r = requests.get(url, params={"limit": 100, "allowed_updates": ["message"]}, timeout=15)
        if r.status_code != 200:
            print(f"  Telegram hata: {r.status_code}")
            return []

        updates = r.json().get("result", [])
        mesajlar = []

        for upd in updates:
            msg = upd.get("message", {})
            text = msg.get("text", "")
            tarih = msg.get("date", 0)
            from_id = msg.get("from", {}).get("id", 0)
            chat_id = str(msg.get("chat", {}).get("id", ""))

            # Sadece bizim chat'ten gelen mesajlar
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue
            if not text or len(text) < 50:
                continue

            # Bot mesajları (from bot) veya bizim kanalımıza gelen mesajlar
            mesajlar.append({
                "tarih": datetime.fromtimestamp(tarih).strftime("%Y-%m-%d %H:%M"),
                "text":  text,
                "uzunluk": len(text),
            })

        # En son mesajları al, tekrar edenleri filtrele
        benzersiz = []
        goruldu = set()
        for m in reversed(mesajlar):
            anahtar = m["text"][:80]
            if anahtar not in goruldu:
                goruldu.add(anahtar)
                benzersiz.append(m)
            if len(benzersiz) >= limit:
                break

        return list(reversed(benzersiz))

    except Exception as e:
        print(f"  Telegram mesaj çekme hatası: {e}")
        return []


def son_bot_mesaji_cek() -> str:
    """
    Bot'un gönderdiği en son rapor mesajını bul.
    BIST SİSTEM veya ALTIN içeren mesajı tercih et.
    """
    mesajlar = telegram_mesajlari_cek(limit=20)
    if not mesajlar:
        return ""

    # BIST raporu olan mesajı bul
    for m in reversed(mesajlar):
        t = m["text"]
        if any(x in t for x in ["BIST SİSTEM", "BIST ALARM", "PORTFÖY", "ALTIN", "GÜMÜŞ"]):
            return t

    # Yoksa en uzun mesaj
    return max(mesajlar, key=lambda x: x["uzunluk"])["text"] if mesajlar else ""


# ────────────────────────────────────────────────────────────
# AI DEĞERLENDİRME
# ────────────────────────────────────────────────────────────

def ai_degerlendir(mesaj: str) -> dict:
    """
    Mesajı AI ile değerlendir:
    - Eksik bilgiler
    - Hatalı/yanıltıcı veriler
    - Format sorunları
    - Geliştirme önerileri
    """
    if not GROQ_API_KEY:
        print("  GROQ_API_KEY yok")
        return {}
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
    except:
        return {}

    prompt = f"""Aşağıdaki Telegram mesajı bir BIST borsa takip botundan geliyor.
Bu mesajı eleştirel bir yazılım geliştirici ve finans analisti gözüyle değerlendir.

MESAJ:
{mesaj}

Şu 5 başlığı Türkçe, net ve madde madde yaz:

1. ✅ DOĞRU ÇALIŞAN: Mesajda hangi bilgiler doğru ve faydalı?

2. ❌ EKSİK BİLGİLER: Hangi veriler mesajda olmalıydı ama yok?
   Örnek: stop-loss seviyeleri, portföydeki tüm hisseler, gün içi değişim gibi.

3. ⚠️ YANILTICI/HATALI: Hangi ifadeler kafa karıştırıcı veya yanlış?
   Örnek: "?/5 VERİ YOK" gibi açıklanmamış hatalar.

4. 🎨 FORMAT: Mesaj okunabilir mi? Daha iyi nasıl düzenlenebilir?

5. 🛠️ KOD GELİŞTİRME ÖNERİLERİ: Bu mesajı üreten kodda yapılması gereken
   somut değişiklikler neler? Her öneri için kısa teknik açıklama ver.
   (Bunlar gelistirme_log.json'a kaydedilecek)

Toplam 250-300 kelime. Teknik ve somut ol."""

    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3,
        )
        return {"yorum": r.choices[0].message.content.strip()}
    except Exception as e:
        print(f"  AI hata: {e}")
        return {}


def oneriler_parse(yorum: str) -> list:
    """🛠️ bölümündeki maddeleri çıkar — gelistirme_log'a eklenecek."""
    oneriler = []
    bolum = False
    for satir in yorum.splitlines():
        if "🛠️" in satir or "KOD GEL" in satir.upper():
            bolum = True
            continue
        if bolum:
            if any(x in satir for x in ["✅","❌","⚠️","🎨","1.","2.","3.","4."]):
                break
            s = satir.strip().lstrip("•-*·0123456789.)").strip()
            if len(s) > 15:
                oneriler.append(s)
    return oneriler


# ────────────────────────────────────────────────────────────
# GELİŞTİRME LOGU
# ────────────────────────────────────────────────────────────

def gelistirme_log_ekle(oneriler: list):
    """Önerileri gelistirme_log.json'a ekle."""
    log = []
    if Path(DEGERLENDIRME_LOG).exists():
        try:
            log = json.loads(Path(DEGERLENDIRME_LOG).read_text(encoding="utf-8"))
        except:
            pass

    mevcut = {g["oneri"][:40] for g in log if g.get("durum") == "BEKLIYOR"}
    eklenenler = []

    for oneri in oneriler:
        if oneri[:40] in mevcut:
            continue
        yeni_id = f"D{datetime.now().strftime('%Y%m%d')}_{len(log)+1:03d}"
        log.append({
            "id":    yeni_id,
            "tarih": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "kaynak": "mesaj_degerlendirici",
            "oneri": oneri,
            "durum": "BEKLIYOR",
            "uygulama_tarihi": None,
        })
        eklenenler.append(yeni_id)

    Path(DEGERLENDIRME_LOG).write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return eklenenler


# ────────────────────────────────────────────────────────────
# DEĞERLENDİRMEYİ TELEGRAM'A GÖNDER
# ────────────────────────────────────────────────────────────

def telegram_gonder(mesaj: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.status_code == 200
    except:
        return False


def rapor_mesaji_olustur(mesaj_ozet: str, yorum: str, eklenenler: list) -> str:
    zaman = datetime.now().strftime("%d.%m.%Y %H:%M")
    s = []
    s.append(f"<b>🔍 MESAJ DEĞERLENDİRME — {zaman}</b>")
    s.append(f"<i>Değerlendirilen: {mesaj_ozet[:60]}...</i>")
    s.append("")
    s.append(yorum)
    if eklenenler:
        s.append(f"\n<b>📋 Geliştirme loguna eklendi: {', '.join(eklenenler)}</b>")
        s.append(f"<i>degerlendirme_log.json güncellendi</i>")
    return "\n".join(s)


# ────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  TELEGRAM MESAJ DEĞERLENDİRİCİ")
    print(f"  {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 55)

    # 1. Son mesajı çek
    print("\n  Son Telegram mesajı çekiliyor...")
    mesaj = son_bot_mesaji_cek()

    if not mesaj:
        print("  Mesaj bulunamadı!")
        print("  Not: getUpdates sadece son 24 saati gösterir.")
        print("  Bot yeni mesaj gönderdikten sonra çalıştır.")
        return

    print(f"  Mesaj bulundu ({len(mesaj)} karakter)")
    print(f"  İlk 100 karakter: {mesaj[:100]}...")

    # 2. AI değerlendirme
    print("\n  AI değerlendirme yapılıyor...")
    sonuc = ai_degerlendir(mesaj)
    yorum = sonuc.get("yorum", "")

    if not yorum:
        print("  AI yanıt vermedi")
        return

    # 3. Önerileri loga ekle
    oneriler = oneriler_parse(yorum)
    eklenenler = []
    if oneriler:
        eklenenler = gelistirme_log_ekle(oneriler)
        print(f"  {len(oneriler)} öneri bulundu, {len(eklenenler)} yeni eklendi")
    
    # 4. Konsola yazdır
    print("\n" + "─" * 55)
    print(yorum)
    print("─" * 55)

    # 5. Telegram'a gönder
    rapor = rapor_mesaji_olustur(mesaj[:80], yorum, eklenenler)
    ok = telegram_gonder(rapor)
    print(f"\n  Telegram: {'✓ Gönderildi' if ok else '✗ Hata'}")
    if eklenenler:
        print(f"  Log: {DEGERLENDIRME_LOG} güncellendi")
    print("=" * 55)


if __name__ == "__main__":
    main()
