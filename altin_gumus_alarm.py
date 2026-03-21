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

try:
    from groq import Groq
    GROQ_AKTIF = True
except ImportError:
    GROQ_AKTIF = False

try:
    from cerebras.cloud.sdk import Cerebras as CerebrasClient
    CEREBRAS_AKTIF = True
except ImportError:
    CEREBRAS_AKTIF = False

# ── Sabitler ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
ALARM_LOG        = "altin_alarm_log.json"
FIYAT_LOG        = "raporlar/altin_fiyat_log.json"

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
        df = df.sort_values("Date").reset_index(drop=True)

        # Bugünün kısmi barını çıkar — sadece dün ve öncesi tam kapanışlar
        bugun = pd.Timestamp.now().normalize()
        df = df[df["Date"] < bugun].tail(gun).reset_index(drop=True)
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

    # DXY — stooq'tan çek (semboller: dxy, usdx, usdidx)
    dxy = None
    for dxy_sym in ["dxy", "usdx", "dx.f"]:
        try:
            dxy = _stooq_gunluk(dxy_sym, gun=30)
            if dxy is not None and len(dxy) >= 5:
                break
            dxy = None
        except:
            continue

    if dxy is not None and len(dxy) >= 20:
        try:
            dxy_son    = float(dxy["Close"].iloc[-1])
            dxy_10g    = float(dxy["Close"].iloc[-10])
            dxy_30g    = float(dxy["Close"].iloc[-22]) if len(dxy) >= 22 else float(dxy["Close"].iloc[0])
            dxy_kisa   = (dxy_son / dxy_10g  - 1) * 100
            dxy_uzun   = (dxy_son / dxy_30g  - 1) * 100

            kor_yorum = ""
            if df_gunluk is not None and len(df_gunluk) >= 20:
                try:
                    altin_k = df_gunluk["Close"].iloc[-22:].reset_index(drop=True)
                    dxy_k   = dxy["Close"].iloc[-22:].reset_index(drop=True)
                    min_len = min(len(altin_k), len(dxy_k))
                    if min_len >= 10:
                        kor = float(altin_k.iloc[:min_len].corr(dxy_k.iloc[:min_len]))
                        if kor < -0.6:
                            kor_yorum = f" [dolar-altın ters ilişkisi güçlü]"
                        elif kor > 0.3:
                            kor_yorum = f" [⚠️ jeopolitik: dolar ve altın birlikte yükseliyor]"
                except:
                    pass

            if dxy_kisa < -1.5 and dxy_uzun < -2.0:
                puan += 3
                notlar.append(f"Dolar endeksi(DXY):{dxy_son:.1f} — güçlü düşüş %{dxy_kisa:.1f}✓✓✓{kor_yorum}")
            elif dxy_kisa < -1.0:
                puan += 2
                notlar.append(f"Dolar endeksi(DXY):{dxy_son:.1f} — zayıflıyor %{dxy_kisa:.1f}✓✓{kor_yorum}")
            elif dxy_kisa < 0:
                puan += 1
                notlar.append(f"Dolar endeksi(DXY):{dxy_son:.1f} — hafif zayıf %{dxy_kisa:.1f}✓{kor_yorum}")
            elif dxy_kisa > 1.5:
                puan -= 1
                notlar.append(f"Dolar endeksi(DXY):{dxy_son:.1f} — güçlü yükseliş %{dxy_kisa:.1f}✗✗{kor_yorum}")
            else:
                notlar.append(f"Dolar endeksi(DXY):{dxy_son:.1f} — hafif yükseliş %{dxy_kisa:.1f}✗{kor_yorum}")
        except:
            notlar.append("DXY:?")
    else:
        notlar.append("DXY:?")

    # VIX — korku yüksekse altın güvenli liman
    try:
        vix_data = _indir("^VIX", interval="1d", period="1mo")
        if vix_data is not None:
            vix = float(vix_data["Close"].iloc[-1])
            if vix > 22:
                puan += 2
                notlar.append(f"Korku endeksi(VIX):{vix:.0f} YÜKSEK✓✓")
            elif vix > 18:
                puan += 1
                notlar.append(f"Korku endeksi(VIX):{vix:.0f} orta✓")
            else:
                notlar.append(f"Korku endeksi(VIX):{vix:.0f} düşük✗")
    except:
        notlar.append("VIX:?")

    # 10Y Tahvil faizi düşüyorsa altın olumlu
    try:
        tnx = _indir("^TNX", interval="1d", period="1mo")
        if tnx is not None and len(tnx) >= 10:
            faiz_son   = float(tnx["Close"].iloc[-1])
            faiz_degisim = faiz_son - float(tnx["Close"].iloc[-10])
            if faiz_degisim < -0.1:
                puan += 1
                notlar.append(f"ABD tahvil faizi:%{faiz_son:.2f}↓✓")
            else:
                notlar.append(f"ABD tahvil faizi:%{faiz_son:.2f}↑✗")
    except:
        notlar.append("Tahvil:?")

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
        s1_tarih = df_stooq.index[-1].strftime("%Y-%m-%d") if df_stooq is not None else "?"
        print(f"  S1 ref  : {s1_ref:.2f} (stooq kapanış: {s1_tarih})")

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
    aciklama = _s5_aciklama(d5, isim)
    for satir in aciklama.split("\n     "):
        print(f"     {satir}")

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

    # ── Destek / Direnç Seviyeleri ─────────────────────────────
    destek1 = destek2 = direnc1 = direnc2 = None
    if df_stooq is not None and len(df_stooq) >= 20:
        kapanislar = df_stooq["Close"].iloc[-20:]
        dusukler   = df_stooq["Low"].iloc[-20:]
        yuksekler  = df_stooq["High"].iloc[-20:]

        son20_dusuk  = float(dusukler.min())
        son20_yuksek = float(yuksekler.max())
        # 10G ile 20G arasındaki bölüm
        son10_20_dusuk  = float(dusukler.iloc[:10].min())  # 10-20 gün arası taban
        son5_dusuk      = float(dusukler.iloc[-5:].min())  # son 5 gün taban
        son5_yuksek     = float(yuksekler.iloc[-5:].max()) # son 5 gün zirve
        son10_yuksek    = float(yuksekler.iloc[-10:].max())

        destek1 = round(son5_dusuk, 1)          # Yakın destek (5G)
        destek2 = round(son10_20_dusuk, 1)       # Uzak destek (10-20G arası)
        direnc1 = round(son5_yuksek, 1)          # Yakın direnç (5G)
        direnc2 = round(son20_yuksek, 1)         # Uzak direnç (20G zirvesi)

        # Destek1 ve destek2 çok yakınsa destek2'yi biraz aşağı al
        if destek2 >= destek1 * 0.99:
            destek2 = round(son20_dusuk * 0.98, 1)

    # Fibonacci seviyeleri (20 günlük swing)
    fib382 = fib500 = fib618 = None
    if destek2 and direnc2 and direnc2 > destek2:
        aralik = direnc2 - destek2
        fib618 = round(direnc2 - aralik * 0.618, 1)
        fib500 = round(direnc2 - aralik * 0.500, 1)
        fib382 = round(direnc2 - aralik * 0.382, 1)

    # Senaryo yorumu — "şunu kırarsa şuraya gider"
    senaryo = ""
    if spot and destek1 and destek2 and direnc1 and fib618:
        gumus_ek = "\n   🏭 Gümüş sanayi metali — sanayi talebi canlanırsa toparlanma daha hızlı olabilir." if "GUM" in isim.upper() else ""

        if spot < fib618:
            # Fib %61.8 altındayız — düşüş bölgesinde
            senaryo = (
                f"📍 Mevcut fiyat ({spot:.1f}) Fib %61.8 ({fib618}) altında — düşüş bölgesi\n"
                f"   🔼 {fib618} kırılırsa: {fib500} hedef, ardından {direnc1}\n"
                f"   🔽 {destek1} kırılırsa: {destek2} test edilir"
                f"{gumus_ek}"
            )
        elif spot < fib500:
            # Fib %50 altındayız
            senaryo = (
                f"📍 Mevcut fiyat ({spot:.1f}) — Fib %50 ({fib500}) altında, toparlanma sınırlı\n"
                f"   🔼 {fib500} kırılırsa: {fib382} hedef, ardından {direnc1}\n"
                f"   🔽 {fib618} kırılırsa: {destek1} test edilir"
                f"{gumus_ek}"
            )
        elif spot < fib382:
            # Fib %38.2 altındayız — kritik bölge
            senaryo = (
                f"📍 Mevcut fiyat ({spot:.1f}) — Fib %38.2 ({fib382}) altında, kritik bölge\n"
                f"   🔼 {fib382} kırılırsa: {direnc1} hedef, trend dönüşü güçlenir\n"
                f"   🔽 {fib500} kırılırsa: {fib618} destek test edilir"
                f"{gumus_ek}"
            )
        else:
            # Fib %38.2 üzerindeyiz — güçlü bölge
            senaryo = (
                f"📍 Mevcut fiyat ({spot:.1f}) — Fib %38.2 ({fib382}) üzerinde, güçlü bölge\n"
                f"   🔼 {direnc1} kırılırsa: {direnc2} hedef\n"
                f"   🔽 {fib382} kırılırsa: {fib500} test edilir"
                f"{gumus_ek}"
            )

    if senaryo:
        print(f"\n  📊 Teknik Senaryo:\n  {senaryo.replace(chr(10), chr(10)+'  ')}")

    print(f"\n  SKOR: {yesil}/5 → {emoji_k} {karar}")

    return {
        "isim": isim,
        "anlik_fiyat": anlik,
        "dun_kapanis": dun_k,
        "degisim_pct": degisim_pct,
        "futures_fiyat": fut_anlik,
        "futures_degisim_pct": fut_degisim_pct,
        "fiyat": anlik,
        "spot_fiyat": spot,
        "skor": yesil,
        "karar": karar,
        "emoji_k": emoji_k,
        "destek1": destek1,
        "destek2": destek2,
        "direnc1": direnc1,
        "direnc2": direnc2,
        "fib382": fib382,
        "fib500": fib500,
        "fib618": fib618,
        "senaryo": senaryo,
        "sinyaller": {
            "S1_Momentum": {"sonuc": s1, "detay": d1},
            "S2_Hacim":    {"sonuc": s2, "detay": d2},
            "S3_RSI":      {"sonuc": s3, "detay": d3},
            "S4_MACD":     {"sonuc": s4, "detay": d4},
            "S5_Makro":    {"sonuc": s5, "detay": d5},
        },
        "sinyaller_ozet": f"S1:{'✅' if s1 else '❌'} S2:{'✅' if s2 else '❌'} S3:{'✅' if s3 else '❌'} S4:{'✅' if s4 else '❌'} S5:{'✅' if s5 else '❌'} | {d5[:60]}"
    }


def makro_yorum_uret(sonuclar: list) -> str:
    """
    Mevcut makro verilere göre kısa yorum satırı üret.
    Petrol, DXY, VIX durumuna göre otomatik yorumlar.
    """
    yorumlar = []
    try:
        # Petrol fiyatı
        brent = yf.Ticker("BZ=F").history(period="5d", interval="1d")
        if brent is not None and len(brent) >= 2:
            brent_son  = float(brent["Close"].iloc[-1])
            brent_prev = float(brent["Close"].iloc[-2])
            brent_pct  = (brent_son / brent_prev - 1) * 100
            if brent_pct > 2:
                yorumlar.append(f"🛢️ Petrol +{brent_pct:.1f}% → enerji enflasyonu riski, Fed faiz kesimi gecikebilir → altın için baskı")
            elif brent_pct < -2:
                yorumlar.append(f"🛢️ Petrol {brent_pct:.1f}% → deflasyon sinyali, Fed yumuşayabilir → altın için olumlu")
            else:
                yorumlar.append(f"🛢️ Petrol {brent_pct:+.1f}% (nötr)")
    except:
        pass

    try:
        # DXY — stooq
        dxy = None
        for sym in ["dxy", "usdx", "dx.f"]:
            dxy = _stooq_gunluk(sym, gun=5)
            if dxy is not None and len(dxy) >= 2:
                break
            dxy = None
        if dxy is not None and len(dxy) >= 2:
            dxy_pct = (float(dxy["Close"].iloc[-1]) / float(dxy["Close"].iloc[-2]) - 1) * 100
            if abs(dxy_pct) > 0.3:
                if dxy_pct > 0:
                    yorumlar.append(f"💵 DXY +{dxy_pct:.1f}% (güçlü dolar) → altın/gümüş üzerinde baskı devam eder")
                else:
                    yorumlar.append(f"💵 DXY {dxy_pct:.1f}% (zayıf dolar) → altın için destek")
    except:
        pass

    try:
        # VIX
        vix_data = yf.Ticker("^VIX").history(period="3d", interval="1d")
        if vix_data is not None and len(vix_data) >= 1:
            vix = float(vix_data["Close"].iloc[-1])
            if vix > 25:
                yorumlar.append(f"😱 VIX {vix:.1f} (yüksek korku) → güvenli liman talebi altını destekler")
            elif vix < 15:
                yorumlar.append(f"😴 VIX {vix:.1f} (düşük korku) → riskli varlıklara geçiş, altın baskı altında")
    except:
        pass

    # S/D skoru yorumu
    for s in sonuclar:
        if s["skor"] == 0:
            yorumlar.append(f"⚠️ {s['isim']}: Tüm sinyaller negatif — hem teknik hem makro baskı var, BEKLE")
        elif s["skor"] >= 4:
            yorumlar.append(f"🚀 {s['isim']}: Güçlü alım sinyali ({s['skor']}/5) — momentum ve makro uyumlu")

    return "\n".join(yorumlar) if yorumlar else ""


def _s5_aciklama(detay: str, isim: str = "ALTIN") -> str:
    """S5 durumunu doğru yorumla + gelecek beklentisi ekle."""
    gumus = "GUM" in isim.upper() or "SILVER" in isim.upper()
    hedef = "gümüş" if gumus else "altın"

    # Detayı bölümlere ayır
    bolumler = detay.split("|")
    dxy_b  = next((b for b in bolumler if "DXY"   in b.upper()), "")
    vix_b  = next((b for b in bolumler if "VIX"   in b.upper() or "KORKU" in b.upper()), "")
    faiz_b = next((b for b in bolumler if "FAİZ"  in b.upper() or "TAHVİL" in b.upper()
                                       or "FAIZ"  in b.upper() or "10Y"    in b.upper()), "")

    # DXY skoru: +2 güçlü düşüş, +1 düşüş, 0 yatay, -1 yükseliş, -2 güçlü yükseliş
    dxy_skor = 0
    dxy_yorum = ""
    if dxy_b:
        d = dxy_b.upper()
        if "GÜÇLÜ DÜŞÜŞ" in d or "✓✓✓" in dxy_b:
            dxy_skor = 2
            dxy_yorum = f"💵 Dolar hızla düşüyor → {hedef} için güçlü destek"
        elif "ZAYIFLIY" in d or ("✓✓" in dxy_b and "✓✓✓" not in dxy_b):
            dxy_skor = 1
            dxy_yorum = f"💵 Dolar zayıflıyor → {hedef} için olumlu"
        elif "HAFİF ZAYIF" in d or ("✓" in dxy_b and "✓✓" not in dxy_b):
            dxy_skor = 1
            dxy_yorum = f"💵 Dolar biraz zayıfladı → {hedef} için hafif destek"
        elif "GÜÇLÜ YÜKSELİŞ" in d or "✗✗" in dxy_b:
            dxy_skor = -2
            dxy_yorum = f"💵 Dolar güçlü yükseliyor → {hedef} satılıyor, düşüş baskısı sert"
        elif "YÜKSELİŞ" in d or "✗" in dxy_b:
            dxy_skor = -1
            dxy_yorum = f"💵 Dolar yükseliyor → {hedef} baskı altında, alım için erken"

    # VIX skoru: +1 yüksek, -1 düşük
    vix_skor = 0
    vix_yorum = ""
    if vix_b:
        v = vix_b.upper()
        if "YÜKSEK" in v or "✓✓" in vix_b:
            vix_skor = 1
            if gumus:
                vix_yorum = "😰 Piyasa korkusu yüksek → güvenli liman talebi var ama gümüş altın kadar koruma sağlamıyor"
            else:
                vix_yorum = "😰 Piyasa korkusu yüksek → güvenli liman talebi altını bir miktar tutuyor"
        elif "DÜŞÜK" in v or "✗" in vix_b:
            vix_skor = -1
            vix_yorum = f"😌 Piyasalar sakin → yatırımcılar riskli varlıklara geçiyor, {hedef} talebi azalıyor"

    # Faiz skoru
    faiz_skor = 0
    faiz_yorum = ""
    if faiz_b:
        if ("↑" in faiz_b or "YÜKSELİ" in faiz_b.upper()) and "✗" in faiz_b:
            faiz_skor = -1
            faiz_yorum = "📈 Faiz yükseliyor → tahvil/banka cazip, yatırımcı altından çıkıyor"
        elif ("↓" in faiz_b or "DÜŞÜ" in faiz_b.upper()) and "✓" in faiz_b:
            faiz_skor = 1
            faiz_yorum = f"📉 Faiz düşüyor → bankada tutmak yerine {hedef} daha mantıklı"

    # Net skor ve gelecek yorumu
    net = dxy_skor + vix_skor + faiz_skor
    satirlar = []
    if dxy_yorum:  satirlar.append(dxy_yorum)
    if vix_yorum:  satirlar.append(vix_yorum)
    if faiz_yorum: satirlar.append(faiz_yorum)

    # Gümüş için ek sanayi baskısı notu
    if gumus and net < 0:
        satirlar.append("🏭 Gümüş aynı zamanda sanayi metali — ekonomi yavaşlama endişesi sanayi talebini de düşürüyor, çift baskı var")

    # Net değerlendirme + gelecek beklentisi
    if gumus:
        if net <= -2:
            satirlar.append("⚠️  NET SONUÇ (GÜMÜŞ): Dolar baskısı + sanayi talebi endişesi çift etki yapıyor → gümüş altından daha sert düşüyor, alım için altın stabilize olana kadar bekle")
        elif net == -1:
            satirlar.append("⚠️  NET SONUÇ (GÜMÜŞ): Baskı faktörleri ağır basıyor → gümüş kısa vadede zayıf, dolar yönü ve sanayi haberleri izle")
        elif net == 0:
            satirlar.append("⚖️  NET SONUÇ (GÜMÜŞ): Faktörler dengede ama gümüş sanayi hassasiyeti yüzünden altından daha oynak — net sinyal için bekle")
        elif net >= 1:
            satirlar.append("✅ NET SONUÇ (GÜMÜŞ): Makro destekleyici + sanayi talebi canlanıyorsa gümüş altından daha hızlı toparlanabilir")
    else:
        if net <= -2:
            satirlar.append("⚠️  NET SONUÇ: Birden fazla baskı faktörü var → altın düşüşü devam edebilir, alım için dolar zirve yapana kadar bekle")
        elif net == -1:
            satirlar.append("⚠️  NET SONUÇ: Baskı faktörleri lehte olanlardan ağır basıyor → altın kısa vadede zayıf kalabilir, dolar yönü izle")
        elif net == 0:
            satirlar.append("⚖️  NET SONUÇ: Baskı ve destek faktörleri dengede → altın yatay seyredebilir, net sinyal için bekle")
        elif net == 1:
            satirlar.append("✅ NET SONUÇ: Makro hafif destekleyici → dolar zirve yaparsa altın toparlanabilir")
        else:
            satirlar.append("✅ NET SONUÇ: Makro altın için olumlu → güçlü alım fırsatı yaklaşıyor olabilir")

    return "\n     ".join(satirlar) if satirlar else "Makro veri işlenemedi."


def telegram_mesaj_olustur(sonuclar: list, tarih: str,
                           takvim: list = None, haberler: list = None,
                           ai_tahmin: str = None) -> str:
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
            _satir(sig['S5_Makro']['sonuc'], "Makro/Dolar", sig['S5_Makro']['detay'],
                   _s5_aciklama(sig['S5_Makro']['detay'], s['isim'])),
        ])

        mesaj += (
            f"\n<b>{s['emoji_k']} {s['isim']}</b>\n"
            f"Fiyat: <b>{fiyat_str}</b> $/oz\n"
            f"{satir}\n"
            f"<b>Skor: {s['skor']}/5 → {s['karar']}</b>\n"
        )

        # Teknik senaryo — destek/direnç
        if s.get("senaryo"):
            mesaj += f"\n📊 <b>Teknik Senaryo:</b>\n{s['senaryo']}\n"

        mesaj += f"{'─'*30}\n"

    # Makro yorum
    makro = makro_yorum_uret(sonuclar)
    if makro:
        mesaj += f"\n\n<b>📌 Makro Yorum:</b>\n{makro}"

    # AI Tahmin
    if ai_tahmin:
        mesaj += f"\n\n🤖 <b>AI Kısa Vadeli Tahmin:</b>\n{ai_tahmin}"

    # Ekonomik takvim + haberler
    haber_yorumu = haber_yorumu_uret(haberler or [], takvim or [])
    if haber_yorumu:
        mesaj += f"\n\n{haber_yorumu}"

    # Ortak makro notu
    mesaj += "\n\n<i>📊 Altın-DXY ters korelasyon (r≈-0.8): DXY↓ = Altın↑ | DXY↑ = Altın↓</i>"
    mesaj += "\n<i>DXY↓ + VIX↑ + 10Y↓ = Altın/Gümüş için olumlu ortam</i>"
    return mesaj


def ekonomik_takvim_cek() -> list:
    """
    Kritik ekonomik olayları çek.
    Fed, ECB, TCMB toplantıları + CPI, NFP, PCE gibi veriler.
    """
    olaylar = []
    bugun = datetime.now()

    # Investing.com ekonomik takvim RSS
    takvim_kaynaklar = [
        "https://tr.investing.com/rss/economic_calendar.rss",
        "https://www.investing.com/rss/economic_calendar.rss",
    ]

    for url in takvim_kaynaklar:
        try:
            import feedparser
            feed = feedparser.parse(url)
            for e in feed.entries[:20]:
                baslik = e.get("title", "")
                ozet   = e.get("summary", "")
                tarih  = e.get("published", "")
                if any(k in baslik.upper() for k in [
                    "FED", "FOMC", "ECB", "TCMB", "CPI", "NFP",
                    "PCE", "GDP", "BÜYÜME", "ENFLASYON", "İSTİHDAM",
                    "FAİZ", "INTEREST RATE", "INFLATION"
                ]):
                    olaylar.append({"baslik": baslik, "tarih": tarih[:10], "ozet": ozet[:100]})
            if olaylar:
                break
        except:
            continue

    # Manuel kritik tarihler — Fed 2026 takvimi
    # Yaklaşan Fed toplantıları (2026)
    fed_toplantilari = [
        ("2026-01-28", "FOMC Faiz Kararı"),
        ("2026-03-18", "FOMC Faiz Kararı"),
        ("2026-05-06", "FOMC Faiz Kararı"),
        ("2026-06-17", "FOMC Faiz Kararı"),
        ("2026-07-29", "FOMC Faiz Kararı"),
        ("2026-09-16", "FOMC Faiz Kararı"),
        ("2026-11-04", "FOMC Faiz Kararı"),
        ("2026-12-16", "FOMC Faiz Kararı"),
    ]

    for tarih_str, isim in fed_toplantilari:
        try:
            etkinlik = datetime.strptime(tarih_str, "%Y-%m-%d")
            fark = (etkinlik - bugun).days
            if 0 <= fark <= 7:
                olaylar.insert(0, {
                    "baslik": f"🏦 {isim}",
                    "tarih": tarih_str,
                    "ozet": f"{fark} gün sonra" if fark > 0 else "BUGÜN",
                    "oncelik": "KRITIK"
                })
        except:
            continue

    return olaylar


def metal_haber_cek() -> list:
    """Metal piyasaları için özel haber kaynakları."""
    haberler = []
    kaynaklar = [
        {"isim": "AA Ekonomi",    "url": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi"},
        {"isim": "Reuters",       "url": "https://feeds.reuters.com/reuters/businessNews"},
        {"isim": "Borsa Gündem",  "url": "https://www.borsagundem.com/feed"},
        {"isim": "Para Analiz",   "url": "https://www.paraanaliz.com/feed/"},
        {"isim": "Ekonomim",      "url": "https://www.ekonomim.com/rss/son-dakika-haberleri.xml"},
    ]

    METAL_ANAHTAR = [
        "ALTIN", "GÜMÜŞ", "GOLD", "SILVER", "METAL", "EMTIA",
        "FED", "FOMC", "FAİZ", "INTEREST", "ENFLASYON", "INFLATION",
        "DOLAR", "DOLLAR", "DXY", "TAHVIL", "BOND",
        "PETROL", "OIL", "BRENT", "ENERJI",
        "SAVAŞ", "ATEŞKES", "GEOPOLİTİK", "IRAN", "UKRAYNA",
        "ÇİN", "CHINA", "TALEP", "DEMAND",
    ]

    for kaynak in kaynaklar:
        try:
            import feedparser
            feed = feedparser.parse(kaynak["url"])
            for e in feed.entries[:20]:
                baslik = e.get("title", "")
                ozet   = e.get("summary", "")[:150]
                metin  = (baslik + " " + ozet).upper()
                if any(k in metin for k in METAL_ANAHTAR):
                    haberler.append({
                        "kaynak": kaynak["isim"],
                        "baslik": baslik[:120],
                        "ozet":   ozet,
                        "tarih":  e.get("published", "")[:10],
                    })
            if len(haberler) >= 6:
                break
        except:
            continue

    return haberler[:6]


def haber_yorumu_uret(haberler: list, takvim: list) -> str:
    """Haber ve takvim verilerinden özet yorum üret."""
    satirlar = []

    # Kritik takvim olayları
    kritik = [t for t in takvim if t.get("oncelik") == "KRITIK"]
    for k in kritik:
        satirlar.append(f"📅 <b>{k['baslik']}</b> — {k['ozet']}")

    # Önemli haberler
    if haberler:
        satirlar.append("\n📰 <b>Güncel Metal Haberleri:</b>")
        for h in haberler[:4]:
            satirlar.append(f"  [{h['kaynak']}] {h['baslik']}")

    return "\n".join(satirlar) if satirlar else ""


def ai_tahmin_uret(sonuclar: list, takvim: list, haberler: list) -> str:
    """
    Groq önce dene, limit aşılınca Cerebras'a geç.
    """
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "")

    # Client seç — AI tahmin için Cerebras öncelikli (token limiti yok)
    client = None
    model_adi = None
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
    if CEREBRAS_AKTIF and cerebras_key:
        try:
            client   = CerebrasClient(api_key=cerebras_key)
            model_adi = "llama-3.3-70b"
        except:
            client = None
    if client is None and GROQ_AKTIF and GROQ_API_KEY:
        try:
            client   = Groq(api_key=GROQ_API_KEY)
            model_adi = "llama-3.3-70b-versatile"
        except:
            pass
    if client is None:
        return ""

    # Her enstrüman için detaylı veri hazırla
    enstruman_detay = []
    for s in sonuclar:
        spot   = s.get("spot_fiyat") or s.get("fiyat") or 0
        dun    = s.get("dun_kapanis") or spot
        skor   = s.get("skor", 0)
        isim   = s.get("isim", "")

        d1 = s.get("destek1") or round(spot * 0.97, 1)
        d2 = s.get("destek2") or round(spot * 0.94, 1)
        r1 = s.get("direnc1") or round(spot * 1.03, 1)
        r2 = s.get("direnc2") or round(spot * 1.06, 1)
        f618 = s.get("fib618") or round(spot * 1.04, 1)
        f382 = s.get("fib382") or round(spot * 1.08, 1)

        gumus_notu = ""
        if "GUM" in isim.upper():
            gumus_notu = "\n  NOT: Gümüş hem güvenli liman hem sanayi metali — büyüme/sanayi talebi haberleri kritik"

        enstruman_detay.append(
            f"{isim}: {spot:.1f} $/oz (dün: {dun:.1f}, değişim: {((spot/dun-1)*100) if dun else 0:+.1f}%)\n"
            f"  Teknik skor: {skor}/5 | Durum: {s.get('karar','?')}\n"
            f"  Sinyaller: {s.get('sinyaller_ozet','')}\n"
            f"  Destek: {d1} (yakın) / {d2} (güçlü)\n"
            f"  Direnç: {r1} (yakın) / {r2} (güçlü)\n"
            f"  Fib %61.8: {f618} | Fib %38.2: {f382}"
            f"{gumus_notu}"
        )

    fiyat_ozet = "\n\n".join(enstruman_detay)

    takvim_ozet = "\n".join([
        f"  {t['baslik']} — {t['ozet']}"
        for t in (takvim or [])[:4]
    ]) or "Kritik takvim olayı yok"

    haber_ozet = "\n".join([
        f"  [{h['kaynak']}] {h['baslik']}"
        for h in (haberler or [])[:5]
    ]) or "Haber bulunamadı"

    sistem = """Sen emtia analistisin. SADECE şu formatta yaz, HER İKİ metali de zorunlu yaz:

🥇 ALTIN: [YÜKSELIR/DÜŞER/YATAY]
Sebep: [1 cümle]
🔼 [direnc] geçilirse: [hedef]
🔽 [destek] kırılırsa: [hedef]

🥈 GÜMÜŞ: [YÜKSELIR/DÜŞER/YATAY]
Sebep: [altından FARKLI sebep - sanayi talebi, büyüme odaklı]
🔼 [direnc] geçilirse: [hedef]
🔽 [destek] kırılırsa: [hedef]

⚠️ Ana Risk: [1 cümle]

Türkçe. Kısa. HER İKİ metal zorunlu."""

    mesaj = f"""MEVCUT DURUM:
{fiyat_ozet}

EKONOMİK TAKVİM:
{takvim_ozet}

GÜNCEL HABERLER:
{haber_ozet}

Her enstrüman için ayrı koşullu tahmin yap."""

    try:
        r = client.chat.completions.create(
            model=model_adi,
            messages=[
                {"role": "system", "content": sistem},
                {"role": "user",   "content": mesaj}
            ],
            temperature=0.3,
            max_tokens=800,
        )
        return r.choices[0].message.content.strip()

    except Exception as e:
        hata = str(e)
        # Groq rate limit → Cerebras ile tekrar dene
        if ("rate_limit" in hata.lower() or "429" in hata) and CEREBRAS_AKTIF:
            cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
            if cerebras_key:
                print("  ⚠️  Groq limit → Cerebras'a geçiliyor...")
                try:
                    c2 = CerebrasClient(api_key=cerebras_key)
                    r2 = c2.chat.completions.create(
                        model="llama-3.3-70b",
                        messages=[{"role":"system","content":sistem},
                                  {"role":"user","content":mesaj}],
                        temperature=0.3, max_tokens=800,
                    )
                    return r2.choices[0].message.content.strip()
                except Exception as e2:
                    print(f"  Cerebras hata: {e2}")
        print(f"  AI tahmin hatası: {hata[:100]}")
        return ""


def alarm_calistir(ai_aktif: bool = False):
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M TR")
    print(f"\n{'='*55}")
    print(f"  ALTIN & GÜMÜŞ ALARM — {tarih}")
    print(f"{'='*55}")

    # Ekonomik takvim + metal haberleri
    print("  📅 Ekonomik takvim çekiliyor...")
    takvim  = ekonomik_takvim_cek()
    print(f"  📰 Metal haberleri çekiliyor...")
    haberler = metal_haber_cek()

    kritik_takvim = [t for t in takvim if t.get("oncelik") == "KRITIK"]
    if kritik_takvim:
        for k in kritik_takvim:
            print(f"  ⚠️  {k['baslik']} — {k['ozet']}")
    print(f"  {len(haberler)} metal haberi bulundu")

    sonuclar = []
    for isim, cfg in ENSTRUMANLAR.items():
        sonuc = enstruman_analiz(isim, cfg)
        sonuclar.append(sonuc)

    # AI tahmin — sadece sabah alarmında (token tasarrufu)
    ai_tahmin = ""
    if ai_aktif:
        print("  🤖 AI tahmin üretiliyor...")
        ai_tahmin = ai_tahmin_uret(sonuclar, takvim, haberler)
        if ai_tahmin:
            print("  AI Tahmin:")
            for satir in ai_tahmin.split("\n"):
                print(f"    {satir}")
    else:
        print("  🤖 AI tahmin: atlandı (token tasarrufu)")

    # Telegram
    mesaj = telegram_mesaj_olustur(sonuclar, tarih, takvim, haberler, ai_tahmin)
    # _telegram(mesaj)  # bist_sistem.py üzerinden gönderiliyor

    # Log
    _log({
        "tarih":    tarih,
        "sonuclar": sonuclar,
        "takvim":   takvim[:5],
        "haberler": haberler[:5],
    })

    # Özet
    print(f"\n{'='*55}")
    for s in sonuclar:
        print(f"  {s['emoji_k']} {s['isim']}: {s['skor']}/5 → {s['karar']}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    import sys
    ai_aktif = "--ai" in sys.argv
    alarm_calistir(ai_aktif=ai_aktif)
