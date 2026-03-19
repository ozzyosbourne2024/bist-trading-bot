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
    # POZİTİF — Savaş/petrol/enerji lehine
    "ASELS.IS": ("POZİTİF", "savunma",    "Savunma sanayii — savaş ortamı sipariş beklentisi"),
    "ALTNY.IS": ("POZİTİF", "savunma",    "Savunma teknolojisi — savaş ortamında talep artar"),
    "TUPRS.IS": ("POZİTİF", "enerji",     "Rafineri — petrol yükselişi stok kârı sağlar"),
    "KRDMD.IS": ("POZİTİF", "metal",      "Demir-çelik — enerji fiyatı arbitrajı"),
    "EREGL.IS": ("POZİTİF", "metal",      "Demir-çelik — benzer etki"),
    "GUBRF.IS": ("POZİTİF", "kimya",      "Gübre — enerji stok değeri artar"),
    "ODAS.IS":  ("POZİTİF", "enerji",     "Enerji üretimi — elektrik fiyatları yükselir"),
    "AKSEN.IS": ("POZİTİF", "enerji",     "Enerji üretimi — benzer etki"),
    "ENJSA.IS": ("POZİTİF", "enerji",     "Enerji — elektrik fiyatı artışından faydalanır"),
    "ZOREN.IS": ("POZİTİF", "enerji",     "Zorlu Enerji — enerji fiyatı yükselişi"),
    "CWENE.IS": ("POZİTİF", "enerji",     "Yenilenebilir — enerji krizinde talep artar"),
    "MAGEN.IS": ("POZİTİF", "enerji",     "Enerji — jeopolitik lehine"),
    "TRALT.IS": ("POZİTİF", "maden",      "Altın madenciliği — altın fiyatı yükseldikçe kâr"),
    "ENERY.IS": ("POZİTİF", "enerji",     "Enerji üretimi — fiyat artışından faydalanır"),
    "ENKAI.IS": ("POZİTİF", "insaat",     "İnşaat/Enerji — savaş sonrası yeniden yapım"),
    "TKFEN.IS": ("POZİTİF", "insaat",     "Tekfen — enerji projeleri, savunma altyapısı"),

    # NOTR — Defansif / İç talep
    "BIMAS.IS": ("NOTR", "perakende",  "Defansif — zorunlu tüketim, kur maliyeti riski"),
    "MGROS.IS": ("NOTR", "perakende",  "Defansif — aynı etki"),
    "SOKM.IS":  ("NOTR", "perakende",  "Defansif — zorunlu tüketim"),
    "ULKER.IS": ("NOTR", "gida",       "Gıda — zorunlu tüketim"),
    "CCOLA.IS": ("NOTR", "gida",       "Gıda — defansif"),
    "AEFES.IS": ("NOTR", "gida",       "Gıda — defansif"),
    "TABGD.IS": ("NOTR", "gida",       "Gıda — defansif"),
    "TUKAS.IS": ("NOTR", "gida",       "Gıda — defansif"),
    "BALSU.IS": ("NOTR", "gida",       "Gıda — defansif"),
    "OBAMS.IS": ("NOTR", "gida",       "Gıda — defansif"),
    "GARAN.IS": ("NOTR", "banka",      "Banka — kur volatilitesi riski ama güçlü bilanço"),
    "AKBNK.IS": ("NOTR", "banka",      "Banka — benzer etki"),
    "YKBNK.IS": ("NOTR", "banka",      "Banka — benzer etki"),
    "ISCTR.IS": ("NOTR", "banka",      "Banka — benzer etki"),
    "HALKB.IS": ("NOTR", "banka",      "Kamu bankası — devlet desteği"),
    "VAKBN.IS": ("NOTR", "banka",      "Kamu bankası — devlet desteği"),
    "SKBNK.IS": ("NOTR", "banka",      "Banka — defansif"),
    "TSKB.IS":  ("NOTR", "banka",      "Kalkınma bankası — defansif"),
    "TCELL.IS": ("NOTR", "telekom",    "Telekom — defansif, iç talep"),
    "TTKOM.IS": ("NOTR", "telekom",    "Telekom — defansif"),
    "SISE.IS":  ("NOTR", "cam",        "Cam — defansif, iç talep"),
    "EKGYO.IS": ("NOTR", "gyo",        "GYO — defansif, kur etkisi sınırlı"),
    "DOHOL.IS": ("NOTR", "holding",    "Holding — karma portföy"),
    "KCHOL.IS": ("NOTR", "holding",    "Koç Holding — otomotiv/enerji dengeli"),
    "SAHOL.IS": ("NOTR", "holding",    "Sabancı — karma portföy"),
    "AGHOL.IS": ("NOTR", "holding",    "AG Holding — defansif"),
    "ISMEN.IS": ("NOTR", "finans",     "Finans — piyasa volatilitesi"),
    "DSTKF.IS": ("NOTR", "finans",     "Faktoring — defansif"),
    "ANSGR.IS": ("NOTR", "sigorta",    "Sigorta — jeopolitik ortamda talep artar"),
    "TURSG.IS": ("NOTR", "sigorta",    "Sigorta — defansif"),
    "MPARK.IS": ("NOTR", "saglik",     "Sağlık — defansif, iç talep"),
    "ECILC.IS": ("NOTR", "saglik",     "İlaç/sağlık — defansif"),
    "CIMSA.IS": ("NOTR", "insaat",     "Çimento — iç talep"),
    "OYAKC.IS": ("NOTR", "insaat",     "Oyak Çimento — iç talep"),
    "BTCIM.IS": ("NOTR", "insaat",     "Çimento — iç talep"),
    "BSOKE.IS": ("NOTR", "insaat",     "Çimento — iç talep"),
    "GLRMK.IS": ("NOTR", "insaat",     "Gülermak — altyapı projeleri"),
    "MAVI.IS":  ("NOTR", "tekstil",    "Mavi — iç talep, ihracat riski var"),
    "REEDR.IS": ("NOTR", "teknoloji",  "Reeder — teknoloji, iç talep"),
    "MIATK.IS": ("NOTR", "teknoloji",  "Mia Teknoloji — iç talep"),
    "PATEK.IS": ("NOTR", "teknoloji",  "Pasifik Teknoloji — iç talep"),
    "BRSAN.IS": ("NOTR", "metal",      "Borusan — çelik, iç ve dış piyasa"),
    "GESAN.IS": ("NOTR", "enerji",     "Girisim Elektrik — iç piyasa"),
    "KONTR.IS": ("NOTR", "teknoloji",  "Kontrolmatik — iç talep"),
    "GRTHO.IS": ("NOTR", "tarim",      "Grainturk — gıda/tarım defansif"),
    "IZENR.IS": ("NOTR", "enerji",     "İzdemir Enerji — iç piyasa"),
    "TRENJ.IS": ("NOTR", "enerji",     "TR Doğal Enerji — iç piyasa"),
    "EUPWR.IS": ("NOTR", "enerji",     "Europower — iç piyasa"),
    "CANTE.IS": ("NOTR", "enerji",     "Çan2 Termik — iç piyasa"),
    "TSPOR.IS": ("NOTR", "spor",       "Trabzonspor — iç talep"),
    "GSRAY.IS": ("NOTR", "spor",       "Galatasaray — iç talep"),
    "FENER.IS": ("NOTR", "spor",       "Fenerbahçe — iç talep"),
    "EFOR.IS":  ("NOTR", "diger",      "Efor — iç talep"),
    "KLRHO.IS": ("NOTR", "holding",    "Kiler Holding — iç talep"),
    "DAPGM.IS": ("NOTR", "gyo",        "DAP GYO — iç talep"),
    "KUYAS.IS": ("NOTR", "holding",    "Kuyaş — iç talep"),
    "KTLEV.IS": ("NOTR", "finans",     "Katılımevim — iç talep"),
    "RALYH.IS": ("NOTR", "holding",    "Ral Yatırım — iç talep"),
    "BRYAT.IS": ("NOTR", "holding",    "Borusan Yatırım — iç talep"),
    "PASEU.IS": ("NOTR", "lojistik",   "Pasifik Eurasia — lojistik, kur etkisi"),
    "TUREX.IS": ("NOTR", "lojistik",   "Tureks Turizm — iç talep"),
    "GRSEL.IS": ("NOTR", "lojistik",   "Gür-Sel — lojistik"),
    "QUAGR.IS": ("NOTR", "insaat",     "Qua Granite — iç talep"),
    "YEOTK.IS": ("NOTR", "enerji",     "Yeo Teknoloji — iç talep"),
    "GENIL.IS": ("NOTR", "saglik",     "Gen İlaç — defansif"),
    "ASTOR.IS": ("NOTR", "enerji",     "Astor Enerji — iç talep"),
    "KCAER.IS": ("NOTR", "metal",      "Kocaer Çelik — iç talep"),
    "TRMET.IS": ("NOTR", "metal",      "TR Metal — iç talep"),

    # NEGATİF — Savaş/jeopolitik aleyhine
    "THYAO.IS": ("NEGATİF", "havacilik",  "Orta Doğu rotaları iptal, yakıt maliyeti artar"),
    "PGSUS.IS": ("NEGATİF", "havacilik",  "Pegasus — Orta Doğu ağırlıklı"),
    "TAVHL.IS": ("NEGATİF", "havalimanı", "Turizm/transit azalır, yolcu trafiği düşer"),
    "FROTO.IS": ("NEGATİF", "otomotiv",   "İhracat güzergahı riski + enerji maliyeti"),
    "TOASO.IS": ("NEGATİF", "otomotiv",   "Tofaş — aynı etki"),
    "TTRAK.IS": ("NEGATİF", "otomotiv",   "Türk Traktör — ihracat riski"),
    "DOAS.IS":  ("NEGATİF", "otomotiv",   "Doğuş Otomotiv — ihracat/ithalat riski"),
    "ARCLK.IS": ("NEGATİF", "dayanikli",  "İhracat + enerji maliyeti çift baskı"),
    "VESTL.IS": ("NEGATİF", "dayanikli",  "Aynı etki"),
    "OTKAR.IS": ("NEGATİF", "otomotiv",   "Otokar — ihracat riski"),
    "PETKM.IS": ("NEGATİF", "petrokimya", "Ham madde fiyatı artar, marj sıkışır"),
    "SASA.IS":  ("NEGATİF", "petrokimya", "Sasa Polyester — hammadde maliyeti"),
    "AKSA.IS":  ("NEGATİF", "kimya",      "Aksa Akrilik — hammadde maliyeti artar"),
}

# Resmi BIST100 listesindeki eksik tickerları NOTR olarak ekle
BIST100_RESMI = [
    "HEKTS.IS","GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","HALKB.IS","VAKBN.IS","SKBNK.IS","TSKB.IS",
    "KCHOL.IS","SAHOL.IS","AGHOL.IS","ALARK.IS","BRYAT.IS","DOHOL.IS","KLRHO.IS","KUYAS.IS","RALYH.IS",
]
for t in BIST100_RESMI:
    if t not in ETKI_HARITASI:
        ETKI_HARITASI[t] = ("NOTR", "diger", "Sektör etkisi değerlendiriliyor")

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
