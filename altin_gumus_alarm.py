#!/usr/bin/env python3
"""
ALTIN & GÜMÜŞ ALARM SİSTEMİ v1.0
===================================
RSI yerine daha güvenilir 5 sinyal:

  S1. Momentum Kırılması  — fiyat önceki 20G direncini geçti mi?
  S2. Hacim Artışı        — son 3 günde hacim ortalamanın üzerinde mi?
  S3. RSI Çift Zaman      — 1H RSI + 4H RSI birlikte yükseliyor mu?
  S4. MACD Kesimi         — 1H MACD sinyal çizgisini yukarı kesti mi?
  S5. Makro / Dolar       — DXY zayıflıyor + VIX yüksek = altın için iyi

Her enstrüman için ayrı skor:
  5/5 → 🟢 KESİN ALIM
  3-4/5 → 🟡 İZLE / KISMİ ALIM
  0-2/5 → 🔴 BEKLE

GitHub Actions: 10:30 / 11:30 / 14:30 / 15:30 TR saati
"""

import os, sys, json, time, warnings
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

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
ALARM_LOG        = "altin_alarm_log.json"
FIYAT_LOG        = "raporlar/altin_fiyat_log.json"  # GitHub Actions'ta biriken log

ENSTRUMANLAR = {
    "ALTIN": {
        "futures":  "GC=F",
        "stooq":    "xauusd",   # Stooq spot — S1 için temiz günlük veri
        "spot_url": "https://api.gold-api.com/price/XAU",
        "birim":    "$/oz",
        "emoji":    "🥇",
    },
    "GUMUS": {
        "futures":  "SI=F",
        "stooq":    "xagusd",   # Stooq spot — S1 için temiz günlük veri
        "spot_url": "https://api.gold-api.com/price/XAG",
        "birim":    "$/oz",
        "emoji":    "🥈",
    },
}


# ════════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════════

def _stooq_gunluk(sembol: str, gun: int = 60) -> Optional[pd.DataFrame]:
    """Stooq.com'dan temiz günlük spot veri çek (rollover yok)."""
    from io import StringIO
    try:
        url = f"https://stooq.com/q/d/l/?s={sembol}&i=d"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or "Date" not in r.text:
            return None
        df = pd.read_csv(StringIO(r.text))
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").tail(gun).reset_index(drop=True)
        df.index = pd.DatetimeIndex(df["Date"])
        return df
    except:
        return None



def _indir(symbol: str, interval: str = "1h", period: str = "1mo",
           retries: int = 4) -> Optional[pd.DataFrame]:
    for _ in range(retries):
        try:
            df = yf.Ticker(symbol).history(interval=interval, period=period,
                                           auto_adjust=True)
            if not df.empty:
                return df
        except:
            pass
        time.sleep(1.5)
    return None

def _spot_fiyat(url: str) -> Optional[float]:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        if r.status_code == 200:
            return float(r.json().get("price", 0)) or None
    except:
        pass
    return None

def _macd(s: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_h = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sinyal = macd_h.ewm(span=9, adjust=False).mean()
    histo  = macd_h - sinyal
    return macd_h, sinyal, histo

def _telegram(mesaj: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram token eksik\n" + mesaj)
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

def _log(kayit: dict):
    log = []
    if os.path.exists(ALARM_LOG):
        try:
            with open(ALARM_LOG, encoding="utf-8") as f:
                log = json.load(f)
        except:
            pass
    log.append(kayit)
    log = log[-180:]
    with open(ALARM_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2, default=str)


# ════════════════════════════════════════════════════════════════════════════
# 5 SİNYAL
# ════════════════════════════════════════════════════════════════════════════

def s1_momentum_kirilmasi(df_stooq: pd.DataFrame, dun_kapanis: float = None) -> Tuple[bool, str]:
    """
    S1: Stooq spot veri ile momentum kirilmasi.
    Son bar = bugünkü anlık fiyat
    dun_kapanis = bir önceki bar (gerçek dün kapanışı)
    """
    if df_stooq is None or len(df_stooq) < 22:
        return False, "Stooq veri yetersiz"

    kapanis = df_stooq["Close"]
    bugun   = float(kapanis.iloc[-1])   # anlık
    dun     = dun_kapanis or float(kapanis.iloc[-2])  # gerçek dün
    max_20g = float(kapanis.iloc[-22:-2].max())
    max_10g = float(kapanis.iloc[-12:-2].max())
    min_20g = float(kapanis.iloc[-22:-2].min())

    # Guclu kirilma: 20 gunluk yuksek gecildi
    yukari_kirilma_20 = bugun > max_20g and dun > max_20g * 0.99

    # Erken sinyal: 10 gunluk yuksek gecildi + 2 gun ust uste yukselis
    yukari_kirilma_10 = (bugun > max_10g and dun > float(kapanis.iloc[-3]))

    asagi_kirilma = bugun < min_20g

    if yukari_kirilma_20:
        return True, f"Guclu kirilma! {bugun:.2f} > Max20G:{max_20g:.2f}"
    elif yukari_kirilma_10:
        return True, f"Erken sinyal: {bugun:.2f} > Max10G:{max_10g:.2f} (momentum var)"
    elif asagi_kirilma:
        return False, f"Asagi kirildi! {bugun:.2f} < Min20G:{min_20g:.2f}"
    else:
        fark_20 = ((bugun / max_20g) - 1) * 100
        fark_10 = ((bugun / max_10g) - 1) * 100
        return False, f"Bugun:{bugun:.2f} | 10G:{fark_10:.1f}% | 20G:{fark_20:.1f}%"


def s2_hacim_artisi(df_futures_gun: pd.DataFrame) -> Tuple[bool, str]:
    """
    S2: Futures günlük hacim analizi (hacimde rollover etkisi yok).
    GC=F / SI=F hacim verisi güvenilir.
    """
    if df_futures_gun is None or len(df_futures_gun) < 22:
        return False, "Futures hacim verisi yetersiz"

    hacim = df_futures_gun["Volume"]
    son3_ort = float(hacim.iloc[-3:].mean())
    ort20    = float(hacim.iloc[-22:-2].mean())

    if ort20 == 0:
        return False, "Hacim verisi yok"

    oran = son3_ort / ort20
    sinyal = oran >= 1.25
    detay  = f"Son3G:{son3_ort:,.0f} | Ort20G:{ort20:,.0f} | Oran:{oran:.2f}x"
    return sinyal, detay


def _rsi(seri: pd.Series, period: int = 14) -> float:
    """RSI hesapla, son değeri döndür."""
    delta = seri.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/period, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, 1e-9)
    rsi_s = 100 - (100 / (1 + rs))
    return float(rsi_s.iloc[-1])


def s3_rsi_cift_zaman(sembol: str) -> Tuple[bool, str]:
    """
    S3: 1H + 4H RSI Trend Takibi
    1H: yfinance interval="1h" direkt
    4H: yfinance interval="30m" → resample("4h") ile OHLCV doğru birleştirme
    Trend takibi: her iki zaman diliminde RSI > 50 ve yükseliyor
    Aşırı alım kontrolü: RSI > 78 ise dur
    """
    # 1H veri — direkt
    df_1h = _indir(sembol, interval="1h", period="7d")
    if df_1h is None or len(df_1h) < 20:
        return False, "1H veri yetersiz"
    k_1h = pd.to_numeric(df_1h["Close"], errors="coerce").dropna()
    rsi_1h_son = _rsi(k_1h)
    rsi_1h_dun = _rsi(k_1h.iloc[:-3])

    # 4H veri — 30 dakikadan OHLCV resample (daha doğru)
    df_30m = _indir(sembol, interval="30m", period="60d")
    if df_30m is None or len(df_30m) < 30:
        return False, "30M veri yetersiz"
    df_4h = df_30m.resample("4h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum"
    }).dropna()
    if len(df_4h) < 10:
        return False, "4H resample yetersiz"
    k_4h = pd.to_numeric(df_4h["Close"], errors="coerce").dropna()
    rsi_4h_son = _rsi(k_4h)
    rsi_4h_dun = _rsi(k_4h.iloc[:-2])

    # Trend takibi: her ikisi >45 ve yukseliyor (50 beklemek gec olabilir)
    # Guclu sinyal: ikisi de >50
    # Erken sinyal: ikisi de >45 ve 3 bar ust uste yukseliyor
    trend_1h_guclu = rsi_1h_son > 50 and rsi_1h_son > rsi_1h_dun
    trend_4h_guclu = rsi_4h_son > 50 and rsi_4h_son > rsi_4h_dun

    rsi_1h_3bar = _rsi(k_1h.iloc[:-5]) if len(k_1h) > 5 else rsi_1h_dun
    trend_1h_erken = rsi_1h_son > 45 and rsi_1h_son > rsi_1h_dun > rsi_1h_3bar
    trend_4h_erken = rsi_4h_son > 45 and rsi_4h_son > rsi_4h_dun

    asiri_alim = rsi_1h_son > 78 or rsi_4h_son > 78

    sinyal = (trend_1h_guclu and trend_4h_guclu and not asiri_alim) or \
             (trend_1h_erken and trend_4h_erken and not asiri_alim)

    yon_1h = "↑" if rsi_1h_son > rsi_1h_dun else "↓"
    yon_4h = "↑" if rsi_4h_son > rsi_4h_dun else "↓"
    erken = not (trend_1h_guclu and trend_4h_guclu) and sinyal
    if sinyal:
        durum = "Erken sinyal" if erken else "Trend onaylı"
    else:
        durum = "Trend yok" if not (trend_1h_erken or trend_4h_erken) else "Zayıf"
    detay = (f"1H:{rsi_1h_son:.1f}{yon_1h} "
             f"4H:{rsi_4h_son:.1f}{yon_4h} "
             f"{durum} "
             f"{'AşırıAlım' if asiri_alim else ''}")
    return sinyal, detay


def s4_macd_kesimi(df_1h: pd.DataFrame) -> Tuple[bool, str]:
    """
    S4: 1H MACD, sinyal çizgisini yukarı kesiyor mu?
    Hem kesim hem histogram pozitife dönüş aranır.
    """
    if df_1h is None or len(df_1h) < 35:
        return False, "1H veri yetersiz"

    kapanis = pd.to_numeric(df_1h["Close"], errors="coerce").dropna()
    macd_h, sinyal, histo = _macd(kapanis)

    histo_son     = float(histo.iloc[-1])
    histo_dun     = float(histo.iloc[-2])
    histo_evvelsi = float(histo.iloc[-3])
    histo_3oncesi = float(histo.iloc[-4])

    macd_son   = float(macd_h.iloc[-1])
    sinyal_son = float(sinyal.iloc[-1])

    # Guclu kesim: negatiften pozitife dondu
    kesim = histo_dun <= 0 and histo_son > 0

    # Momentum yukseliyor: histogram buyuyor (pozitif bolgede)
    momentum_yukseliyor = histo_son > histo_dun > histo_evvelsi and histo_son > 0

    # Erken sinyal: histogram 3 bar ust uste yukseliyor (hala negatif olsa da)
    erken_donus = (histo_son > histo_dun > histo_evvelsi > histo_3oncesi and
                   histo_son > histo_3oncesi * 0.3)  # ciddi yukselis

    sinyal_var = kesim or momentum_yukseliyor or erken_donus
    detay = (f"MACD:{macd_son:.3f} Histo:{histo_son:.3f} "
             f"{'Kesim✓' if kesim else ''}"
             f"{'Mom↑' if momentum_yukseliyor else ''}"
             f"{'Erken↑' if erken_donus and not kesim else ''}"
             f"{'Bekle' if not sinyal_var else ''}")
    return sinyal_var, detay


def s5_makro_dolar(df_gunluk: pd.DataFrame) -> Tuple[bool, str]:
    """
    S5: Makro ortam altın/gümüş için uygun mu?
    DXY (dolar endeksi) zayıflıyor + VIX yüksek = altın için pozitif

    Altın-DXY ters korelasyon: DXY yükselince altın düşer (r ≈ -0.8)
    Hem kısa vadeli trend hem korelasyon gücü takip edilir.
    """
    puan   = 0
    notlar = []

    # DXY — dolar zayıflıyorsa altın güçlenir
    # Ters korelasyon: DXY↑ = altın için negatif, DXY↓ = pozitif
    try:
        dxy = _indir("DX-Y.NYB", interval="1d", period="3mo")
        if dxy is not None and len(dxy) >= 20:
            dxy_son    = float(dxy["Close"].iloc[-1])
            dxy_10g    = float(dxy["Close"].iloc[-10])
            dxy_30g    = float(dxy["Close"].iloc[-22]) if len(dxy) >= 22 else float(dxy["Close"].iloc[0])
            dxy_kisa   = (dxy_son / dxy_10g  - 1) * 100   # 10 günlük
            dxy_uzun   = (dxy_son / dxy_30g  - 1) * 100   # 1 aylık

            # Korelasyon katsayısı: altın ile DXY (son 30 gün)
            if df_gunluk is not None and len(df_gunluk) >= 20:
                try:
                    altin_k = df_gunluk["Close"].iloc[-22:].reset_index(drop=True)
                    dxy_k   = dxy["Close"].iloc[-22:].reset_index(drop=True)
                    min_len = min(len(altin_k), len(dxy_k))
                    if min_len >= 10:
                        kor = float(altin_k.iloc[:min_len].corr(dxy_k.iloc[:min_len]))
                        kor_str = f" Kor:{kor:.2f}"
                    else:
                        kor_str = ""
                except:
                    kor_str = ""
            else:
                kor_str = ""

            # Trend değerlendirme
            if dxy_kisa < -1.5 and dxy_uzun < -2.0:
                puan += 3
                notlar.append(f"DXY:{dxy_kisa:.1f}%↓↓✓✓✓{kor_str}")
            elif dxy_kisa < -1.0:
                puan += 2
                notlar.append(f"DXY:{dxy_kisa:.1f}%↓✓✓{kor_str}")
            elif dxy_kisa < 0:
                puan += 1
                notlar.append(f"DXY:{dxy_kisa:.1f}%↓✓{kor_str}")
            elif dxy_kisa > 1.5:
                puan -= 1  # Güçlü dolar = altın için belirgin baskı
                notlar.append(f"DXY:{dxy_kisa:.1f}%↑↑✗✗{kor_str}")
            else:
                notlar.append(f"DXY:{dxy_kisa:.1f}%↑✗{kor_str}")
        else:
            notlar.append("DXY:?")
    except:
        notlar.append("DXY:?")

    # VIX — korku yüksekse altın güvenli liman
    try:
        vix_data = _indir("^VIX", interval="1d", period="1mo")
        if vix_data is not None:
            vix = float(vix_data["Close"].iloc[-1])
            if vix > 22:
                puan += 2
                notlar.append(f"VIX:{vix:.1f}↑✓✓")
            elif vix > 18:
                puan += 1
                notlar.append(f"VIX:{vix:.1f}~✓")
            else:
                notlar.append(f"VIX:{vix:.1f}↓✗")
    except:
        notlar.append("VIX:?")

    # 10Y Tahvil faizi düşüyorsa altın olumlu
    try:
        tnx = _indir("^TNX", interval="1d", period="1mo")
        if tnx is not None and len(tnx) >= 10:
            faiz_degisim = float(tnx["Close"].iloc[-1]) - float(tnx["Close"].iloc[-10])
            if faiz_degisim < -0.1:
                puan += 1
                notlar.append(f"10Y:{faiz_degisim:+.2f}✓")
            else:
                notlar.append(f"10Y:{faiz_degisim:+.2f}✗")
    except:
        notlar.append("10Y:?")

    sinyal = puan >= 3
    detay  = " | ".join(notlar) + f" (Puan:{puan}/6)"
    return sinyal, detay


# ════════════════════════════════════════════════════════════════════════════
# ANA ALARM
# ════════════════════════════════════════════════════════════════════════════




def enstruman_analiz(isim: str, cfg: dict) -> dict:
    print(f"\n  {'─'*40}")
    print(f"  {cfg['emoji']} {isim} analiz ediliyor...")

    futures_sym = cfg["futures"]

    # Stooq: temiz spot günlük veri — S1 için
    df_stooq = _stooq_gunluk(cfg["stooq"], gun=60)

    # yfinance futures: S2 hacim + S3/S4 RSI/MACD + S5 makro
    df_gun   = _indir(futures_sym, interval="1d", period="3mo")

    # 4H: 30 dakikadan OHLCV resample
    df_30m_fut = _indir(futures_sym, interval="30m", period="60d")
    if df_30m_fut is not None:
        df_4h_futures = df_30m_fut.resample("4h").agg({
            "Open": "first", "High": "max",
            "Low": "min", "Close": "last", "Volume": "sum"
        }).dropna()
    else:
        df_4h_futures = None

    # Anlık SPOT fiyat — gold-api.com (gerçek zamanlı)
    spot_goldapi = _spot_fiyat(cfg["spot_url"])

    # Anlık futures — yfinance 1m (ikincil kontrol)
    df_anlik = _indir(futures_sym, interval="1m", period="1d")
    anlik_fiyat = float(df_anlik["Close"].iloc[-1]) if df_anlik is not None and not df_anlik.empty else None

    # Stooq günlük CSV — SADECE S1 momentum referansı (dün kapanış)
    s1_ref = float(df_stooq["Close"].iloc[-1]) if df_stooq is not None else None
    fut_fiyat = float(df_gun["Close"].iloc[-1]) if df_gun is not None else None

    # Anlık gösterim: gold-api öncelikli
    spot = spot_goldapi or anlik_fiyat or fut_fiyat

    if spot_goldapi:
        print(f"  Spot    : {spot_goldapi:.2f} $/oz (gold-api anlık)")
    if anlik_fiyat:
        print(f"  Futures : {anlik_fiyat:.2f} $/oz ({futures_sym} yfinance 1m)")
    if s1_ref:
        print(f"  S1 ref  : {s1_ref:.2f} (stooq dün kapanış)")

    # 5 Sinyal
    s1, d1 = s1_momentum_kirilmasi(df_stooq, s1_ref)
    s2, d2 = s2_hacim_artisi(df_gun)
    s3, d3 = s3_rsi_cift_zaman(futures_sym)
    s4, d4 = s4_macd_kesimi(df_4h_futures)
    s5, d5 = s5_makro_dolar(df_gun)

    ACIKLAMALAR = {
        "S1": "Fiyat son 20 günün zirvesini kırdı mı? Kırarsa güçlü yukarı sinyal.",
        "S2": "Son 3 gün hacmi normalin kaç katı? 1.3x+ = gerçek alıcı var.",
        "S3": "1H RSI + 4H RSI çift filtre: ikisi de yükseliyor + dip dönüşü = güçlü AL.",
        "S4": "MACD çizgisi sinyal çizgisini yukarı kesti mi? Kestiyse trend dönüyor.",
        "S5": "Dolar zayıf + korku yüksek + faiz düşük = altın/gümüş için olumlu ortam.",
    }
    print(f"  {'✅' if s1 else '❌'} S1 Momentum Kırılması : {d1}")
    print(f"     💡 {ACIKLAMALAR['S1']}")
    print(f"  {'✅' if s2 else '❌'} S2 Hacim Artışı       : {d2}")
    print(f"     💡 {ACIKLAMALAR['S2']}")
    print(f"  {'✅' if s3 else '❌'} S3 RSI 1H+4H          : {d3}")
    print(f"     💡 {ACIKLAMALAR['S3']}")
    print(f"  {'✅' if s4 else '❌'} S4 MACD Kesimi        : {d4}")
    print(f"     💡 {ACIKLAMALAR['S4']}")
    print(f"  {'✅' if s5 else '❌'} S5 Makro/Dolar        : {d5}")
    print(f"     💡 {ACIKLAMALAR['S5']}")

    yesil = sum([s1, s2, s3, s4, s5])

    if yesil >= 5:
        karar = "KESİN ALIM"; emoji_k = "🟢🟢🟢"
    elif yesil >= 3:
        karar = "KISMİ ALIM / İZLE"; emoji_k = "🟡🟡"
    elif yesil == 2:
        karar = "YAKLAŞIYOR"; emoji_k = "🟠"
    else:
        karar = "BEKLE"; emoji_k = "🔴"

    # Değişim hesapla — stooq dün kapanışına göre
    anlik  = spot  # yfinance 1m anlık
    dun_k  = s1_ref  # stooq dün kapanış
    degisim_pct = ((anlik / dun_k) - 1) * 100 if (anlik and dun_k and dun_k > 0) else None

    # Futures vs spot farkı
    fut_anlik = anlik_fiyat or fut_fiyat
    fut_degisim_pct = None
    if fut_anlik and dun_k and dun_k > 0:
        # Futures dünkü kapanışına göre değişim (yfinance günlük)
        if df_gun is not None and len(df_gun) >= 2:
            fut_dun = float(df_gun["Close"].iloc[-2])
            fut_degisim_pct = ((fut_anlik / fut_dun) - 1) * 100

    print(f"\n  SKOR: {yesil}/5 → {emoji_k} {karar}")

    return {
        "isim": isim,
        "anlik_fiyat": anlik,
        "dun_kapanis": dun_k,
        "degisim_pct": degisim_pct,
        "futures_fiyat": fut_anlik,
        "futures_degisim_pct": fut_degisim_pct,
        # eski alanlar — geriye dönük uyumluluk
        "fiyat": anlik,
        "spot_fiyat": spot,
        "skor": yesil,
        "karar": karar,
        "emoji_k": emoji_k,
        "sinyaller": {
            "S1_Momentum": {"sonuc": s1, "detay": d1},
            "S2_Hacim":    {"sonuc": s2, "detay": d2},
            "S3_RSI":      {"sonuc": s3, "detay": d3},
            "S4_MACD":     {"sonuc": s4, "detay": d4},
            "S5_Makro":    {"sonuc": s5, "detay": d5},
        }
    }


def telegram_mesaj_olustur(sonuclar: list, tarih: str) -> str:
    mesaj = f"<b>⚡ ALTIN & GÜMÜŞ ALARM</b>\n{tarih}\n{'─'*30}\n"

    for s in sonuclar:
        # Spot birincil, futures ikincil
        spot_str = f"{s['spot_fiyat']:.2f}" if s['spot_fiyat'] else "?"
        fut_str  = f"{s['futures_fiyat']:.2f}" if s['futures_fiyat'] else "?"
        fiyat_str = f"Spot:{spot_str} | Fut:{fut_str}"

        sig = s["sinyaller"]
        def _satir(ok, baslik, detay, acik):
            return f"{'✅' if ok else '❌'} {baslik}: {detay[:40]}\n   ↳ {acik}"
        satir = "\n".join([
            _satir(sig['S1_Momentum']['sonuc'], "Momentum", sig['S1_Momentum']['detay'],
                   "20G zirvesi kırıldı mı? Kırarsa güçlü yukarı sinyal."),
            _satir(sig['S2_Hacim']['sonuc'], "Hacim", sig['S2_Hacim']['detay'],
                   "1.3x+ = gerçek alıcı var, yükseliş inandırıcı."),
            _satir(sig['S3_RSI']['sonuc'], "RSI1H+4H", sig['S3_RSI']['detay'],
                   "Backwardation = fiziksel talep çok güçlü."),
            _satir(sig['S4_MACD']['sonuc'], "MACD", sig['S4_MACD']['detay'],
                   "Sinyal çizgisi kesildi mi? Kestiyse trend dönüyor."),
            _satir(sig['S5_Makro']['sonuc'], "Makro", sig['S5_Makro']['detay'],
                   "Dolar zayıf + korku yüksek = altın için olumlu."),
        ])

        mesaj += (
            f"\n<b>{s['emoji_k']} {s['isim']}</b>\n"
            f"Fiyat: <b>{fiyat_str}</b> $/oz\n"
            f"{satir}\n"
            f"<b>Skor: {s['skor']}/5 → {s['karar']}</b>\n"
            f"{'─'*30}\n"
        )

    # Ortak makro notu — DXY korelasyon açıklaması
    mesaj += "\n<i>📊 Altın-DXY ters korelasyon (r≈-0.8): DXY↓ = Altın↑ | DXY↑ = Altın↓</i>"
    mesaj += "\n<i>DXY↓ + VIX↑ + 10Y↓ = Altın/Gümüş için olumlu ortam</i>"
    return mesaj


def alarm_calistir():
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M TR")
    print(f"\n{'='*55}")
    print(f"  ALTIN & GÜMÜŞ ALARM — {tarih}")
    print(f"{'='*55}")

    sonuclar = []
    for isim, cfg in ENSTRUMANLAR.items():
        sonuc = enstruman_analiz(isim, cfg)
        sonuclar.append(sonuc)

    # Telegram
    mesaj = telegram_mesaj_olustur(sonuclar, tarih)
    # _telegram(mesaj)  # bist_sistem.py üzerinden gönderiliyor

    # Log
    _log({
        "tarih": tarih,
        "sonuclar": sonuclar,
    })

    # Özet
    print(f"\n{'='*55}")
    for s in sonuclar:
        print(f"  {s['emoji_k']} {s['isim']}: {s['skor']}/5 → {s['karar']}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    alarm_calistir()
