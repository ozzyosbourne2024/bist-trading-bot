"""
HAFTALIK RAPOR v1.0
════════════════════════════════════════════════════════════════
Her Cumartesi 08:00 TR saati çalışır.

Rapor içeriği:
  1. BIST100 haftalık performans
  2. En iyi 10 / en kötü 5 hisse
  3. Portföyümüz vs BIST100 karşılaştırması
  4. Altın & Gümüş haftalık performans (1 hafta önce vs şimdi)

Kurulum:
    pip install yfinance pandas numpy requests python-dotenv rich
"""

import os, json, warnings
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
PORTFOY_DOSYA    = "portfoy_pozisyonlar.json"

GELISTIRME_LOG = "gelistirme_log.json"


# ════════════════════════════════════════════════════════════════
# GELİŞTİRME LOGU
# ════════════════════════════════════════════════════════════════

def gelistirme_log_oku() -> list:
    """Bekleyen ve tamamlanan geliştirme önerilerini oku."""
    if not Path(GELISTIRME_LOG).exists():
        return []
    try:
        return json.loads(Path(GELISTIRME_LOG).read_text(encoding="utf-8"))
    except:
        return []


def gelistirme_log_kaydet(yeni_oneriler: list):
    """
    Yeni önerileri loga ekle.
    Her öneri: {id, tarih, oneri, durum: "BEKLIYOR"|"YAPILDI"|"REDDEDILDI"}
    """
    log = gelistirme_log_oku()
    mevcut_ids = {g["id"] for g in log}

    for oneri_metni in yeni_oneriler:
        oneri_metni = oneri_metni.strip()
        if not oneri_metni:
            continue
        # Aynı öneri tekrar eklenmesin (benzerlik kontrolü)
        tekrar = any(oneri_metni[:40] in g["oneri"] for g in log if g["durum"] == "BEKLIYOR")
        if tekrar:
            continue
        yeni_id = f"G{datetime.now().strftime('%Y%m%d')}_{len(log)+1:03d}"
        if yeni_id in mevcut_ids:
            continue
        log.append({
            "id":    yeni_id,
            "tarih": datetime.now().strftime("%Y-%m-%d"),
            "oneri": oneri_metni,
            "durum": "BEKLIYOR",
            "uygulama_tarihi": None,
        })

    Path(GELISTIRME_LOG).write_text(
        json.dumps(log, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return log


def ai_onerilerini_parse(yorum: str) -> list:
    """AI yorumundan 🛠️ bölümündeki maddeleri çıkar."""
    oneriler = []
    bolum = False
    for satir in yorum.splitlines():
        if "🛠️" in satir or "KOD" in satir.upper():
            bolum = True
            continue
        if bolum:
            # Sonraki başlık gelince dur
            if any(x in satir for x in ["💡","✅","❌","🔍","📅","🤖"]):
                break
            s = satir.strip().lstrip("•-*·").strip()
            if len(s) > 15:
                oneriler.append(s)
    return oneriler


def bekleyen_oneriler_ozet() -> str:
    """Bekleyen geliştirme önerilerini mesaj formatında döndür."""
    log = gelistirme_log_oku()
    bekleyenler = [g for g in log if g["durum"] == "BEKLIYOR"]
    yapilanlar  = [g for g in log if g["durum"] == "YAPILDI"]

    if not bekleyenler and not yapilanlar:
        return ""

    satirlar = ["\n<b>🛠️ GELİŞTİRME LOGU</b>"]

    if bekleyenler:
        satirlar.append(f"  Bekleyen: {len(bekleyenler)} öneri")
        for g in bekleyenler[-5:]:  # Son 5 bekleyen
            satirlar.append(f"  ⏳ [{g['id']}] {g['oneri'][:70]}")

    if yapilanlar:
        son_yapilan = sorted(yapilanlar, key=lambda x: x.get("uygulama_tarihi",""), reverse=True)[:3]
        satirlar.append(f"  Son uygulananlar:")
        for g in son_yapilan:
            satirlar.append(f"  ✅ [{g['id']}] {g['oneri'][:60]}")

    return "\n".join(satirlar)


BIST100_TICKERS = [
    "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","HALKB.IS","VAKBN.IS",
    "KCHOL.IS","SAHOL.IS","AGHOL.IS","DOHOL.IS","GLYHO.IS",
    "FROTO.IS","TOASO.IS","EREGL.IS","ARCLK.IS","VESTL.IS","OTKAR.IS",
    "ASELS.IS","LOGO.IS","NETAS.IS","KAREL.IS","INDES.IS",
    "TUPRS.IS","PETKM.IS","ODAS.IS",
    "THYAO.IS","PGSUS.IS","TAVHL.IS",
    "TCELL.IS","TTKOM.IS",
    "BIMAS.IS","MGROS.IS","SOKM.IS","ULKER.IS","CCOLA.IS","AEFES.IS",
    "TATGD.IS","MERKO.IS","BANVT.IS","PENGD.IS","EKGYO.IS",
    "ISGYO.IS","KLGYO.IS","ENKAI.IS","TKFEN.IS","SISE.IS",
    "ISDMR.IS","KRDMD.IS","ISFIN.IS","ISMEN.IS","ALARK.IS",
    "BRISA.IS","KORDS.IS","GESAN.IS","SELEC.IS","MAVI.IS",
    "GUBRF.IS","AKCNS.IS","CIMSA.IS",
]


# ════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════

def _yf_haftalik(ticker: str) -> Optional[float]:
    """Son 7 günlük değişim yüzdesi."""
    try:
        df = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=True)
        if df is None or len(df) < 2:
            return None
        son  = float(df["Close"].iloc[-1])
        once = float(df["Close"].iloc[0])
        return (son / once - 1) * 100
    except:
        return None


def _yf_fiyat_hafta_once(ticker: str) -> tuple:
    """(hafta_once_fiyat, bugun_fiyat) döndür."""
    try:
        df = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=True)
        if df is None or len(df) < 2:
            return None, None
        return float(df["Close"].iloc[0]), float(df["Close"].iloc[-1])
    except:
        return None, None


def _telegram(mesaj: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(mesaj)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "HTML"},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram hata: {e}")
        return False


def _portfoy_oku() -> dict:
    """portfoy_pozisyonlar.json oku."""
    for yol in [PORTFOY_DOSYA, f"raporlar/{PORTFOY_DOSYA}"]:
        if Path(yol).exists():
            try:
                return json.loads(Path(yol).read_text(encoding="utf-8"))
            except:
                pass
    return {}


# ════════════════════════════════════════════════════════════════
# BIST100 HAFTALIK TARAMA
# ════════════════════════════════════════════════════════════════

def bist_haftalik_tara() -> list:
    """Tüm BIST100 hisselerini haftalık değişime göre tara."""
    print("  BIST100 taranıyor...")
    sonuclar = []
    for i, ticker in enumerate(BIST100_TICKERS):
        degisim = _yf_haftalik(ticker)
        if degisim is not None:
            isim = ticker.replace(".IS", "")
            sonuclar.append({"ticker": isim, "degisim": degisim})
        if (i + 1) % 10 == 0:
            print(f"    {i+1}/{len(BIST100_TICKERS)} tarandı...")
    sonuclar.sort(key=lambda x: x["degisim"], reverse=True)
    return sonuclar


# ════════════════════════════════════════════════════════════════
# PORTFÖY PERFORMANS
# ════════════════════════════════════════════════════════════════

def portfoy_performans(bist_sonuclar: list) -> dict:
    """Portföydeki hisselerin haftalık performansını hesapla."""
    kayit = _portfoy_oku()
    pozisyonlar = kayit.get("pozisyonlar", {})

    if not pozisyonlar:
        return {}

    bist_map = {h["ticker"]: h["degisim"] for h in bist_sonuclar}

    hisse_perf = []
    for ticker, poz in pozisyonlar.items():
        degisim = bist_map.get(ticker)
        if degisim is None:
            degisim = _yf_haftalik(ticker + ".IS")
        agirlik = poz.get("agirlik_pct", 0) / 100
        hisse_perf.append({
            "ticker":  ticker,
            "degisim": degisim,
            "agirlik": agirlik,
            "giris":   poz.get("giris_fiyati"),
            "hedef":   poz.get("hedef"),
            "stop":    poz.get("stop"),
        })

    # Ağırlıklı ortalama
    toplam_agirlik = sum(h["agirlik"] for h in hisse_perf if h["degisim"] is not None)
    if toplam_agirlik > 0:
        portfoy_getiri = sum(
            h["degisim"] * h["agirlik"]
            for h in hisse_perf if h["degisim"] is not None
        ) / toplam_agirlik
    else:
        portfoy_getiri = None

    return {
        "hisseler": sorted(hisse_perf, key=lambda x: x["degisim"] or 0, reverse=True),
        "portfoy_getiri": portfoy_getiri,
    }


# ════════════════════════════════════════════════════════════════
# ALTIN & GÜMÜŞ HAFTALIK
# ════════════════════════════════════════════════════════════════

def altin_gumus_haftalik() -> dict:
    """Altın ve gümüş 1 haftalık performans."""
    print("  Altın & Gümüş verisi çekiliyor...")
    sonuc = {}
    for isim, ticker in [("ALTIN", "GC=F"), ("GUMUS", "SI=F")]:
        once, simdi = _yf_fiyat_hafta_once(ticker)
        if once and simdi:
            deg = (simdi / once - 1) * 100
            sonuc[isim] = {
                "hafta_once": once,
                "simdi":      simdi,
                "degisim":    deg,
            }
    return sonuc


# ════════════════════════════════════════════════════════════════
# MESAJ OLUŞTUR
# ════════════════════════════════════════════════════════════════

def mesaj_olustur(
    tarih: str,
    bist_endeks: tuple,
    bist_sirali: list,
    portfoy: dict,
    ag: dict,
    yorum: str = "",
) -> str:

    s = []
    s.append(f"<b>📊 HAFTALIK RAPOR — {tarih}</b>")

    # ── BIST100 ──────────────────────────────────────────────────
    bist_once, bist_simdi, bist_deg = bist_endeks
    if bist_deg is not None:
        yon  = "↑" if bist_deg > 0 else "↓"
        renk = "🟢" if bist_deg > 0 else "🔴"
        s.append(f"\n{renk} <b>BIST100: {bist_once:,.0f} → {bist_simdi:,.0f} | {bist_deg:+.1f}% {yon}</b>")

    # ── En iyi 10 ────────────────────────────────────────────────
    s.append(f"\n<b>🏆 HAFTALIK EN İYİ 10</b>")
    for i, h in enumerate(bist_sirali[:10], 1):
        yon = "↑" if h["degisim"] > 0 else "↓"
        s.append(f"  {i:2d}. {h['ticker']:<6} {h['degisim']:+.1f}% {yon}")

    # ── En kötü 5 ────────────────────────────────────────────────
    s.append(f"\n<b>📉 EN KÖTÜ 5</b>")
    for h in bist_sirali[-5:]:
        yon = "↑" if h["degisim"] > 0 else "↓"
        s.append(f"  {h['ticker']:<6} {h['degisim']:+.1f}% {yon}")

    # ── Portföy ──────────────────────────────────────────────────
    if portfoy.get("hisseler"):
        s.append(f"\n<b>💼 PORTFÖYÜMܠ</b>")
        for h in portfoy["hisseler"]:
            deg = h["degisim"]
            if deg is None:
                s.append(f"  {h['ticker']:<6} —")
                continue
            yon  = "↑" if deg > 0 else "↓"
            renk = "✅" if deg > (bist_deg or 0) else "❌"
            s.append(f"  {renk} {h['ticker']:<6} {deg:+.1f}% {yon}")

        pg = portfoy.get("portfoy_getiri")
        if pg is not None and bist_deg is not None:
            fark = pg - bist_deg
            sonuc = "BIST100'ü GEÇTİ 🎉" if fark > 0 else "BIST100'ün Altında 😔"
            s.append(f"\n  Portföy: <b>{pg:+.1f}%</b> | BIST100: {bist_deg:+.1f}% | Fark: {fark:+.1f}% → {sonuc}")

    # ── Altın & Gümüş ────────────────────────────────────────────
    if ag:
        s.append(f"\n<b>⚡ ALTIN & GÜMÜŞ (haftalık)</b>")
        for isim, v in ag.items():
            deg  = v["degisim"]
            yon  = "↑" if deg > 0 else "↓"
            renk = "🟢" if deg > 0 else "🔴"
            em   = "🥇" if isim == "ALTIN" else "🥈"
            s.append(f"  {em} {isim}: {v['hafta_once']:.2f}$ → {v['simdi']:.2f}$ | {renk} {deg:+.1f}% {yon}")

    # ── Kaçırılan Fırsatlar ──────────────────────────────────────
    kacirildi = alim_firsatlari_analiz(bist_sirali, portfoy)
    if kacirildi:
        s.append(f"\n<b>🔍 KAÇIRILAN FIRSATLAR</b>")
        for h in kacirildi[:5]:
            s.append(f"  {h['ticker']:<6} {h['degisim']:+.1f}% ↑  (portföyde yoktu)")

    # ── Geliştirme Logu ──────────────────────────────────────────
    log_ozet = bekleyen_oneriler_ozet()
    if log_ozet:
        s.append(log_ozet)
    s.append(f"\n📋 Tüm öneriler: github.com'dan <code>gelistirme_log.json</code> indir")

    # ── AI Yorum & Eleştiri ──────────────────────────────────────
    if yorum:
        s.append(f"\n<b>🤖 AI DEĞERLENDİRME & ELEŞTİRİ</b>")
        s.append(yorum)

    return "\n".join(s)


# ════════════════════════════════════════════════════════════════
# AI YORUM & ELEŞTİRİ
# ════════════════════════════════════════════════════════════════

def alim_firsatlari_analiz(bist_sirali: list, portfoy: dict) -> list:
    """
    Hafta içinde portföyde olmayan ama iyi performans gösteren hisseleri bul.
    Bunlar 'kaçırılan fırsat' — neden seçilmedi?
    """
    portfoy_tickers = {h["ticker"] for h in portfoy.get("hisseler", [])}
    kacirildi = []
    for h in bist_sirali:
        if h["ticker"] not in portfoy_tickers and h["degisim"] > 3.0:
            kacirildi.append(h)
    return kacirildi[:10]


def ai_yorum(bist_deg: float, portfoy: dict, ag: dict, bist_sirali: list) -> str:
    """Groq ile haftalık performans analizi, eleştiri, kod geliştirme önerileri."""
    if not GROQ_API_KEY:
        return ""

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
    except ImportError:
        return ""

    hisseler   = portfoy.get("hisseler", [])
    pg         = portfoy.get("portfoy_getiri", 0) or 0
    kacirildi  = alim_firsatlari_analiz(bist_sirali, portfoy)
    en_iyi_10  = {h["ticker"] for h in bist_sirali[:10]}
    portfoy_t  = {h["ticker"] for h in hisseler}
    isabet     = portfoy_t & en_iyi_10

    # Kaybeden pozisyonlar — stop tetiklenmeli miydi?
    kaybedenler = [h for h in hisseler if (h["degisim"] or 0) < -5]

    ozet = f"""
HAFTALIK VERİ:
- BIST100: {bist_deg:+.1f}% | Portföy: {pg:+.1f}% | Fark: {pg - bist_deg:+.1f}%
- ALTIN: {ag.get('ALTIN',{}).get('degisim',0):+.1f}% | GÜMÜŞ: {ag.get('GUMUS',{}).get('degisim',0):+.1f}%

PORTFÖYÜMܠ:
{chr(10).join(f"  {h['ticker']}: {h['degisim']:+.1f}% (giriş:{h['giris']}, hedef:{h['hedef']}, stop:{h['stop']})" for h in hisseler if h['degisim'] is not None)}

KAÇIRILAN FIRSATLAR (portföyde yoktu, +3%+ yaptı):
{chr(10).join(f"  {h['ticker']}: {h['degisim']:+.1f}%" for h in kacirildi) if kacirildi else "  Yok"}

EN İYİ 10'DAN PORTFÖYDEKİLER: {', '.join(isabet) if isabet else 'Yok'}

AĞIR KAYBEDENLER (>-%5): {', '.join(f"{h['ticker']}:{h['degisim']:+.1f}%" for h in kaybedenler) if kaybedenler else 'Yok'}
"""

    prompt = f"""Sen bir kıdemli portföy yöneticisi ve quant analistisin. Aşağıdaki haftalık performans verisini analiz et.

{ozet}

Şu 6 başlığı Türkçe, kısa ve net yaz:

1. 💡 GENEL DEĞERLENDİRME (2 cümle): Ne oldu, portföy nasıl performans gösterdi?

2. ✅ NE İYİ GİTTİ (2 cümle): Hangi kararlar doğruydu?

3. ❌ HATALAR & ELEŞTİRİ (3 cümle): Açık sözlü ol. Hangi hisseler hayal kırıklığı yarattı? Stop-loss çalışmalı mıydı? Seçim kriterleri yeterli miydi?

4. 🔍 KAÇIRILAN FIRSATLAR (2 cümle): Yukarıdaki listedeki hisseler neden seçilmedi? Filtreleme sistemi bu hisseleri neden eledi?

5. 🛠️ KOD & SİSTEM GELİŞTİRME (3-4 madde): Mevcut algoritmada hangi somut değişiklikler yapılmalı? Örnek: "RSI filtresi çok katı, 45→50 yükseltilmeli", "Stop-loss ATR x2 yerine x1.5 olmalı", "Momentum filtresi haftalık yerine 3 günlük bakmalı" gibi spesifik öneriler.

6. 📅 ÖNÜMÜZDEK HAFTA (2 cümle): Dikkat edilmesi gereken sektörler veya hisseler.

Toplam max 250 kelime. Gerçekten eleştirel ve teknik ol."""

    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.3,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"  AI yorum hata: {e}")
        return ""




def main():
    tarih = datetime.now().strftime("%d %b %Y")
    print("=" * 55)
    print(f"  HAFTALIK RAPOR — {tarih}")
    print("=" * 55)

    # BIST100 endeks haftalık
    print("  BIST100 endeks çekiliyor...")
    bist_once, bist_simdi = _yf_fiyat_hafta_once("XU100.IS")
    bist_deg = ((bist_simdi / bist_once) - 1) * 100 if (bist_once and bist_simdi) else None

    # BIST100 hisse tarama
    bist_sirali = bist_haftalik_tara()

    # Portföy performans
    print("  Portföy hesaplanıyor...")
    portfoy = portfoy_performans(bist_sirali)

    # Altın & Gümüş
    ag = altin_gumus_haftalik()

    # AI yorum & eleştiri
    print("  AI yorumu hazırlanıyor...")
    yorum = ai_yorum(bist_deg, portfoy, ag, bist_sirali)

    # AI önerilerini parse edip loga kaydet
    if yorum:
        yeni_oneriler = ai_onerilerini_parse(yorum)
        if yeni_oneriler:
            gelistirme_log_kaydet(yeni_oneriler)
            print(f"  {len(yeni_oneriler)} yeni geliştirme önerisi loga eklendi")

    # Mesaj
    mesaj = mesaj_olustur(
        tarih,
        (bist_once, bist_simdi, bist_deg),
        bist_sirali,
        portfoy,
        ag,
        yorum,
    )

    print("\n" + mesaj.replace("<b>","").replace("</b>",""))
    print("\n  Telegram gönderiliyor...")
    ok = _telegram(mesaj)
    print(f"  {'✓ Gönderildi' if ok else '✗ Hata'}")
    print("=" * 55)

    # Raporu kaydet
    rapor = {
        "tarih": tarih,
        "bist100": {"hafta_once": bist_once, "simdi": bist_simdi, "degisim": bist_deg},
        "en_iyi_10": bist_sirali[:10],
        "en_kotu_5": bist_sirali[-5:],
        "portfoy": portfoy,
        "altin_gumus": ag,
    }
    Path("raporlar").mkdir(exist_ok=True)
    dosya = f"raporlar/haftalik_{datetime.now().strftime('%Y%m%d')}.json"
    with open(dosya, "w", encoding="utf-8") as f:
        json.dump(rapor, f, ensure_ascii=False, indent=2, default=str)
    print(f"  Rapor → {dosya}")


if __name__ == "__main__":
    main()
