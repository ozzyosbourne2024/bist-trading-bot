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
        # Telegram mesaj limiti 4096 karakter
        if len(mesaj) > 4096:
            mesaj = mesaj[:4090] + "..."
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": parse_mode},
            timeout=15,
        )
        return r.status_code == 200
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

def hisse_hareketleri() -> list:
    """Sabah raporundaki önerilen hisselerin günlük hareketi."""
    rapor = _son_rapor_oku("bist_rapor")

    # Portföy ticker'larını çıkar — farklı JSON yapılarına uyum
    portfoy = rapor.get("portfoy", [])
    secimler = rapor.get("secimler", [])
    tum_hisseler = portfoy or secimler

    # Eğer rapor yoksa varsayılan izleme listesi
    if not tum_hisseler:
        tum_hisseler = [
            {"ticker": "GARAN.IS"}, {"ticker": "ENKAI.IS"},
            {"ticker": "TAVHL.IS"}, {"ticker": "ISCTR.IS"},
            {"ticker": "THYAO.IS"}, {"ticker": "AKBNK.IS"},
            {"ticker": "YKBNK.IS"},
        ]

    hareketler = []
    for h in tum_hisseler[:8]:
        ticker = h.get("ticker", h.get("sembol", h.get("hisse", "")))
        if not ticker:
            continue
        if not ticker.endswith(".IS"):
            ticker += ".IS"
        df = _yf_cek(ticker, period="5d", interval="1d")
        if df is None or len(df) < 2:
            continue
        son    = float(df["Close"].iloc[-1])
        dun    = float(df["Close"].iloc[-2])
        degisim = (son / dun - 1) * 100
        hareketler.append({
            "ticker": ticker.replace(".IS", ""),
            "son": son,
            "degisim": degisim,
        })
    return sorted(hareketler, key=lambda x: x["degisim"], reverse=True)


# ════════════════════════════════════════════════════════════════════════════
# MODÜL ÇALIŞTIR
# ════════════════════════════════════════════════════════════════════════════

def bist_alarm_calistir() -> dict:
    print("  [1/3] BIST Alarm çalışıyor...")
    _script_calistir("bist_alarm.py")
    return _alarm_json_oku("bist_alarm_log.json")

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

    satirlar = [f"<b>📊 BIST SİSTEM RAPORU</b>"]
    satirlar.append(f"{tarih}")
    satirlar.append("─" * 30)

    # ── BIST Alarm ──────────────────────────────────────────────
    bist_skor  = bist.get("skor", "?")
    bist_karar = bist.get("karar", "VERİ YOK")
    bist_endeks = bist.get("endeks")
    bist_emoji = {
        "KESİN ALIM ZAMANI": "🟢🟢🟢",
        "KISMİ ALIM BAŞLA":  "🟡🟡",
        "YAKLAŞIYOR — İzle": "🟠",
        "BEKLE":             "🔴",
    }.get(bist_karar, "⚪")

    endeks_str = f" | BIST100: {bist_endeks:,.0f}" if bist_endeks else ""
    satirlar.append(f"\n<b>🎯 BIST: {bist_skor}/5 {bist_emoji} {bist_karar}</b>{endeks_str}")

    # Sinyaller — sadece aktif olanlar + önemli detay
    sinyaller = bist.get("sinyaller", {})
    for key, label in [
        ("S1_Momentum", "Momentum"),
        ("S2_Breadth",  "Breadth"),
        ("S3_RSI",      "RSI"),
        ("S4_Hisse",    "Hisseler"),
        ("S5_Makro",    "Makro"),
    ]:
        if key in sinyaller:
            s = sinyaller[key]
            icon = "✅" if s.get("sonuc") else "❌"
            detay = s.get("detay", "")[:45]
            satirlar.append(f"  {icon} {label}: {detay}")

    # ── Piyasa Sağlığı ──────────────────────────────────────────
    ps_skor  = piyasa.get("risk_skoru", "?")
    ps_rejim = piyasa.get("rejim_adi", "VERİ YOK")
    if ps_skor != "?":
        ps_renk = "🔴" if ps_skor >= 65 else "🟡" if ps_skor >= 45 else "🟢"
        satirlar.append(f"\n<b>🏥 PİYASA SAĞLIĞI: {ps_skor}/100</b>")
        satirlar.append(f"{ps_renk} {ps_rejim}")

    # ── Denetçi ─────────────────────────────────────────────────
    ihlal = denetci.get("ihlal_sayisi", "?")
    kod_test = denetci.get("kod_testleri", {})
    gecen = kod_test.get("gecen", "?")
    satirlar.append(f"\n<b>🔎 DENETÇİ</b>")
    if ihlal == 0:
        satirlar.append(f"  ✅ Kural ihlali yok")
    elif ihlal != "?":
        satirlar.append(f"  ⚠️ {ihlal} kural ihlali!")
    if gecen != "?":
        satirlar.append(f"  🧪 Kod testleri: {gecen}/8")

    # Backtest özeti
    bt = denetci.get("backtest", {})
    if bt and bt.get("hedef_isabet_pct"):
        satirlar.append(f"  📈 Backtest: Hedef isabet %{bt['hedef_isabet_pct']:.0f} | Ort getiri: %{bt.get('ort_getiri',0):.1f}")

    satirlar.append("─" * 30)

    # ── Altın / Gümüş ───────────────────────────────────────────
    satirlar.append(f"\n<b>⚡ ALTIN & GÜMÜŞ</b>")
    sonuclar = altin.get("sonuclar", [])
    for s in sonuclar:
        isim     = s.get("isim", "?")
        skor     = s.get("skor", "?")
        karar    = s.get("karar", "?")
        emoji_k  = s.get("emoji_k", "")
        spot     = s.get("spot_fiyat")
        enstruman_emoji = "🥇" if isim == "ALTIN" else "🥈"
        futures = s.get("futures_fiyat")
        spot_str = f"Spot:{spot:.2f}" if spot else ""
        fut_str  = f"Fut:{futures:.2f}" if futures else ""
        fiyat_str = " | ".join(filter(None, [spot_str, fut_str]))
        if fiyat_str:
            fiyat_str = f" | {fiyat_str}"

        satirlar.append(f"\n{enstruman_emoji} <b>{isim}{fiyat_str}</b>")
        satirlar.append(f"  {emoji_k} {skor}/5 → {karar}")

        # Alt sinyaller
        sig = s.get("sinyaller", {})
        for key, label in [
            ("S1_Momentum", "Momentum"),
            ("S2_Hacim",    "Hacim"),
            ("S3_RSI",      "RSI1H+4H"),
            ("S4_MACD",     "MACD"),
            ("S5_Makro",    "Makro"),
        ]:
            if key in sig:
                icon = "✅" if sig[key].get("sonuc") else "❌"
                detay = sig[key].get("detay", "")[:35]
                satirlar.append(f"  {icon} {label}: {detay}")

    # ── Genel Tavsiye ───────────────────────────────────────────
    satirlar.append(f"\n{'─'*30}")

    # En önemli aksiyon
    if bist_skor != "?" and bist_skor >= 5:
        satirlar.append("⚡ <b>BIST: ALIM ZAMANI — bist_agents.py çalıştır!</b>")
    elif bist_skor != "?" and bist_skor >= 3:
        satirlar.append("⚠️ <b>BIST: Kısmi alım düşün</b>")
    else:
        satirlar.append("⏳ <b>BIST: Bekle</b>")

    altin_karar = next((s.get("karar") for s in sonuclar if s.get("isim") == "ALTIN"), None)
    gumus_karar = next((s.get("karar") for s in sonuclar if s.get("isim") == "GUMUS"), None)
    if altin_karar and "ALIM" in altin_karar:
        satirlar.append(f"🥇 <b>ALTIN: {altin_karar}</b>")
    if gumus_karar and "ALIM" in gumus_karar:
        satirlar.append(f"🥈 <b>GÜMÜŞ: {gumus_karar}</b>")

    # Senaryo uyarıları
    if bist.get("senaryo_a"):
        satirlar.append(f"\n🚨 <b>SENARYO A: DİP ALIM FIRSATI!</b>")
        satirlar.append(f"  BIST100 dip bölgesinde, hisseler aşırı satımda")
    if bist.get("senaryo_b"):
        satirlar.append(f"\n🚀 <b>SENARYO B: KIRILMA AKTİF!</b>")
        satirlar.append(f"  14.400 direnci kırıldı, momentum alımı zamanı")

    # ── NASDAQ100 ───────────────────────────────────────────────
    if nasdaq:
        yon = "↑" if nasdaq.get("degisim_dun", 0) > 0 else "↓"
        renk = "🟢" if nasdaq.get("degisim_dun", 0) > 0 else "🔴"
        satirlar.append(f"\n{'─'*30}")
        satirlar.append(f"\n<b>📈 NASDAQ100</b>")
        satirlar.append(f"  {renk} {nasdaq['son']:,.0f} | Dünden: {nasdaq['degisim_dun']:+.2f}% {yon}")
        satirlar.append(f"  Gün içi: {nasdaq['gun_degisim']:+.2f}%")

    # ── Altın & Gümüş Gün İçi ──────────────────────────────────
    if altin_gun:
        yon = "↑" if altin_gun.get("degisim_dun", 0) > 0 else "↓"
        renk = "🟢" if altin_gun.get("degisim_dun", 0) > 0 else "🔴"
        satirlar.append(f"\n<b>🥇 ALTIN GÜN İÇİ</b>")
        satirlar.append(f"  {renk} {altin_gun['son']:,.0f} | Dünden: {altin_gun['degisim_dun']:+.2f}% {yon}")
        satirlar.append(f"  Gün içi: {altin_gun['gun_degisim']:+.2f}% | 1H: {altin_gun['trend_1h']}")
        satirlar.append(f"  H:{altin_gun['gun_high']:,.0f} L:{altin_gun['gun_low']:,.0f}")
    if gumus_gun:
        yon = "↑" if gumus_gun.get("degisim_dun", 0) > 0 else "↓"
        renk = "🟢" if gumus_gun.get("degisim_dun", 0) > 0 else "🔴"
        satirlar.append(f"\n<b>🥈 GÜMÜŞ GÜN İÇİ</b>")
        satirlar.append(f"  {renk} {gumus_gun['son']:.2f} | Dünden: {gumus_gun['degisim_dun']:+.2f}% {yon}")
        satirlar.append(f"  Gün içi: {gumus_gun['gun_degisim']:+.2f}% | 1H: {gumus_gun['trend_1h']}")
        satirlar.append(f"  H:{gumus_gun['gun_high']:.2f} L:{gumus_gun['gun_low']:.2f}")

    # ── Hisse Hareketleri ────────────────────────────────────────
    if hisse_hareketler:
        satirlar.append(f"\n<b>📊 HİSSE HAREKETLERİ</b>")
        # BIST100 en üstte
        bist_endeks = bist.get("endeks")
        if bist_endeks:
            bist_gun = _yf_cek_endeks()
            if bist_gun:
                yon = "↑" if bist_gun["degisim"] > 0 else "↓"
                renk = "🟢" if bist_gun["degisim"] > 0 else "🔴"
                satirlar.append(f"  {renk} <b>BIST100: {bist_endeks:,.0f} | {bist_gun['degisim']:+.1f}% {yon}</b>")
            else:
                satirlar.append(f"  📊 <b>BIST100: {bist_endeks:,.0f}</b>")
        satirlar.append(f"  {'─'*25}")
        for h in hisse_hareketler:
            yon = "↑" if h["degisim"] > 0 else "↓"
            renk = "🟢" if h["degisim"] > 0 else "🔴"
            satirlar.append(f"  {renk} {h['ticker']}: {h['son']:.1f} | {h['degisim']:+.1f}% {yon}")

    return "\n".join(satirlar)


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

    # Gönder
    print("\n  Telegram mesajı gönderiliyor...")
    ok = _telegram(mesaj)
    print(f"  {'✓ Gönderildi' if ok else '✗ Gönderilemedi'}")

    print(f"\n{'='*55}")


if __name__ == "__main__":
    main()
