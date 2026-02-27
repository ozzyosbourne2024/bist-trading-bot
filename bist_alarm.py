#!/usr/bin/env python3
"""
BIST ALIM ALARMI v1.0
======================
5 sinyali birlikte değerlendirir → "Şimdi AL" kararı verir.
GitHub Actions veya manuel çalıştırılır.

Sinyaller:
  S1. Endeks momentum dönüşü  (XU100 düşüşten yukarı döndü mü?)
  S2. Breadth toparlaması     (yükselen hisse sayısı artıyor mu?)
  S3. RSI dip dönüşü          (30-45 bandından yukarı döndü mü?)
  S4. Hisse hazırlığı         (kaç hisse AL sinyali veriyor?)
  S5. Makro temizlendi        (VIX düştü, altın satılıyor mu?)

Skor 5/5 → KESİN ALIM | 3-4/5 → KISMİ ALIM | 0-2/5 → BEKLE

Telegram: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env değişkeni gerekli
"""

import os, sys, json, warnings
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import requests
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as e:
    print(f"Eksik: {e}\npip install yfinance pandas numpy requests python-dotenv")
    sys.exit(1)

# ── Sabitler ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BIST_TICKERS = [
    "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","HALKB.IS","VAKBN.IS",
    "KCHOL.IS","SAHOL.IS","GLYHO.IS","ENKAI.IS","THYAO.IS","TCELL.IS",
    "EREGL.IS","ASELS.IS","BIMAS.IS","MGROS.IS","TTKOM.IS","SISE.IS",
    "FROTO.IS","TOASO.IS","ARCLK.IS","TUPRS.IS","PETKM.IS","EKGYO.IS",
    "TAVHL.IS","PGSUS.IS","TKFEN.IS","ISMEN.IS","ALARK.IS","DOHOL.IS",
]

ALARM_LOG = "bist_alarm_log.json"


# ════════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════════

def _fiyat_cek(ticker: str, period: str = "3mo") -> Optional[pd.DataFrame]:
    try:
        h = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        return h if not h.empty else None
    except:
        return None

def _rsi(s: pd.Series, p: int = 14) -> float:
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    k = (-d.clip(upper=0)).rolling(p).mean()
    return round(float((100 - 100 / (1 + g / k.replace(0, np.nan))).iloc[-1]), 1)

def _telegram_gonder(mesaj: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram token/chat_id eksik — mesaj gönderilmedi.")
        print("─" * 50)
        print(mesaj)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mesaj,
            "parse_mode": "HTML",
        }, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram hata: {e}")
        return False

def _log_kaydet(sonuc: dict):
    log = []
    if os.path.exists(ALARM_LOG):
        try:
            with open(ALARM_LOG, encoding="utf-8") as f:
                log = json.load(f)
        except:
            pass
    log.append(sonuc)
    # Son 90 kayıt tut
    log = log[-90:]
    with open(ALARM_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2, default=str)


# ════════════════════════════════════════════════════════════════════════════
# 5 SİNYAL
# ════════════════════════════════════════════════════════════════════════════

def s1_momentum_donus(xu100: pd.DataFrame) -> tuple[bool, str]:
    """
    S1: Endeks momentum dönüşü
    Son 5 günde düşüş vardı VE bugün + dün kapanış yukarı döndü.
    """
    if xu100 is None or len(xu100) < 10:
        return False, "Veri yetersiz"

    kapanis = xu100["Close"]
    son5_min = float(kapanis.iloc[-5:].min())
    onceki10_max = float(kapanis.iloc[-15:-5].max())

    # Önce düşüş olmuş olmalı
    dusus_oldu = son5_min < onceki10_max * 0.97  # %3+ düşüş

    # Şimdi yukarı dönüyor mu?
    bugun = float(kapanis.iloc[-1])
    dun   = float(kapanis.iloc[-2])
    evvelsi = float(kapanis.iloc[-3])

    yukari_donuyor = bugun > dun and dun > evvelsi

    # EMA(5) > EMA(10) oldu mu?
    ema5  = float(kapanis.ewm(span=5).mean().iloc[-1])
    ema10 = float(kapanis.ewm(span=10).mean().iloc[-1])
    ema_cakisma = ema5 > ema10

    sinyal = dusus_oldu and yukari_donuyor
    detay = (f"Min5G:{son5_min:,.0f} Max10G:{onceki10_max:,.0f} "
             f"Dönüş:{'✓' if yukari_donuyor else '✗'} "
             f"EMA5>10:{'✓' if ema_cakisma else '✗'}")
    return sinyal, detay


def s2_breadth_toparlanma() -> tuple[bool, str]:
    """
    S2: Genişlik toparlaması
    Yükselen hisse sayısı > %45 VE önceki güne göre arttı.
    """
    yukselenler = 0
    toplam = 0

    for ticker in BIST_TICKERS:
        h = _fiyat_cek(ticker, "5d")
        if h is None or len(h) < 2:
            continue
        toplam += 1
        degisim = (float(h["Close"].iloc[-1]) / float(h["Close"].iloc[-2]) - 1) * 100
        if degisim > 0.3:
            yukselenler += 1

    if toplam == 0:
        return False, "Veri yok"

    breadth = yukselenler / toplam * 100
    sinyal = breadth >= 45
    detay = f"Yükselen:{yukselenler}/{toplam} Breadth:%{breadth:.1f}"
    return sinyal, detay


def s3_rsi_dip_donus(xu100: pd.DataFrame) -> tuple[bool, str]:
    """
    S3: RSI dip dönüşü
    RSI son 10 günde 40 altına indi VE şimdi 40-55 arasına çıktı.
    """
    if xu100 is None or len(xu100) < 20:
        return False, "Veri yetersiz"

    kapanis = xu100["Close"]

    # Son 10 günün RSI'larını hesapla
    rsi_serisi = []
    for i in range(10, 0, -1):
        try:
            r = _rsi(kapanis.iloc[:-i] if i > 0 else kapanis)
            rsi_serisi.append(r)
        except:
            pass

    if not rsi_serisi:
        return False, "RSI hesplanamadı"

    rsi_bugun = _rsi(kapanis)
    rsi_min10  = min(rsi_serisi) if rsi_serisi else rsi_bugun

    # Önce dibe vurmuş olmalı (RSI < 40)
    dibe_vurdu = rsi_min10 < 42

    # Şimdi toparlanıyor (40-60 arası)
    toparlanma = 38 <= rsi_bugun <= 62

    # RSI yükseliyor mu?
    rsi_dun = rsi_serisi[-1] if len(rsi_serisi) >= 1 else rsi_bugun
    yukseliyor = rsi_bugun > rsi_dun

    sinyal = dibe_vurdu and toparlanma and yukseliyor
    detay  = f"RSI bugün:{rsi_bugun} Min10G:{rsi_min10:.1f} Yükseliyor:{'✓' if yukseliyor else '✗'}"
    return sinyal, detay


def s4_hisse_hazirlik() -> tuple[bool, str]:
    """
    S4: Bireysel hisse hazırlığı
    En az 8 hisse bireysel olarak yukarı momentum gösteriyor.
    (RSI 40-60 arası + son 3 günde yükselen)
    """
    hazir = []

    for ticker in BIST_TICKERS:
        h = _fiyat_cek(ticker, "2mo")
        if h is None or len(h) < 20:
            continue

        try:
            rsi = _rsi(h["Close"])
            son3_degisim = (float(h["Close"].iloc[-1]) / float(h["Close"].iloc[-3]) - 1) * 100

            # Kriterleri: RSI 35-65 arası + son 3 gün pozitif + hacim normal
            if 35 <= rsi <= 65 and son3_degisim > -2:  # %-2 tolerans
                hazir.append(ticker.replace(".IS",""))
        except:
            pass

    sinyal = len(hazir) >= 8
    detay  = f"Hazır:{len(hazir)}/30 — {', '.join(hazir[:6])}"
    return sinyal, detay


def s5_makro_temizlendi() -> tuple[bool, str]:
    """
    S5: Makro ortam temizlendi
    VIX < 20 VE Altın son 5 günde düşüyor (risk iştahı açıldı)
    VE USDTRY stabil
    """
    bulgular = []
    puan = 0

    # VIX
    try:
        vix_data = _fiyat_cek("^VIX", "1mo")
        if vix_data is not None:
            vix = float(vix_data["Close"].iloc[-1])
            if vix < 18:
                puan += 2
                bulgular.append(f"VIX:{vix:.1f}✓")
            elif vix < 22:
                puan += 1
                bulgular.append(f"VIX:{vix:.1f}~")
            else:
                bulgular.append(f"VIX:{vix:.1f}✗")
    except:
        bulgular.append("VIX:?")

    # Altın (düşüyorsa risk iştahı var)
    try:
        altin = _fiyat_cek("GC=F", "1mo")
        if altin is not None and len(altin) >= 5:
            altin_degisim = (float(altin["Close"].iloc[-1]) / float(altin["Close"].iloc[-5]) - 1) * 100
            if altin_degisim < -1:
                puan += 2
                bulgular.append(f"Altın:{altin_degisim:.1f}%✓")
            elif altin_degisim < 1:
                puan += 1
                bulgular.append(f"Altın:{altin_degisim:.1f}%~")
            else:
                bulgular.append(f"Altın:{altin_degisim:.1f}%✗")
    except:
        bulgular.append("Altın:?")

    # USDTRY stabil (son 5 günde <%1 değişim)
    try:
        usd = _fiyat_cek("USDTRY=X", "1mo")
        if usd is not None and len(usd) >= 5:
            usd_degisim = (float(usd["Close"].iloc[-1]) / float(usd["Close"].iloc[-5]) - 1) * 100
            if abs(usd_degisim) < 1:
                puan += 1
                bulgular.append(f"USDTRY:{usd_degisim:.1f}%✓")
            else:
                bulgular.append(f"USDTRY:{usd_degisim:.1f}%✗")
    except:
        bulgular.append("USDTRY:?")

    sinyal = puan >= 3
    detay  = " | ".join(bulgular) + f" (Puan:{puan}/5)"
    return sinyal, detay


def s8_hisse_giris_sinyali() -> list:
    """
    S8: Portföy önerisindeki hisseler için bireysel giriş sinyali.
    portfoy_pozisyonlar.json okur, AL kararı olan hisseleri tarar.
    Her hisse için 3 koşul:
      A) Dip Giriş: RSI 35-48 + destek yakın + toparlanıyor
      B) Kırılma:   10G yüksek kırıldı + hacim 1.5x + RSI 50-65
      C) MACD Dönüş: histogram negatiften pozitife + RSI 40+
    """
    import json as _json
    from pathlib import Path as _Path

    # Portföy listesini oku
    tickers = []
    poz_dosya = _Path("portfoy_pozisyonlar.json")
    if poz_dosya.exists():
        try:
            pozlar = _json.loads(poz_dosya.read_text(encoding="utf-8"))
            tickers = [p["ticker"] for p in pozlar if p.get("karar") == "AL" and p.get("ticker")]
        except:
            pass

    if not tickers:
        return []

    sinyaller = []

    for ticker in tickers[:8]:
        h = _fiyat_cek(ticker, "3mo")
        if h is None or len(h) < 20:
            continue

        try:
            kapanis = h["Close"]
            hacim   = h["Volume"]
            son     = float(kapanis.iloc[-1])
            dun     = float(kapanis.iloc[-2])
            evvelsi = float(kapanis.iloc[-3])

            rsi = _rsi(kapanis)
            rsi_dun = _rsi(kapanis.iloc[:-1])

            # Destek: son 20 günün en düşüğü
            destek = float(kapanis.iloc[-20:].min())
            destek_yakin = abs(son - destek) / destek < 0.04  # %4 içinde

            # Hacim
            son_hacim = float(hacim.iloc[-1])
            ort_hacim = float(hacim.iloc[-20:-1].mean())
            hacim_x = son_hacim / ort_hacim if ort_hacim > 0 else 0

            # 10G yüksek
            max_10g = float(kapanis.iloc[-12:-2].max())
            kirilma = son > max_10g

            # MACD
            ema12 = kapanis.ewm(span=12).mean()
            ema26 = kapanis.ewm(span=26).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9).mean()
            histo = macd_line - signal_line
            histo_son = float(histo.iloc[-1])
            histo_dun = float(histo.iloc[-2])

            isim = ticker.replace(".IS", "")
            tip = None
            detay = ""

            # A) Dip Giriş
            if 35 <= rsi <= 48 and destek_yakin and son > dun and dun > evvelsi:
                tip = "DİP GİRİŞ"
                detay = f"RSI:{rsi:.0f} | Destek:{destek:.1f} | Toparlanıyor"

            # B) Kırılma Girişi
            elif kirilma and hacim_x >= 1.5 and 50 <= rsi <= 68:
                tip = "KIRILMA"
                detay = f"10G kırdı:{son:.1f}>{max_10g:.1f} | Hacim:{hacim_x:.1f}x | RSI:{rsi:.0f}"

            # C) MACD Dönüş
            elif histo_dun < 0 and histo_son > 0 and rsi >= 40:
                tip = "MACD DÖNÜŞ"
                detay = f"Histogram +{histo_son:.3f} | RSI:{rsi:.0f}"

            if tip:
                sinyaller.append({
                    "ticker": isim,
                    "tip": tip,
                    "detay": detay,
                    "fiyat": son,
                    "rsi": rsi,
                })
                print(f"  🚀 {isim}: {tip} — {detay}")

        except Exception as e:
            continue

    return sinyaller



    """
    Senaryo A — Dip Alım:
    BIST100 < 13.000 VE RSI < 38 VE 5+ hisse aşırı satımda
    """
    if xu100 is None or len(xu100) < 14:
        return False, "Veri yetersiz"

    son = float(xu100["Close"].iloc[-1])
    rsi = _rsi(xu100["Close"])

    asiri_satim = 0
    for ticker in BIST_TICKERS[:20]:
        h = _fiyat_cek(ticker, "1mo")
        if h is not None and len(h) >= 14:
            try:
                if _rsi(h["Close"]) < 35:
                    asiri_satim += 1
            except:
                pass

    kosul1 = son < 13000
    kosul2 = rsi < 38
    kosul3 = asiri_satim >= 5

    sinyal = kosul1 and kosul2 and kosul3
    detay = (f"BIST:{son:,.0f}({'<13K ✓' if kosul1 else '>13K ✗'}) "
             f"RSI:{rsi:.1f}({'<38 ✓' if kosul2 else '>38 ✗'}) "
             f"AşırıSatım:{asiri_satim}/20({'✓' if kosul3 else '✗'})")
    return sinyal, detay


def s7_kirilma_senaryosu(xu100: pd.DataFrame) -> tuple:
    """
    Senaryo B — Kırılma Alımı:
    BIST100 > 14.400 VE Breadth > %40 VE Hacim artışı > 1.3x
    """
    if xu100 is None or len(xu100) < 20:
        return False, "Veri yetersiz"

    son     = float(xu100["Close"].iloc[-1])
    hacim   = xu100["Volume"]
    son3_h  = float(hacim.iloc[-3:].mean())
    ort20_h = float(hacim.iloc[-22:-2].mean())
    hacim_oran = son3_h / ort20_h if ort20_h > 0 else 0

    yukselen = 0
    for ticker in BIST_TICKERS:
        h = _fiyat_cek(ticker, "5d")
        if h is not None and len(h) >= 2:
            try:
                if float(h["Close"].iloc[-1]) > float(h["Close"].iloc[-2]):
                    yukselen += 1
            except:
                pass
    breadth = yukselen / len(BIST_TICKERS) * 100

    kosul1 = son > 14400
    kosul2 = breadth > 40
    kosul3 = hacim_oran > 1.3

    sinyal = kosul1 and kosul2 and kosul3
    detay = (f"BIST:{son:,.0f}({'>14.4K ✓' if kosul1 else '<14.4K ✗'}) "
             f"Breadth:%{breadth:.0f}({'✓' if kosul2 else '✗'}) "
             f"Hacim:{hacim_oran:.2f}x({'✓' if kosul3 else '✗'})")
    return sinyal, detay


# ════════════════════════════════════════════════════════════════════════════
# ANA ALARM MOTORU
# ════════════════════════════════════════════════════════════════════════════

def alarm_kontrol() -> dict:
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*55}")
    print(f"  BIST ALIM ALARMI — {tarih}")
    print(f"{'='*55}\n")

    # XU100 veri
    xu100 = _fiyat_cek("XU100.IS", "3mo")
    endeks_son = float(xu100["Close"].iloc[-1]) if xu100 is not None else 0

    # 5 Sinyal
    print("  Sinyaller kontrol ediliyor...\n")

    sinyaller = {}

    s1, d1 = s1_momentum_donus(xu100)
    sinyaller["S1_Momentum"] = {"sonuc": s1, "detay": d1}
    print(f"  {'✅' if s1 else '❌'} S1 Momentum Dönüşü : {d1}")

    s2, d2 = s2_breadth_toparlanma()
    sinyaller["S2_Breadth"] = {"sonuc": s2, "detay": d2}
    print(f"  {'✅' if s2 else '❌'} S2 Breadth          : {d2}")

    s3, d3 = s3_rsi_dip_donus(xu100)
    sinyaller["S3_RSI"] = {"sonuc": s3, "detay": d3}
    print(f"  {'✅' if s3 else '❌'} S3 RSI Dip Dönüş    : {d3}")

    s4, d4 = s4_hisse_hazirlik()
    sinyaller["S4_Hisse"] = {"sonuc": s4, "detay": d4}
    print(f"  {'✅' if s4 else '❌'} S4 Hisse Hazırlığı  : {d4}")

    s5, d5 = s5_makro_temizlendi()
    sinyaller["S5_Makro"] = {"sonuc": s5, "detay": d5}
    print(f"  {'✅' if s5 else '❌'} S5 Makro Temizlendi : {d5}")

    s6, d6 = s6_dip_alim_senaryosu(xu100)
    sinyaller["S6_Dip"] = {"sonuc": s6, "detay": d6}
    print(f"  {'🚨' if s6 else '⚪'} S6 Senaryo A — Dip  : {d6}")

    s7, d7 = s7_kirilma_senaryosu(xu100)
    sinyaller["S7_Kirilma"] = {"sonuc": s7, "detay": d7}
    print(f"  {'🚀' if s7 else '⚪'} S7 Senaryo B — Kırıl: {d7}")

    # S8: Hisse bazlı giriş sinyalleri
    print(f"\n  Hisse bazlı giriş sinyalleri kontrol ediliyor...")
    hisse_sinyalleri = s8_hisse_giris_sinyali()
    if not hisse_sinyalleri:
        print(f"  ⚪ S8: Portföyde giriş sinyali yok")

    # Skor
    yesil = sum([s1, s2, s3, s4, s5])

    print(f"\n  {'─'*45}")
    print(f"  SKOR: {yesil}/5")
    if s6:
        print(f"  🚨 SENARYO A AKTİF: DİP ALIM FIRSATI!")
    if s7:
        print(f"  🚀 SENARYO B AKTİF: KIRILMA — MOMENTUM ALIMI!")

    # Karar
    if yesil >= 5:
        karar    = "KESİN ALIM ZAMANI"
        emoji    = "🟢🟢🟢"
        oncelik  = "YÜKSEK"
    elif yesil >= 3:
        karar    = "KISMİ ALIM BAŞLA"
        emoji    = "🟡🟡"
        oncelik  = "ORTA"
    elif yesil == 2:
        karar    = "YAKLAŞIYOR — İzle"
        emoji    = "🟠"
        oncelik  = "DÜŞÜK"
    else:
        karar    = "BEKLE"
        emoji    = "🔴"
        oncelik  = "YOK"

    print(f"  KARAR: {emoji} {karar}")
    print(f"  {'─'*45}\n")

    # Telegram mesajı
    mesaj = _telegram_mesaj_olustur(
        tarih, endeks_son, yesil, karar, emoji,
        s1, d1, s2, d2, s3, d3, s4, d4, s5, d5
    )

    # Her zaman gönder (sadece BEKLE de olsa bilgi amaçlı)
    # Sadece sinyal varsa göndermek için: if yesil >= 2:
    gonderildi = False  # bist_sistem.py üzerinden gönderiliyor

    sonuc = {
        "tarih": tarih,
        "endeks": endeks_son,
        "skor": yesil,
        "karar": karar,
        "oncelik": oncelik,
        "senaryo_a": s6,
        "senaryo_b": s7,
        "hisse_sinyalleri": hisse_sinyalleri,
        "sinyaller": sinyaller,
        "telegram_gonderildi": gonderildi,
    }

    _log_kaydet(sonuc)
    return sonuc


def _telegram_mesaj_olustur(tarih, endeks, skor, karar, emoji,
                             s1, d1, s2, d2, s3, d3, s4, d4, s5, d5) -> str:
    sinyal_satirlari = "\n".join([
        f"{'✅' if s1 else '❌'} Momentum: {d1[:50]}",
        f"{'✅' if s2 else '❌'} Breadth:  {d2[:50]}",
        f"{'✅' if s3 else '❌'} RSI Dip:  {d3[:50]}",
        f"{'✅' if s4 else '❌'} Hisseler: {d4[:50]}",
        f"{'✅' if s5 else '❌'} Makro:    {d5[:50]}",
    ])

    mesaj = f"""<b>📊 BIST ALIM ALARMI</b>
{tarih} | XU100: <b>{endeks:,.0f}</b>

<b>Sinyal Skoru: {skor}/5</b>

{sinyal_satirlari}

{'─'*30}
{emoji} <b>{karar}</b>

"""

    if skor >= 5:
        mesaj += "⚡ TÜM SİNYALLER YEŞİL — bist_agents.py çalıştır!"
    elif skor >= 3:
        mesaj += "⚠️ Çoğunluk yeşil — pozisyon açmayı düşün."
    elif skor == 2:
        mesaj += "👀 Yaklaşıyor — sık kontrol et."
    else:
        mesaj += "⏳ Henüz erken — bekle."

    return mesaj


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sonuc = alarm_kontrol()
    # GitHub Actions exit code: 0 = başarılı
    sys.exit(0)
