import json, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import yfinance as yf

PORTFOY_DOSYA = "portfoy_pozisyonlar.json"
GIRIS_TARIHI  = "2026-03-01"

YENI_HISSELER = {
    "KRDMD": {"ticker": "KRDMD.IS", "agirlik_pct": 5, "hedef_carpan": 1.15, "stop_carpan": 0.91},
    "ISDMR": {"ticker": "ISDMR.IS", "agirlik_pct": 5, "hedef_carpan": 1.15, "stop_carpan": 0.91},
    "GUBRF": {"ticker": "GUBRF.IS", "agirlik_pct": 5, "hedef_carpan": 1.15, "stop_carpan": 0.91},
}

def giris_fiyati_cek(ticker):
    try:
        df = yf.Ticker(ticker).history(period="3d", interval="5m", auto_adjust=True)
        if df is None or df.empty:
            return None
        # Sadece dun
        dun = df[df.index.strftime("%Y-%m-%d") == GIRIS_TARIHI]
        if dun.empty:
            print(f"  {ticker}: 5m veri yok, gunluk onceki kapanis")
            gun = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
            return float(gun["Close"].iloc[-2]) if gun is not None and len(gun) >= 2 else None
        # 10:45 TR = 07:45 UTC
        hedef = dun.between_time("07:40", "07:55")
        if not hedef.empty:
            f = float(hedef["Close"].iloc[0])
            print(f"  {ticker}: {f:.2f} TL  (10:45 TR)")
            return f
        # Biraz genis
        yakin = dun.between_time("07:00", "08:30")
        if not yakin.empty:
            f = float(yakin["Close"].iloc[0])
            print(f"  {ticker}: {f:.2f} TL  (en yakin bar)")
            return f
        f = float(dun["Open"].iloc[0])
        print(f"  {ticker}: {f:.2f} TL  (gun acilisi)")
        return f
    except Exception as e:
        print(f"  {ticker} hata: {e}")
        return None

def portfoy_ekle():
    # Portfoyu bul
    portfoy_yol = None
    for yol in [PORTFOY_DOSYA, "raporlar/" + PORTFOY_DOSYA]:
        if Path(yol).exists():
            portfoy_yol = yol
            break
    if portfoy_yol:
        kayit = json.loads(Path(portfoy_yol).read_text(encoding="utf-8"))
        print(f"Portfoy: {portfoy_yol}")
        print(f"Mevcut : {list(kayit.get('pozisyonlar',{}).keys())}")
    else:
        kayit = {"tarih": GIRIS_TARIHI, "pozisyonlar": {}}
        portfoy_yol = PORTFOY_DOSYA
        print("Yeni portfoy olusturuluyor")

    poz = kayit.get("pozisyonlar", {})

    print("\nFiyatlar cekiliyor...")
    for kisa, bilgi in YENI_HISSELER.items():
        if kisa in poz:
            print(f"  {kisa}: zaten var, atlanıyor")
            continue
        fiyat = giris_fiyati_cek(bilgi["ticker"])
        if not fiyat:
            print(f"  {kisa}: fiyat alinamadi!")
            continue
        poz[kisa] = {
            "agirlik_pct":  bilgi["agirlik_pct"],
            "giris_fiyati": round(fiyat, 2),
            "guncel_fiyat": round(fiyat, 2),
            "hedef":        round(fiyat * bilgi["hedef_carpan"], 2),
            "stop":         round(fiyat * bilgi["stop_carpan"],  2),
            "tarih":        GIRIS_TARIHI,
        }
        print(f"  + {kisa}: giris:{fiyat:.2f}  hedef:{poz[kisa]['hedef']:.2f}  stop:{poz[kisa]['stop']:.2f}")

    kayit["pozisyonlar"] = poz
    # Yedek
    Path(portfoy_yol.replace(".json","_yedek.json")).write_text(
        json.dumps(kayit, ensure_ascii=False, indent=2), encoding="utf-8")
    # Kaydet
    Path(portfoy_yol).write_text(
        json.dumps(kayit, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nKaydedildi: {portfoy_yol}")
    print(f"Toplam {len(poz)} hisse: {list(poz.keys())}")

print("="*50)
print("  PORTFOY EKLE: KRDMD / ISDMR / GUBRF")
print("="*50)
portfoy_ekle()
print("="*50)
