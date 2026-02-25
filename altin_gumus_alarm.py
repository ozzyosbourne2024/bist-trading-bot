#!/usr/bin/env python3
"""
ALTIN & GÜMÜŞ ALARM SİSTEMİ v1.0
===================================
RSI yerine daha güvenilir 5 sinyal:

  S1. Momentum Kırılması  — fiyat önceki 20G direncini geçti mi?
  S2. Hacim Artışı        — son 3 günde hacim ortalamanın üzerinde mi?
  S3. Spot-Futures Farkı  — backwardation var mı? (güçlü fiziksel talep)
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
        "futures":  "GC=F",   # MACD/teknik için
        "etf":      "GLD",    # S1 momentum + S2 hacim için
        "spot_url": "https://api.gold-api.com/price/XAU",
        "birim":    "$/oz",
        "emoji":    "🥇",
    },
    "GUMUS": {
        "futures":  "SI=F",   # MACD/teknik için
        "etf":      None,       # SLV verisi bozuk — gold-api log kullanılır
        "spot_url": "https://api.gold-api.com/price/XAG",
        "log_sembol": "XAG",  # gold-api log anahtarı
        "birim":    "$/oz",
        "emoji":    "🥈",
    },
}


# ════════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════════

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

def s1_momentum_kirilmasi(df_etf: pd.DataFrame) -> Tuple[bool, str]:
    """
    S1: ETF (GLD/SLV) ile momentum kırılması.
    Rollover sorunu yok — kesintisiz fiyat serisi.
    """
    if df_etf is None or len(df_etf) < 22:
        return False, "ETF veri yetersiz"

    kapanis = df_etf["Close"]
    bugun   = float(kapanis.iloc[-1])
    dun     = float(kapanis.iloc[-2])

    # Son 20 günün yüksek/düşük kapanışı (bugün hariç)
    max_20g = float(kapanis.iloc[-22:-2].max())
    min_20g = float(kapanis.iloc[-22:-2].min())

    # Yukarı kırılma: bugün VE dün max üstünde (teyit)
    yukari_kirilma = bugun > max_20g and dun > max_20g * 0.99

    # Aşağı kırılma
    asagi_kirilma = bugun < min_20g

    if yukari_kirilma:
        return True, f"↗ Kırıldı! {bugun:.2f} > Max20G:{max_20g:.2f}"
    elif asagi_kirilma:
        return False, f"↘ Aşağı kırıldı! {bugun:.2f} < Min20G:{min_20g:.2f} ⚠️"
    else:
        return False, f"Bugün:{bugun:.2f} | Max20G:{max_20g:.2f} | Fark:{((bugun/max_20g)-1)*100:.1f}%"


def s2_hacim_artisi(df_etf: pd.DataFrame) -> Tuple[bool, str]:
    """
    S2: ETF (GLD/SLV) hacim analizi.
    Milyonlarca işlem/gün — güvenilir hacim verisi.
    """
    if df_etf is None or len(df_etf) < 22:
        return False, "ETF veri yetersiz"

    hacim = df_etf["Volume"]
    son3_ort = float(hacim.iloc[-3:].mean())
    ort20    = float(hacim.iloc[-22:-2].mean())

    if ort20 == 0:
        return False, "Hacim verisi yok"

    oran = son3_ort / ort20

    # ETF için eşik biraz daha düşük — normal hacim zaten yüksek
    sinyal = oran >= 1.20
    detay  = f"Son3G:{son3_ort:,.0f} | Ort20G:{ort20:,.0f} | Oran:{oran:.2f}x"
    return sinyal, detay


def s3_spot_futures_farki(futures_fiyat: float,
                           spot_fiyat: Optional[float]) -> Tuple[bool, str]:
    """
    S3: Spot-Futures farkı analizi
    Normal: Futures > Spot (contango) — taşıma maliyeti
    Backwardation: Spot > Futures → fiziksel talep çok güçlü → bullish
    Fark çok açılmış contango → zayıf fiziksel talep → dikkat
    """
    if spot_fiyat is None or futures_fiyat is None or futures_fiyat == 0:
        return False, "Spot veri alınamadı"

    fark_pct = (futures_fiyat - spot_fiyat) / spot_fiyat * 100

    if fark_pct < 0:
        # Backwardation — çok güçlü fiziksel talep
        return True, f"BACKWARDATION ✓ Spot:{spot_fiyat:.2f} > Fut:{futures_fiyat:.2f} ({fark_pct:.2f}%)"
    elif fark_pct < 0.5:
        # Minimal contango — nötr/hafif bullish
        return True, f"Minimal contango ✓ Fark:{fark_pct:.2f}% (sağlıklı)"
    elif fark_pct < 1.5:
        return False, f"Normal contango Fark:{fark_pct:.2f}% | Spot:{spot_fiyat:.2f}"
    else:
        return False, f"Geniş contango ✗ Fark:{fark_pct:.2f}% — fiziksel talep zayıf"


def s4_macd_kesimi(df_1h: pd.DataFrame) -> Tuple[bool, str]:
    """
    S4: 1H MACD, sinyal çizgisini yukarı kesiyor mu?
    Hem kesim hem histogram pozitife dönüş aranır.
    """
    if df_1h is None or len(df_1h) < 35:
        return False, "1H veri yetersiz"

    kapanis = pd.to_numeric(df_1h["Close"], errors="coerce").dropna()
    macd_h, sinyal, histo = _macd(kapanis)

    histo_son  = float(histo.iloc[-1])
    histo_dun  = float(histo.iloc[-2])
    histo_evvelsi = float(histo.iloc[-3])

    macd_son   = float(macd_h.iloc[-1])
    sinyal_son = float(sinyal.iloc[-1])

    # Kesim: önceki histogram negatif, şimdi pozitife döndü
    kesim = histo_dun <= 0 and histo_son > 0

    # Momentum artıyor: histogram büyüyor
    momentum_yukseliyor = histo_son > histo_dun > histo_evvelsi

    sinyal_var = kesim or (histo_son > 0 and momentum_yukseliyor)
    detay = (f"MACD:{macd_son:.3f} Sinyal:{sinyal_son:.3f} "
             f"Histo:{histo_son:.3f} "
             f"Kesim:{'✓' if kesim else '✗'} "
             f"Mom:{'↑' if momentum_yukseliyor else '→'}")
    return sinyal_var, detay


def s5_makro_dolar(df_gunluk: pd.DataFrame) -> Tuple[bool, str]:
    """
    S5: Makro ortam altın/gümüş için uygun mu?
    DXY (dolar endeksi) zayıflıyor + VIX yüksek = altın için pozitif
    """
    puan   = 0
    notlar = []

    # DXY — dolar zayıflıyorsa altın güçlenir
    try:
        dxy = _indir("DX-Y.NYB", interval="1d", period="1mo")
        if dxy is not None and len(dxy) >= 10:
            dxy_degisim = (float(dxy["Close"].iloc[-1]) / float(dxy["Close"].iloc[-10]) - 1) * 100
            if dxy_degisim < -1.5:
                puan += 2
                notlar.append(f"DXY:{dxy_degisim:.1f}%↓✓✓")
            elif dxy_degisim < 0:
                puan += 1
                notlar.append(f"DXY:{dxy_degisim:.1f}%↓✓")
            else:
                notlar.append(f"DXY:{dxy_degisim:.1f}%↑✗")
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
    detay  = " | ".join(notlar) + f" (Puan:{puan}/5)"
    return sinyal, detay


# ════════════════════════════════════════════════════════════════════════════
# ANA ALARM
# ════════════════════════════════════════════════════════════════════════════

def _log_fiyat_serisi(sembol: str) -> Optional[pd.Series]:
    """gold-api log dosyasından fiyat serisi oluştur."""
    try:
        if not Path(FIYAT_LOG).exists():
            return None
        log = json.loads(Path(FIYAT_LOG).read_text(encoding="utf-8"))
        kayitlar = [(k["tarih"], k[sembol]) for k in log
                    if sembol in k and k[sembol] is not None]
        if len(kayitlar) < 5:
            return None
        idx    = pd.to_datetime([k[0] for k in kayitlar])
        values = [k[1] for k in kayitlar]
        return pd.Series(values, index=idx)
    except:
        return None


def s1_momentum_log(sembol: str, spot_bugun: Optional[float]) -> tuple[bool, str]:
    """S1: gold-api log ile momentum kırılması (gümüş için)."""
    seri = _log_fiyat_serisi(sembol)
    if seri is None or len(seri) < 5:
        return False, f"Log yetersiz ({len(seri) if seri is not None else 0} kayıt — birikiyor)"

    bugun   = spot_bugun or float(seri.iloc[-1])
    max_20  = float(seri.iloc[:-1].max())
    min_20  = float(seri.iloc[:-1].min())

    if bugun > max_20 * 0.998:
        return True, f"↗ Kırıldı! {bugun:.2f} > Max:{max_20:.2f}"
    elif bugun < min_20:
        return False, f"↘ Dip kırıldı! {bugun:.2f} < Min:{min_20:.2f} ⚠️"
    else:
        return False, f"Bugün:{bugun:.2f} | Max:{max_20:.2f} | Fark:{((bugun/max_20)-1)*100:.1f}%"


def s2_hacim_log(sembol: str) -> tuple[bool, str]:
    """S2: gold-api log ile fiyat hızlanması (hacim proxy — gümüş için)."""
    seri = _log_fiyat_serisi(sembol)
    if seri is None or len(seri) < 6:
        return False, f"Log yetersiz — hacim proxy henüz hazır değil"

    # Fiyat değişim hızı: son 3 ölçüm vs önceki 3 ölçüm
    son3_volatilite  = float(seri.iloc[-3:].pct_change().abs().mean()) * 100
    once3_volatilite = float(seri.iloc[-6:-3].pct_change().abs().mean()) * 100

    if once3_volatilite == 0:
        return False, "Volatilite hesaplanamadı"

    oran = son3_volatilite / once3_volatilite
    sinyal = oran >= 1.3 and son3_volatilite > 0.3
    detay  = f"Son volatilite:%{son3_volatilite:.2f} | Önceki:%{once3_volatilite:.2f} | Oran:{oran:.2f}x"
    return sinyal, detay


def enstruman_analiz(isim: str, cfg: dict) -> dict:
    print(f"\n  {'─'*40}")
    print(f"  {cfg['emoji']} {isim} analiz ediliyor...")

    # Veri çek
    etf_sembol = cfg.get("etf")
    log_sembol = cfg.get("log_sembol")
    df_etf = _indir(etf_sembol, interval="1d", period="3mo") if etf_sembol else None
    df_gun = _indir(cfg["futures"], interval="1d", period="3mo")
    df_1h  = _indir(etf_sembol or cfg["futures"], interval="1h", period="1mo")
    spot   = _spot_fiyat(cfg["spot_url"])
    fut_fiyat = float(df_gun["Close"].iloc[-1]) if df_gun is not None else None

    # Fiyat gösterimi: spot birincil
    gosterim_fiyat = spot if spot else fut_fiyat
    etf_fiyat = float(df_etf["Close"].iloc[-1]) if df_etf is not None else None
    if gosterim_fiyat:
        etf_str = f" | {etf_sembol}:{etf_fiyat:.2f}" if etf_fiyat and etf_sembol else ""
        print(f"  Spot: {gosterim_fiyat:.2f} (gold-api.com){etf_str}")

    # 5 Sinyal — ETF varsa ETF, yoksa gold-api log kullan
    if df_etf is not None:
        s1, d1 = s1_momentum_kirilmasi(df_etf)
        s2, d2 = s2_hacim_artisi(df_etf)
    else:
        # gold-api log bazlı (gümüş)
        s1, d1 = s1_momentum_log(log_sembol or "XAG", spot)
        s2, d2 = s2_hacim_log(log_sembol or "XAG")
    s3, d3 = s3_spot_futures_farki(fut_fiyat, spot)
    s4, d4 = s4_macd_kesimi(df_1h)
    s5, d5 = s5_makro_dolar(df_gun)

    ACIKLAMALAR = {
        "S1": "Fiyat son 20 günün zirvesini kırdı mı? Kırarsa güçlü yukarı sinyal.",
        "S2": "Son 3 gün hacmi normalin kaç katı? 1.3x+ = gerçek alıcı var.",
        "S3": "Spot fiyat futures fiyatından yüksekse (backwardation) fiziksel talep çok güçlü.",
        "S4": "MACD çizgisi sinyal çizgisini yukarı kesti mi? Kestiyse trend dönüyor.",
        "S5": "Dolar zayıf + korku yüksek + faiz düşük = altın/gümüş için olumlu ortam.",
    }
    print(f"  {'✅' if s1 else '❌'} S1 Momentum Kırılması : {d1}")
    print(f"     💡 {ACIKLAMALAR['S1']}")
    print(f"  {'✅' if s2 else '❌'} S2 Hacim Artışı       : {d2}")
    print(f"     💡 {ACIKLAMALAR['S2']}")
    print(f"  {'✅' if s3 else '❌'} S3 Spot-Futures       : {d3}")
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

    print(f"\n  SKOR: {yesil}/5 → {emoji_k} {karar}")

    return {
        "isim": isim,
        "futures_fiyat": fut_fiyat,
        "spot_fiyat": spot,
        "skor": yesil,
        "karar": karar,
        "emoji_k": emoji_k,
        "sinyaller": {
            "S1_Momentum": {"sonuc": s1, "detay": d1},
            "S2_Hacim":    {"sonuc": s2, "detay": d2},
            "S3_SpotFut":  {"sonuc": s3, "detay": d3},
            "S4_MACD":     {"sonuc": s4, "detay": d4},
            "S5_Makro":    {"sonuc": s5, "detay": d5},
        }
    }


def telegram_mesaj_olustur(sonuclar: list, tarih: str) -> str:
    mesaj = f"<b>⚡ ALTIN & GÜMÜŞ ALARM</b>\n{tarih}\n{'─'*30}\n"

    for s in sonuclar:
        # Spot birincil, futures ikincil
        if s['spot_fiyat']:
            fiyat_str = f"{s['spot_fiyat']:.2f} (spot)"
            if s['futures_fiyat']:
                fiyat_str += f" / {s['futures_fiyat']:.2f} (fut)"
        else:
            fiyat_str = f"{s['futures_fiyat']:.2f} (fut)" if s['futures_fiyat'] else "?"

        sig = s["sinyaller"]
        def _satir(ok, baslik, detay, acik):
            return f"{'✅' if ok else '❌'} {baslik}: {detay[:40]}\n   ↳ {acik}"
        satir = "\n".join([
            _satir(sig['S1_Momentum']['sonuc'], "Momentum", sig['S1_Momentum']['detay'],
                   "20G zirvesi kırıldı mı? Kırarsa güçlü yukarı sinyal."),
            _satir(sig['S2_Hacim']['sonuc'], "Hacim", sig['S2_Hacim']['detay'],
                   "1.3x+ = gerçek alıcı var, yükseliş inandırıcı."),
            _satir(sig['S3_SpotFut']['sonuc'], "Spot/Fut", sig['S3_SpotFut']['detay'],
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

    # Ortak makro notu
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
    _telegram(mesaj)

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
