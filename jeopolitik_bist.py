"""
BIST100 JEOPOLİTİK RİSK ANALİZİ — ABD-İRAN SAVAŞI
2 Mart 2026
"""
import os, warnings
from datetime import datetime
warnings.filterwarnings("ignore")
import yfinance as yf
import requests
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY","")

ETKI_HARITASI = {
    "ASELS.IS": ("POZİTİF", "savunma",    "Savunma sanayii — savaş ortamı sipariş beklentisi"),
    "TUPRS.IS": ("POZİTİF", "enerji",     "Rafineri — petrol yükselişi stok kari saglar"),
    "KRDMD.IS": ("POZİTİF", "metal",      "Demir-celik — enerji fiyati arbitraji"),
    "ISDMR.IS": ("POZİTİF", "metal",      "Demir-celik — benzer etki"),
    "GUBRF.IS": ("POZİTİF", "kimya",      "Gubre — enerji stok degeri artar"),
    "BIMAS.IS": ("NOTR",    "perakende",  "Defansif — zorunlu tuketim, kur maliyeti riski"),
    "MGROS.IS": ("NOTR",    "perakende",  "Defansif — ayni etki"),
    "ULKER.IS": ("NOTR",    "gida",       "Gida — zorunlu tuketim"),
    "TATGD.IS": ("NOTR",    "gida",       "Gida — defansif"),
    "GARAN.IS": ("NOTR",    "banka",      "Banka — kur volatilitesi riski ama guclu bilanco"),
    "AKBNK.IS": ("NOTR",    "banka",      "Banka — benzer etki"),
    "YKBNK.IS": ("NOTR",    "banka",      "Banka — benzer etki"),
    "ISCTR.IS": ("NOTR",    "banka",      "Banka — benzer etki"),
    "TCELL.IS": ("NOTR",    "telekom",    "Telekom — defansif, ic talep"),
    "TTKOM.IS": ("NOTR",    "telekom",    "Telekom — defansif"),
    "ENKAI.IS": ("NOTR",    "insaat",     "Insaat/Enerji — savas sonrasi yeniden yapim"),
    "SISE.IS":  ("NOTR",    "cam",        "Cam — defansif, ic talep"),
    "EKGYO.IS": ("NOTR",    "gyo",        "GYO — defansif, kur etkisi sinirli"),
    "THYAO.IS": ("NEGATİF", "havacilik",  "Orta Dogu rotaları iptal, yakit maliyeti artar"),
    "PGSUS.IS": ("NEGATİF", "havacilik",  "Pegasus — Orta Dogu agirlikli"),
    "TAVHL.IS": ("NEGATİF", "havalimanı", "Turizm/transit azalir, yolcu trafiği duşer"),
    "FROTO.IS": ("NEGATİF", "otomotiv",   "Ihracat guzergahi riski + enerji maliyeti"),
    "TOASO.IS": ("NEGATİF", "otomotiv",   "Ayni etki"),
    "ARCLK.IS": ("NEGATİF", "dayanikli",  "Ihracat + enerji maliyeti cift baski"),
    "VESTL.IS": ("NEGATİF", "dayanikli",  "Ayni etki"),
    "PETKM.IS": ("NEGATİF", "petrokimya", "Ham madde fiyati artar, marj sikisir"),
    "KCHOL.IS": ("NEGATİF", "holding",    "Havacilik/otomotiv portfoy agirligı"),
    "TKFEN.IS": ("NEGATİF", "insaat",     "Orta Dogu projeleri riskli"),
    "MAVI.IS":  ("NEGATİF", "tekstil",    "Ihracat + kur belirsizligi"),
    "TUPRS.IS": ("POZİTİF", "enerji",     "Rafineri — petrol yuks stok kari"),
    "GLYHO.IS": ("NOTR",    "holding",    "Holding — karma portfoy"),
    "EREGL.IS": ("NOTR",    "metal",      "Celik — enerji maliyeti artar ama talep sabit"),
    "ISMEN.IS": ("NOTR",    "finans",     "Finans — piyasa volatilitesi"),
    "LOGO.IS":  ("NOTR",    "teknoloji",  "Teknoloji — ic talep, kur avantaji"),
    "HALKB.IS": ("NOTR",    "banka",      "Kamu bankasi — devlet destegi"),
    "VAKBN.IS": ("NOTR",    "banka",      "Kamu bankasi — devlet destegi"),
}

TICKERS = list(dict.fromkeys(ETKI_HARITASI.keys()))


def makro_cek():
    m = {}
    for isim, ticker in [("brent","BZ=F"),("vix","^VIX"),("usdtry","USDTRY=X"),("dxy","DX-Y.NYB"),("altin","GC=F")]:
        try:
            df = yf.Ticker(ticker).history(period="3d", interval="1d", auto_adjust=True)
            if df is not None and len(df) >= 2:
                m[isim]           = float(df["Close"].iloc[-1])
                m[isim+"_gunluk"] = (float(df["Close"].iloc[-1])/float(df["Close"].iloc[-2])-1)*100
        except:
            pass
    return m


def hisse_verileri_cek():
    print("  Hisse verileri cekiliyor...")
    sonuclar = {}
    for ticker in TICKERS:
        try:
            df = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
            if df is None or len(df) < 2:
                continue
            bugun = float(df["Close"].iloc[-1])
            dun   = float(df["Close"].iloc[-2])
            hfta  = float(df["Close"].iloc[0])
            hacim = float(df["Volume"].iloc[-1])
            hort  = float(df["Volume"].mean())
            sonuclar[ticker] = {
                "fiyat":        bugun,
                "gunluk_deg":   (bugun/dun - 1)*100,
                "haftalik_deg": (bugun/hfta - 1)*100,
                "hacim_oran":   hacim/hort if hort > 0 else 1.0,
            }
        except:
            pass
    return sonuclar


def karar_ver(ticker, veri, makro):
    etki, sektor, sebep = ETKI_HARITASI.get(ticker, ("NOTR","diger","Sektor etkisi belirsiz"))
    gunluk  = veri.get("gunluk_deg", 0)
    hacim_x = veri.get("hacim_oran", 1.0)

    if etki == "POZİTİF":
        if gunluk < -2:
            karar, guc = "AL 🟢", 8
        elif gunluk <= 5:
            karar, guc = "AL 🟢", 6
        else:
            karar, guc = "BEKLE 🟡", 4   # Cok yükselmiş
    elif etki == "NOTR":
        if gunluk < -5:
            karar, guc = "DİKKATLİ AL 🟡", 6
        elif gunluk < -2:
            karar, guc = "DİKKATLİ AL 🟡", 5
        else:
            karar, guc = "BEKLE 🟡", 3
    else:  # NEGATİF
        if gunluk < -10:
            karar, guc = "BEKLE 🟠", 3   # Asiri dustü, ama trend kotü
        else:
            karar, guc = "KAÇIN 🔴", 1

    return {
        "ticker":   ticker.replace(".IS",""),
        "fiyat":    veri.get("fiyat"),
        "gunluk":   gunluk,
        "haftalik": veri.get("haftalik_deg"),
        "hacim_x":  hacim_x,
        "etki":     etki,
        "sektor":   sektor,
        "sebep":    sebep,
        "karar":    karar,
        "guc":      guc,
    }


def ai_yorum(kararlar, makro):
    if not GROQ_API_KEY:
        return ""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
    except:
        return ""

    al    = [k for k in kararlar if "AL" in k["karar"] and "KAÇIN" not in k["karar"]]
    kacin = [k for k in kararlar if "KAÇIN" in k["karar"]]

    ozet = f"""
MAKRO (2 Mart 2026 — ABD-IRAN SAVASI):
  Brent: {makro.get('brent',0):.1f}$ ({makro.get('brent_gunluk',0):+.1f}%)
  VIX: {makro.get('vix',0):.1f} ({makro.get('vix_gunluk',0):+.1f}%)
  USDTRY: {makro.get('usdtry',0):.2f} ({makro.get('usdtry_gunluk',0):+.1f}%)
  Altin: {makro.get('altin',0):.0f}$ ({makro.get('altin_gunluk',0):+.1f}%)

AL karari alanlar:
{chr(10).join(f"  {k['ticker']}: {k['gunluk']:+.1f}% HacimX:{k['hacim_x']:.1f} | {k['sebep']}" for k in al)}

KAÇIN karari alanlar:
{chr(10).join(f"  {k['ticker']}: {k['gunluk']:+.1f}% | {k['sebep']}" for k in kacin[:5])}
"""
    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content": f"""{ozet}

Sen deneyimli BIST analistisin. Verilere bakarak yaz (Turkce, max 180 kelime):

1. OZET KARAR: Bugun BIST genel strateji ne olmali? (2 cumle)
2. EN IYI 3 FIRSAT: Neden bu 3 hisse one cikiyor?
3. UYARI: En riskli pozisyonlar hangileri?
4. TAKTİK: Alim yapilacaksa hangi saatte, ne kadar pozisyon?

Spekulatif degil, veriye dayali ol."""}],
            max_tokens=500,
            temperature=0.3,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"  AI hata: {e}")
        return ""


def mesaj_olustur(kararlar, makro, ai):
    zaman = datetime.now().strftime("%d.%m.%Y %H:%M")
    s = []
    s.append(f"<b>🎯 BIST JEOPOLİTİK ANALİZ — {zaman}</b>")
    s.append(f"Brent:{makro.get('brent',0):.0f}$ {makro.get('brent_gunluk',0):+.1f}% | VIX:{makro.get('vix',0):.0f} | TRY:{makro.get('usdtry_gunluk',0):+.1f}%")

    al = [k for k in kararlar if "AL" in k["karar"] and "KAÇIN" not in k["karar"]]
    if al:
        s.append(f"\n<b>🟢 AL / DİKKATLİ AL</b>")
        for k in sorted(al, key=lambda x: x["guc"], reverse=True):
            s.append(f"  {k['karar']} <b>{k['ticker']}</b> {k['gunluk']:+.1f}% | HacX:{k['hacim_x']:.1f} | {k['sebep'][:42]}")

    bekle = [k for k in kararlar if k["karar"].startswith("BEKLE 🟡")]
    if bekle:
        s.append(f"\n<b>🟡 BEKLE</b>")
        for k in bekle[:5]:
            s.append(f"  {k['ticker']:<7} {k['gunluk']:+.1f}% | {k['sektor']}")

    kacin = [k for k in kararlar if "KAÇIN" in k["karar"]]
    if kacin:
        s.append(f"\n<b>🔴 KAÇIN</b>")
        for k in kacin:
            s.append(f"  {k['ticker']:<7} {k['gunluk']:+.1f}% | {k['sebep'][:42]}")

    if ai:
        s.append(f"\n<b>🤖 AI STRATEJİ</b>")
        s.append(ai)

    s.append(f"\n<i>⚠️ Yatirim tavsiyesi degildir.</i>")
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


def main():
    print("="*55)
    print(f"  BIST JEOPOLITIK ANALIZ — {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("="*55)

    makro   = makro_cek()
    veriler = hisse_verileri_cek()

    print(f"  Brent:{makro.get('brent',0):.1f}$ {makro.get('brent_gunluk',0):+.1f}% | VIX:{makro.get('vix',0):.0f} | {len(veriler)} hisse")

    kararlar = []
    for ticker, veri in veriler.items():
        kararlar.append(karar_ver(ticker, veri, makro))
    kararlar.sort(key=lambda x: x["guc"], reverse=True)

    print(f"\n  {'Hisse':<8} {'Gunluk':>8} {'HacimX':>7}  Karar")
    print("  "+"-"*52)
    for k in kararlar:
        print(f"  {k['ticker']:<8} {k['gunluk']:>+7.1f}%  {k['hacim_x']:>5.1f}x  {k['karar']}")

    print("\n  AI strateji hazirlanıyor...")
    ai = ai_yorum(kararlar, makro)

    mesaj = mesaj_olustur(kararlar, makro, ai)
    print("\n"+"-"*55)
    print(mesaj.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>",""))

    ok = telegram_gonder(mesaj)
    print(f"\n  Telegram: {'Gonderildi' if ok else 'Hata'}")
    print("="*55)


if __name__ == "__main__":
    main()
