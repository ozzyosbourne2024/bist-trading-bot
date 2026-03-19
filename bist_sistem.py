#!/usr/bin/env python3
"""
BIST SİSTEM ORKESTRASYONU v1.0
================================
Tüm modülleri çalıştırır, tek Telegram mesajı gönderir.

Sabah 09:00 → bist_sabah.py (bu değil, ayrı workflow)
10:30/11:30/14:30/15:30 → bu script:
  1. bist_alarm.py       → BIST 5 sinyal
  2. altin_gumus_alarm   → Altın/Gümüş sinyalleri
  3. bist_denetci        → Kural kontrolü + backtest özeti
  4. bist_piyasa_sagligi → Son raporu oku
  5. Tek Telegram mesajı
"""

import os, sys, json, subprocess, warnings, time
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

try:
    import requests
    import pandas as pd
    import yfinance as yf
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as e:
    print(f"Eksik: {e}")
    sys.exit(1)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
RAPORLAR_DIR     = Path("raporlar")


# ════════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════════

def _telegram(mesaj: str, parse_mode: str = "HTML") -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram token eksik")
        print(mesaj)
        return False
    try:
        if len(mesaj) > 4096:
            mesaj = mesaj[:4090] + "..."
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": parse_mode},
            timeout=15,
        )
        if r.status_code == 200:
            return True
        # HTML parse hatası → tag'leri temizle, düz metin olarak tekrar dene
        if r.status_code == 400 and "parse" in r.text.lower():
            print(f"  HTML parse hatası, düz metin deneniyor...")
            import re
            temiz = re.sub(r"<[^>]+>", "", mesaj)
            r2 = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": temiz},
                timeout=15,
            )
            if r2.status_code == 200:
                return True
            print(f"  Telegram HTTP {r2.status_code}: {r2.text[:200]}")
        else:
            print(f"  Telegram HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"Telegram hata: {e}")
        return False

def _script_calistir(script: str, args: list = []) -> dict:
    """Script çalıştır, stdout'u JSON olarak parse et."""
    try:
        r = subprocess.run(
            [sys.executable, script] + args,
            capture_output=True, text=True, timeout=120
        )
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Timeout (120s)"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}

def _son_rapor_oku(prefix: str) -> dict:
    """raporlar/ klasöründen en son raporu oku."""
    if not RAPORLAR_DIR.exists():
        return {}
    dosyalar = sorted(RAPORLAR_DIR.glob(f"{prefix}*.json"), reverse=True)
    if not dosyalar:
        return {}
    try:
        return json.loads(dosyalar[0].read_text(encoding="utf-8"))
    except:
        return {}

def _alarm_json_oku(dosya: str) -> dict:
    """Lokal alarm log dosyasından son kaydı oku."""
    try:
        if not Path(dosya).exists():
            return {}
        log = json.loads(Path(dosya).read_text(encoding="utf-8"))
        return log[-1] if log else {}
    except:
        return {}


# ════════════════════════════════════════════════════════════════════════════
# PİYASA VERİLERİ
# ════════════════════════════════════════════════════════════════════════════

def _yf_cek(ticker: str, period: str = "2d", interval: str = "1d"):
    for _ in range(3):
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
            if not df.empty:
                return df
        except:
            pass
        time.sleep(1)
    return None

def nasdaq_ozet() -> dict:
    """NASDAQ100 günlük özet."""
    df = _yf_cek("^NDX", period="5d", interval="1d")
    if df is None or len(df) < 2:
        return {}
    son   = float(df["Close"].iloc[-1])
    dun   = float(df["Close"].iloc[-2])
    acilis = float(df["Open"].iloc[-1])
    degisim = (son / dun - 1) * 100
    gun_degisim = (son / acilis - 1) * 100
    return {
        "son": son,
        "degisim_dun": degisim,
        "gun_degisim": gun_degisim,
    }

def _emtia_gun_ici(ticker: str) -> dict:
    """Emtia gün içi hareket özeti (GC=F, SI=F vb.)"""
    df_gun = _yf_cek(ticker, period="5d", interval="1d")
    df_1h  = _yf_cek(ticker, period="2d", interval="1h")
    if df_gun is None or len(df_gun) < 2:
        return {}
    son        = float(df_gun["Close"].iloc[-1])
    dun        = float(df_gun["Close"].iloc[-2])
    acilis     = float(df_gun["Open"].iloc[-1])
    degisim_dun = (son / dun - 1) * 100
    gun_degisim = (son / acilis - 1) * 100
    gun_high   = float(df_gun["High"].iloc[-1])
    gun_low    = float(df_gun["Low"].iloc[-1])
    trend = "→"
    if df_1h is not None and len(df_1h) >= 4:
        son4 = df_1h["Close"].iloc[-4:]
        if float(son4.iloc[-1]) > float(son4.iloc[0]):
            trend = "↑"
        elif float(son4.iloc[-1]) < float(son4.iloc[0]):
            trend = "↓"
    return {"son": son, "degisim_dun": degisim_dun, "gun_degisim": gun_degisim,
            "gun_high": gun_high, "gun_low": gun_low, "trend_1h": trend}

def altin_gun_ici() -> dict:
    return _emtia_gun_ici("GC=F")

def gumus_gun_ici() -> dict:
    return _emtia_gun_ici("SI=F")

def _yf_cek_endeks() -> dict:
    """BIST100 günlük değişim."""
    df = _yf_cek("XU100.IS", period="5d", interval="1d")
    if df is None or len(df) < 2:
        return None
    son = float(df["Close"].iloc[-1])
    dun = float(df["Close"].iloc[-2])
    return {"son": son, "degisim": (son / dun - 1) * 100}

def _rsi(seri, period=14):
    delta  = seri.diff()
    kazan  = delta.clip(lower=0).rolling(period).mean()
    kayip  = (-delta).clip(lower=0).rolling(period).mean()
    rs     = kazan / kayip
    return float(100 - (100 / (1 + rs)).iloc[-1])


def _teknik_durum(ticker: str, giris: float = None, hedef: float = None, stop: float = None) -> dict:
    """Her hisse için RSI, MA, MACD, destek/direnç, uyarı hesapla."""
    t = ticker if ticker.endswith(".IS") else ticker + ".IS"
    df = _yf_cek(t, period="3mo", interval="1d")
    if df is None or len(df) < 20:
        return {}

    kapanis = df["Close"]
    son     = float(kapanis.iloc[-1])
    dun     = float(kapanis.iloc[-2])
    degisim = (son / dun - 1) * 100

    # RSI
    try:
        rsi     = _rsi(kapanis)
        rsi_dun = _rsi(kapanis.iloc[:-1])
        rsi_yon = "↑" if rsi > rsi_dun else "↓"
    except:
        rsi, rsi_yon = 50, "?"

    # MA
    ma20 = float(kapanis.rolling(20).mean().iloc[-1])
    ma50 = float(kapanis.rolling(50).mean().iloc[-1]) if len(kapanis) >= 50 else None

    # MACD
    ema12  = kapanis.ewm(span=12).mean()
    ema26  = kapanis.ewm(span=26).mean()
    macd   = ema12 - ema26
    sinyal = macd.ewm(span=9).mean()
    histo  = macd - sinyal
    macd_yon = "↑" if float(histo.iloc[-1]) > float(histo.iloc[-2]) else "↓"

    # Destek / direnç (son 2 ay)
    son2ay  = kapanis.iloc[-44:] if len(kapanis) >= 44 else kapanis
    destek  = float(son2ay.min())
    direnc  = float(son2ay.max())

    # Trend
    if son > ma20 > ma50 if ma50 else son > ma20:
        trend = "YUKARI ↑"
    elif son < ma20:
        trend = "ASAGI ↓"
    else:
        trend = "YATAY →"

    # Uyarılar
    uyarilar = []
    if hedef and son >= hedef * 0.95:
        uyarilar.append(f"🎯 Hedefe yakın! ({son:.2f}/{hedef:.2f}) — kısmi satış düşün")
    if stop and son <= stop * 1.03:
        uyarilar.append(f"🛑 Stop yakın! ({son:.2f}/{stop:.2f}) — dikkat")
    if rsi > 75:
        uyarilar.append(f"📈 RSI:{rsi:.0f} — aşırı alım, balon riski")
    if rsi < 30:
        uyarilar.append(f"📉 RSI:{rsi:.0f} — aşırı satım, dip olabilir")
    if giris and son >= giris * 1.15:
        kar_pct = (son / giris - 1) * 100
        uyarilar.append(f"💰 Giriş üstünde +{kar_pct:.1f}% — kâr realizasyonu?")

    return {
        "son":      son,
        "degisim":  degisim,
        "rsi":      rsi,
        "rsi_yon":  rsi_yon,
        "ma20_pos": son > ma20,
        "macd_yon": macd_yon,
        "trend":    trend,
        "destek":   destek,
        "direnc":   direnc,
        "uyarilar": uyarilar,
    }


def hisse_hareketleri() -> list:
    """Portföy hisselerinin günlük hareketi + teknik durum."""
    tum_tickers  = []
    pozisyonlar  = {}

    # 1. portfoy_pozisyonlar.json
    for poz_yol in ["portfoy_pozisyonlar.json", "raporlar/portfoy_pozisyonlar.json"]:
        if Path(poz_yol).exists():
            try:
                veri = json.loads(Path(poz_yol).read_text(encoding="utf-8"))
                if isinstance(veri, list):
                    tum_tickers = [p["ticker"] for p in veri if p.get("karar") == "AL" and p.get("ticker")]
                elif isinstance(veri, dict):
                    pozisyonlar = veri.get("pozisyonlar", {})
                    tum_tickers = list(pozisyonlar.keys())
                if tum_tickers:
                    print(f"  Portföy: {tum_tickers} ({poz_yol})")
                    break
            except:
                pass

    # 2. raporlar/ klasöründen bist_rapor JSON
    if not tum_tickers:
        rapor = _son_rapor_oku("bist_rapor")
        for key in ["kararlar", "portfoy", "secimler"]:
            liste = rapor.get(key, [])
            if liste:
                tum_tickers = [h.get("ticker","") for h in liste if h.get("karar") == "AL" and h.get("ticker")]
                if tum_tickers:
                    break

    # 3. Yedek liste
    if not tum_tickers:
        print("  ⚠️  Portföy dosyası bulunamadı, varsayılan liste")
        tum_tickers = ["GLYHO","ENKAI","TAVHL","BIMAS","THYAO","TCELL","ASELS"]

    hareketler = []
    for ticker in tum_tickers[:12]:
        poz   = pozisyonlar.get(ticker, {})
        giris = poz.get("giris_fiyati")
        hedef = poz.get("hedef")
        stop  = poz.get("stop")

        t = _teknik_durum(ticker, giris, hedef, stop)
        if not t:
            continue

        hareketler.append({
            "ticker":   ticker.replace(".IS",""),
            "son":      t["son"],
            "degisim":  t["degisim"],
            "rsi":      t.get("rsi"),
            "rsi_yon":  t.get("rsi_yon","?"),
            "ma20_pos": t.get("ma20_pos"),
            "macd_yon": t.get("macd_yon","?"),
            "trend":    t.get("trend","?"),
            "destek":   t.get("destek"),
            "direnc":   t.get("direnc"),
            "giris":    giris,
            "hedef":    hedef,
            "stop":     stop,
            "uyarilar": t.get("uyarilar", []),
        })

    # Bileşik skora göre sırala (JSON'dan), yoksa günlük değişime göre
    try:
        poz_dosya = next((y for y in ["portfoy_pozisyonlar.json","raporlar/portfoy_pozisyonlar.json"] if Path(y).exists()), None)
        if poz_dosya:
            poz_json = json.loads(Path(poz_dosya).read_text(encoding="utf-8"))
            poz_map  = poz_json.get("pozisyonlar", {})
            for h in hareketler:
                h["bilsik_skor"] = poz_map.get(h["ticker"], {}).get("bilsik_skor", 0) or 0
            return sorted(hareketler, key=lambda x: x.get("bilsik_skor", 0), reverse=True)
    except:
        pass
    return sorted(hareketler, key=lambda x: x["degisim"], reverse=True)


# ════════════════════════════════════════════════════════════════════════════
# MODÜL ÇALIŞTIR
# ════════════════════════════════════════════════════════════════════════════

def bist_alarm_calistir() -> dict:
    print("  [1/3] BIST Alarm çalışıyor...")
    r = _script_calistir("bist_alarm.py")
    # Önce log dosyasını dene
    sonuc = _alarm_json_oku("bist_alarm_log.json")
    if sonuc:
        return sonuc
    # Log yoksa stdout'tan JSON parse et
    try:
        for satir in reversed(r.get("stdout", "").splitlines()):
            satir = satir.strip()
            if satir.startswith("{"):
                return json.loads(satir)
    except:
        pass
    if r.get("stderr"):
        print(f"  BIST Alarm hata: {r['stderr'][:200]}")
    return {}

def altin_alarm_calistir() -> dict:
    print("  [2/3] Altın/Gümüş Alarm çalışıyor...")
    _script_calistir("altin_gumus_alarm.py")
    return _alarm_json_oku("altin_alarm_log.json")

def denetci_calistir() -> dict:
    print("  [3/3] Denetçi çalışıyor...")
    r = _script_calistir("bist_denetci.py", ["--kural"])
    # Son denetim raporunu oku
    return _son_rapor_oku("denetim_raporu")


# ════════════════════════════════════════════════════════════════════════════
# TELEGRAM MESAJI OLUŞTUR
# ════════════════════════════════════════════════════════════════════════════

def mesaj_olustur(tarih: str, bist: dict, altin: dict, denetci: dict,
                  piyasa: dict, nasdaq: dict = None, altin_gun: dict = None,
                  gumus_gun: dict = None, hisse_hareketler: list = None) -> str:

    s = []

    # ── Başlık ──────────────────────────────────────────────────
    s.append(f"<b>📊 BIST SİSTEM — {tarih}</b>")

    # ── BIST100 yön ─────────────────────────────────────────────
    bist_endeks = bist.get("endeks")
    bist_gun    = _yf_cek_endeks()
    if bist_endeks and bist_gun:
        deg = bist_gun.get("degisim", 0)
        yon = "↑" if deg > 0 else "↓"
        renk = "🟢" if deg > 0 else "🔴"
        s.append(f"\n{renk} <b>BIST100: {bist_endeks:,.0f} | {deg:+.1f}% {yon}</b>")
    elif bist_endeks:
        s.append(f"\n<b>BIST100: {bist_endeks:,.0f}</b>")

    # ── BIST Alarm sinyalleri ────────────────────────────────────
    bist_skor  = bist.get("skor", "?")
    bist_karar = bist.get("karar", "VERİ YOK")
    bist_emoji = {"KESİN ALIM ZAMANI": "🟢🟢🟢", "KISMİ ALIM BAŞLA": "🟡🟡",
                  "YAKLAŞIYOR — İzle": "🟠", "BEKLE": "🔴"}.get(bist_karar, "⚪")
    s.append(f"\n🎯 <b>BIST: {bist_skor}/5 {bist_emoji} {bist_karar}</b>")

    sinyaller = bist.get("sinyaller", {})
    for key, label in [("S1_Momentum","Momentum"),("S2_Breadth","Breadth"),
                       ("S3_RSI","RSI"),("S4_Hisse","Hisseler"),("S5_Makro","Makro")]:
        if key in sinyaller:
            ok    = sinyaller[key].get("sonuc", False)
            detay = sinyaller[key].get("detay", "")[:50]
            s.append(f"  {'✅' if ok else '❌'} {label}: {detay}")

    # Senaryo uyarıları
    if bist.get("senaryo_a"):
        s.append(f"\n🚨 <b>SENARYO A: DİP ALIM FIRSATI!</b>")
    if bist.get("senaryo_b"):
        s.append(f"\n🚀 <b>SENARYO B: KIRILMA AKTİF!</b>")

    # ── Hisse Bazlı Giriş Sinyalleri ─────────────────────────────
    hisse_sig = bist.get("hisse_sinyalleri", [])
    if hisse_sig:
        s.append(f"\n🔔 <b>GİRİŞ SİNYALLERİ</b>")
        for hs in hisse_sig:
            tip   = hs.get("tip", "")
            isim  = hs.get("ticker", "")
            fiyat = hs.get("fiyat", 0)
            detay = hs.get("detay", "")
            em    = {"DİP GİRİŞ": "📉➡️📈", "KIRILMA": "🚀", "MACD DÖNÜŞ": "🔄"}.get(tip, "⚡")
            s.append(f"  {em} <b>{isim}</b> {fiyat:.1f} — {tip}")
            s.append(f"     {detay}")

    # ── Piyasa Sağlığı ───────────────────────────────────────────
    ps_skor  = piyasa.get("risk_skoru", "?")
    ps_rejim = piyasa.get("rejim_adi", "")
    if ps_skor != "?":
        ps_renk = "🔴" if ps_skor >= 65 else "🟡" if ps_skor >= 45 else "🟢"
        s.append(f"\n🏥 Piyasa: {ps_renk} {ps_skor}/100 {ps_rejim}")

    # ── Denetçi ──────────────────────────────────────────────────
    ihlal = denetci.get("ihlal_sayisi", "?")
    if ihlal == 0:
        s.append(f"🔎 Denetçi: ✅ Temiz")
    elif ihlal != "?":
        s.append(f"🔎 Denetçi: ⚠️ {ihlal} ihlal!")

    # ── Portföy Önerisi ──────────────────────────────────────────
    if hisse_hareketler:
        s.append(f"\n<b>💼 PORTFÖY TAKİP</b>")
        madalya = ["🥇","🥈","🥉"]
        for i, h in enumerate(hisse_hareketler):
            yon  = "↑" if h["degisim"] > 0 else "↓"
            renk = "🟢" if h["degisim"] > 0 else "🔴"
            sira = madalya[i] if i < 3 else f"{i+1}."
            bilsik_str = f" 〔{h['bilsik_skor']:.0f}〕" if h.get("bilsik_skor") else ""
            s.append(f"  {sira} {renk} <b>{h['ticker']}</b>{bilsik_str}: {h['son']:.2f} | {h['degisim']:+.1f}% {yon}")
            ma_em  = "✅" if h.get("ma20_pos") else "❌"
            s.append(
                f"    RSI:{h['rsi']:.0f}{h['rsi_yon']} | MA20:{ma_em} | "
                f"MACD:{h['macd_yon']} | {h['trend']}"
            )
            if h.get("destek") and h.get("direnc"):
                hedef_str = f" | 🎯{h['hedef']:.2f}" if h.get("hedef") else ""
                stop_str  = f" | 🛑{h['stop']:.2f}"  if h.get("stop")  else ""
                s.append(f"    Des:{h['destek']:.2f} | Dir:{h['direnc']:.2f}{hedef_str}{stop_str}")
            for u in h.get("uyarilar", []):
                s.append(f"    {u}")

    # ── Altın & Gümüş ────────────────────────────────────────────
    s.append(f"\n<b>⚡ ALTIN & GÜMÜŞ</b>")
    sonuclar = altin.get("sonuclar", [])
    for enst in sonuclar:
        isim      = enst.get("isim", "?")
        skor      = enst.get("skor", "?")
        karar     = enst.get("karar", "?")
        emoji_k   = enst.get("emoji_k", "")
        anlik     = enst.get("anlik_fiyat") or enst.get("fiyat")
        dun_k     = enst.get("dun_kapanis")
        deg       = enst.get("degisim_pct")
        em        = "🥇" if isim == "ALTIN" else "🥈"

        # Fiyat satırı: Dün:5185$ | Spot:5179$ -0.1%↓ | Fut:5182$ +0.2%↑
        fiyat_parca = []
        if dun_k:
            fiyat_parca.append(f"Dün:{dun_k:.2f}$")
        if anlik:
            yon = "↑" if (deg or 0) > 0 else "↓"
            fiyat_parca.append(f"Spot:{anlik:.2f}$ {deg:+.1f}%{yon}" if deg is not None else f"Spot:{anlik:.2f}$")

        fut     = enst.get("futures_fiyat")
        fut_deg = enst.get("futures_degisim_pct")
        if fut:
            yon_f = "↑" if (fut_deg or 0) > 0 else "↓"
            fut_str = f"Fut:{fut:.2f}$ {fut_deg:+.1f}%{yon_f}" if fut_deg is not None else f"Fut:{fut:.2f}$"
            # Futures spot'tan güçlüyse vurgula
            if fut_deg and deg and fut_deg > deg + 0.3:
                fut_str += " ⚡"
            fiyat_parca.append(fut_str)
        fiyat_str = " | ".join(fiyat_parca)

        s.append(f"\n{em} <b>{isim}</b> — {fiyat_str}")
        s.append(f"  {emoji_k} {skor}/5 → {karar}")
        # Sinyal detayları
        sig = enst.get("sinyaller", {})
        for key, label in [("S1_Momentum","Momentum"),("S2_Hacim","Hacim"),
                           ("S3_RSI","RSI 1H+4H"),("S4_MACD","MACD"),("S5_Makro","Makro")]:
            if key in sig:
                ok    = sig[key].get("sonuc", False)
                detay = sig[key].get("detay", "")[:50]
                s.append(f"  {'✅' if ok else '❌'} {label}: {detay}")

    # ── Piyasa özeti ─────────────────────────────────────────────
    s.append("")
    if nasdaq:
        deg  = nasdaq.get("degisim_dun", 0)
        yon  = "↑" if deg > 0 else "↓"
        renk = "🟢" if deg > 0 else "🔴"
        s.append(f"📈 NASDAQ: {renk} {nasdaq['son']:,.0f} | {deg:+.1f}% {yon}")

    return "\n".join(s)


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    tarih = datetime.now().strftime("%Y-%m-%d %H:%M TR")
    print(f"\n{'='*55}")
    print(f"  BIST SİSTEM ORKESTRASYONU — {tarih}")
    print(f"{'='*55}\n")

    # Tüm modülleri çalıştır
    bist   = bist_alarm_calistir()
    altin  = altin_alarm_calistir()
    denetci = denetci_calistir()

    # En son piyasa sağlığı raporunu oku (sabah çalışmasından)
    piyasa = _son_rapor_oku("piyasa_sagligi")

    # Piyasa verileri
    print("  [4/4] Piyasa verileri çekiliyor...")
    ndx    = nasdaq_ozet()
    au     = altin_gun_ici()
    ag     = gumus_gun_ici()
    hisse  = hisse_hareketleri()

    # Tek mesaj oluştur
    mesaj = mesaj_olustur(tarih, bist, altin, denetci, piyasa, ndx, au, ag, hisse)

    # HTML güvenlik: izin verilen tag'ler dışındaki < > karakterlerini temizle
    import re
    IZINLI = re.compile(r'<(/?(b|i|u|s|code|pre|a|em|strong|blockquote|br))(\s[^>]*)?>',
                        re.IGNORECASE)
    def html_temizle(m: str) -> str:
        # İzinli tag'leri koru, diğer < > işaretlerini escape et
        parcalar = []
        pos = 0
        for mat in IZINLI.finditer(m):
            # mat öncesi kısmı escape et
            oncesi = m[pos:mat.start()]
            oncesi = oncesi.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            parcalar.append(oncesi)
            parcalar.append(mat.group(0))
            pos = mat.end()
        son = m[pos:]
        son = son.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parcalar.append(son)
        return "".join(parcalar)

    mesaj = html_temizle(mesaj)

    # Gönder
    print("\n  Telegram mesajı gönderiliyor...")
    ok = _telegram(mesaj)
    print(f"  {'✓ Gönderildi' if ok else '✗ Gönderilemedi'}")

    # Son mesajı kaydet — mesaj_degerlendirici.py okuyacak
    try:
        Path("son_mesaj.txt").write_text(mesaj, encoding="utf-8")
    except:
        pass

    print(f"\n{'='*55}")


if __name__ == "__main__":
    main()
