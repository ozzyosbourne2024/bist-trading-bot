#!/usr/bin/env python3
"""
BIST PIYASA SAĞLIĞI ANALİZÖRÜ v1.0
=====================================
Her sabah çalıştırılır. 4 katmanlı analiz:

  1. Endeks Genişlik Analizi   — kaç hisse yükseliyor vs düşüyor
  2. Yabancı Net Alım/Satım    — MKK haftalık verisi
  3. Makro Risk (CDS + USDTRY) — Türkiye risk barometresi
  4. Piyasa Rejimi Tespiti     — Trend / Düzeltme / Balon → 0-100 risk skoru

Kullanım:
  python bist_piyasa_sagligi.py            # Tam analiz
  python bist_piyasa_sagligi.py --ozet     # Sadece özet skor
"""

import os, sys, json, argparse, warnings
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import requests
    from bs4 import BeautifulSoup
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
    from dotenv import load_dotenv
    from groq import Groq
    load_dotenv()
except ImportError as e:
    print(f"Eksik kütüphane: {e}")
    print("pip install yfinance pandas numpy requests beautifulsoup4 rich python-dotenv groq")
    sys.exit(1)

console = Console()

# ── Sabitler ────────────────────────────────────────────────────────────────
GROQ_MODEL = "llama-3.3-70b-versatile"
HEADERS    = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

BIST100_TICKERS = [
    "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","HALKB.IS","VAKBN.IS",
    "KCHOL.IS","SAHOL.IS","AGHOL.IS","DOHOL.IS","GLYHO.IS",
    "FROTO.IS","TOASO.IS","EREGL.IS","ARCLK.IS","VESTL.IS","OTKAR.IS",
    "ASELS.IS","LOGO.IS","NETAS.IS","KAREL.IS","INDES.IS",
    "TUPRS.IS","PETKM.IS","GUBRF.IS","ODAS.IS",
    "THYAO.IS","PGSUS.IS","TAVHL.IS",
    "TCELL.IS","TTKOM.IS",
    "BIMAS.IS","MGROS.IS","SOKM.IS","ULKER.IS","CCOLA.IS","AEFES.IS",
    "MAVI.IS","MERKO.IS","BANVT.IS","PENGD.IS",
    "EKGYO.IS","ISGYO.IS","KLGYO.IS","ENKAI.IS","TKFEN.IS",
    "AKCNS.IS","CIMSA.IS","SISE.IS",
    "ISDMR.IS","KRDMD.IS","KRSTL.IS",
    "ISFIN.IS","ISMEN.IS","ALARK.IS",
    "BRISA.IS","KORDS.IS","DOAS.IS","JANTS.IS",
    "GESAN.IS","SELEC.IS","TATGD.IS",
]

# Endeks ağırlıklı hisseler (bunlar yükselince endeks yükselir ama genişlik yanıltıcı olur)
AGIR_HISSELER = {
    "GARAN.IS","YKBNK.IS","AKBNK.IS","ISCTR.IS",
    "EREGL.IS","THYAO.IS","KCHOL.IS","SISE.IS","TCELL.IS","ENKAI.IS"
}

# ════════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════════

def baslik(metin: str, renk: str = "cyan"):
    console.print()
    console.rule(f"[bold {renk}]{metin}[/bold {renk}]")

def _rsi(s: pd.Series, p: int = 14) -> float:
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    k = (-d.clip(upper=0)).rolling(p).mean()
    rs = g / k.replace(0, np.nan)
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

def _yfinance_cek(ticker: str, period: str = "3mo") -> Optional[pd.DataFrame]:
    try:
        t = yf.Ticker(ticker)
        h = t.history(period=period, auto_adjust=True)
        return h if not h.empty else None
    except:
        return None

def _llm_yorum(prompt: str) -> str:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return "⚠️  GROQ_API_KEY yok — LLM yorumu atlandı."
    try:
        client = Groq(api_key=key)
        r = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content":
                 "Sen deneyimli bir Türkiye piyasa stratejistisin. "
                 "Verilen verileri analiz et. Kısa, net, Türkçe yaz. "
                 "Spekülatif konuşma, veri odaklı ol."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800, temperature=0.2,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"LLM hatası: {e}"


# ════════════════════════════════════════════════════════════════════════════
# MODÜL 1: ENDEKS GENİŞLİK ANALİZİ
# ════════════════════════════════════════════════════════════════════════════

def genisl_analizi() -> dict:
    baslik("📊 MODÜL 1 — Endeks Genişlik Analizi")

    rprint("  [dim]BIST hisseleri taranıyor...[/dim]")

    yukselenler = []
    dusenler    = []
    yataylar    = []
    hacim_agir_yukseliyor = 0  # Ağır hisseler yükseliyor mu?
    hacim_agir_dusuyor    = 0
    rsi_asiri_yuksel = []      # RSI > 70
    rsi_asiri_dusuk  = []      # RSI < 30
    ust_20_gun = []            # 20 günlük yüksekte
    alt_20_gun = []            # 20 günlük düşükte

    toplam = 0
    hata   = 0

    for ticker in BIST100_TICKERS:
        h = _yfinance_cek(ticker, "2mo")
        if h is None or len(h) < 5:
            hata += 1
            continue
        toplam += 1

        bugunki  = float(h["Close"].iloc[-1])
        dunku    = float(h["Close"].iloc[-2])
        degisim  = (bugunki - dunku) / dunku * 100

        t_kisa = ticker.replace(".IS","")

        if degisim > 0.5:
            yukselenler.append((t_kisa, round(degisim, 2)))
            if ticker in AGIR_HISSELER:
                hacim_agir_yukseliyor += 1
        elif degisim < -0.5:
            dusenler.append((t_kisa, round(degisim, 2)))
            if ticker in AGIR_HISSELER:
                hacim_agir_dusuyor += 1
        else:
            yataylar.append(t_kisa)

        # RSI
        if len(h) >= 15:
            try:
                rsi = _rsi(h["Close"])
                if rsi > 70: rsi_asiri_yuksel.append((t_kisa, rsi))
                if rsi < 30: rsi_asiri_dusuk.append((t_kisa, rsi))
            except: pass

        # 20 günlük yüksek/düşük
        if len(h) >= 20:
            max20 = float(h["High"].iloc[-20:].max())
            min20 = float(h["Low"].iloc[-20:].min())
            if bugunki >= max20 * 0.995: ust_20_gun.append(t_kisa)
            if bugunki <= min20 * 1.005: alt_20_gun.append(t_kisa)

    # Advance-Decline çizgisi
    adl_oran = len(yukselenler) / max(len(dusenler), 1)
    breadth_skoru = round(len(yukselenler) / max(toplam, 1) * 100, 1)

    # Ağır hisse manipülasyon testi
    # Eğer ağır hisseler yükseliyor ama geniş piyasa değilse → uyarı
    uyari_indikasyon = (
        hacim_agir_yukseliyor >= 5 and
        breadth_skoru < 45
    )

    # Tablo
    t = Table(title="📈 Piyasa Genişlik Özeti", border_style="blue", show_lines=True)
    t.add_column("Metrik"); t.add_column("Değer", justify="right"); t.add_column("Yorum")

    def renk_breadth(b):
        if b > 60: return f"[green]{b}%[/green]"
        if b < 40: return f"[red]{b}%[/red]"
        return f"[yellow]{b}%[/yellow]"

    t.add_row("Taranan hisse",        str(toplam), "")
    t.add_row("Yükselenler",          f"[green]{len(yukselenler)}[/green]",
              ", ".join([x[0] for x in yukselenler[:8]]))
    t.add_row("Düşenler",             f"[red]{len(dusenler)}[/red]",
              ", ".join([x[0] for x in dusenler[:8]]))
    t.add_row("Yataylar",             str(len(yataylar)), "")
    t.add_row("Genişlik skoru",       renk_breadth(breadth_skoru),
              "Yükselen/Toplam %")
    t.add_row("A/D oranı",            f"{adl_oran:.2f}",
              ">1.5 güçlü, <0.7 zayıf")
    t.add_row("RSI>70 (aşırı alım)",  f"[yellow]{len(rsi_asiri_yuksel)}[/yellow]",
              ", ".join([x[0] for x in rsi_asiri_yuksel[:6]]))
    t.add_row("RSI<30 (aşırı satım)", f"[cyan]{len(rsi_asiri_dusuk)}[/cyan]",
              ", ".join([x[0] for x in rsi_asiri_dusuk[:6]]))
    t.add_row("20 gün zirvesinde",    str(len(ust_20_gun)),
              ", ".join(ust_20_gun[:6]))
    t.add_row("20 gün dibinde",       str(len(alt_20_gun)),
              ", ".join(alt_20_gun[:6]))
    t.add_row("Ağır hisse yükseliyor", str(hacim_agir_yukseliyor),
              f"Düşen: {hacim_agir_dusuyor}")
    console.print(t)

    if uyari_indikasyon:
        rprint("\n  [red bold]⚠️  UYARI: Endeks az sayıda ağır hisse tarafından taşınıyor![/red bold]")
        rprint(f"  [red]Ağır hisse yükselen: {hacim_agir_yukseliyor} | Genel genişlik: %{breadth_skoru}[/red]")
    else:
        rprint(f"\n  [green]✓ Yükseliş geniş tabanlı görünüyor. Genişlik: %{breadth_skoru}[/green]")

    # Düzeltme riski katkısı (0-40 puan)
    risk_katki = 0
    if breadth_skoru < 40: risk_katki += 20
    elif breadth_skoru < 50: risk_katki += 10
    if uyari_indikasyon: risk_katki += 15
    if len(rsi_asiri_yuksel) > toplam * 0.25: risk_katki += 5  # %25+ hisse aşırı alımda

    return {
        "yukselenler": len(yukselenler),
        "dusenler": len(dusenler),
        "breadth_skoru": breadth_skoru,
        "adl_oran": round(adl_oran, 2),
        "rsi_asiri_yuksel_sayi": len(rsi_asiri_yuksel),
        "rsi_asiri_dusuk_sayi": len(rsi_asiri_dusuk),
        "ust_20_gun_sayi": len(ust_20_gun),
        "agir_hisse_tasiyor": uyari_indikasyon,
        "risk_katki": min(risk_katki, 40),
    }


# ════════════════════════════════════════════════════════════════════════════
# MODÜL 2: YABANCI NET ALIM/SATIM (MKK)
# ════════════════════════════════════════════════════════════════════════════

def yabanci_analizi() -> dict:
    baslik("🌍 MODÜL 2 — Yabancı Net Alım/Satım (MKK)")

    sonuc = {
        "veri_var": False,
        "net_pozisyon": None,
        "trend": "BİLİNMİYOR",
        "risk_katki": 0,
        "kaynak": "",
    }

    # MKK verisi — Borsa İstanbul yabancı yatırımcı sayfası
    mkk_urls = [
        "https://borsaistanbul.com/tr/sayfa/1151/yabancilar",
        "https://www.mkk.com.tr/istatistikler",
    ]

    # Alternatif: BIST istatistik sayfası
    try:
        url = "https://borsaistanbul.com/tr/sayfa/1151/yabancilar"
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        # Tablo bul
        tablolar = soup.find_all("table")
        if tablolar:
            # İlk sayısal veriyi parse et
            for tablo in tablolar[:3]:
                satirlar = tablo.find_all("tr")
                for satir in satirlar[1:5]:
                    hucreler = satir.find_all(["td","th"])
                    if len(hucreler) >= 3:
                        metin = " | ".join(h.get_text(strip=True) for h in hucreler)
                        rprint(f"  [dim]MKK veri: {metin[:80]}[/dim]")
                        sonuc["veri_var"] = True
                        sonuc["kaynak"] = "Borsa İstanbul"
                        break
    except Exception as e:
        rprint(f"  [yellow]MKK direkt erişim başarısız: {e}[/yellow]")

    # Alternatif: EPIAS veya yfinance ile proxy
    # USD/TRY ve BIST yabancı akış proxy'si:
    # Yabancı çıkışı → USDTRY yükselir + BIST düşer
    try:
        usdtry = _yfinance_cek("USDTRY=X", "1mo")
        xu100  = _yfinance_cek("XU100.IS", "1mo")

        if usdtry is not None and xu100 is not None and len(usdtry) >= 10:
            # Son 10 gün: kur artışı + BIST düşüşü = yabancı satışı sinyali
            usd_degisim  = (float(usdtry["Close"].iloc[-1]) / float(usdtry["Close"].iloc[-10]) - 1) * 100
            bist_degisim = (float(xu100["Close"].iloc[-1])  / float(xu100["Close"].iloc[-10])  - 1) * 100

            # Korelasyon hesapla (negatif olmalı normalde)
            ortak = pd.concat([usdtry["Close"].rename("usd"),
                               xu100["Close"].rename("bist")], axis=1).dropna()
            kor = round(float(ortak.corr().iloc[0,1]), 3) if len(ortak) > 5 else None

            rprint(f"\n  [bold]Proxy Göstergeler (Son 10 Gün):[/bold]")
            rprint(f"  USDTRY değişim  : {'[red]+' if usd_degisim > 0 else '[green]'}{usd_degisim:.2f}%[/{'red' if usd_degisim > 0 else 'green'}]")
            rprint(f"  BIST100 değişim : {'[green]+' if bist_degisim > 0 else '[red]'}{bist_degisim:.2f}%[/{'green' if bist_degisim > 0 else 'red'}]")
            if kor:
                rprint(f"  USDTRY↔BIST kor : {kor} ({'[red]Pozitif korelasyon — yabancı kaçıyor!' if kor > 0.3 else '[green]Normal negatif korelasyon[/green]' if kor < -0.1 else '[yellow]Nötr[/yellow]'})")

            sonuc["veri_var"] = True
            sonuc["usd_degisim_10g"] = round(usd_degisim, 2)
            sonuc["bist_degisim_10g"] = round(bist_degisim, 2)
            sonuc["usdtry_bist_kor"] = kor
            sonuc["kaynak"] = "yfinance proxy (USDTRY + XU100)"

            # Risk katkısı
            if usd_degisim > 3 and bist_degisim < 0:
                sonuc["trend"] = "YABANCI SATIŞI"
                sonuc["risk_katki"] = 20
            elif usd_degisim > 1.5:
                sonuc["trend"] = "TEMKINLI"
                sonuc["risk_katki"] = 10
            elif usd_degisim < -1 and bist_degisim > 0:
                sonuc["trend"] = "YABANCI ALIMI"
                sonuc["risk_katki"] = 0
            else:
                sonuc["trend"] = "NÖTR"
                sonuc["risk_katki"] = 5

    except Exception as e:
        rprint(f"  [yellow]Proxy analiz hatası: {e}[/yellow]")

    # Yabancı oranı — BIST resmi
    try:
        # Son hafta verisi için alternatif kaynak
        url2 = "https://www.isyatirim.com.tr/analiz-ve-raporlar/veriler/hisse/yabanci-yatirimci"
        r2 = requests.get(url2, headers=HEADERS, timeout=8)
        if r2.status_code == 200 and "yabancı" in r2.text.lower():
            rprint("  [green]✓ İş Yatırım yabancı veri sayfasına erişildi[/green]")
    except:
        pass

    rprint(f"\n  [bold]Sonuç:[/bold] {sonuc['trend']} | Risk katkısı: +{sonuc['risk_katki']} puan")
    return sonuc


# ════════════════════════════════════════════════════════════════════════════
# MODÜL 3: MAKRO RİSK — CDS + USDTRY + ALTIN
# ════════════════════════════════════════════════════════════════════════════

def makro_risk_analizi() -> dict:
    baslik("📉 MODÜL 3 — Makro Risk (CDS + USDTRY + Altın)")

    sonuc = {"risk_katki": 0, "gorunum": "NÖTR"}

    risk_puan = 0
    bulgular  = []

    # ── USDTRY Trendi ─────────────────────────────────────────────────────
    try:
        usdtry = _yfinance_cek("USDTRY=X", "3mo")
        if usdtry is not None and len(usdtry) >= 20:
            son   = float(usdtry["Close"].iloc[-1])
            ort20 = float(usdtry["Close"].iloc[-20:].mean())
            ort60 = float(usdtry["Close"].iloc[-60:].mean()) if len(usdtry) >= 60 else ort20
            aylık_degisim = (son / float(usdtry["Close"].iloc[-20]) - 1) * 100

            usdtry_rsi = _rsi(usdtry["Close"]) if len(usdtry) >= 15 else None

            rprint(f"  [bold]USDTRY:[/bold]")
            rprint(f"    Güncel: {son:.2f} | 20G ort: {ort20:.2f} | Aylık değişim: {aylık_degisim:+.2f}%")
            if usdtry_rsi:
                rprint(f"    RSI(14): {usdtry_rsi}")

            if aylık_degisim > 5:
                risk_puan += 15
                bulgular.append(f"USDTRY +{aylık_degisim:.1f}% son ay — kur baskısı YÜKSEKEEEEee")
            elif aylık_degisim > 2:
                risk_puan += 8
                bulgular.append(f"USDTRY +{aylık_degisim:.1f}% son ay — hafif kur baskısı")
            elif aylık_degisim < -2:
                bulgular.append(f"USDTRY {aylık_degisim:.1f}% — kur olumlu seyirde")

            sonuc["usdtry"] = son
            sonuc["usdtry_aylik_pct"] = round(aylık_degisim, 2)
    except Exception as e:
        rprint(f"  [yellow]USDTRY hatası: {e}[/yellow]")

    # ── Türkiye CDS (proxy: EURUSD + Tahvil spread) ───────────────────────
    # CDS doğrudan yfinance'da yok. Proxy: TUR ETF veya tahvil
    try:
        # TUR = iShares MSCI Turkey ETF — CDS proxy
        tur_etf = _yfinance_cek("TUR", "3mo")
        if tur_etf is not None and len(tur_etf) >= 20:
            tur_son    = float(tur_etf["Close"].iloc[-1])
            tur_onceki = float(tur_etf["Close"].iloc[-20])
            tur_degisim = (tur_son / tur_onceki - 1) * 100

            rprint(f"\n  [bold]TUR ETF (CDS Proxy):[/bold]")
            rprint(f"    Güncel: ${tur_son:.2f} | 20G değişim: {tur_degisim:+.2f}%")

            if tur_degisim < -10:
                risk_puan += 15
                bulgular.append(f"TUR ETF {tur_degisim:.1f}% — yabancı Türkiye'den kaçıyor")
            elif tur_degisim < -5:
                risk_puan += 8
                bulgular.append(f"TUR ETF {tur_degisim:.1f}% — yabancı temkinli")
            elif tur_degisim > 5:
                bulgular.append(f"TUR ETF +{tur_degisim:.1f}% — yabancı ilgisi arttı")

            sonuc["tur_etf_degisim"] = round(tur_degisim, 2)
    except Exception as e:
        rprint(f"  [yellow]TUR ETF hatası: {e}[/yellow]")

    # ── Altın (güvenli liman talebi) ──────────────────────────────────────
    try:
        altin = _yfinance_cek("GC=F", "1mo")
        if altin is not None and len(altin) >= 10:
            altin_degisim = (float(altin["Close"].iloc[-1]) / float(altin["Close"].iloc[-10]) - 1) * 100
            rprint(f"\n  [bold]Altın (GC=F):[/bold]")
            rprint(f"    10 günlük değişim: {altin_degisim:+.2f}%")

            if altin_degisim > 3:
                risk_puan += 5
                bulgular.append(f"Altın +{altin_degisim:.1f}% — güvenli liman talebi arttı")
            sonuc["altin_degisim"] = round(altin_degisim, 2)
    except Exception as e:
        rprint(f"  [yellow]Altın hatası: {e}[/yellow]")

    # ── VIX (Küresel korku endeksi) ───────────────────────────────────────
    try:
        vix = _yfinance_cek("^VIX", "1mo")
        if vix is not None and len(vix) >= 5:
            vix_son = float(vix["Close"].iloc[-1])
            rprint(f"\n  [bold]VIX (Küresel Korku):[/bold]")
            rprint(f"    Güncel: {vix_son:.1f} "
                   f"({'[red]YÜKSEK RİSK' if vix_son > 25 else '[yellow]ORTA' if vix_son > 18 else '[green]DÜŞÜK'}[/{'red' if vix_son > 25 else 'yellow' if vix_son > 18 else 'green'}])")

            if vix_son > 25:
                risk_puan += 10
                bulgular.append(f"VIX={vix_son:.1f} — küresel panik seviyesi")
            elif vix_son > 20:
                risk_puan += 5
                bulgular.append(f"VIX={vix_son:.1f} — küresel endişe")
            sonuc["vix"] = round(vix_son, 1)
    except Exception as e:
        rprint(f"  [yellow]VIX hatası: {e}[/yellow]")

    # ── Özet ──────────────────────────────────────────────────────────────
    console.print()
    if bulgular:
        rprint("  [bold]Makro Bulgular:[/bold]")
        for b in bulgular:
            rprint(f"    • {b}")

    sonuc["risk_katki"] = min(risk_puan, 30)
    sonuc["gorunum"] = (
        "RİSKLİ"   if risk_puan >= 20 else
        "TEMKİNLİ" if risk_puan >= 10 else
        "NÖTR"
    )
    rprint(f"\n  Makro görünüm: [bold]{'red' if sonuc['gorunum']=='RİSKLİ' else 'yellow' if sonuc['gorunum']=='TEMKİNLİ' else 'green'}]{sonuc['gorunum']}[/bold] | Risk katkısı: +{sonuc['risk_katki']} puan")
    return sonuc


# ════════════════════════════════════════════════════════════════════════════
# MODÜL 4: PİYASA REJİMİ TESPİTİ
# ════════════════════════════════════════════════════════════════════════════

def piyasa_rejimi_tespiti(genisl: dict, yabanci: dict, makro: dict) -> dict:
    baslik("🎯 MODÜL 4 — Piyasa Rejimi Tespiti")

    # ── BIST100 Endeks Analizi ─────────────────────────────────────────────
    xu100_data = _yfinance_cek("XU100.IS", "6mo")
    endeks_sonuc = {}

    if xu100_data is not None and len(xu100_data) >= 50:
        kapanis = xu100_data["Close"]
        son     = float(kapanis.iloc[-1])
        ort20   = float(kapanis.rolling(20).mean().iloc[-1])
        ort50   = float(kapanis.rolling(50).mean().iloc[-1])
        xu_rsi  = _rsi(kapanis)

        # MACD
        macd_h = kapanis.ewm(span=12).mean() - kapanis.ewm(span=26).mean()
        macd_s = macd_h.ewm(span=9).mean()
        macd_son  = float(macd_h.iloc[-1])
        macd_sinyal = float(macd_s.iloc[-1])
        macd_histo  = round(macd_son - macd_sinyal, 0)

        # Momentum: Son 1, 3, 6 ay
        ay1  = round((son / float(kapanis.iloc[-20])  - 1) * 100, 1)
        ay3  = round((son / float(kapanis.iloc[-60])  - 1) * 100, 1) if len(kapanis) >= 60 else None
        ay6  = round((son / float(kapanis.iloc[-120]) - 1) * 100, 1) if len(kapanis) >= 120 else None

        # Bollinger Bandı
        bb_ort  = float(kapanis.rolling(20).mean().iloc[-1])
        bb_std  = float(kapanis.rolling(20).std().iloc[-1])
        bb_ust  = bb_ort + 2 * bb_std
        bb_alt  = bb_ort - 2 * bb_std
        bb_poz  = (son - bb_alt) / (bb_ust - bb_alt) * 100  # 0=alt band, 100=üst band

        endeks_sonuc = {
            "son": son, "ort20": round(ort20), "ort50": round(ort50),
            "rsi": xu_rsi, "macd_histo": macd_histo,
            "ay1_pct": ay1, "ay3_pct": ay3, "ay6_pct": ay6,
            "bb_pozisyon": round(bb_poz, 1),
            "ust_band": round(bb_ust), "alt_band": round(bb_alt),
        }

        t = Table(title="📈 BIST100 Teknik Görünüm", border_style="magenta", show_lines=True)
        t.add_column("Gösterge"); t.add_column("Değer", justify="right"); t.add_column("Sinyal")

        def sinyal(kosul_iyi, iyi_metin, kotu_metin):
            return f"[green]{iyi_metin}[/green]" if kosul_iyi else f"[red]{kotu_metin}[/red]"

        t.add_row("Kapanış",          f"{son:,.0f}", "")
        t.add_row("MA20",             f"{ort20:,.0f}", sinyal(son > ort20, "Üstünde ✓", "Altında ✗"))
        t.add_row("MA50",             f"{ort50:,.0f}", sinyal(son > ort50, "Üstünde ✓", "Altında ✗"))
        t.add_row("RSI(14)",          f"{xu_rsi}",
                  f"[red]Aşırı Alım[/red]" if xu_rsi > 70 else
                  f"[cyan]Aşırı Satım[/cyan]" if xu_rsi < 30 else
                  f"[green]Normal[/green]")
        t.add_row("MACD Histogram",   f"{macd_histo:+.0f}",
                  sinyal(macd_histo > 0, "Pozitif ✓", "Negatif ✗"))
        t.add_row("Bollinger %B",     f"%{bb_poz:.0f}",
                  f"[red]Üst Banda Yakın[/red]" if bb_poz > 80 else
                  f"[cyan]Alt Banda Yakın[/cyan]" if bb_poz < 20 else
                  f"[green]Orta Bölge[/green]")
        t.add_row("1 Ay Getiri",      f"{ay1:+.1f}%", "")
        if ay3: t.add_row("3 Ay Getiri", f"{ay3:+.1f}%", "")
        if ay6: t.add_row("6 Ay Getiri", f"{ay6:+.1f}%", "")
        console.print(t)

    # ── Düzeltme Risk Skoru (0-100) ────────────────────────────────────────
    toplam_risk = (
        genisl.get("risk_katki", 0) +    # max 40
        yabanci.get("risk_katki", 0) +   # max 20
        makro.get("risk_katki", 0)        # max 30
    )

    # Endeks bazlı ek puan
    endeks_ek = 0
    if endeks_sonuc:
        rsi = endeks_sonuc.get("rsi", 50)
        bb  = endeks_sonuc.get("bb_pozisyon", 50)
        if rsi > 75: endeks_ek += 8
        elif rsi > 70: endeks_ek += 4
        if bb > 85: endeks_ek += 7
        elif bb > 75: endeks_ek += 3
        if endeks_sonuc.get("macd_histo", 0) < 0: endeks_ek += 3

    toplam_risk = min(toplam_risk + endeks_ek, 100)

    # ── Rejim Kararı ──────────────────────────────────────────────────────
    if toplam_risk >= 65:
        rejim = "⚠️  BALON / AŞIRI ALINAN"
        rejim_renk = "red"
        tavsiye = "Yeni pozisyon AÇMA. Mevcut pozisyonlarda stop-loss'ları sıkılaştır."
    elif toplam_risk >= 45:
        rejim = "🟡 DÜZELTME RİSKİ YÜKSEK"
        rejim_renk = "yellow"
        tavsiye = "Nakit oranını artır (%25+). Yeni alımları küçük tut."
    elif toplam_risk >= 30:
        rejim = "🟠 TEMKİNLİ TREND"
        rejim_renk = "yellow"
        tavsiye = "Mevcut pozisyonları koru, agresif alım yapma."
    elif toplam_risk >= 15:
        rejim = "🟢 SAĞLIKLI TREND"
        rejim_renk = "green"
        tavsiye = "Normal portföy işlemleri yapılabilir."
    else:
        rejim = "💚 GÜÇLÜ TREND / FIRSAT"
        rejim_renk = "green"
        tavsiye = "Pozisyon artırma fırsatı değerlendirilebilir."

    console.print()
    console.print(Panel(
        f"[bold {rejim_renk}]{rejim}[/bold {rejim_renk}]\n\n"
        f"Düzeltme Risk Skoru: [bold {rejim_renk}]{toplam_risk}/100[/bold {rejim_renk}]\n\n"
        f"[dim]Genişlik katkısı : {genisl.get('risk_katki',0)}/40\n"
        f"Yabancı katkısı   : {yabanci.get('risk_katki',0)}/20\n"
        f"Makro katkısı     : {makro.get('risk_katki',0)}/30\n"
        f"Endeks teknik     : {endeks_ek}/10[/dim]\n\n"
        f"[bold]Tavsiye:[/bold] {tavsiye}",
        title="🎯 PİYASA REJİMİ",
        border_style=rejim_renk
    ))

    # ── LLM Yorumu ───────────────────────────────────────────────────────
    prompt = f"""BIST100 Piyasa Sağlığı Analizi ({datetime.now().strftime('%Y-%m-%d')}):

GENIŞLIK: Yükselen {genisl.get('yukselenler',0)} / Düşen {genisl.get('dusenler',0)} hisse
Breadth skoru: %{genisl.get('breadth_skoru',0)}
Ağır hisse taşıyor mu: {genisl.get('agir_hisse_tasiyor',False)}
Aşırı alım (RSI>70): {genisl.get('rsi_asiri_yuksel_sayi',0)} hisse

YABANCI: {yabanci.get('trend','BİLİNMİYOR')}
USDTRY 10 günlük değişim: {yabanci.get('usd_degisim_10g','?')}%

MAKRO: {makro.get('gorunum','NÖTR')}
VIX: {makro.get('vix','?')}
TUR ETF 20G: {makro.get('tur_etf_degisim','?')}%

ENDEKS: RSI={endeks_sonuc.get('rsi','?')} | 
1ay={endeks_sonuc.get('ay1_pct','?')}% | 
BB%B={endeks_sonuc.get('bb_pozisyon','?')}

DÜZELTMERİSK SKORU: {toplam_risk}/100 → {rejim}

Bu verileri yorumla:
1. "Az sayıda hisse endeksi taşıyor" hipotezi doğrulanıyor mu?
2. Yakın vadede düzeltme beklenir mi? Tetikleyici ne olabilir?
3. Yatırımcı için somut 3 öneri ver."""

    rprint("\n  [dim]LLM piyasa yorumu hazırlanıyor...[/dim]")
    yorum = _llm_yorum(prompt)
    console.print(Panel(yorum, title="🤖 AI Piyasa Yorumu", border_style="blue"))

    return {
        "rejim": rejim,
        "risk_skoru": toplam_risk,
        "tavsiye": tavsiye,
        "endeks": endeks_sonuc,
        "yorum": yorum,
    }


# ════════════════════════════════════════════════════════════════════════════
# RAPOR KAYDET
# ════════════════════════════════════════════════════════════════════════════

def raporu_kaydet(genisl, yabanci, makro, rejim):
    rapor = {
        "tarih":     datetime.now().isoformat(),
        "genisl":    genisl,
        "yabanci":   yabanci,
        "makro":     makro,
        "rejim":     rejim,
        "risk_skoru": rejim.get("risk_skoru"),
        "rejim_adi": rejim.get("rejim"),
        "tavsiye":   rejim.get("tavsiye"),
    }
    dosya = f"piyasa_sagligi_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(dosya, "w", encoding="utf-8") as f:
        json.dump(rapor, f, ensure_ascii=False, indent=2, default=str)
    return dosya


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="BIST Piyasa Sağlığı Analizörü v1.0")
    parser.add_argument("--ozet", action="store_true", help="Sadece risk skoru")
    args = parser.parse_args()

    console.print(Panel(
        "[bold cyan]BIST PİYASA SAĞLIĞI ANALİZÖRÜ v1.0[/bold cyan]\n"
        f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        "[dim]Düzeltme Riski = Genişlik(40) + Yabancı(20) + Makro(30) + Teknik(10)[/dim]",
        border_style="cyan"
    ))

    if args.ozet:
        # Hızlı özet — sadece endeks
        xu = _yfinance_cek("XU100.IS", "1mo")
        if xu is not None:
            rsi = _rsi(xu["Close"])
            ay1 = (float(xu["Close"].iloc[-1]) / float(xu["Close"].iloc[-20]) - 1) * 100
            rprint(f"\nBIST100 RSI: {rsi} | Son ay: {ay1:+.1f}%")
            rprint(f"{'[red]AŞIRI ALIM — Dikkat!' if rsi>70 else '[green]Normal bölge'}")
        return

    # Tam analiz
    genisl  = genisl_analizi()
    yabanci = yabanci_analizi()
    makro   = makro_risk_analizi()
    rejim   = piyasa_rejimi_tespiti(genisl, yabanci, makro)

    dosya = raporu_kaydet(genisl, yabanci, makro, rejim)

    console.print()
    console.rule("[bold]✅ Analiz Tamamlandı[/bold]")
    rprint(f"\n[green]✓ Rapor → {dosya}[/green]")

    risk = rejim.get("risk_skoru", 0)
    renk = "red" if risk >= 65 else "yellow" if risk >= 45 else "green"
    rprint(f"[{renk} bold]Düzeltme Risk Skoru: {risk}/100[/{renk} bold]")


if __name__ == "__main__":
    main()
