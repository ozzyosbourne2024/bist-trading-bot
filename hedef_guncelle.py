"""
Portföydeki tüm hisseler için hedef ve stop-loss otomatik hesapla.
Yöntem: Fibonacci 1.272 uzantısı (hedef) + ATR x1.5 (stop)
"""
import json, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np
import yfinance as yf

PORTFOY_DOSYA = "portfoy_pozisyonlar.json"

def atr_hesapla(df, period=14):
    h = df["High"]; l = df["Low"]; c = df["Close"]
    tr = np.maximum(h - l, np.maximum(abs(h - c.shift(1)), abs(l - c.shift(1))))
    return float(tr.rolling(period).mean().iloc[-1])

def fibonacci_hedef(giris, destek, direnc):
    """Fib 1.272 uzantısı: giris + (direnc-destek)*1.272"""
    hedef = giris + (direnc - destek) * 1.272
    return round(hedef, 2)

def hedef_stop_hesapla(ticker, giris):
    t = ticker if ticker.endswith(".IS") else ticker + ".IS"
    try:
        df = yf.Ticker(t).history(period="3mo", interval="1d", auto_adjust=True)
        if df is None or len(df) < 20:
            return None, None

        son    = float(df["Close"].iloc[-1])
        destek = float(df["Low"].iloc[-44:].min()  if len(df) >= 44 else df["Low"].min())
        direnc = float(df["High"].iloc[-44:].max() if len(df) >= 44 else df["High"].max())
        atr    = atr_hesapla(df)

        # Hedef: Fibonacci 1.272 uzantısı — giriş fiyatından
        hedef = fibonacci_hedef(giris, destek, direnc)
        # Hedef en az %8, en fazla %40 yukarıda olsun
        hedef = max(round(giris * 1.08, 2), min(hedef, round(giris * 1.40, 2)))

        # Stop: ATR × 1.5
        stop = round(giris - 1.5 * atr, 2)
        # Stop en fazla -%12, en az -%6
        stop = max(round(giris * 0.88, 2), min(stop, round(giris * 0.94, 2)))

        return hedef, stop
    except Exception as e:
        print(f"  {ticker} hata: {e}")
        return None, None

def main():
    portfoy_yol = None
    for yol in [PORTFOY_DOSYA, "raporlar/" + PORTFOY_DOSYA]:
        if Path(yol).exists():
            portfoy_yol = yol
            break
    if not portfoy_yol:
        print("Portfoy dosyasi bulunamadi!")
        return

    kayit = json.loads(Path(portfoy_yol).read_text(encoding="utf-8"))
    poz   = kayit.get("pozisyonlar", {})

    print(f"{'Hisse':<8} {'Giris':>8} {'Yeni Hedef':>12} {'Yeni Stop':>10} {'Hedef%':>8} {'Stop%':>8}")
    print("-" * 60)

    for ticker, v in poz.items():
        giris = v.get("giris_fiyati") or v.get("guncel_fiyat")
        if not giris:
            continue

        hedef, stop = hedef_stop_hesapla(ticker, giris)
        if not hedef or not stop:
            print(f"  {ticker}: hesaplanamadi, eski deger korunuyor")
            continue

        hedef_pct = (hedef / giris - 1) * 100
        stop_pct  = (stop  / giris - 1) * 100

        print(f"{ticker:<8} {giris:>8.2f} {hedef:>12.2f} {stop:>10.2f} {hedef_pct:>+7.1f}% {stop_pct:>+7.1f}%")

        v["hedef"] = hedef
        v["stop"]  = stop

    kayit["pozisyonlar"] = poz
    # Yedek
    Path(portfoy_yol.replace(".json", "_yedek.json")).write_text(
        json.dumps(kayit, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(portfoy_yol).write_text(
        json.dumps(kayit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nKaydedildi: {portfoy_yol}")

if __name__ == "__main__":
    print("="*60)
    print("  HEDEF & STOP-LOSS OTOMATİK GÜNCELLE")
    print("  Yöntem: Fibonacci 1.272 + ATR x1.5")
    print("="*60)
    main()
    print("="*60)
