"""
KARDEMİR / İSDEMİR / GÜBRETAŞ — Pozisyon Analizi
Dün alındı, hala düşüyor — ne yapmalı?
"""
import os, warnings
from datetime import datetime
warnings.filterwarnings("ignore")
import numpy as np
import yfinance as yf
import requests
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY","")

# Dün alınan pozisyonlar
POZISYONLAR = {
    "KRDMD.IS": {"isim": "KARDEMİR",    "giris_tarihi": "2026-03-01"},
    "ISDMR.IS": {"isim": "İSDEMİR",     "giris_tarihi": "2026-03-01"},
    "GUBRF.IS": {"isim": "GÜBRETAŞ",    "giris_tarihi": "2026-03-01"},
}


# ────────────────────────────────────────────────────────────
# TEKNİK ANALİZ
# ────────────────────────────────────────────────────────────

def rsi_hesapla(seri, period=14):
    delta = seri.diff()
    kazanc = delta.clip(lower=0)
    kayip  = (-delta).clip(lower=0)
    ort_k  = kazanc.rolling(period).mean()
    ort_ka = kayip.rolling(period).mean()
    rs     = ort_k / ort_ka
    return 100 - (100 / (1 + rs))


def teknik_analiz(ticker: str) -> dict:
    try:
        df = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=True)
        if df is None or len(df) < 30:
            return {}

        kapanis = df["Close"]
        hacim   = df["Volume"]

        # Fiyatlar
        bugun   = float(kapanis.iloc[-1])
        dun     = float(kapanis.iloc[-2])
        hfta    = float(kapanis.iloc[-6]) if len(kapanis) >= 6 else None
        ay1     = float(kapanis.iloc[-22]) if len(kapanis) >= 22 else None
        ay3     = float(kapanis.iloc[-66]) if len(kapanis) >= 66 else None

        # Değişimler
        gun_deg  = (bugun/dun - 1)*100
        hfta_deg = (bugun/hfta - 1)*100 if hfta else None
        ay1_deg  = (bugun/ay1  - 1)*100 if ay1  else None
        ay3_deg  = (bugun/ay3  - 1)*100 if ay3  else None

        # RSI
        rsi_seri = rsi_hesapla(kapanis)
        rsi      = float(rsi_seri.iloc[-1])
        rsi_dun  = float(rsi_seri.iloc[-2])

        # Hareketli ortalamalar
        ma20  = float(kapanis.rolling(20).mean().iloc[-1])
        ma50  = float(kapanis.rolling(50).mean().iloc[-1])
        ma200 = float(kapanis.rolling(200).mean().iloc[-1]) if len(kapanis) >= 200 else None

        # Destek: Son 3 aylık dip
        son3ay    = kapanis.iloc[-66:] if len(kapanis) >= 66 else kapanis
        destek    = float(son3ay.min())
        direnc    = float(son3ay.max())
        dipe_uzak = (bugun/destek - 1)*100
        zirveye   = (direnc/bugun - 1)*100

        # Hacim
        hacim_bugun = float(hacim.iloc[-1])
        hacim_ort   = float(hacim.iloc[-20:].mean())
        hacim_x     = hacim_bugun / hacim_ort if hacim_ort > 0 else 1.0

        # Parabolic SAR (basit yaklaşım)
        sar_yon = "ASAGI" if bugun < ma20 else "YUKARI"

        # Bollinger
        std20   = float(kapanis.rolling(20).std().iloc[-1])
        bb_ust  = ma20 + 2*std20
        bb_alt  = ma20 - 2*std20
        bb_poz  = (bugun - bb_alt) / (bb_ust - bb_alt) * 100 if (bb_ust - bb_alt) > 0 else 50

        # MACD
        ema12   = kapanis.ewm(span=12).mean()
        ema26   = kapanis.ewm(span=26).mean()
        macd    = ema12 - ema26
        sinyal  = macd.ewm(span=9).mean()
        histo   = macd - sinyal
        macd_yon = "YUKARI" if float(histo.iloc[-1]) > float(histo.iloc[-2]) else "ASAGI"

        return {
            "bugun":     bugun,
            "dun":       dun,
            "gun_deg":   gun_deg,
            "hfta_deg":  hfta_deg,
            "ay1_deg":   ay1_deg,
            "ay3_deg":   ay3_deg,
            "rsi":       rsi,
            "rsi_dun":   rsi_dun,
            "rsi_yon":   "↑" if rsi > rsi_dun else "↓",
            "ma20":      ma20,
            "ma50":      ma50,
            "ma200":     ma200,
            "ma20_pos":  bugun > ma20,
            "ma50_pos":  bugun > ma50,
            "destek":    destek,
            "direnc":    direnc,
            "dipe_uzak": dipe_uzak,
            "zirveye":   zirveye,
            "hacim_x":   hacim_x,
            "sar_yon":   sar_yon,
            "bb_poz":    bb_poz,
            "macd_yon":  macd_yon,
            "macd_histo":float(histo.iloc[-1]),
        }
    except Exception as e:
        print(f"  Hata {ticker}: {e}")
        return {}


def makro_cek():
    m = {}
    for isim, ticker in [
        ("brent","BZ=F"), ("vix","^VIX"),
        ("usdtry","USDTRY=X"), ("altin","GC=F"),
        ("demir","X")  # US Steel — küresel demir talebi göstergesi
    ]:
        try:
            df = yf.Ticker(ticker).history(period="3d", interval="1d", auto_adjust=True)
            if df is not None and len(df) >= 2:
                m[isim]            = float(df["Close"].iloc[-1])
                m[isim+"_gunluk"]  = (float(df["Close"].iloc[-1])/float(df["Close"].iloc[-2])-1)*100
        except:
            pass
    return m


# ────────────────────────────────────────────────────────────
# KARAR MOTORU
# ────────────────────────────────────────────────────────────

def karar_ver(ticker: str, t: dict, makro: dict) -> dict:
    """
    TUT / BEKLE / SAT kararı
    Çok boyutlu: teknik + jeopolitik + momentum
    """
    puan = 0
    notlar = []

    if not t:
        return {"karar": "VERİ YOK", "puan": 0, "notlar": []}

    rsi = t.get("rsi", 50)

    # ── RSI ──────────────────────────────────────────────────
    if rsi < 30:
        puan += 3
        notlar.append(f"RSI:{rsi:.0f} → aşırı satış bölgesi (+3)")
    elif rsi < 40:
        puan += 2
        notlar.append(f"RSI:{rsi:.0f} → satış bölgesi (+2)")
    elif rsi < 50:
        puan += 1
        notlar.append(f"RSI:{rsi:.0f} → nötr-zayıf (+1)")
    else:
        notlar.append(f"RSI:{rsi:.0f} → yüksek, dikkat (0)")

    # RSI yükseliyor mu?
    if t.get("rsi_yon") == "↑":
        puan += 1
        notlar.append("RSI yükseliyor → dönüş sinyali (+1)")

    # ── Destek seviyesi ──────────────────────────────────────
    dipe = t.get("dipe_uzak", 10)
    if dipe < 2:
        puan += 3
        notlar.append(f"3 aylık dibe çok yakın ({dipe:.1f}%) → güçlü destek (+3)")
    elif dipe < 5:
        puan += 2
        notlar.append(f"Dibe yakın ({dipe:.1f}%) → destek bölgesi (+2)")
    elif dipe < 10:
        puan += 1
        notlar.append(f"Dipten {dipe:.1f}% uzak (+1)")
    else:
        notlar.append(f"Dipten {dipe:.1f}% uzak, destek yok (0)")

    # ── Hareketli ortalamalar ────────────────────────────────
    if not t.get("ma20_pos") and not t.get("ma50_pos"):
        puan -= 1
        notlar.append("MA20 ve MA50 altında → trend aşağı (-1)")
    elif not t.get("ma20_pos"):
        notlar.append("MA20 altında, MA50 üstünde → karışık (0)")
    else:
        puan += 1
        notlar.append("MA20 üstünde → kısa vade pozitif (+1)")

    # ── Hacim ────────────────────────────────────────────────
    hx = t.get("hacim_x", 1.0)
    if hx > 2.0:
        notlar.append(f"Hacim {hx:.1f}x — panik satışı veya güçlü ilgi (nötr)")
    elif hx > 1.3:
        puan += 1
        notlar.append(f"Hacim {hx:.1f}x — artan ilgi (+1)")

    # ── MACD ─────────────────────────────────────────────────
    if t.get("macd_yon") == "YUKARI":
        puan += 1
        notlar.append("MACD histogramı yukarı → momentum dönüşü (+1)")
    else:
        notlar.append("MACD histogramı aşağı → momentum zayıf (0)")

    # ── Jeopolitik bağlam ────────────────────────────────────
    # KRDMD/ISDMR: Demir-çelik, Brent bağlantısı dolaylı
    # GUBRF: Enerji fiyatı = gübre ham madde = olumlu
    brent_g = makro.get("brent_gunluk", 0)
    if "GUBRF" in ticker:
        if brent_g > 5:
            puan += 2
            notlar.append(f"Petrol +{brent_g:.1f}% → gübre ham madde değerlenir (+2)")
    elif "KRDMD" in ticker or "ISDMR" in ticker:
        if brent_g > 5:
            puan += 1
            notlar.append(f"Petrol +{brent_g:.1f}% → enerji fiyatı çelik maliyetini artırır ama talep belirsiz (+1)")

    # ── Son karar ────────────────────────────────────────────
    if puan >= 8:
        karar = "TUT / EKLE 🟢"
    elif puan >= 5:
        karar = "TUT 🟡"
    elif puan >= 3:
        karar = "BEKLE 🟠"
    else:
        karar = "STOP-LOSS DÜŞÜN 🔴"

    return {"karar": karar, "puan": puan, "notlar": notlar}


# ────────────────────────────────────────────────────────────
# AI YORUM
# ────────────────────────────────────────────────────────────

def ai_yorum(analizler: list, makro: dict) -> str:
    if not GROQ_API_KEY:
        return ""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
    except:
        return ""

    ozet_satirlar = []
    for a in analizler:
        t = a["teknik"]
        k = a["karar"]
        ozet_satirlar.append(
            f"{a['isim']} ({a['ticker']}):\n"
            f"  Bugun:{t.get('bugun','?'):.2f} | Gun:{t.get('gun_deg',0):+.1f}% | "
            f"Hfta:{t.get('hfta_deg',0):+.1f}% | 1Ay:{t.get('ay1_deg',0):+.1f}% | 3Ay:{t.get('ay3_deg',0):+.1f}%\n"
            f"  RSI:{t.get('rsi',0):.0f}{t.get('rsi_yon','?')} | "
            f"Dibe:{t.get('dipe_uzak',0):.1f}% uzak | MA20:{'UST' if t.get('ma20_pos') else 'ALTI'} | "
            f"MACD:{t.get('macd_yon','?')} | HacimX:{t.get('hacim_x',1):.1f}\n"
            f"  KARAR: {k['karar']} (puan:{k['puan']}/12)\n"
            f"  Notlar: {' | '.join(k['notlar'][:3])}"
        )

    prompt = f"""Dun KARDEMİR (KRDMD), İSDEMİR (ISDMR) ve GÜBRETAŞ (GUBRF) hisselerini aldım.
Bugun hala dusuyorlar. Elimde tutmalı mıyım?

MAKRO:
  Brent: {makro.get('brent',0):.1f}$ ({makro.get('brent_gunluk',0):+.1f}%)
  VIX: {makro.get('vix',0):.1f}
  USDTRY: {makro.get('usdtry',0):.2f} ({makro.get('usdtry_gunluk',0):+.1f}%)
  ABD-İran savas ortami var.

TEKNİK ANALİZ:
{chr(10).join(ozet_satirlar)}

Lutfen su sorulari Turkce, net ve kisa yanıtla (max 220 kelime):

1. GENEL: Bu 3 hisse neden dusuyor? Jeopolitik mi, teknik mi, her ikisi mi?
2. HER HİSSE İÇİN: TUT mu, ekle mi, stop-loss mu? Kisa sebep.
3. STOP-LOSS: Hangi fiyat kırılırsa kesinlikle çık?
4. BEKLENTI: Bu hisseler ne zaman toparlar? Katalizor ne olabilir?

Spekulatif degil, verilere dayali ol. Acik sozlu ol."""

    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"  AI hata: {e}")
        return ""


# ────────────────────────────────────────────────────────────
# MESAJ
# ────────────────────────────────────────────────────────────

def mesaj_olustur(analizler: list, makro: dict, ai: str) -> str:
    zaman = datetime.now().strftime("%d.%m.%Y %H:%M")
    s = []
    s.append(f"<b>🔍 POZİSYON ANALİZİ — {zaman}</b>")
    s.append(f"<b>KRDMD | ISDMR | GUBRF — Dün Alındı</b>")
    s.append(f"Brent:{makro.get('brent',0):.0f}$ {makro.get('brent_gunluk',0):+.1f}% | VIX:{makro.get('vix',0):.0f} | TRY:{makro.get('usdtry_gunluk',0):+.1f}%")

    for a in analizler:
        t = a["teknik"]
        k = a["karar"]
        if not t:
            continue

        s.append(f"\n<b>{'─'*35}</b>")
        s.append(f"<b>{a['isim']} ({a['ticker']})</b>  {k['karar']}")
        s.append(
            f"  Fiyat: {t.get('bugun',0):.2f} TL\n"
            f"  Gün:{t.get('gun_deg',0):+.1f}% | Hafta:{t.get('hfta_deg',0):+.1f}% | "
            f"1Ay:{t.get('ay1_deg',0):+.1f}% | 3Ay:{t.get('ay3_deg',0):+.1f}%"
        )
        s.append(
            f"  RSI:{t.get('rsi',0):.0f}{t.get('rsi_yon','?')} | "
            f"MA20:{'✅' if t.get('ma20_pos') else '❌'} | "
            f"MACD:{t.get('macd_yon','?')} | "
            f"HacX:{t.get('hacim_x',1):.1f}"
        )
        s.append(
            f"  3Ay Dip:{t.get('destek',0):.2f} ({t.get('dipe_uzak',0):.1f}% uzak) | "
            f"BB:%{t.get('bb_poz',50):.0f}"
        )
        s.append(f"  Puan: {k['puan']}/12")
        for n in k["notlar"]:
            s.append(f"    • {n}")

    if ai:
        s.append(f"\n<b>🤖 AI DEĞERLENDİRME</b>")
        s.append(ai)

    s.append(f"\n<i>⚠️ Yatırım tavsiyesi değildir.</i>")
    return "\n".join(s)


def telegram_gonder(mesaj):
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


# ────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────

def main():
    print("="*55)
    print(f"  POZİSYON ANALİZİ — KRDMD / ISDMR / GUBRF")
    print(f"  {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("="*55)

    makro = makro_cek()
    print(f"  Brent:{makro.get('brent',0):.1f}$ {makro.get('brent_gunluk',0):+.1f}% | VIX:{makro.get('vix',0):.0f} | USDTRY:{makro.get('usdtry',0):.2f}")

    analizler = []
    for ticker, poz in POZISYONLAR.items():
        print(f"\n  {poz['isim']} analiz ediliyor...")
        t = teknik_analiz(ticker)
        k = karar_ver(ticker, t, makro)
        analizler.append({
            "ticker":  ticker.replace(".IS",""),
            "isim":    poz["isim"],
            "teknik":  t,
            "karar":   k,
        })
        print(f"  → {k['karar']} (puan:{k['puan']}/12)")
        print(f"     RSI:{t.get('rsi',0):.0f} | Dibe:{t.get('dipe_uzak',0):.1f}% uzak | {t.get('gun_deg',0):+.1f}% bugün")

    print("\n  AI değerlendirme yapılıyor...")
    ai = ai_yorum(analizler, makro)

    mesaj = mesaj_olustur(analizler, makro, ai)
    print("\n" + "─"*55)
    print(mesaj.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>",""))

    ok = telegram_gonder(mesaj)
    print(f"\n  Telegram: {'✓ Gönderildi' if ok else '✗ Hata'}")
    print("="*55)


if __name__ == "__main__":
    main()
