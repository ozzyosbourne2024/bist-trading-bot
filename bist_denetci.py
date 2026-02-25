#!/usr/bin/env python3
"""
BIST100 AI AJAN DENETÇİSİ v1.0
================================
4 katmanlı denetim sistemi:
  1. Kural İhlali Tarayıcısı   — portföy kurallarını çapraz kontrol eder
  2. Backtesting               — tahmin vs gerçekleşen P&L karşılaştırması
  3. Agent 4 (Denetçi Ajan)    — portföy kararlarının mantığını LLM ile sorgular
  4. Kod Sağlık Testleri       — unit test + hata yakalama

Kullanım:
  python bist_denetci.py                    # Tüm denetimler
  python bist_denetci.py --kural            # Sadece kural ihlali
  python bist_denetci.py --backtest         # Sadece backtesting
  python bist_denetci.py --agent4           # Sadece denetçi ajan
  python bist_denetci.py --test             # Sadece kod testleri
  python bist_denetci.py --rapor DOSYA.json # Belirli rapor dosyasını denetle
"""

import json, os, glob, sys, argparse, traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint
    from rich.panel import Panel
    from rich.rule import Rule
    import requests
    from dotenv import load_dotenv
    from groq import Groq
    load_dotenv()   # .env dosyasındaki GROQ_API_KEY'i yükle
    DEPS_OK = True
except ImportError as e:
    print(f"Eksik kütüphane: {e}\npip install yfinance pandas rich requests python-dotenv groq")
    DEPS_OK = False

console = Console()

# ── Sabitler (bist_agents.py ile aynı) ──────────────────────────────────────
PORTFOY_KAYIT_DOSYA = "portfoy_pozisyonlar.json"
RISK_PROFIL = {
    "muhafazakar": {"min_kural_puan": 70, "max_tek_hisse": 10, "min_nakit": 20, "max_toplam": 80},
    "dengeli":     {"min_kural_puan": 55, "max_tek_hisse": 18, "min_nakit": 10, "max_toplam": 90},
    "agresif":     {"min_kural_puan": 45, "max_tek_hisse": 30, "min_nakit":  5, "max_toplam": 95},
}
SEKTOR_GRUPLAR = {
    "bankacilik": {"GARAN","AKBNK","YKBNK","ISCTR","HALKB","VAKBN","ISFIN","ISMEN"},
    "holding":    {"KCHOL","SAHOL","AGHOL","DOHOL","GLYHO"},
    "gyo":        {"EKGYO","ISGYO","KLGYO"},
    "enerji":     {"TUPRS","PETKM","ODAS"},
    "telekom":    {"TCELL","TTKOM"},
}
MAX_BANKACI_PCT = 30
MAX_KOR_PCT     = 12   # korelasyon > 0.85 ise her biri max %12
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = "llama-3.3-70b-versatile"
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"

# ════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════

def _son_raporu_bul() -> Optional[str]:
    """Dizindeki en yeni bist_rapor_*.json dosyasını döner."""
    dosyalar = sorted(glob.glob("bist_rapor_*.json"), reverse=True)
    return dosyalar[0] if dosyalar else None

def _raporu_yukle(dosya: str) -> dict:
    with open(dosya, encoding="utf-8") as f:
        return json.load(f)

def _guncel_fiyat(ticker: str) -> Optional[float]:
    try:
        t = yf.Ticker(ticker + ".IS")
        h = t.history(period="2d")
        return round(float(h["Close"].iloc[-1]), 2) if not h.empty else None
    except:
        return None

def _llm(sistem: str, kullanici: str) -> str:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return "⚠️  GROQ_API_KEY bulunamadı. .env dosyasını kontrol edin."
    try:
        client = Groq(api_key=key)
        r = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system",  "content": sistem},
                {"role": "user",    "content": kullanici},
            ],
            max_tokens=2000, temperature=0.3,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"LLM hatası: {e}"

def baslik(metin: str):
    console.print()
    console.rule(f"[bold cyan]{metin}[/bold cyan]")

def sonuc_satiri(ok: bool, metin: str, detay: str = ""):
    simge = "[green]✓[/green]" if ok else "[red]✗[/red]"
    d = f" [dim]{detay}[/dim]" if detay else ""
    rprint(f"  {simge} {metin}{d}")

# ════════════════════════════════════════════════════════════════════════
# MODÜL 1: KURAL İHLALİ TARAYICISI
# ════════════════════════════════════════════════════════════════════════

def kural_ihlali_tara(rapor: dict, risk_modu: str = "dengeli") -> list:
    """
    Agent 2 portföy kararlarını kural motoruna göre çapraz kontrol eder.
    İhlalleri liste olarak döner.
    """
    baslik("🔎 MODÜL 1 — Kural İhlali Tarayıcısı")
    ihlaller = []

    portfoy = rapor.get("agent2", {})
    kararlar = portfoy.get("kararlar", [])
    profil   = RISK_PROFIL.get(risk_modu, RISK_PROFIL["dengeli"])

    al_kararlar = [k for k in kararlar if k.get("karar") == "AL"]
    agirliklar  = {k["ticker"]: k.get("agirlik_pct", 0) for k in al_kararlar}
    toplam_al   = sum(agirliklar.values())

    # Hisse bazlı kural puanları (rapordaki kural_motoru'ndan)
    kural_map = {h["ticker"]: h.get("toplam_puan", 0)
                 for h in rapor.get("kural_motoru", [])}

    # Sharpe map
    sharpe_map = {h["ticker"]: h.get("sharpe")
                  for h in rapor.get("hisseler", [])}

    console.print(f"  Risk Modu: [bold]{risk_modu.upper()}[/bold] | "
                  f"AL kararı: {len(al_kararlar)} hisse | "
                  f"Toplam: %{toplam_al:.1f}")
    console.print()

    # ── K1: Tek hisse limiti ─────────────────────────────────────────────
    for t, a in agirliklar.items():
        ok = a <= profil["max_tek_hisse"]
        if not ok:
            ihlaller.append({"kural": "K1-TekHisse", "ticker": t, "deger": a,
                              "limit": profil["max_tek_hisse"]})
        sonuc_satiri(ok, f"K1 Tek hisse: {t} %{a:.1f}",
                     f"limit %{profil['max_tek_hisse']}")

    # ── K2: Bankacılık sektörü toplam ────────────────────────────────────
    banka_toplam = sum(a for t, a in agirliklar.items()
                       if t in SEKTOR_GRUPLAR["bankacilik"])
    ok = banka_toplam <= MAX_BANKACI_PCT
    if not ok:
        ihlaller.append({"kural": "K2-Bankacilik", "ticker": "SEKTOR",
                         "deger": banka_toplam, "limit": MAX_BANKACI_PCT})
    sonuc_satiri(ok, f"K2 Bankacılık toplam: %{banka_toplam:.1f}",
                 f"limit %{MAX_BANKACI_PCT}")

    # ── K3: Kural puanı eşiği ────────────────────────────────────────────
    for k in al_kararlar:
        t   = k["ticker"]
        puan = kural_map.get(t, k.get("kural_puan", 0))
        ok = puan >= profil["min_kural_puan"]
        if not ok:
            ihlaller.append({"kural": "K3-KuralPuan", "ticker": t,
                              "deger": puan, "limit": profil["min_kural_puan"]})
        sonuc_satiri(ok, f"K3 Kural puanı: {t} = {puan:.0f}",
                     f"min {profil['min_kural_puan']}")

    # ── K4: Hedef fiyat > giriş fiyatı ──────────────────────────────────
    fiyat_map = {h["ticker"]: h.get("fiyat") for h in rapor.get("hisseler", [])}
    for k in al_kararlar:
        t = k["ticker"]
        hedef = k.get("hedef_fiyat") or 0
        giris = fiyat_map.get(t) or k.get("hedef_fiyat", 1)
        ok = hedef > (giris or 0)
        if not ok:
            ihlaller.append({"kural": "K4-HedefFiyat", "ticker": t,
                              "deger": hedef, "limit": giris})
        sonuc_satiri(ok, f"K4 Hedef fiyat: {t} hedef={hedef:.2f} giriş={giris:.2f}" if giris else
                     f"K4 Hedef fiyat: {t} hedef={hedef}")

    # ── K5: Nakit oranı ──────────────────────────────────────────────────
    nakit = portfoy.get("nakit_orani_pct", 100 - toplam_al)
    ok = nakit >= profil["min_nakit"]
    if not ok:
        ihlaller.append({"kural": "K5-Nakit", "ticker": "PORTFOY",
                         "deger": nakit, "limit": profil["min_nakit"]})
    sonuc_satiri(ok, f"K5 Nakit oranı: %{nakit:.1f}", f"min %{profil['min_nakit']}")

    # ── K6: Stop-Loss mantığı (stop < giriş) ─────────────────────────────
    for k in al_kararlar:
        t     = k["ticker"]
        stop  = k.get("stop_loss") or 0
        giris = fiyat_map.get(t) or 0
        if stop and giris:
            ok = stop < giris
            if not ok:
                ihlaller.append({"kural": "K6-StopLoss", "ticker": t,
                                  "deger": stop, "limit": giris})
            sonuc_satiri(ok, f"K6 Stop-Loss: {t} stop={stop:.2f} giriş={giris:.2f}")

    # ── K7: Minimum hisse sayısı ──────────────────────────────────────────
    ok = len(al_kararlar) >= 5
    if not ok:
        ihlaller.append({"kural": "K7-MinHisse", "ticker": "PORTFOY",
                          "deger": len(al_kararlar), "limit": 5})
    sonuc_satiri(ok, f"K7 Hisse sayısı: {len(al_kararlar)}", "min 5")

    # ── Özet ─────────────────────────────────────────────────────────────
    console.print()
    if ihlaller:
        rprint(f"  [red bold]⚠️  {len(ihlaller)} kural ihlali tespit edildi![/red bold]")
        t = Table(border_style="red", show_lines=True)
        for col in ["Kural","Ticker","Değer","Limit"]:
            t.add_column(col)
        for i in ihlaller:
            t.add_row(i["kural"], i["ticker"],
                      f"{i['deger']:.1f}", f"{i['limit']:.1f}")
        console.print(t)
    else:
        rprint("  [green bold]✅ Tüm kurallar geçti. İhlal yok.[/green bold]")

    return ihlaller


# ════════════════════════════════════════════════════════════════════════
# MODÜL 2: BACKTESTING — TAHMİN vs GERÇEKLEŞEN
# ════════════════════════════════════════════════════════════════════════

def backtest_calistir(rapor_dosyalari: list = None) -> dict:
    """
    Tüm bist_rapor_*.json dosyalarındaki AL kararlarını tarihsel fiyatlarla karşılaştırır.
    """
    baslik("📊 MODÜL 2 — Backtesting (Tahmin vs Gerçekleşen)")

    if not rapor_dosyalari:
        rapor_dosyalari = sorted(glob.glob("bist_rapor_*.json"))

    if not rapor_dosyalari:
        rprint("  [yellow]⚠️  Hiç rapor dosyası bulunamadı.[/yellow]")
        return {}

    tum_sonuclar = []
    hedef_isabetleri = 0
    stop_tetiklemeler = 0
    toplam_karar = 0

    for dosya in rapor_dosyalari:
        try:
            rapor = _raporu_yukle(dosya)
        except:
            continue

        rapor_tarihi = rapor.get("tarih", "")[:10]
        if not rapor_tarihi:
            continue

        try:
            baslangic = datetime.strptime(rapor_tarihi, "%Y-%m-%d")
        except:
            continue

        bitis = baslangic + timedelta(days=30)   # 1 aylık performans penceresi
        bugun = datetime.now()
        bitis = min(bitis, bugun)

        kararlar = rapor.get("agent2", {}).get("kararlar", [])
        al_kararlar = [k for k in kararlar if k.get("karar") == "AL"]
        fiyat_map = {h["ticker"]: h.get("fiyat")
                     for h in rapor.get("hisseler", [])}

        for k in al_kararlar:
            ticker  = k["ticker"]
            hedef   = k.get("hedef_fiyat") or 0
            stop    = k.get("stop_loss") or 0
            giris   = fiyat_map.get(ticker) or 0
            agirlik = k.get("agirlik_pct", 0)

            if not giris or not ticker:
                continue

            # Tarihsel fiyat çek
            try:
                t_obj = yf.Ticker(ticker + ".IS")
                tarih_str_b = baslangic.strftime("%Y-%m-%d")
                tarih_str_e = bitis.strftime("%Y-%m-%d")
                h = t_obj.history(start=tarih_str_b, end=tarih_str_e)
                if h.empty:
                    continue
                son_fiyat   = round(float(h["Close"].iloc[-1]), 2)
                max_fiyat   = round(float(h["High"].max()), 2)
                min_fiyat   = round(float(h["Low"].min()), 2)
                pnl_pct     = round((son_fiyat - giris) / giris * 100, 2)
                hedef_ulas  = max_fiyat >= hedef if hedef else None
                stop_tetik  = min_fiyat <= stop  if stop  else None
            except:
                continue

            toplam_karar += 1
            if hedef_ulas:
                hedef_isabetleri += 1
            if stop_tetik:
                stop_tetiklemeler += 1

            tum_sonuclar.append({
                "rapor":       dosya,
                "tarih":       rapor_tarihi,
                "ticker":      ticker,
                "giris":       giris,
                "hedef":       hedef,
                "stop":        stop,
                "son_fiyat":   son_fiyat,
                "max_fiyat":   max_fiyat,
                "min_fiyat":   min_fiyat,
                "pnl_pct":     pnl_pct,
                "agirlik":     agirlik,
                "hedef_ulas":  hedef_ulas,
                "stop_tetik":  stop_tetik,
            })

    if not tum_sonuclar:
        rprint("  [yellow]⚠️  Yeterli geçmiş veri yok veya tüm raporlar çok yeni.[/yellow]")
        return {}

    # ── Tablo ─────────────────────────────────────────────────────────────
    t = Table(title="📈 AL Kararları Performansı", border_style="blue", show_lines=True)
    for col, kw in [
        ("Tarih",    {}),
        ("Ticker",   {"style":"bold"}),
        ("Giriş",    {"justify":"right"}),
        ("Hedef",    {"justify":"right"}),
        ("Stop",     {"justify":"right"}),
        ("Son",      {"justify":"right"}),
        ("P&L%",     {"justify":"right"}),
        ("Hedef✓",   {"justify":"center"}),
        ("Stop✗",    {"justify":"center"}),
    ]:
        t.add_column(col, **kw)

    for s in sorted(tum_sonuclar, key=lambda x: x["tarih"]):
        pnl_str = f"[green]+{s['pnl_pct']:.1f}%[/green]" if s["pnl_pct"] >= 0 \
                  else f"[red]{s['pnl_pct']:.1f}%[/red]"
        hedef_str = "[green]✓[/green]" if s["hedef_ulas"] else \
                    "[yellow]—[/yellow]" if s["hedef_ulas"] is None else "[dim]✗[/dim]"
        stop_str  = "[red]✗[/red]" if s["stop_tetik"] else \
                    "[yellow]—[/yellow]" if s["stop_tetik"] is None else "[green]✓[/green]"
        t.add_row(
            s["tarih"], s["ticker"],
            f"{s['giris']:.2f}", f"{s['hedef']:.2f}" if s["hedef"] else "—",
            f"{s['stop']:.2f}"  if s["stop"]  else "—",
            f"{s['son_fiyat']:.2f}", pnl_str,
            hedef_str, stop_str,
        )
    console.print(t)

    # ── Özet istatistikler ─────────────────────────────────────────────
    if tum_sonuclar:
        karlar     = [s["pnl_pct"] for s in tum_sonuclar]
        pozitif    = sum(1 for p in karlar if p > 0)
        ort_getiri = round(sum(karlar) / len(karlar), 2)
        max_kazan  = max(karlar)
        max_kayip  = min(karlar)
        isabet_ort = round(hedef_isabetleri / toplam_karar * 100, 1) if toplam_karar else 0
        stop_oran  = round(stop_tetiklemeler / toplam_karar * 100, 1) if toplam_karar else 0

        console.print()
        ozet = Table(title="📊 Özet İstatistikler", border_style="cyan")
        ozet.add_column("Metrik"); ozet.add_column("Değer", justify="right")
        ozet.add_row("Toplam AL kararı",     str(toplam_karar))
        ozet.add_row("Pozitif kapanan",       f"{pozitif}/{toplam_karar} (%{round(pozitif/toplam_karar*100,1)})" if toplam_karar else "—")
        ozet.add_row("Ortalama getiri",       f"[green]+{ort_getiri}%[/green]" if ort_getiri >= 0 else f"[red]{ort_getiri}%[/red]")
        ozet.add_row("En iyi karar",          f"[green]+{max_kazan:.1f}%[/green]")
        ozet.add_row("En kötü karar",         f"[red]{max_kayip:.1f}%[/red]")
        ozet.add_row("Hedefe ulaşma oranı",   f"%{isabet_ort}")
        ozet.add_row("Stop tetiklenme oranı", f"%{stop_oran}")
        console.print(ozet)

    return {"sonuclar": tum_sonuclar, "toplam": toplam_karar,
            "hedef_isabet_pct": isabet_ort if tum_sonuclar else 0,
            "ort_getiri": ort_getiri if tum_sonuclar else 0}


# ════════════════════════════════════════════════════════════════════════
# MODÜL 3: AGENT 4 — DENETÇİ AJAN
# ════════════════════════════════════════════════════════════════════════

def agent4_denetci(rapor: dict) -> str:
    """
    LLM tabanlı denetçi: portföy kararlarının tutarlılığını ve mantığını sorgular.
    """
    baslik("🔴 MODÜL 3 — Agent 4: Denetçi Ajan")

    portfoy  = rapor.get("agent2", {})
    kararlar = portfoy.get("kararlar", [])
    al_k     = [k for k in kararlar if k.get("karar") == "AL"]

    if not al_k:
        rprint("  [yellow]Portföyde AL kararı yok, denetim atlandı.[/yellow]")
        return ""

    # Hisse teknik verilerini hazırla
    hisse_map = {h["ticker"]: h for h in rapor.get("hisseler", [])}
    kural_map = {h["ticker"]: h for h in rapor.get("kural_motoru", [])}

    portfoy_ozeti = []
    for k in al_k:
        t  = k["ticker"]
        hm = hisse_map.get(t, {})
        km = kural_map.get(t, {})
        portfoy_ozeti.append(
            f"• {t}: %{k.get('agirlik_pct',0)} | "
            f"KuralPuan:{km.get('toplam_puan','?')} | "
            f"ADX:{km.get('adx','?')} | "
            f"Ichimoku:{km.get('ichimoku',{}).get('durum','?') if isinstance(km.get('ichimoku'),dict) else km.get('ichimoku','?')} | "
            f"SAR:{km.get('sar_yon','?')} | "
            f"Sharpe:{hm.get('sharpe','?')} | "
            f"Hedef:{k.get('hedef_fiyat','?')} | "
            f"Stop:{k.get('stop_loss','?')} | "
            f"Gerekçe:{k.get('gerekce','')[:80]}"
        )

    portfoy_str = "\n".join(portfoy_ozeti)
    genel_gorunum = rapor.get("agent1", "")[:400] if isinstance(rapor.get("agent1"), str) else ""
    makro = rapor.get("agent3", {})
    if isinstance(makro, dict):
        kritik = "; ".join(makro.get("kritik_gelismeler", [])[:3])
    else:
        kritik = ""

    sistem = """Sen bağımsız bir portföy denetçisisin. 
Sana bir AI portföy yöneticisinin kararları verilecek.
Şüpheci ve eleştirel bir bakışla şu soruları yanıtla:
1. Hangi kararlar teknik göstergelerle çelişiyor?
2. Hangi pozisyonlar aşırı riskli veya yetersiz gerekçeli?
3. Sektör yoğunlaşması veya korelasyon riski var mı?
4. Hedef/stop oranları (risk/reward) makul mu?
5. Genel piyasa görünümüyle portföy tutarlı mı?
6. Genel değerlendirme: Portföy ONAYLANDI / ŞARTLARI ONAYLANDI / REDDEDİLDİ
Türkçe, kısa ve net yaz. Somut ticker isimlerini belirt."""

    kullanici = f"""PORTFÖY KARARLARI:
{portfoy_str}

PIYASA GÖRÜNÜMÜ: {genel_gorunum}
KRİTİK HABERLER: {kritik}

Bu portföyü denetle. Güçlü ve zayıf noktaları belirt. Son satırda: ONAYLANDI / ŞARTLI ONAY / REDDEDİLDİ"""

    rprint("  [dim]Denetçi ajan analiz yapıyor...[/dim]")
    yanit = _llm(sistem, kullanici)

    panel_renk = "green" if "ONAYLANDI" in yanit and "REDDEDİLDİ" not in yanit \
                 else "red" if "REDDEDİLDİ" in yanit else "yellow"
    console.print(Panel(yanit, title="🔴 Agent 4 — Denetçi Değerlendirmesi",
                         border_style=panel_renk))
    return yanit


# ════════════════════════════════════════════════════════════════════════
# MODÜL 4: KOD SAĞLIK TESTLERİ
# ════════════════════════════════════════════════════════════════════════

def kod_testleri_calistir() -> dict:
    """
    bist_agents.py kodunun sağlığını test eder.
    """
    baslik("🧪 MODÜL 4 — Kod Sağlık Testleri")

    sonuclar = {"gecen": 0, "gecemeyen": 0, "hatalar": []}

    def test(isim: str, fonksiyon):
        try:
            fonksiyon()
            sonuc_satiri(True, isim)
            sonuclar["gecen"] += 1
        except AssertionError as e:
            sonuc_satiri(False, isim, str(e))
            sonuclar["gecemeyen"] += 1
            sonuclar["hatalar"].append({"test": isim, "hata": str(e)})
        except Exception as e:
            sonuc_satiri(False, isim, f"Exception: {type(e).__name__}: {e}")
            sonuclar["gecemeyen"] += 1
            sonuclar["hatalar"].append({"test": isim, "hata": traceback.format_exc()})

    # ── Import testi ──────────────────────────────────────────────────────
    def t_import():
        import importlib.util
        spec = importlib.util.spec_from_file_location("bist_agents", "bist_agents.py")
        assert spec is not None, "bist_agents.py bulunamadı"
    test("bist_agents.py import edilebilir", t_import)

    # ── Fibonacci testi ───────────────────────────────────────────────────
    def t_fibonacci():
        import pandas as pd
        import sys
        sys.path.insert(0, ".")
        try:
            from bist_agents import hesapla_fibonacci
            s = pd.Series([100.0]*20 + [110.0]*20 + [105.0]*20)
            fib = hesapla_fibonacci(s)
            assert "1.272" in fib, "1.272 uzantısı eksik"
            assert "1.618" in fib, "1.618 uzantısı eksik"
            assert fib["1.272"] > fib["1.0"], "Uzantı seviyesi yanlış hesaplanmış"
            assert fib["0.618"] < fib["1.0"], "Destek seviyesi yanlış"
        except ImportError:
            raise AssertionError("hesapla_fibonacci import edilemedi")
    test("Fibonacci uzantı seviyeleri doğru", t_fibonacci)

    # ── JSON parser testi ─────────────────────────────────────────────────
    def t_json_parser():
        sys.path.insert(0, ".")
        try:
            from bist_agents import FinansalAjanlar
            import unittest.mock as mock
            # Dummy ajan oluştur
            fa = FinansalAjanlar.__new__(FinansalAjanlar)

            # Test 1: Direkt JSON
            r1 = fa._json('{"kararlar":[],"strateji":"test"}')
            assert r1.get("strateji") == "test", "Direkt JSON parse başarısız"

            # Test 2: Markdown sarmalı
            md = '```json\n{"kararlar":[{"ticker":"ENKAI"}]}\n```'
            r2 = fa._json(md)
            assert r2.get("kararlar"), "Markdown JSON parse başarısız"

            # Test 3: Gömülü JSON
            embedded = 'Açıklama metni {"kararlar":[],"nakit_orani_pct":30} ve devam'
            r3 = fa._json(embedded)
            assert r3.get("nakit_orani_pct") == 30, "Gömülü JSON parse başarısız"

        except ImportError:
            raise AssertionError("FinansalAjanlar import edilemedi")
    test("JSON parser (3 mod) çalışıyor", t_json_parser)

    # ── Kural motoru testi ────────────────────────────────────────────────
    def t_kural_motoru():
        sys.path.insert(0, ".")
        try:
            from bist_agents import portfoy_kurallari_uygula
            import pandas as pd
            # Bankacılık limiti ihlali testi
            portfoy = {"kararlar": [
                {"ticker": "YKBNK", "karar": "AL", "agirlik_pct": 20, "gerekce": ""},
                {"ticker": "GARAN", "karar": "AL", "agirlik_pct": 20, "gerekce": ""},
            ], "nakit_orani_pct": 60}

            class FakeHisse:
                def __init__(self, t, kp=70, sh=0.5, fib=None):
                    self.ticker=t; self.sharpe=sh; self.fib=fib or {}
                    self.adx=30; self.ichimoku={"durum":"BULUT_USTU"}; self.sar_yon="YUKARI"
                    self.rsi_iraksama="YOK"; self.macd_iraksama="YOK"; self.obv_iraksama="YOK"
                    self.mum_formasyonlari=[]; self.fiyat=100
                    from bist_agents import KuralMotorSonuc
                    self.kural_sonuc=KuralMotorSonuc(ticker=t,teknik_puan=70,temel_puan=70,toplam_puan=kp,
                                                golden_cross="YUKARI",adx=30,
                                                ichimoku_durum="BULUT_USTU",sar_yon="YUKARI",
                                                rsi_iraksama="YOK",macd_iraksama="YOK",
                                                obv_iraksama="YOK",mum_formasyonlar=[],
                                                aciklamalar=[])

            hisseler = [FakeHisse("YKBNK"), FakeHisse("GARAN")]
            kor_df = pd.DataFrame({"YKBNK":{"YKBNK":1.0,"GARAN":0.91},
                                   "GARAN":{"YKBNK":0.91,"GARAN":1.0}})

            sonuc = portfoy_kurallari_uygula(portfoy, hisseler, kor_df)
            al_kararlar = [k for k in sonuc["kararlar"] if k["karar"]=="AL"]
            banka_toplam = sum(k["agirlik_pct"] for k in al_kararlar)
            assert banka_toplam <= 30, f"Bankacılık limiti aşıldı: %{banka_toplam}"
        except ImportError as e:
            raise AssertionError(f"portfoy_kurallari_uygula import edilemedi: {e}")
    test("Kural motoru bankacılık limitini zorluyor", t_kural_motoru)

    # ── Hedef fiyat testi ─────────────────────────────────────────────────
    def t_hedef_fiyat():
        sys.path.insert(0, ".")
        try:
            from bist_agents import portfoy_kurallari_uygula
            import pandas as pd
            portfoy = {"kararlar": [
                {"ticker": "ENKAI", "karar": "AL", "agirlik_pct": 10,
                 "hedef_fiyat": 50.0, "stop_loss": 80.0, "gerekce": ""},
            ], "nakit_orani_pct": 90}

            class FakeHisse2:
                def __init__(self):
                    self.ticker="ENKAI"; self.sharpe=1.0; self.fib={"1.272":130.0,"1.618":160.0}
                    self.adx=50; self.ichimoku={"durum":"BULUT_USTU"}; self.sar_yon="YUKARI"
                    self.rsi_iraksama="YOK"; self.macd_iraksama="YOK"; self.obv_iraksama="YOK"
                    self.mum_formasyonlari=[]; self.fiyat=103.0
                    from bist_agents import KuralMotorSonuc
                    self.kural_sonuc=KuralMotorSonuc(ticker="ENKAI",teknik_puan=90,temel_puan=80,toplam_puan=86,
                                                golden_cross="YUKARI",adx=50,
                                                ichimoku_durum="BULUT_USTU",sar_yon="YUKARI",
                                                rsi_iraksama="YOK",macd_iraksama="YOK",
                                                obv_iraksama="YOK",mum_formasyonlar=[],
                                                aciklamalar=[])
            hisseler = [FakeHisse2()]
            kor_df = pd.DataFrame({"ENKAI":{"ENKAI":1.0}})
            sonuc = portfoy_kurallari_uygula(portfoy, hisseler, kor_df)
            al = [k for k in sonuc["kararlar"] if k["karar"]=="AL"]
            assert al, "AL kararı kalmadı"
            assert al[0]["hedef_fiyat"] > 103.0, \
                f"Hedef düzeltme çalışmadı: {al[0]['hedef_fiyat']}"
        except ImportError as e:
            raise AssertionError(f"import hatası: {e}")
    test("Hedef fiyat otomatik düzeltmesi çalışıyor", t_hedef_fiyat)

    # ── Portföy dosyası testi ─────────────────────────────────────────────
    def t_portfoy_dosyasi():
        if not os.path.exists(PORTFOY_KAYIT_DOSYA):
            raise AssertionError(f"{PORTFOY_KAYIT_DOSYA} bulunamadı — bist_agents.py çalıştırıldı mı?")
        with open(PORTFOY_KAYIT_DOSYA, encoding="utf-8") as f:
            kayit = json.load(f)
        assert "pozisyonlar" in kayit, "'pozisyonlar' anahtarı eksik"
        assert len(kayit["pozisyonlar"]) > 0, "Portföy boş"
    test("portfoy_pozisyonlar.json geçerli", t_portfoy_dosyasi)

    # ── Rapor dosyası testi ───────────────────────────────────────────────
    def t_rapor_dosyasi():
        dosya = _son_raporu_bul()
        assert dosya, "Hiç bist_rapor_*.json bulunamadı"
        rapor = _raporu_yukle(dosya)
        for alan in ["tarih", "agent2", "hisseler", "kural_motoru"]:
            assert alan in rapor, f"'{alan}' alanı eksik: {dosya}"
        portfoy = rapor["agent2"]
        assert "kararlar" in portfoy, "'kararlar' eksik"
    test("Son rapor dosyası geçerli yapıda", t_rapor_dosyasi)

    # ── yfinance bağlantı testi ───────────────────────────────────────────
    def t_yfinance():
        t = yf.Ticker("YKBNK.IS")
        h = t.history(period="5d")
        assert not h.empty, "yfinance YKBNK verisi çekemedi"
        assert "Close" in h.columns, "Close kolonu eksik"
    test("yfinance bağlantısı (YKBNK.IS)", t_yfinance)

    # ── Özet ─────────────────────────────────────────────────────────────
    console.print()
    toplam = sonuclar["gecen"] + sonuclar["gecemeyen"]
    renk = "green" if sonuclar["gecemeyen"] == 0 else \
           "yellow" if sonuclar["gecen"] > sonuclar["gecemeyen"] else "red"
    rprint(f"  [{renk}]Sonuç: {sonuclar['gecen']}/{toplam} test geçti.[/{renk}]")

    if sonuclar["hatalar"]:
        rprint("\n  [red]Başarısız testler:[/red]")
        for h in sonuclar["hatalar"]:
            rprint(f"    • [bold]{h['test']}[/bold]: {h['hata'][:120]}")

    return sonuclar


# ════════════════════════════════════════════════════════════════════════
# ANA RAPOR
# ════════════════════════════════════════════════════════════════════════

def denetim_raporu_kaydet(ihlaller, backtest, agent4_yanit, test_sonuclari):
    """Tüm denetim sonuçlarını JSON dosyasına kaydeder."""
    rapor = {
        "tarih":          datetime.now().isoformat(),
        "kural_ihlalleri": ihlaller,
        "ihlal_sayisi":    len(ihlaller),
        "backtest":        backtest,
        "agent4":          agent4_yanit,
        "kod_testleri": {
            "gecen":       test_sonuclari.get("gecen", 0),
            "gecemeyen":   test_sonuclari.get("gecemeyen", 0),
            "hatalar":     test_sonuclari.get("hatalar", []),
        },
        "genel_durum": "SORUN_YOK" if (
            len(ihlaller) == 0 and test_sonuclari.get("gecemeyen", 0) == 0
        ) else "DİKKAT_GEREKİYOR",
    }
    dosya = f"denetim_raporu_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(dosya, "w", encoding="utf-8") as f:
        json.dump(rapor, f, ensure_ascii=False, indent=2, default=str)
    return dosya


# ════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="BIST100 AI Ajan Denetçisi v1.0")
    parser.add_argument("--kural",    action="store_true", help="Sadece kural ihlali taraması")
    parser.add_argument("--backtest", action="store_true", help="Sadece backtesting")
    parser.add_argument("--agent4",   action="store_true", help="Sadece denetçi ajan")
    parser.add_argument("--test",     action="store_true", help="Sadece kod testleri")
    parser.add_argument("--rapor",    type=str,            help="Belirli rapor dosyasını kullan")
    args = parser.parse_args()

    tumu = not any([args.kural, args.backtest, args.agent4, args.test])

    console.print(Panel(
        "[bold cyan]BIST100 AI AJAN DENETÇİSİ v1.0[/bold cyan]\n"
        f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        border_style="cyan"
    ))

    # Rapor yükle
    rapor_dosyasi = args.rapor or _son_raporu_bul()
    rapor = {}
    if rapor_dosyasi and os.path.exists(rapor_dosyasi):
        rapor = _raporu_yukle(rapor_dosyasi)
        rprint(f"  Rapor: [cyan]{rapor_dosyasi}[/cyan]")
    else:
        rprint("  [yellow]⚠️  Rapor dosyası bulunamadı. Bazı modüller atlanacak.[/yellow]")

    ihlaller       = []
    backtest_sonuc = {}
    agent4_yanit   = ""
    test_sonuclari = {}

    # Modül 1: Kural ihlali
    if tumu or args.kural:
        if rapor:
            ihlaller = kural_ihlali_tara(rapor)
        else:
            rprint("\n[yellow]Modül 1 atlandı: rapor yok[/yellow]")

    # Modül 2: Backtest
    if tumu or args.backtest:
        if args.rapor:
            backtest_sonuc = backtest_calistir([args.rapor])
        else:
            backtest_sonuc = backtest_calistir()

    # Modül 3: Agent 4
    if tumu or args.agent4:
        if rapor:
            agent4_yanit = agent4_denetci(rapor)
        else:
            rprint("\n[yellow]Modül 3 atlandı: rapor yok[/yellow]")

    # Modül 4: Kod testleri
    if tumu or args.test:
        test_sonuclari = kod_testleri_calistir()

    # Raporu kaydet
    if tumu:
        dosya = denetim_raporu_kaydet(ihlaller, backtest_sonuc, agent4_yanit, test_sonuclari)
        console.print()
        console.rule("[bold]✅ Denetim Tamamlandı[/bold]")
        rprint(f"\n[green]✓ Denetim raporu → {dosya}[/green]")

        # Genel durum
        sorunlu = len(ihlaller) > 0 or test_sonuclari.get("gecemeyen", 0) > 0
        if sorunlu:
            rprint("[yellow bold]⚠️  DİKKAT GEREKİYOR — Yukarıdaki uyarıları inceleyin.[/yellow bold]")
        else:
            rprint("[green bold]✅ SORUN YOK — Sistem sağlıklı.[/green bold]")


if __name__ == "__main__":
    main()
