"""
BIST100 Finansal Ajan Sistemi v4.0
════════════════════════════════════════════════════════════════
MİMARİ:
  Filtre Agent  → 100 hisse hızlı tara, manipülasyon/balon tespit et
  Kural Motoru  → Deterministik teknik puanlama (LLM'siz)
  Agent 3       → Türkiye haberleri + sentiment
  Agent 1       → Derin analiz + BIST genel + skor sentezi
  Agent 2       → Kelly Criterion + korelasyon + sektör çeşitlendirme

YENİ TEKNİK İNDİKATÖRLER:
  Golden/Death Cross | RSI+MACD+OBV Iraksama | Mum Formasyonları
  ADX Trend Gücü | Ichimoku Cloud | Parabolic SAR

YENİ PORTFÖY YÖNETİMİ:
  Kelly Criterion | Korelasyon Matrisi | Sektör Çeşitlendirme
  Sharpe Ratio | Max Drawdown

Kurulum:
    pip install groq yfinance pandas numpy rich python-dotenv feedparser requests beautifulsoup4

Kullanım:
    .env → GROQ_API_KEY=gsk_...
    python bist_agents.py
"""

import os, json, time, requests, feedparser, warnings
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from itertools import combinations

warnings.filterwarnings("ignore")

import pandas as pd
import yfinance as yf
from groq import Groq
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint
from dotenv import load_dotenv

load_dotenv()
console = Console()

# ════════════════════════════════════════════════════════════════
# KONFİGÜRASYON
# ════════════════════════════════════════════════════════════════

BIST100_TICKERS = [
    # Büyük bankalar
    "GARAN.IS","AKBNK.IS","YKBNK.IS","ISCTR.IS","HALKB.IS","VAKBN.IS",
    # Holdingler
    "KCHOL.IS","SAHOL.IS","AGHOL.IS","DOHOL.IS","GLYHO.IS",
    # Sanayi / Otomotiv
    "FROTO.IS","TOASO.IS","EREGL.IS","ARCLK.IS","VESTL.IS","VESBE.IS","OTKAR.IS",
    # Savunma / Teknoloji
    "ASELS.IS","LOGO.IS","NETAS.IS","KAREL.IS","INDES.IS",
    # Enerji / Kimya
    "TUPRS.IS","PETKM.IS","GUBRF.IS","ODAS.IS",
    # Havacılık / Ulaşım
    "THYAO.IS","PGSUS.IS","TAVHL.IS",
    # Telecom / Medya
    "TCELL.IS","TTKOM.IS",
    # Perakende / Gıda
    "BIMAS.IS","MGROS.IS","SOKM.IS","ULKER.IS","CCOLA.IS","AEFES.IS",
    "MAVI.IS","TATGD.IS","MERKO.IS","BANVT.IS","PENGD.IS",
    # GYO / İnşaat
    "EKGYO.IS","ISGYO.IS","KLGYO.IS","ENKAI.IS","TKFEN.IS","AKCNS.IS","CIMSA.IS",
    # Cam / Kimya
    "SISE.IS",
    # Demir Çelik / Maden
    "ISDMR.IS","KRDMD.IS","KRSTL.IS",
    # Finans / Sigorta
    "ISFIN.IS","ISMEN.IS","ALARK.IS",
    # Diğer sanayi
    "BRISA.IS","KORDS.IS","DOAS.IS","FROTO.IS","JANTS.IS",
    "GESAN.IS","ATAGY.IS","PRKME.IS","SELEC.IS","MERCN.IS",
    "KATMR.IS","KLMSN.IS","KONTR.IS","KONYA.IS",
    "MAALT.IS","MAGEN.IS","METRO.IS","KMPUR.IS",
    "HURGZ.IS","KARTN.IS","ISDMR.IS",
]
# Tekrar edenleri temizle
# Tekrar edenleri temizle (liste tanımında duplikasyon olabilir)
BIST100_TICKERS = list(dict.fromkeys(BIST100_TICKERS))

PORTFOY_BUYUKLUGU   = 100_000   # TL
MODEL               = "llama-3.3-70b-versatile"
PERIOD              = "6mo"
FILTRE_LIMIT        = 35
MANIPULASYON_ESIK   = 65    # 60'tan 65'e çıkarıldı: TATGD gibi anlık hacim patlamaları için tolerans
BALON_ESIK          = 65
MAX_SEKTOR_AGIRLIK  = 30        # tek sektöre max %30
MAX_KOR_AGIRLIK     = 12        # 0.85+ korelasyonlu hisse max %12

# ── RİSK MODU ──────────────────────────────────────────────────────────────────
# "muhafazakar" → nakit %20+, tek hisse max %10, sadece kural_puan>70
# "dengeli"     → nakit %10, tek hisse max %18, kural_puan>55
# "agresif"     → nakit %5,  tek hisse max %25, kural_puan>45 + momentum filtresi
RISK_MODU = "dengeli"   # "muhafazakar" | "dengeli" | "agresif"

RISK_PROFIL = {
    "muhafazakar": {
        "min_kural_puan": 70, "max_tek_hisse": 10, "min_nakit": 20,
        "max_toplam_yatirim": 80, "kelly_carpan": 0.4,
        "min_sharpe": 0.0, "aciklama": "Güvenli — sadece yüksek puanlı, nakit ağır"
    },
    "dengeli": {
        "min_kural_puan": 55, "max_tek_hisse": 18, "min_nakit": 10,
        "max_toplam_yatirim": 90, "kelly_carpan": 0.7,
        "min_sharpe": -0.5, "aciklama": "Dengeli — çeşitlendirilmiş, orta risk"
    },
    "agresif": {
        "min_kural_puan": 45, "max_tek_hisse": 30, "min_nakit": 5,
        "max_toplam_yatirim": 95, "kelly_carpan": 1.0,
        "min_sharpe": -999, "aciklama": "Agresif — yüksek getiri odaklı, yüksek risk"
    },
}

# Günlük P&L takibi için portföy kayıt dosyası
PORTFOY_KAYIT_DOSYA = "portfoy_pozisyonlar.json"


# Filtreden otomatik geçen hisseler
# Sebep: F/K N/A, yüksek F/K (büyüme/havacılık/teknoloji), holding yapısı
# veya BIST100 endeks ağırlığı nedeniyle kural dışı değerlendirme gerektirir
ZORUNLU_GECIS = [
    # ── Holdinglar (konsolide yapı → F/K yanıltıcı) ──
    "KCHOL", "SAHOL", "AGHOL", "DOHOL", "GLYHO", "THYAO",
    "TKFEN",   # Kalker/gübre/inşaat holding
    "ALARK",   # Alarko Holding
    # ── Savunma (yüksek F/K büyüme primidir) ──
    "ASELS",
    # ── Havacılık/Turizm (F/K döngüsel, yüksek F/K normal) ──
    "TAVHL", "PGSUS",
    # ── Teknoloji/Yazılım (yüksek F/K büyüme beklentisi) ──
    "LOGO", "KAREL", "NETAS", "SELEC",
    # ── GYO (F/K N/A, gelir modeli farklı) ──
    "KLGYO", "ISGYO", "EKGYO", "TRGYO",
    # ── Büyük endeks ağırlıklı (likidite/endeks ağırlığı) ──
    "EREGL", "TCELL", "SISE", "BIMAS", "ARCLK",
    "MGROS", "ISMEN", "ENKAI",
    # ── Petrokimya/Enerji (F/K N/A dönemleri olur) ──
    "PETKM", "KORDS", "BRISA",
    # ── Diğer (F/K N/A ama BIST100 bileşeni) ──
    "BANVT", "PENGD", "GESAN",
]

# Sektör bazlı F/K değerlendirme eşikleri
FK_ESIK_SEKTOREL = {
    "savunma":    (15, 120),   # ASELS gibi — yüksek F/K büyüme primidir
    "holding":    (0, 9999),   # F/K anlamsız — atla
    "teknoloji":  (10, 80),
    "default":    (4, 25),
}

HOLDING_SEKTORLER = {"holding", "conglomerates", "diversified"}
SAVUNMA_SEKTORLER = {"defense", "aerospace & defense", "industrials"}
RISKSIZ_FAIZ        = 0.42      # TCMB faiz oranı (yıllık)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

RSS_KAYNAKLARI = [
    {"isim":"AA Ekonomi",     "url":"https://www.aa.com.tr/tr/rss/default?cat=ekonomi"},
    {"isim":"Reuters TR",     "url":"https://tr.reuters.com/news/rss/businessNews"},
    {"isim":"Dünya Gazetesi", "url":"https://www.dunya.com/rss/haber.xml"},
    {"isim":"Ekonomim",       "url":"https://www.ekonomim.com/rss/son-dakika-haberleri.xml"},
    {"isim":"Para Analiz",    "url":"https://www.paraanaliz.com/feed/"},
    {"isim":"Borsa Gündem",   "url":"https://www.borsagundem.com/feed"},
]

RESMI_KAYNAKLAR = [
    {"isim":"BDDK",           "url":"https://www.bddk.org.tr/Duyuru",                "selector":"div.haberListesi li"},
    {"isim":"Hazine",         "url":"https://www.hmb.gov.tr/duyurular",             "selector":"div.news-list li"},
    {"isim":"EPDK",           "url":"https://www.epdk.gov.tr/Detay/Icerik/3-0-24-3","selector":"div.haberler li"},
    {"isim":"KAP",            "url":"https://www.kap.org.tr/tr/bildirim-sorgu",     "selector":"div.w-clearfix.w-inline-block.comp-row"},
]

# ════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ════════════════════════════════════════════════════════════════

def _f(v) -> Optional[float]:
    try: return float(v) if v is not None else None
    except: return None

def _fmt(v, fmt="{:.2f}"):
    try: return fmt.format(float(v)) if v is not None else "N/A"
    except: return "N/A"

# ════════════════════════════════════════════════════════════════
# TEKNİK İNDİKATÖRLER
# ════════════════════════════════════════════════════════════════

def hesapla_rsi(s: pd.Series, p=14) -> float:
    d = s.diff(); g = d.clip(lower=0).rolling(p).mean()
    k = (-d.clip(upper=0)).rolling(p).mean()
    return round((100 - 100/(1+g/k)).iloc[-1], 2)

def hesapla_macd(s: pd.Series):
    m = s.ewm(span=12,adjust=False).mean() - s.ewm(span=26,adjust=False).mean()
    sg = m.ewm(span=9,adjust=False).mean()
    return round(m.iloc[-1],4), round(sg.iloc[-1],4), round((m-sg).iloc[-1],4), m, sg

def hesapla_bollinger(s: pd.Series, w=20):
    o=s.rolling(w).mean(); st=s.rolling(w).std()
    u=o+2*st; l=o-2*st
    return round(u.iloc[-1],2), round(o.iloc[-1],2), round(l.iloc[-1],2), round(((s-l)/(u-l)).iloc[-1],3)

def hesapla_stochastic(h,l,c,k=14,d=3):
    hi=h.rolling(k).max(); lo=l.rolling(k).min()
    pk=100*(c-lo)/(hi-lo)
    return round(pk.iloc[-1],2), round(pk.rolling(d).mean().iloc[-1],2)

def hesapla_atr(h,l,c,p=14) -> float:
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return round(tr.rolling(p).mean().iloc[-1],4)

def hesapla_adx(h,l,c,p=14) -> float:
    """ADX — trend gücü. 25+ güçlü trend, 50+ çok güçlü."""
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    dm_pos = (h.diff()).clip(lower=0)
    dm_neg = (-l.diff()).clip(lower=0)
    dm_pos = dm_pos.where(dm_pos > (-l.diff()).clip(lower=0), 0)
    dm_neg = dm_neg.where(dm_neg > (h.diff()).clip(lower=0), 0)
    atr_s  = tr.rolling(p).mean()
    di_pos = 100 * dm_pos.rolling(p).mean() / atr_s
    di_neg = 100 * dm_neg.rolling(p).mean() / atr_s
    dx     = 100 * (di_pos - di_neg).abs() / (di_pos + di_neg)
    adx    = dx.rolling(p).mean()
    return round(adx.iloc[-1], 2) if not adx.isna().iloc[-1] else 0.0

def hesapla_obv(c,v) -> float:
    yon = c.diff().apply(lambda x: 1 if x>0 else(-1 if x<0 else 0))
    ob  = (yon*v).cumsum()
    return round(ob.iloc[-1]-ob.iloc[-20],0) if len(ob)>=20 else 0.0

def hesapla_destek_direnc(c,w=20):
    s=c.tail(w*2); mx=[]; mn=[]
    for i in range(w,len(s)-w):
        d=s.iloc[i-w:i+w]
        if s.iloc[i]==d.max(): mx.append(round(s.iloc[i],2))
        if s.iloc[i]==d.min(): mn.append(round(s.iloc[i],2))
    return (min(mn) if mn else round(c.tail(20).min(),2),
            max(mx) if mx else round(c.tail(20).max(),2))

def hesapla_fibonacci(c,p=60):
    s=c.tail(p); hi=s.max(); lo=s.min(); r=hi-lo
    levels = {k:round(lo+v*r,2) for k,v in
              [("0.0",0),("0.236",.236),("0.382",.382),("0.5",.5),("0.618",.618),("1.0",1)]}
    # Uzantı seviyeleri (hedef fiyat için) — hi üzerinde
    levels["1.272"] = round(hi + 0.272*r, 2)
    levels["1.618"] = round(hi + 0.618*r, 2)
    return levels

def hesapla_ichimoku(h,l,c):
    """
    Ichimoku Cloud:
    Tenkan-sen (9): kısa dönem orta nokta
    Kijun-sen (26): uzun dönem orta nokta
    Senkou A: bulut üst sınırı
    Senkou B: bulut alt sınırı
    Chikou: gecikmeli kapanış fiyatı karşılaştırması
    """
    def mid(s, p): return (s.rolling(p).max() + s.rolling(p).min()) / 2
    tenkan  = mid(h, 9)
    kijun   = mid(h, 26)
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = mid(h, 52).shift(26)
    chikou   = c.shift(-26)
    fiyat = c.iloc[-1]
    t = round(tenkan.iloc[-1], 2)
    k = round(kijun.iloc[-1], 2)
    sa = round(senkou_a.iloc[-1], 2) if not pd.isna(senkou_a.iloc[-1]) else None
    sb = round(senkou_b.iloc[-1], 2) if not pd.isna(senkou_b.iloc[-1]) else None
    # Sinyal yorumu
    bulut_ust = max(sa, sb) if sa and sb else None
    bulut_alt = min(sa, sb) if sa and sb else None
    if bulut_ust and bulut_alt:
        if fiyat > bulut_ust: durum = "BULUT_USTU"    # güçlü boğa
        elif fiyat < bulut_alt: durum = "BULUT_ALTI"  # güçlü ayı
        else: durum = "BULUT_ICINDE"                   # nötr/kararsız
    else: durum = "BELIRSIZ"
    tenkan_kijun = "YUKARI" if t > k else "ASAGI"
    return {"tenkan":t, "kijun":k, "senkou_a":sa, "senkou_b":sb,
            "durum":durum, "tenkan_kijun":tenkan_kijun,
            "bulut_ust":bulut_ust, "bulut_alt":bulut_alt}

def hesapla_parabolic_sar(h,l,c, af=0.02, max_af=0.2):
    """Parabolic SAR — trend dönüş noktaları."""
    sar = l.copy(); trend = 1; ep = h.iloc[0]; af_cur = af
    for i in range(2, len(c)):
        if trend == 1:
            sar.iloc[i] = sar.iloc[i-1] + af_cur*(ep - sar.iloc[i-1])
            sar.iloc[i] = min(sar.iloc[i], l.iloc[i-1], l.iloc[i-2])
            if l.iloc[i] < sar.iloc[i]:
                trend=-1; sar.iloc[i]=ep; ep=l.iloc[i]; af_cur=af
        else:
            sar.iloc[i] = sar.iloc[i-1] + af_cur*(ep - sar.iloc[i-1])
            sar.iloc[i] = max(sar.iloc[i], h.iloc[i-1], h.iloc[i-2])
            if h.iloc[i] > sar.iloc[i]:
                trend=1; sar.iloc[i]=ep; ep=h.iloc[i]; af_cur=af
        if trend==1 and h.iloc[i]>ep: ep=h.iloc[i]; af_cur=min(af_cur+af,max_af)
        if trend==-1 and l.iloc[i]<ep: ep=l.iloc[i]; af_cur=min(af_cur+af,max_af)
    son_sar = round(sar.iloc[-1], 2)
    return son_sar, "YUKARI" if c.iloc[-1] > son_sar else "ASAGI"

# ── Iraksama Analizleri ────────────────────────────────────────

def rsi_iraksama(c: pd.Series, rsi_s: pd.Series, pencere=20) -> str:
    """
    POZ_IRAKSAMA: Fiyat düşüyor ama RSI yükseliyor → dip sinyali
    NEG_IRAKSAMA: Fiyat yükseliyor ama RSI düşüyor → tepe sinyali
    """
    if len(c) < pencere: return "YOK"
    f_trend = c.iloc[-1] - c.iloc[-pencere]
    r_trend = rsi_s.iloc[-1] - rsi_s.iloc[-pencere]
    if f_trend < 0 and r_trend > 3:  return "POZ_IRAKSAMA"
    if f_trend > 0 and r_trend < -3: return "NEG_IRAKSAMA"
    return "YOK"

def macd_iraksama(c: pd.Series, macd_s: pd.Series, pencere=20) -> str:
    if len(c) < pencere: return "YOK"
    f_trend = c.iloc[-1] - c.iloc[-pencere]
    m_trend = macd_s.iloc[-1] - macd_s.iloc[-pencere]
    if f_trend < 0 and m_trend > 0: return "POZ_IRAKSAMA"
    if f_trend > 0 and m_trend < 0: return "NEG_IRAKSAMA"
    return "YOK"

def obv_iraksama(c: pd.Series, v: pd.Series, pencere=20) -> str:
    if len(c) < pencere: return "YOK"
    yon = c.diff().apply(lambda x: 1 if x>0 else(-1 if x<0 else 0))
    ob  = (yon*v).cumsum()
    f_trend = c.iloc[-1] - c.iloc[-pencere]
    o_trend = ob.iloc[-1] - ob.iloc[-pencere]
    if f_trend < 0 and o_trend > 0: return "POZ_IRAKSAMA"
    if f_trend > 0 and o_trend < 0: return "NEG_IRAKSAMA"
    return "YOK"

# ── Mum Formasyonları ─────────────────────────────────────────

def mum_formasyonlari(o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series) -> list:
    """Son 3 mumu analiz ederek formasyonları tespit eder."""
    formasyonlar = []
    if len(c) < 3: return formasyonlar

    # Son mumlar
    o1,h1,l1,c1 = o.iloc[-1],h.iloc[-1],l.iloc[-1],c.iloc[-1]
    o2,h2,l2,c2 = o.iloc[-2],h.iloc[-2],l.iloc[-2],c.iloc[-2]
    o3,h3,l3,c3 = o.iloc[-3],h.iloc[-3],l.iloc[-3],c.iloc[-3]

    govde1 = abs(c1-o1); govde2 = abs(c2-o2); govde3 = abs(c3-o3)
    range1 = h1-l1; range2 = h2-l2

    # Hammer (çekiç) — dipte oluşur, AL sinyali
    if range1 > 0:
        alt_fitil = min(o1,c1) - l1
        ust_fitil = h1 - max(o1,c1)
        if alt_fitil >= 2*govde1 and ust_fitil < govde1*0.3:
            formasyonlar.append("HAMMER(AL)")

    # Shooting Star — tepede oluşur, SAT sinyali
    if range1 > 0:
        alt_fitil = min(o1,c1) - l1
        ust_fitil = h1 - max(o1,c1)
        if ust_fitil >= 2*govde1 and alt_fitil < govde1*0.3:
            formasyonlar.append("SHOOTING_STAR(SAT)")

    # Doji — kararsızlık
    if range1 > 0 and govde1 < range1 * 0.1:
        formasyonlar.append("DOJI(KARARSIZ)")

    # Bullish Engulfing — önceki kırmızıyı yutar, AL sinyali
    if c2 < o2 and c1 > o1 and o1 < c2 and c1 > o2:
        formasyonlar.append("BULLISH_ENGULFING(AL)")

    # Bearish Engulfing — önceki yeşili yutar, SAT sinyali
    if c2 > o2 and c1 < o1 and o1 > c2 and c1 < o2:
        formasyonlar.append("BEARISH_ENGULFING(SAT)")

    # Morning Star (Sabah Yıldızı) — 3 mum, AL sinyali
    if (c3 < o3 and govde3 > range3*0.5 if (range3:=h3-l3)>0 else False):
        if govde2 < range2 * 0.3:  # küçük orta mum
            if c1 > o1 and c1 > (o3+c3)/2:
                formasyonlar.append("MORNING_STAR(AL)")

    # Evening Star (Akşam Yıldızı) — 3 mum, SAT sinyali
    if (c3 > o3 and govde3 > (h3-l3)*0.5):
        if govde2 < range2 * 0.3:
            if c1 < o1 and c1 < (o3+c3)/2:
                formasyonlar.append("EVENING_STAR(SAT)")

    # Three White Soldiers — güçlü AL
    if c1>o1 and c2>o2 and c3>o3 and c1>c2>c3 and o1>o2>o3:
        formasyonlar.append("THREE_WHITE_SOLDIERS(GUCLU_AL)")

    # Three Black Crows — güçlü SAT
    if c1<o1 and c2<o2 and c3<o3 and c1<c2<c3 and o1<o2<o3:
        formasyonlar.append("THREE_BLACK_CROWS(GUCLU_SAT)")

    return formasyonlar

# ── Golden/Death Cross ─────────────────────────────────────────

def golden_death_cross(c: pd.Series):
    """
    Golden Cross: SMA50 SMA200'ü yukarı keser → güçlü AL sinyali
    Death Cross:  SMA50 SMA200'ü aşağı keser → güçlü SAT sinyali
    """
    if len(c) < 200: return "VERI_YOK", 0, 0
    sma50  = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    son50  = round(sma50.iloc[-1], 2)
    son200 = round(sma200.iloc[-1], 2)
    # Önceki 5 gün içinde kesişim var mıydı?
    for i in range(2, min(6, len(c))):
        prev_diff = sma50.iloc[-i] - sma200.iloc[-i]
        curr_diff = sma50.iloc[-1] - sma200.iloc[-1]
        if prev_diff < 0 and curr_diff > 0:
            return "GOLDEN_CROSS", son50, son200
        if prev_diff > 0 and curr_diff < 0:
            return "DEATH_CROSS", son50, son200
    if son50 > son200: return "SMA50_USTUNDE", son50, son200
    return "SMA50_ALTINDA", son50, son200

# ── Portföy Metrikleri ─────────────────────────────────────────

def hesapla_sharpe(c: pd.Series, risksiz=RISKSIZ_FAIZ) -> float:
    """Sharpe Ratio. >1 iyi, >2 mükemmel."""
    gunluk = c.pct_change().dropna()
    if len(gunluk) < 10: return 0.0
    yillik_getiri = gunluk.mean() * 252
    yillik_std    = gunluk.std() * (252**0.5)
    return round((yillik_getiri - risksiz) / yillik_std, 2) if yillik_std > 0 else 0.0

def hesapla_max_drawdown(c: pd.Series) -> float:
    """Max Drawdown %. Tepeden dibe en büyük düşüş."""
    peak   = c.cummax()
    dd     = (c - peak) / peak
    return round(dd.min() * 100, 2)

def kelly_criterion(beklenen_getiri: float, volatilite: float,
                    risksiz=RISKSIZ_FAIZ) -> float:
    """
    Kelly f = (μ - r) / σ²
    Yarı-Kelly (f/2) kullanılır — daha güvenli.
    Max %25 ile sınırlandırılır.
    """
    if volatilite <= 0: return 0.05
    f = (beklenen_getiri - risksiz) / (volatilite ** 2)
    f = f / 2  # yarı-Kelly
    return round(min(max(f, 0.02), 0.25), 3)  # %2 min, %25 max

# ════════════════════════════════════════════════════════════════
# KURAL MOTORU — Deterministik Puanlama
# ════════════════════════════════════════════════════════════════

@dataclass
class KuralMotorSonuc:
    ticker: str
    teknik_puan: float       # 0-100
    temel_puan: float        # 0-100
    toplam_puan: float       # 0-100
    golden_cross: str
    adx: float
    rsi_iraksama: str
    macd_iraksama: str
    obv_iraksama: str
    mum_formasyonlar: list
    ichimoku_durum: str
    sar_yon: str
    aciklamalar: list        # insan okunabilir sinyaller

def kural_motoru_hesapla(h) -> KuralMotorSonuc:
    """
    Deterministik puanlama sistemi. LLM çağrısı olmadan saf kural tabanlı.
    """
    puan = 0.0
    acik = []

    # ── Trend Puanları (max 30) ──────────────────────────────
    gc = h.golden_cross_durum
    if gc == "GOLDEN_CROSS":     puan += 30; acik.append("✅ Golden Cross (güçlü AL)")
    elif gc == "SMA50_USTUNDE":  puan += 18; acik.append("✅ SMA50 > SMA200 (pozitif trend)")
    elif gc == "DEATH_CROSS":    puan -= 25; acik.append("❌ Death Cross (güçlü SAT)")
    elif gc == "SMA50_ALTINDA":  puan -= 10; acik.append("⚠️ SMA50 < SMA200 (negatif trend)")

    adx_v = _f(h.adx)
    if adx_v:
        if adx_v > 50:   puan += 8; acik.append(f"✅ ADX:{adx_v:.0f} çok güçlü trend")
        elif adx_v > 25: puan += 4; acik.append(f"✅ ADX:{adx_v:.0f} güçlü trend")
        else:            acik.append(f"⚠️ ADX:{adx_v:.0f} zayıf/yok trend")

    # ── Ichimoku (max 15) ────────────────────────────────────
    ich = h.ichimoku
    if ich:
        if ich["durum"] == "BULUT_USTU":
            puan += 15; acik.append(f"✅ Ichimoku: bulut üstünde ({ich['tenkan_kijun']})")
        elif ich["durum"] == "BULUT_ALTINDA":
            puan -= 12; acik.append(f"❌ Ichimoku: bulut altında")
        else:
            acik.append(f"⚠️ Ichimoku: bulut içinde (kararsız)")
        if ich["tenkan_kijun"] == "YUKARI":
            puan += 5; acik.append("✅ Tenkan > Kijun (kısa vade pozitif)")

    # ── Parabolic SAR (max 8) ────────────────────────────────
    if h.sar_yon == "YUKARI": puan += 8; acik.append("✅ Parabolic SAR: yükseliş")
    else:                     puan -= 5; acik.append("❌ Parabolic SAR: düşüş")

    # ── Momentum / İndikatörler (max 20) ────────────────────
    rsi_v = _f(h.rsi_14)
    if rsi_v:
        if 30 <= rsi_v <= 50:    puan += 10; acik.append(f"✅ RSI:{rsi_v} alım fırsatı bölgesi")
        elif 50 < rsi_v <= 65:   puan += 6;  acik.append(f"✅ RSI:{rsi_v} momentum pozitif")
        elif rsi_v > 75:         puan -= 8;  acik.append(f"⚠️ RSI:{rsi_v} aşırı alım")
        elif rsi_v < 30:         puan += 12; acik.append(f"✅ RSI:{rsi_v} aşırı satım (dip fırsatı)")

    macd_v = _f(h.macd); macd_s = _f(h.macd_sinyal)
    if macd_v is not None and macd_s is not None:
        if macd_v > macd_s: puan += 6; acik.append("✅ MACD: sinyal üstünde")
        else:               puan -= 4; acik.append("❌ MACD: sinyal altında")

    stoch_k = _f(h.stoch_k)
    if stoch_k:
        if stoch_k < 20: puan += 8; acik.append(f"✅ Stoch:{stoch_k:.0f} aşırı satım")
        elif stoch_k > 80: puan -= 6; acik.append(f"⚠️ Stoch:{stoch_k:.0f} aşırı alım")

    # ── Iraksama Analizleri (max 15) ─────────────────────────
    ira_sayisi = 0
    for ira_tip, ira_val in [("RSI",h.rsi_iraksama),("MACD",h.macd_iraksama),("OBV",h.obv_iraksama)]:
        if ira_val == "POZ_IRAKSAMA":
            puan += 5; ira_sayisi += 1
            acik.append(f"✅ {ira_tip} pozitif ıraksama (güçlü dip sinyali)")
        elif ira_val == "NEG_IRAKSAMA":
            puan -= 5
            acik.append(f"❌ {ira_tip} negatif ıraksama (tepe sinyali)")
    if ira_sayisi >= 2:
        puan += 5; acik.append("✅✅ Çoklu pozitif ıraksama — güçlü dip onayı")

    # ── Mum Formasyonları (max 12) ────────────────────────────
    for form in h.mum_formasyonlari:
        if "AL" in form:
            puan += 6; acik.append(f"✅ Mum: {form}")
        elif "SAT" in form:
            puan -= 5; acik.append(f"❌ Mum: {form}")
        elif "KARARSIZ" in form:
            acik.append(f"⚠️ Mum: {form}")

    # ── Hacim / OBV (max 8) ──────────────────────────────────
    if h.obv_trend and h.obv_trend > 0: puan += 8; acik.append("✅ OBV: para girişi var")
    elif h.obv_trend and h.obv_trend < 0: puan -= 5; acik.append("❌ OBV: para çıkışı var")

    # ── Bollinger Band (max 8) ────────────────────────────────
    bb_p = _f(h.bb_pct)
    if bb_p is not None:
        if bb_p < 0.1:   puan += 8; acik.append(f"✅ BB%:{bb_p:.2f} alt band (alım bölgesi)")
        elif bb_p > 0.9: puan -= 6; acik.append(f"⚠️ BB%:{bb_p:.2f} üst band (satım bölgesi)")

    teknik_puan = max(0, min(100, 50 + puan))

    # ── Temel Puan (max 100) ─────────────────────────────────
    t_puan = 50.0; t_acik = []
    fk = _f(h.fk_orani)
    if fk:
        if 4 < fk < 12:    t_puan += 20; t_acik.append(f"✅ F/K:{fk:.1f} değer hissesi")
        elif 12 <= fk < 20: t_puan += 10; t_acik.append(f"✅ F/K:{fk:.1f} makul")
        elif fk >= 50:      t_puan -= 15; t_acik.append(f"❌ F/K:{fk:.1f} pahalı")

    roe = _f(h.roe)
    if roe:
        if roe > 20:  t_puan += 15; t_acik.append(f"✅ ROE:{roe:.1f}% yüksek karlılık")
        elif roe > 10: t_puan += 8; t_acik.append(f"✅ ROE:{roe:.1f}% iyi karlılık")
        elif roe < 0:  t_puan -= 15; t_acik.append(f"❌ ROE:{roe:.1f}% zararda")

    de = _f(h.borc_ozsermaye)
    if de:
        if de < 0.5:  t_puan += 10; t_acik.append(f"✅ D/E:{de:.2f} düşük borç")
        elif de > 2.0: t_puan -= 10; t_acik.append(f"❌ D/E:{de:.2f} yüksek borç")

    fcf = _f(h.fcf_m)
    if fcf and fcf > 0:  t_puan += 10; t_acik.append(f"✅ FCF:{fcf:,.0f}M pozitif")
    elif fcf and fcf < 0: t_puan -= 8;  t_acik.append(f"❌ FCF negatif")

    yoy = _f(h.gelir_buyume_yoy)
    if yoy:
        if yoy > 30:  t_puan += 10; t_acik.append(f"✅ YoY büyüme:{yoy:.1f}%")
        elif yoy > 10: t_puan += 5; t_acik.append(f"✅ YoY büyüme:{yoy:.1f}%")
        elif yoy < 0:  t_puan -= 8; t_acik.append(f"❌ Gelir düşüyor:{yoy:.1f}%")

    sharpe = _f(h.sharpe)
    if sharpe:
        if sharpe > 1.5:  t_puan += 8; t_acik.append(f"✅ Sharpe:{sharpe:.2f} iyi risk-getiri")
        elif sharpe < 0:  t_puan -= 5; t_acik.append(f"❌ Sharpe:{sharpe:.2f} negatif")

    temel_puan = max(0, min(100, t_puan))
    acik.extend(t_acik)

    toplam = round((teknik_puan * 0.6 + temel_puan * 0.4), 1)

    return KuralMotorSonuc(
        ticker=h.ticker,
        teknik_puan=round(teknik_puan,1),
        temel_puan=round(temel_puan,1),
        toplam_puan=toplam,
        golden_cross=h.golden_cross_durum,
        adx=_f(h.adx) or 0,
        rsi_iraksama=h.rsi_iraksama,
        macd_iraksama=h.macd_iraksama,
        obv_iraksama=h.obv_iraksama,
        mum_formasyonlar=h.mum_formasyonlari,
        ichimoku_durum=h.ichimoku.get("durum","?") if h.ichimoku else "?",
        sar_yon=h.sar_yon,
        aciklamalar=acik,
    )

# ════════════════════════════════════════════════════════════════
# VERİ SINIFLARI
# ════════════════════════════════════════════════════════════════

@dataclass
class HisseOzet:
    ticker: str; isim: str; sektor: str
    fiyat: float; degisim_1ay: float; degisim_3ay: float
    hacim_son: float; hacim_ort: float; hacim_anomali: float
    rsi_14: float
    fk_orani: Optional[float]; pd_dd: Optional[float]
    piyasa_degeri_m: Optional[float]; serbest_dolasim: Optional[float]
    gelir_m: Optional[float]
    manipulasyon_skoru: float = 0.0
    balon_skoru: float = 0.0
    kalite_skoru: float = 0.0

@dataclass
class HisseDerin:
    ticker: str; isim: str; sektor: str
    fiyat: float; degisim_1g: float; degisim_1h: Optional[float]; degisim_6ay: float
    # Temel teknik
    rsi_14: Optional[float]; stoch_k: Optional[float]; stoch_d: Optional[float]
    macd: Optional[float]; macd_sinyal: Optional[float]; macd_hist: Optional[float]
    sma_50: Optional[float]; sma_200: Optional[float]
    bb_ust: Optional[float]; bb_orta: Optional[float]; bb_alt: Optional[float]; bb_pct: Optional[float]
    atr: Optional[float]; volatilite: Optional[float]; yillik_getiri: Optional[float]
    obv_trend: Optional[float]; hacim_ort: Optional[float]
    destek: Optional[float]; direnc: Optional[float]; fib: Optional[dict]
    # Yeni teknik
    adx: Optional[float]
    golden_cross_durum: str = "VERI_YOK"
    ichimoku: Optional[dict] = None
    sar_deger: Optional[float] = None; sar_yon: str = "?"
    rsi_iraksama: str = "YOK"; macd_iraksama: str = "YOK"; obv_iraksama: str = "YOK"
    mum_formasyonlari: list = field(default_factory=list)
    # Temel
    fk_orani: Optional[float] = None; pd_dd: Optional[float] = None
    temettü_verimi: Optional[float] = None; piyasa_degeri_m: Optional[float] = None
    roe: Optional[float] = None; roa: Optional[float] = None
    borc_ozsermaye: Optional[float] = None; fcf_m: Optional[float] = None
    gelir_buyume_yoy: Optional[float] = None
    # Portföy metrikleri
    sharpe: Optional[float] = None; max_drawdown: Optional[float] = None
    kelly_f: Optional[float] = None
    # Kural motoru sonucu
    kural_sonuc: Optional[KuralMotorSonuc] = None
    manipulasyon_skoru: float = 0.0; balon_skoru: float = 0.0

# ════════════════════════════════════════════════════════════════
# VERİ ÇEKME
# ════════════════════════════════════════════════════════════════

def _to_float(val):
    try: return float(val) if val is not None else None
    except: return None

def hisse_ozet_cek(ticker: str) -> Optional[HisseOzet]:
    try:
        t=yf.Ticker(ticker); info=t.info; hist=t.history(period="3mo")
        if hist.empty or len(hist)<20: return None
        c=hist["Close"]; v=hist["Volume"]
        return HisseOzet(
            ticker=ticker.replace(".IS",""), isim=info.get("longName",ticker),
            sektor=info.get("sector","Bilinmiyor"),
            fiyat=round(c.iloc[-1],2),
            degisim_1ay=round((c.iloc[-1]/c.iloc[-22]-1)*100,2) if len(c)>=22 else 0,
            degisim_3ay=round((c.iloc[-1]/c.iloc[0]-1)*100,2),
            hacim_son=round(v.iloc[-1],0), hacim_ort=round(v.mean(),0),
            hacim_anomali=round(v.iloc[-1]/v.mean(),2) if v.mean()>0 else 0,
            rsi_14=hesapla_rsi(c),
            fk_orani=_to_float(info.get("trailingPE")),
            pd_dd=_to_float(info.get("priceToBook")),
            piyasa_degeri_m=round(info.get("marketCap",0)/1e6,0) if info.get("marketCap") else None,
            serbest_dolasim=info.get("floatShares"),
            gelir_m=round(info.get("totalRevenue",0)/1e6,0) if info.get("totalRevenue") else None,
        )
    except: return None

def manipulasyon_skoru_hesapla(h: HisseOzet) -> float:
    s=0.0
    if h.hacim_anomali>10: s+=35
    elif h.hacim_anomali>5: s+=20
    elif h.hacim_anomali>3: s+=10
    if h.rsi_14>85: s+=20
    elif h.rsi_14>80: s+=12
    if h.degisim_1ay>200: s+=25
    elif h.degisim_1ay>100: s+=18
    elif h.degisim_1ay>50: s+=8
    if h.serbest_dolasim and h.serbest_dolasim<5_000_000: s+=20
    elif h.serbest_dolasim and h.serbest_dolasim<20_000_000: s+=10
    return min(s,100.0)

def _is_holding(sektor: str) -> bool:
    return sektor.lower() in HOLDING_SEKTORLER or "holding" in sektor.lower()

def _is_savunma(sektor: str) -> bool:
    return any(k in sektor.lower() for k in ("defense","aerospace","savunma"))

def _is_zorunlu(ticker: str) -> bool:
    return ticker.replace(".IS","") in ZORUNLU_GECIS

def balon_skoru_hesapla(h: HisseOzet) -> float:
    # Zorunlu geçiş listesindeki hisseler için balon skoru hesaplama
    if _is_zorunlu(h.ticker): return 0.0

    s=0.0
    fk=_to_float(h.fk_orani); pddd=_to_float(h.pd_dd)
    pdm=_to_float(h.piyasa_degeri_m); gelir=_to_float(h.gelir_m)

    sektor = h.sektor.lower()
    is_hold = _is_holding(sektor)
    is_savun = _is_savunma(sektor)

    if fk is not None:
        if fk < 0:      s += 30                    # zarar eden her zaman şüpheli
        elif fk > 200:  s += 30
        elif fk > 100 and not (is_hold or is_savun): s += 20
        elif fk > 50  and not (is_hold or is_savun): s += 10
    elif not is_hold:
        s += 15   # Holding için N/A F/K normal — ceza verme

    if pddd:
        if pddd > 50:   s += 25
        elif pddd > 20: s += 15
        elif pddd > 10: s += 8

    if pdm and gelir and gelir > 0:
        pg = pdm / gelir
        if pg > 100:    s += 30
        elif pg > 50:   s += 20
        elif pg > 20 and not is_hold: s += 8

    if h.degisim_3ay > 300:   s += 15
    elif h.degisim_3ay > 150: s += 8
    return min(s, 100.0)

def kalite_skoru_hesapla(h: HisseOzet) -> float:
    # Zorunlu geçiş listesi — holding/savunma/büyük endeks hisseleri
    if _is_zorunlu(h.ticker): return 88.0  # her zaman top 35'e girer

    sektor = h.sektor.lower()
    is_hold = _is_holding(sektor)
    is_savun = _is_savunma(sektor)

    s = 50.0
    fk = _to_float(h.fk_orani)
    pddd = _to_float(h.pd_dd)

    if fk is not None:
        if is_hold:
            pass  # holding için F/K değerlendirme yapma
        elif is_savun:
            if 15 < fk < 120: s += 15   # savunmada yüksek F/K büyüme primidir
            elif fk < 0:       s -= 15
        else:
            if 4 < fk < 15:    s += 20
            elif 15 <= fk < 25: s += 10
            elif fk >= 25:      s -= 5
    elif is_hold:
        s += 5   # holding için N/A F/K normal

    if pddd:
        if pddd < 1.5:   s += 15
        elif pddd < 3.0: s += 8
        elif pddd > 8.0 and not is_hold: s -= 8

    if 40 < h.rsi_14 < 60:    s += 10
    elif 30 < h.rsi_14 <= 40: s += 15
    elif h.rsi_14 > 70:        s -= 10

    if 5 < h.degisim_3ay < 40:  s += 10
    elif h.degisim_3ay < 0:     s -= 5

    if 0.5 < h.hacim_anomali < 3: s += 5

    return min(max(s, 0), 100.0)

def hisse_derin_cek(ticker: str, ozet: HisseOzet) -> Optional[HisseDerin]:
    try:
        t=yf.Ticker(ticker); info=t.info; hist=t.history(period=PERIOD)
        if hist.empty or len(hist)<60: return None
        c=hist["Close"]; v=hist["Volume"]; h=hist["High"]; l=hist["Low"]
        o=hist["Open"]
        fiyat=c.iloc[-1]
        mv,ms,mh,macd_s,_ = hesapla_macd(c)
        bu,bo,ba,bp = hesapla_bollinger(c)
        sk,sd = hesapla_stochastic(h,l,c)
        at = hesapla_atr(h,l,c)
        adx_v = hesapla_adx(h,l,c)
        ob = hesapla_obv(c,v)
        de,di = hesapla_destek_direnc(c)
        fi = hesapla_fibonacci(c)
        ich = hesapla_ichimoku(h,l,c)
        sar_v, sar_y = hesapla_parabolic_sar(h,l,c)
        gc, sma50, sma200 = golden_death_cross(c)
        rsi_s = pd.Series([hesapla_rsi(c.iloc[:i+1]) for i in range(len(c))])
        ri = rsi_iraksama(c, rsi_s)
        mi = macd_iraksama(c, macd_s)
        oi = obv_iraksama(c, v)
        mf = mum_formasyonlari(o,h,l,c)
        sh = hesapla_sharpe(c)
        md = hesapla_max_drawdown(c)
        vol = round(c.pct_change().std()*(252**0.5),4)
        yillik = round(c.pct_change().mean()*252, 4)
        kf = kelly_criterion(yillik, vol)
        roe=_to_float(info.get("returnOnEquity")); roa=_to_float(info.get("returnOnAssets"))
        de2=_to_float(info.get("debtToEquity")); fcf=info.get("freeCashflow"); rg=_to_float(info.get("revenueGrowth"))
        hisse = HisseDerin(
            ticker=ticker.replace(".IS",""), isim=ozet.isim, sektor=ozet.sektor,
            fiyat=round(fiyat,2),
            degisim_1g=round((c.iloc[-1]/c.iloc[-2]-1)*100,2),
            degisim_1h=round((c.iloc[-1]/c.iloc[-5]-1)*100,2) if len(c)>=5 else None,
            degisim_6ay=round((c.iloc[-1]/c.iloc[0]-1)*100,2),
            rsi_14=hesapla_rsi(c), stoch_k=sk, stoch_d=sd,
            macd=mv, macd_sinyal=ms, macd_hist=mh,
            sma_50=round(sma50,2) if sma50 else None,
            sma_200=round(sma200,2) if sma200 else None,
            bb_ust=bu, bb_orta=bo, bb_alt=ba, bb_pct=bp,
            atr=at, volatilite=round(vol*100,2), yillik_getiri=round(yillik*100,2),
            obv_trend=ob, hacim_ort=info.get("averageVolume"),
            destek=de, direnc=di, fib=fi,
            adx=adx_v, golden_cross_durum=gc,
            ichimoku=ich, sar_deger=sar_v, sar_yon=sar_y,
            rsi_iraksama=ri, macd_iraksama=mi, obv_iraksama=oi,
            mum_formasyonlari=mf,
            fk_orani=_to_float(info.get("trailingPE")),
            pd_dd=_to_float(info.get("priceToBook")),
            temettü_verimi=round(info.get("dividendYield",0)*100,2) if info.get("dividendYield") else None,
            piyasa_degeri_m=round(info.get("marketCap",0)/1e6,0) if info.get("marketCap") else None,
            roe=round(roe*100,1) if roe else None,
            roa=round(roa*100,1) if roa else None,
            borc_ozsermaye=round(de2,2) if de2 else None,
            fcf_m=round(fcf/1e6,0) if fcf else None,
            gelir_buyume_yoy=round(rg*100,1) if rg else None,
            sharpe=sh, max_drawdown=md, kelly_f=kf,
            manipulasyon_skoru=ozet.manipulasyon_skoru,
            balon_skoru=ozet.balon_skoru,
        )
        hisse.kural_sonuc = kural_motoru_hesapla(hisse)
        return hisse
    except Exception as e:
        console.print(f"[yellow]Derin: {ticker} → {e}[/yellow]")
        return None

# ── Korelasyon Matrisi ─────────────────────────────────────────

def korelasyon_matrisi_hesapla(hisseler: list) -> pd.DataFrame:
    """Hisseler arası korelasyon. Yüksek korelasyon = az çeşitlendirme."""
    veriler = {}
    for h in hisseler:
        try:
            hist = yf.Ticker(h.ticker+".IS").history(period="3mo")
            if not hist.empty:
                veriler[h.ticker] = hist["Close"].pct_change().dropna()
        except: pass
    if len(veriler) < 2: return pd.DataFrame()
    df = pd.DataFrame(veriler).dropna()
    return df.corr().round(2)

def piyasa_ozeti_olustur(hisseler: list) -> str:
    satirlar = []
    for h in hisseler:
        kr = h.kural_sonuc
        ich = h.ichimoku or {}
        formlar = ", ".join(h.mum_formasyonlari[:3]) if h.mum_formasyonlari else "Yok"
        satirlar.append(
            f"--- {h.ticker} ({h.isim}) | {h.sektor} ---\n"
            f"  Fiyat:{h.fiyat:.2f}TL 1G:{h.degisim_1g:+.1f}% 6Ay:{h.degisim_6ay:+.1f}%\n"
            f"  KURAL MOTORU: TeknikPuan:{kr.teknik_puan} TemelPuan:{kr.temel_puan} TOPLAM:{kr.toplam_puan}/100\n"
            f"  TREND: {kr.golden_cross} | ADX:{_fmt(h.adx,'{:.1f}')} | Ichimoku:{ich.get('durum','?')} | SAR:{h.sar_yon}\n"
            f"  MUM FORMASYONLARI: {formlar}\n"
            f"  IRAKSAMA: RSI:{h.rsi_iraksama} MACD:{h.macd_iraksama} OBV:{h.obv_iraksama}\n"
            f"  MOMENTUM: RSI:{h.rsi_14} Stoch:{_fmt(h.stoch_k,'{:.1f}')} MACD:{_fmt(h.macd_hist,'{:.4f}')}\n"
            f"  BAND/SEVİYE: BB%:{_fmt(h.bb_pct,'{:.2f}')} Destek:{_fmt(h.destek,'{:.2f}')} Direnc:{_fmt(h.direnc,'{:.2f}')}\n"
            f"  Fib618:{_fmt(h.fib.get('0.618') if h.fib else None,'{:.2f}')}\n"
            f"  PORTFÖY: Sharpe:{_fmt(h.sharpe,'{:.2f}')} MaxDD:{_fmt(h.max_drawdown,'{:.1f}')}% Kelly:{_fmt(h.kelly_f,'{:.3f}')}\n"
            f"  TEMEL: FK:{_fmt(h.fk_orani,'{:.1f}')} PDDD:{_fmt(h.pd_dd,'{:.2f}')} ROE:{_fmt(h.roe,'{:.1f}')}% "
            f"ROA:{_fmt(h.roa,'{:.1f}')}% DE:{_fmt(h.borc_ozsermaye,'{:.2f}')} "
            f"FCF:{_fmt(h.fcf_m,'{:.0f}')}M YoY:{_fmt(h.gelir_buyume_yoy,'{:.1f}')}%"
        )
    return "\n".join(satirlar)

# ════════════════════════════════════════════════════════════════
# HABER KATMANI
# ════════════════════════════════════════════════════════════════

@dataclass
class Haber:
    baslik: str; kaynak: str; tarih: str; ozet: str; ilgili_hisseler: list

def ticker_tespit(metin: str) -> list:
    up=metin.upper()
    return [t.replace(".IS","") for t in BIST100_TICKERS if t.replace(".IS","") in up]

def rss_cek() -> list:
    haberler=[]
    for k in RSS_KAYNAKLARI:
        try:
            feed=feedparser.parse(k["url"])
            for e in feed.entries[:25]:
                baslik=e.get("title","")
                ozet=BeautifulSoup(e.get("summary",e.get("description","")),"html.parser").get_text()[:300]
                haberler.append(Haber(baslik=baslik,kaynak=k["isim"],
                    tarih=e.get("published","")[:16],ozet=ozet.strip(),
                    ilgili_hisseler=ticker_tespit(baslik+" "+ozet)))
        except: pass
    return haberler

def resmi_cek() -> list:
    haberler=[]
    for k in RESMI_KAYNAKLAR:
        try:
            r=requests.get(k["url"],headers=HEADERS,timeout=12)
            soup=BeautifulSoup(r.text,"html.parser")
            for satir in soup.select(k["selector"])[:15]:
                metin=satir.get_text(" ",strip=True)
                if len(metin)>20:
                    haberler.append(Haber(baslik=metin[:150],kaynak=k["isim"],
                        tarih=datetime.now().strftime("%Y-%m-%d"),
                        ozet=metin[:300],ilgili_hisseler=ticker_tespit(metin)))
        except: pass
    return haberler

def haber_ozeti(haberler: list, secili: list) -> str:
    satirlar=[]
    for ticker in secili:
        ilgili=[h for h in haberler if ticker in h.ilgili_hisseler]
        if ilgili:
            satirlar.append(f"\n[{ticker}]")
            for h in ilgili[:4]:
                satirlar.append(f"  [{h.kaynak}] {h.baslik} | {h.tarih}")
    resmi=[h for h in haberler if not h.ilgili_hisseler and h.kaynak in ("BDDK","Hazine","EPDK","KAP")]
    genel=[h for h in haberler if not h.ilgili_hisseler and h not in resmi][:8]
    if resmi:
        satirlar.append("\n[RESMİ KURUM]")
        for h in resmi[:8]: satirlar.append(f"  [{h.kaynak}] {h.baslik}")
    if genel:
        satirlar.append("\n[GENEL]")
        for h in genel: satirlar.append(f"  [{h.kaynak}] {h.baslik}")
    return "\n".join(satirlar) or "Haber alinamadi."

# ════════════════════════════════════════════════════════════════
# LLM / AJAN KATMANI
# ════════════════════════════════════════════════════════════════

class FinansalAjanlar:
    def __init__(self):
        key=os.getenv("GROQ_API_KEY")
        if not key: raise EnvironmentError("GROQ_API_KEY bulunamadi.")
        self.client=Groq(api_key=key); self.hafiza=[]

    def _llm(self, sistem, mesaj, sicaklik=0.3, max_token=2500):
        r=self.client.chat.completions.create(
            model=MODEL,temperature=sicaklik,max_tokens=max_token,
            messages=[{"role":"system","content":sistem},{"role":"user","content":mesaj}])
        return r.choices[0].message.content.strip()

    def _json(self, raw):
        if not raw: return {}
        import re as _re
        # markdown kod bloğu varsa içini al
        if "```" in raw:
            parts = raw.split("```")
            for part in parts[1::2]:
                cleaned = part[4:].strip() if part.startswith("json") else part.strip()
                try: return json.loads(cleaned)
                except: continue
        # direkt parse dene
        try: return json.loads(raw.strip())
        except: pass
        # En büyük JSON bloğunu bul
        for start_idx in [i for i, c in enumerate(raw) if c == '{']:
            depth = 0
            for j, c in enumerate(raw[start_idx:]):
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                if depth == 0:
                    candidate = raw[start_idx:start_idx+j+1]
                    try: return json.loads(candidate)
                    except: break
        # SON ÇARE: Markdown numbered list parse
        kararlar = []
        pattern = _re.compile(
            r'[*]{0,2}([A-Z]{3,6})[*]{0,2}.*?%\s*(\d+).*?(?:[Hh]edef|hedef_fiyat).*?([0-9]+[.,][0-9]+).*?(?:[Ss]top|stop_loss).*?([0-9]+[.,][0-9]+)',
            _re.DOTALL
        )
        for m in pattern.finditer(raw):
            try:
                ticker = m.group(1)
                agirlik = int(m.group(2))
                hedef = float(m.group(3).replace(',','.'))
                stop = float(m.group(4).replace(',','.'))
                if agirlik > 0 and ticker not in [k["ticker"] for k in kararlar]:
                    kararlar.append({
                        "ticker": ticker, "karar": "AL",
                        "agirlik_pct": agirlik, "hedef_fiyat": hedef,
                        "stop_loss": stop, "kural_puan": 70,
                        "kelly_f": 0.1, "gerekce": "Markdown-parse"
                    })
            except: continue
        if kararlar:
            toplam = sum(k["agirlik_pct"] for k in kararlar)
            return {"kararlar": kararlar, "strateji": "Dengeli",
                    "risk_seviyesi": "ORTA", "piyasa_gorusu": "BULLISH",
                    "nakit_orani_pct": max(10, 100 - toplam)}
        return {}


    def agent3(self, haber_oz: str, secili: list) -> dict:
        sistem="""Sen Türkiye finansal piyasaları haber analistisin.
BDDK, Hazine, EPDK, KAP duyurularına özellikle dikkat et.
SADECE JSON:
{
  "piyasa_duyarliligi": "NOTR",
  "kritik_gelismeler": ["..."],
  "sektor_haberleri": {"Bankacılık":"..."},
  "hisse_sentiment": {"THYAO":{"sentiment":"POZITIF","gerekce":"...","etki":"YUKSEK"}},
  "makro_riskler": ["..."],
  "firsatlar": ["..."]
}"""
        mesaj=(f"Tarih:{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
               f"HABERLER:\n{haber_oz}\n\n"
               f"Sentiment analizi: {', '.join(secili)}\nHaberde gecmeyen=NOTR")
        sonuc=self._json(self._llm(sistem,mesaj,0.1,2000)) or {
            "piyasa_duyarliligi":"NOTR","kritik_gelismeler":[],
            "hisse_sentiment":{},"makro_riskler":[],"firsatlar":[]}
        self.hafiza.append({"agent":"Agent3","icerik":sonuc}); return sonuc

    def agent1(self, piyasa_oz: str, sentiment: dict,
               elinen: list, bist_ozet: str, kor_ozet: str) -> str:
        sistem="""Sen kıdemli BIST100 analistisin. Deterministik kural motoru puanlarını,
tam teknik analizi (Golden/Death Cross, ADX, Ichimoku, SAR, Iraksama, Mum Formasyonları,
Sharpe, MaxDD) ve temel analizi (ROE, ROA, D/E, FCF, YoY, Kelly) sentezlersin.

Görev:
1. BIST100 genel değerlendirmesi
2. Sektör analizi + korelasyon yorumu
3. Elinen hisselerin kısa notu
4. Kural motoru puanı yüksek hisseler için detaylı analiz
5. TOP 8 hisse (kural motoru puanı + sentiment birlikte)
6. Önerilen Kelly Criterion ağırlıkları
7. Genel görünüm: GUCLU_BULLISH/BULLISH/NOTR/BEARISH/GUCLU_BEARISH
Türkçe, profesyonel yaz."""
        mesaj=(f"BIST100 GENEL:\n{bist_ozet}\n\n"
               f"KOReLASYON ÖZETİ:\n{kor_ozet}\n\n"
               f"ELİNEN:{', '.join([e['ticker'] for e in elinen[:15]])}\n\n"
               f"SEÇİLEN HİSSELER:\n{piyasa_oz}\n\n"
               f"SENTIMENT:\n{json.dumps(sentiment,ensure_ascii=False,indent=2)}")
        yanit=self._llm(sistem,mesaj,0.3,3000)
        self.hafiza.append({"agent":"Agent1","icerik":yanit}); return yanit

    def agent2(self, analiz: str, sentiment: dict,
               hisseler: list, kor_df: pd.DataFrame) -> dict:
        profil = RISK_PROFIL[RISK_MODU]
        sistem=f"""SEN BİR JSON API'SİN. YANIT OLARAK SADECE HAM JSON VER. AÇIKLAMA, GİRİŞ METNİ, MARKDOWN, NUMARA LİSTESİ YAZMA. İLK KARAKTER {{ OLMALI.

Sen deneyimli portföy yöneticisisin. Portföy: {PORTFOY_BUYUKLUGU:,} TL
Risk Modu: {RISK_MODU.upper()} — {profil['aciklama']}

KARAR KURALLARI ({RISK_MODU.upper()} MOD):
1. Kural motoru puanı < {profil['min_kural_puan']} → BEKLE/SAT (kesinlikle AL deme)
2. Stop-loss = fiyat - (2 × ATR)
3. Hedef fiyat = Fibonacci 1.272 veya 1.618 UZANTI seviyesi (HER ZAMAN giriş fiyatının ÜSTÜNDE olmalı — altında hedef koymak yasaktır)
4. Tek hisse max %{profil['max_tek_hisse']} (Kelly × {profil['kelly_carpan']:.1f})
5. Tek sektöre max %{MAX_SEKTOR_AGIRLIK}
6. Yüksek korelasyon (>0.85) olan hisseleri birlikte alma
6.5. En az 5 hisse AL kararı ver (çeşitlendirme zorunlu)
7. AL ağırlıklar toplamı MAX %{profil['max_toplam_yatirim']}
10. BANKACILIK TOPLAM MAX %30: GARAN+AKBNK+YKBNK+ISCTR ağırlıkları toplamı %30 geçemez
11. KORELASYON: 0.85+ korelasyonlu çiftlerde her birine max %12
12. GEREKCE: Her hisse için ADX değeri, Ichimoku, iraksama, formasyon yaz. 'Kural puanı yüksek' YAZMA
{"10. AGRESİF MOD: Momentum güçlü (ADX>40, SAR YUKARI) hisseler için ağırlığı Kelly maksimuma çek." if RISK_MODU=="agresif" else ""}
{"10. MUHAFAZAKÂR MOD: Sadece Ichimoku BULUT_USTU + Golden/SMA50_USTUNDE hisseler." if RISK_MODU=="muhafazakar" else ""}

YANIT SADECE JSON OLMALI, BAŞKA HİÇBİR ŞEY YAZMA:
{{
  "strateji": "...",
  "risk_seviyesi": "ORTA",
  "piyasa_gorusu": "BULLISH",
  "nakit_orani_pct": 10,
  "kararlar": [
    {{
      "ticker": "THYAO",
      "karar": "AL",
      "agirlik_pct": 18,
      "hedef_fiyat": 380.0,
      "stop_loss": 285.0,
      "kural_puan": 72.5,
      "kelly_f": 0.18,
      "gerekce": "..."
    }}
  ]
}}"""
        kor_str=""
        if not kor_df.empty:
            yuksek=[(a,b,round(float(kor_df.loc[a,b]),2))
                    for a,b in combinations(kor_df.columns,2)
                    if abs(float(kor_df.loc[a,b]))>0.8]
            if yuksek: kor_str="Yüksek korelasyon çiftleri: " + ", ".join([f"{a}-{b}:{c}" for a,b,c in yuksek[:8]])

        def _hs(h):
            kr = h.kural_sonuc
            ich = h.ichimoku or {}
            form = ",".join(h.mum_formasyonlari[:2]) if h.mum_formasyonlari else "Yok"
            ira = "RSI:" + h.rsi_iraksama[:3] + " MACD:" + h.macd_iraksama[:3] + " OBV:" + h.obv_iraksama[:3]
            adx_s  = str(round(h.adx, 0)) if h.adx else "N/A"
            sh_s   = str(round(h.sharpe, 2)) if h.sharpe else "N/A"
            kp     = str(kr.toplam_puan) if kr else "?"
            fib_s  = str(h.fib.get("0.618", "N/A")) if h.fib else "N/A"
            fib127 = str(h.fib.get("1.272", "N/A")) if h.fib else "N/A"
            fib162 = str(h.fib.get("1.618", "N/A")) if h.fib else "N/A"
            roe_s  = str(round(h.roe, 1)) + "%" if h.roe else "N/A"
            de_s   = str(round(h.borc_ozsermaye, 1)) if h.borc_ozsermaye else "N/A"
            fcf_s  = str(round(h.fcf_m, 0)) + "M" if h.fcf_m else "N/A"
            return (
                h.ticker + ":" + str(h.fiyat) + "TL"
                + " | ATR:" + str(h.atr)
                + " | Destek:" + str(h.destek)
                + " | Direnc:" + str(h.direnc)
                + " | Fib618(destek):" + fib_s + " | Fib127(hedef):" + fib127 + " | Fib162(hedef):" + fib162
                + " | Kelly:" + str(h.kelly_f)
                + " | Sharpe:" + sh_s
                + " | KuralPuan:" + kp
                + " | ADX:" + adx_s
                + " | Ichimoku:" + ich.get("durum", "?")
                + " | SAR:" + h.sar_yon
                + " | Iraksama:[" + ira + "]"
                + " | Formasyonlar:" + form
                + " | ROE:" + roe_s
                + " | DE:" + de_s
                + " | FCF:" + fcf_s
                + " | Sektor:" + h.sektor
            )
        fiyat_str = "\n".join([_hs(h) for h in hisseler])
        sent_str=json.dumps(
            {k:v.get("sentiment","N/A") for k,v in sentiment.get("hisse_sentiment",{}).items()},
            ensure_ascii=False)
        mesaj=(f"AGENT 1 ANALIZI:\n{analiz}\n\n"
               f"SENTIMENT: {sent_str}\n"
               f"Piyasa: {sentiment.get('piyasa_duyarliligi','NOTR')}\n\n"
               f"KOReLASYON: {kor_str}\n\n"
               f"HİSSE VERİLERİ:\n{fiyat_str}")
        raw=self._llm(sistem,mesaj,0.1,4000)
        portfoy = self._json(raw)
        if not portfoy or not portfoy.get("kararlar"):
            console.print(f"[yellow]⚠️  Agent2 JSON parse hatası. LLM yanıtı ({len(raw)} karakter):[/yellow]")
            console.print(raw[:500] if raw else "(boş yanıt)")
            portfoy = {"kararlar": [], "strateji": "Parse hatası", "nakit_orani_pct": 100}

        # ── Python seviyesinde kural uygulama (LLM override engeli) ──────────
        portfoy = portfoy_kurallari_uygula(portfoy, hisseler, kor_df)

        self.hafiza.append({"agent": "Agent2", "icerik": portfoy})
        return portfoy



# ════════════════════════════════════════════════════════════════
# PORTFÖY KURAL UYGULAYICI (Python seviyesinde — LLM override engeli)
# ════════════════════════════════════════════════════════════════

SEKTOR_GRUPLAR = {
    "bankacilik": {"GARAN","AKBNK","YKBNK","ISCTR","HALKB","VAKBN","ISFIN","ISMEN"},
    "holding":     {"KCHOL","SAHOL","AGHOL","DOHOL","GLYHO"},
    "gyo":         {"EKGYO","ISGYO","KLGYO"},
    "enerji":      {"TUPRS","PETKM","ODAS"},
    "telekom":     {"TCELL","TTKOM"},
}

def portfoy_kurallari_uygula(portfoy: dict, hisseler: list, kor_df) -> dict:
    """
    LLM çıktısını alıp deterministic kuralları uygular:
    1. Tek hisse max %18 (dengeli mod)
    2. Bankacılık sektörü toplamı max %30
    3. Korelasyon 0.85+ → her biri max %12
    4. Negatif Sharpe → max %5
    5. Kural puanı < 55 → kararı BEKLE'ye çevir
    6. Toplam yatırım %90'ı geçmesin, nakit en az %10
    7. Gerekçe zenginleştirme — ham teknik veri ekle
    """
    from itertools import combinations

    profil   = RISK_PROFIL[RISK_MODU]
    max_hisse = profil["max_tek_hisse"]
    min_kural = profil["min_kural_puan"]
    min_nakit = profil["min_nakit"]
    max_top   = profil["max_toplam_yatirim"]

    # Hisse lookup tabloları
    sharpe_map  = {h.ticker: h.sharpe  for h in hisseler}
    kural_map   = {h.ticker: (h.kural_sonuc.toplam_puan if h.kural_sonuc else 0) for h in hisseler}
    adx_map     = {h.ticker: h.adx     for h in hisseler}
    ich_map     = {h.ticker: (h.ichimoku or {}).get("durum","?") for h in hisseler}
    sar_map     = {h.ticker: h.sar_yon for h in hisseler}
    ira_map     = {h.ticker: (h.rsi_iraksama, h.macd_iraksama, h.obv_iraksama) for h in hisseler}
    form_map    = {h.ticker: h.mum_formasyonlari for h in hisseler}

    # Yüksek korelasyonlu çiftler
    kor_ciftler = {}
    if not kor_df.empty:
        for a, b in combinations(kor_df.columns, 2):
            try:
                v = abs(float(kor_df.loc[a, b]))
                if v >= 0.85:
                    kor_ciftler.setdefault(a, set()).add(b)
                    kor_ciftler.setdefault(b, set()).add(a)
            except: pass

    kararlar = portfoy.get("kararlar", [])

    # ── Kural 0: Hedef fiyat AL kararlarında giriş fiyatının üstünde olmalı ──
    hisse_map = {h.ticker: h for h in hisseler}
    for k in kararlar:
        if k.get("karar") == "AL":
            t = k.get("ticker","")
            h_obj = hisse_map.get(t)
            if h_obj:
                fiyat = h_obj.fiyat or 0
                hedef = k.get("hedef_fiyat") or 0
                if hedef <= fiyat and fiyat > 0:
                    fib127 = h_obj.fib.get("1.272") if h_obj.fib else None
                    k["hedef_fiyat"] = fib127 if (fib127 and fib127 > fiyat) else round(fiyat * 1.08, 2)

    # ── Kural 1: kural puanı < eşik → BEKLE ─────────────────────────────────
    for k in kararlar:
        t = k["ticker"]
        if kural_map.get(t, 0) < min_kural and k["karar"] == "AL":
            k["karar"] = "BEKLE"
            k["agirlik_pct"] = 0
            k["gerekce"] = f"Kural puanı {kural_map.get(t,0):.0f} < {min_kural} eşiği — BEKLE"

    # ── Kural 1b: VETO — Kesin düşüş trendi (LOGO tipi hatayı önler) ─────────
    #   SAR AŞAĞI + BULUT_ALTI → her iki gösterge düşüş diyor → VETO
    #   Sharpe < -1.5 + teknik_puan < 65 → kötü risk/getiri + zayıf teknik → VETO
    #   6ay < -%15 + SAR AŞAĞI → uzun süreli düşüş trendi → VETO
    teknik_map = {h.ticker: (h.kural_sonuc.teknik_puan if h.kural_sonuc else 0) for h in hisseler}
    altiay_map = {h.ticker: h.degisim_6ay for h in hisseler}

    for k in kararlar:
        if k.get("karar") != "AL":
            continue
        t   = k["ticker"]
        ich = ich_map.get(t, "?")
        sar = sar_map.get(t, "?")
        sh  = sharpe_map.get(t) or 0
        tek = teknik_map.get(t, 0)
        alt = altiay_map.get(t) or 0

        veto_sebebi = None

        if sar == "ASAGI" and ich == "BULUT_ALTI":
            veto_sebebi = f"VETO: SAR↓ + Bulut Altı — kesin düşüş trendi"
        elif sh < -1.5 and tek < 65:
            veto_sebebi = f"VETO: Sharpe:{sh:.2f} + Teknik:{tek:.0f} — kötü risk/getiri"
        elif alt < -15 and sar == "ASAGI":
            veto_sebebi = f"VETO: 6Ay:{alt:.1f}% + SAR↓ — uzun süreli düşüş"

        if veto_sebebi:
            k["karar"]      = "BEKLE"
            k["agirlik_pct"] = 0
            k["gerekce"]    = veto_sebebi

    # ── Kural 2: tek hisse max ────────────────────────────────────────────────
    for k in kararlar:
        if k["karar"] == "AL" and k.get("agirlik_pct", 0) > max_hisse:
            k["agirlik_pct"] = max_hisse

    # ── Kural 3: negatif Sharpe → max %5 ─────────────────────────────────────
    for k in kararlar:
        t = k["ticker"]
        s = sharpe_map.get(t)
        if k["karar"] == "AL" and s is not None and s < profil.get("min_sharpe", -999):
            k["agirlik_pct"] = min(k.get("agirlik_pct", 0), 5)

    # ── Kural 4: korelasyon 0.85+ → max %12 ──────────────────────────────────
    MAX_KOR = 12
    for k in kararlar:
        t = k["ticker"]
        if k["karar"] == "AL" and t in kor_ciftler:
            if k.get("agirlik_pct", 0) > MAX_KOR:
                k["agirlik_pct"] = MAX_KOR

    # ── Kural 5: bankacılık sektörü max %30 ──────────────────────────────────
    MAX_BANK = 30
    banka_top = sum(k.get("agirlik_pct",0) for k in kararlar
                    if k["karar"]=="AL" and k["ticker"] in SEKTOR_GRUPLAR["bankacilik"])
    if banka_top > MAX_BANK:
        carpan = MAX_BANK / banka_top
        for k in kararlar:
            if k["karar"]=="AL" and k["ticker"] in SEKTOR_GRUPLAR["bankacilik"]:
                k["agirlik_pct"] = round(k["agirlik_pct"] * carpan, 1)

    # ── Kural 6: toplam max %90, eksikse nakit ────────────────────────────────
    toplam_al = sum(k.get("agirlik_pct",0) for k in kararlar if k["karar"]=="AL")
    if toplam_al > max_top:
        carpan = max_top / toplam_al
        for k in kararlar:
            if k["karar"] == "AL":
                k["agirlik_pct"] = round(k["agirlik_pct"] * carpan, 1)
        toplam_al = max_top

    portfoy["nakit_orani_pct"] = max(min_nakit, round(100 - toplam_al, 1))

    # ── Kural 7: gerekçe zenginleştirme ──────────────────────────────────────
    for k in kararlar:
        if k["karar"] not in ("AL", "SAT"): continue
        t  = k["ticker"]
        adx = adx_map.get(t)
        ich = ich_map.get(t, "?")
        sar = sar_map.get(t, "?")
        kp  = kural_map.get(t, 0)
        sh  = sharpe_map.get(t)
        rsi_ira, macd_ira, obv_ira = ira_map.get(t, ("?","?","?"))
        forms = form_map.get(t, [])
        form_s = forms[0] if forms else "—"

        # gerekce zenginlestirme:
        adx_s = str(round(adx, 0)) if adx else "N/A"
        sh_s  = str(round(sh, 2))  if sh  else "N/A"
        ek = (f"KP:{kp:.0f} ADX:{adx_s} Ich:{ich} SAR:{sar} "
              f"Ira[RSI:{rsi_ira[:3]} MACD:{macd_ira[:3]} OBV:{obv_ira[:3]}] "
              f"Form:{form_s} Sharpe:{sh_s}")

        mevcut = k.get("gerekce","")
        if "KP:" not in mevcut:  # zaten zenginleştirilmemişse
            k["gerekce"] = ek + (" | " + mevcut[:30] if mevcut else "")

    portfoy["kararlar"] = kararlar
    return portfoy

# ════════════════════════════════════════════════════════════════
# GÖRSELLEŞTIRME
# ════════════════════════════════════════════════════════════════

def filtre_tablosu(ozetler, secilen, elinen):
    t=Table(title=f"🔍 Filtre — {len(ozetler)} Hisse Tarandı",border_style="cyan",show_lines=True)
    for col,kw in [("Hisse",{"style":"bold"}),("1Ay%",{"justify":"right"}),
                   ("RSI",{"justify":"right"}),("HacimX",{"justify":"right"}),
                   ("F/K",{"justify":"right"}),("Manip.",{"justify":"right"}),
                   ("Balon",{"justify":"right"}),("Kalite",{"justify":"right"}),
                   ("Sonuç",{"justify":"center"})]:
        t.add_column(col,**kw)
    for h in sorted(ozetler,key=lambda x:x.kalite_skoru,reverse=True):
        secildi=h.ticker in [s.ticker for s in secilen]
        rm="red" if h.manipulasyon_skoru>50 else "yellow" if h.manipulasyon_skoru>30 else "green"
        rb="red" if h.balon_skoru>50 else "yellow" if h.balon_skoru>30 else "green"
        t.add_row(
            h.ticker,f"{h.degisim_1ay:+.1f}%",f"{h.rsi_14:.0f}",f"{h.hacim_anomali:.1f}x",
            _fmt(h.fk_orani,"{:.1f}"),
            f"[{rm}]{h.manipulasyon_skoru:.0f}[/]",
            f"[{rb}]{h.balon_skoru:.0f}[/]",
            f"{h.kalite_skoru:.0f}",
            "[green]✓[/green]" if secildi else "[red]✗[/red]")
    console.print(t)
    ym=[h for h in elinen if h.manipulasyon_skoru>=MANIPULASYON_ESIK]
    yb=[h for h in elinen if h.balon_skoru>=BALON_ESIK and h not in ym]
    if ym: rprint(f"\n[bold red]⚠️  Manipülasyon ({len(ym)}):[/bold red] "+", ".join([f"{h.ticker}({h.manipulasyon_skoru:.0f})" for h in ym]))
    if yb: rprint(f"[bold yellow]🫧  Balon ({len(yb)}):[/bold yellow] "+", ".join([f"{h.ticker}({h.balon_skoru:.0f})" for h in yb]))

def kural_tablosu(hisseler):
    t=Table(title="⚙️  Kural Motoru Puanları",border_style="green",show_lines=True)
    for col,kw in [("Hisse",{"style":"bold"}),("Teknik",{"justify":"right"}),
                   ("Temel",{"justify":"right"}),("TOPLAM",{"justify":"right"}),
                   ("Cross",{}),("ADX",{"justify":"right"}),
                   ("Ichimoku",{}),("SAR",{"justify":"center"}),
                   ("Iraksama",{}),("Formasyonlar",{"max_width":30})]:
        t.add_column(col,**kw)
    for h in sorted(hisseler,key=lambda x:x.kural_sonuc.toplam_puan if x.kural_sonuc else 0,reverse=True):
        kr=h.kural_sonuc
        if not kr: continue
        rt="green" if kr.toplam_puan>=65 else "yellow" if kr.toplam_puan>=45 else "red"
        gc_renk="green" if "GOLDEN" in kr.golden_cross else "red" if "DEATH" in kr.golden_cross else "white"
        sar_renk="green" if kr.sar_yon=="YUKARI" else "red"
        formlar=", ".join(kr.mum_formasyonlar[:2]) if kr.mum_formasyonlar else "—"
        ira_str=f"R:{kr.rsi_iraksama[:3]} M:{kr.macd_iraksama[:3]} O:{kr.obv_iraksama[:3]}"
        t.add_row(
            h.ticker,
            f"{kr.teknik_puan:.0f}",f"{kr.temel_puan:.0f}",
            f"[{rt}]{kr.toplam_puan:.0f}[/]",
            f"[{gc_renk}]{kr.golden_cross[:10]}[/]",
            f"{kr.adx:.0f}",
            kr.ichimoku_durum[:12],
            f"[{sar_renk}]{kr.sar_yon}[/]",
            ira_str, formlar)
    console.print(t)

def derin_tablo(hisseler):
    t=Table(title="📊 Seçilen Hisseler — Tam Teknik+Temel",border_style="blue",show_lines=True)
    for col,kw in [("Hisse",{"style":"bold cyan"}),("Fiyat",{"justify":"right"}),
                   ("6Ay%",{"justify":"right"}),("Sharpe",{"justify":"right"}),
                   ("MaxDD%",{"justify":"right"}),("Kelly",{"justify":"right"}),
                   ("ROE%",{"justify":"right"}),("D/E",{"justify":"right"}),
                   ("FCF(M)",{"justify":"right"})]:
        t.add_column(col,**kw)
    for h in hisseler:
        r6="green" if h.degisim_6ay>=0 else "red"
        rs=f"{h.sharpe:.2f}" if h.sharpe else "N/A"
        rsh="green" if h.sharpe and h.sharpe>1 else "red" if h.sharpe and h.sharpe<0 else "white"
        t.add_row(
            h.ticker,f"{h.fiyat:.2f}",
            f"[{r6}]{h.degisim_6ay:+.1f}%[/]",
            f"[{rsh}]{rs}[/]",
            f"{h.max_drawdown:.1f}%" if h.max_drawdown else "N/A",
            f"{h.kelly_f:.3f}" if h.kelly_f else "N/A",
            f"{h.roe:.1f}%" if h.roe else "N/A",
            f"{h.borc_ozsermaye:.2f}" if h.borc_ozsermaye else "N/A",
            f"{h.fcf_m:,.0f}" if h.fcf_m else "N/A")
    console.print(t)

def sentiment_goster(sentiment, hisseler):
    t=Table(title="📰 Agent 3 — Türkiye Sentiment",border_style="magenta",show_lines=True)
    t.add_column("Hisse",style="bold"); t.add_column("Sentiment",justify="center")
    t.add_column("Etki",justify="center"); t.add_column("Gerekçe",max_width=55)
    renk={"POZITIF":"green","NEGATIF":"red","NOTR":"yellow",
          "POZİTİF":"green","NEGATİF":"red","NÖTR":"yellow"}
    hs=sentiment.get("hisse_sentiment",{})
    for h in hisseler:
        v=hs.get(h.ticker,{}); s=v.get("sentiment","NOTR")
        t.add_row(h.ticker,f"[{renk.get(s,'white')}]{s}[/]",
                  v.get("etki","—"),v.get("gerekce","")[:70])
    console.print(t)
    for baslik,alan,rnk in [("⚡ Kritik","kritik_gelismeler","magenta"),
                             ("⚠️  Makro","makro_riskler","red"),
                             ("💡 Fırsat","firsatlar","green")]:
        items=sentiment.get(alan,[])
        if items:
            rprint(f"\n[bold {rnk}]{baslik}:[/bold {rnk}]")
            for item in items: rprint(f"  • {item}")


# ════════════════════════════════════════════════════════════════
# GÜNLÜK P&L TAKİP SİSTEMİ
# ════════════════════════════════════════════════════════════════

def portfoy_kaydet(portfoy: dict, hisseler: list):
    """Güncel portföy pozisyonlarını dosyaya kaydeder."""
    fiyat_map = {h.ticker: h.fiyat for h in hisseler}
    kayit = {
        "tarih": datetime.now().isoformat(),
        "pozisyonlar": {}
    }
    for k in portfoy.get("kararlar", []):
        if k["karar"] == "AL":
            ticker = k["ticker"]
            agirlik = k.get("agirlik_pct", 0)
            fiyat = fiyat_map.get(ticker, 0)
            tutar = PORTFOY_BUYUKLUGU * agirlik / 100
            adet = round(tutar / fiyat, 2) if fiyat > 0 else 0
            kayit["pozisyonlar"][ticker] = {
                "agirlik_pct": agirlik,
                "giris_fiyati": fiyat,
                "guncel_fiyat": fiyat,
                "adet": adet,
                "tutar_tl": round(tutar, 2),
                "hedef": k.get("hedef_fiyat"),
                "stop": k.get("stop_loss"),
                "tarih": datetime.now().strftime("%Y-%m-%d"),
            }
    # Mevcut dosyayı önceki gün olarak sakla
    onceki_dosya2 = PORTFOY_KAYIT_DOSYA.replace(".json","_prev.json")
    if os.path.exists(PORTFOY_KAYIT_DOSYA):
        try:
            import shutil
            shutil.copy(PORTFOY_KAYIT_DOSYA, onceki_dosya2)
        except: pass
    with open(PORTFOY_KAYIT_DOSYA, "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False, indent=2)
    return kayit


def pnl_hesapla_goster(hisseler: list):
    """
    Kaydedilmiş portföy ile güncel fiyatları karşılaştırır.
    Günlük / toplam P&L gösterir.
    """
    if not os.path.exists(PORTFOY_KAYIT_DOSYA):
        rprint("[yellow]📋 Kayıtlı portföy bulunamadı — ilk çalıştırma.[/yellow]")
        return

    with open(PORTFOY_KAYIT_DOSYA, encoding="utf-8") as f:
        kayit = json.load(f)

    if not kayit.get("pozisyonlar"):
        return

    fiyat_map = {h.ticker: h.fiyat for h in hisseler}
    giris_tarihi = kayit.get("tarih", "")[:10]
    bugun = datetime.now().strftime("%Y-%m-%d")

    t = Table(title=f"📈 Portföy P&L — Giriş: {giris_tarihi} | Bugün: {bugun}",
              border_style="green", show_lines=True)
    # Onceki gun fiyatlari
    onceki_fiyat = {}
    onceki_dosya = PORTFOY_KAYIT_DOSYA.replace(".json","_prev.json")
    if os.path.exists(onceki_dosya):
        try:
            with open(onceki_dosya, encoding="utf-8") as f2:
                prev = json.load(f2)
                onceki_fiyat = {t: p.get("guncel_fiyat", p.get("giris_fiyati",0))
                                for t, p in prev.get("pozisyonlar",{}).items()}
        except: pass

    for col, kw in [
        ("Hisse",    {"style":"bold"}),
        ("Giriş ₺",  {"justify":"right"}),
        ("Güncel ₺", {"justify":"right"}),
        ("Günlük%",  {"justify":"right"}),
        ("Adet",     {"justify":"right"}),
        ("Tutar ₺",  {"justify":"right"}),
        ("P&L ₺",    {"justify":"right"}),
        ("P&L %",    {"justify":"right"}),
        ("Hedef",    {"justify":"right"}),
        ("Stop",     {"justify":"right"}),
        ("Durum",    {"justify":"center"}),
    ]:
        t.add_column(col, **kw)

    toplam_giris = 0; toplam_guncel = 0; toplam_pnl = 0
    for ticker, poz in kayit["pozisyonlar"].items():
        giris  = poz["giris_fiyati"]
        adet   = poz["adet"]
        hedef  = poz.get("hedef")
        stop   = poz.get("stop")
        guncel = fiyat_map.get(ticker, giris)  # güncel fiyat yoksa giriş fiyatı

        giris_tutar  = giris * adet
        guncel_tutar = guncel * adet
        pnl_tl  = guncel_tutar - giris_tutar
        pnl_pct = (guncel / giris - 1) * 100 if giris > 0 else 0

        toplam_giris  += giris_tutar
        toplam_guncel += guncel_tutar
        toplam_pnl    += pnl_tl

        # Durum belirleme
        if stop and guncel <= stop:
            durum = "[bold red]⛔ STOP[/bold red]"
        elif hedef and guncel >= hedef:
            durum = "[bold green]🎯 HEDEF[/bold green]"
        elif pnl_pct > 5:
            durum = "[green]↑ KAR[/green]"
        elif pnl_pct < -3:
            durum = "[red]↓ ZARAR[/red]"
        else:
            durum = "[yellow]→ BEKLE[/yellow]"

        r = "green" if pnl_tl >= 0 else "red"
        prev_g = onceki_fiyat.get(ticker, 0)
        if prev_g and prev_g > 0 and prev_g != guncel:
            gp = (guncel/prev_g - 1)*100
            rg = "green" if gp >= 0 else "red"
            gunluk_s = f"[{rg}]{gp:+.1f}%[/]"
        else:
            gunluk_s = "—"
        t.add_row(
            ticker,
            f"{giris:.2f}", f"{guncel:.2f}",
            gunluk_s,
            f"{adet:.1f}",
            f"{guncel_tutar:,.0f}",
            f"[{r}]{pnl_tl:+,.0f}[/]",
            f"[{r}]{pnl_pct:+.1f}%[/]",
            f"{hedef:.2f}" if hedef else "—",
            f"{stop:.2f}" if stop else "—",
            durum,
        )

    console.print(t)
    genel_r = "green" if toplam_pnl >= 0 else "red"
    genel_pct = (toplam_guncel/toplam_giris - 1)*100 if toplam_giris > 0 else 0
    console.print()
    rprint("[bold]💰 PORTFÖY ÖZET:[/bold]")
    rprint(f"  Başlangıç:  {toplam_giris:>12,.0f} ₺")
    rprint(f"  Güncel:     {toplam_guncel:>12,.0f} ₺")
    rprint(f"  [{genel_r}]Toplam P&L: {toplam_pnl:>+12,.0f} ₺  ({genel_pct:+.2f}%)[/{genel_r}]")

    # Stop uyarıları
    uyarilar = [(t, p) for t, p in kayit["pozisyonlar"].items()
                if p.get("stop") and fiyat_map.get(t, 999) <= p["stop"]]
    if uyarilar:
        console.print()
        rprint("[bold red]⛔ STOP-LOSS UYARISI:[/bold red]")
        for tick, poz in uyarilar:
            rprint(f"  {tick}: güncel {fiyat_map.get(tick,0):.2f} ≤ stop {poz['stop']:.2f} → SATMANIZ ÖNERİLİR")


def risk_profili_goster():
    """Aktif risk profili bilgisini gösterir."""
    profil = RISK_PROFIL[RISK_MODU]
    renk = {"muhafazakar":"blue","dengeli":"green","agresif":"red"}[RISK_MODU]
    rprint(f"\n[bold {renk}]⚙️  Aktif Risk Modu: {RISK_MODU.upper()}[/bold {renk}]")
    rprint(f"  {profil['aciklama']}")
    rprint(f"  Min kural puanı: {profil['min_kural_puan']} | "
           f"Max tek hisse: %{profil['max_tek_hisse']} | "
           f"Min nakit: %{profil['min_nakit']}")

def portfoy_goster(portfoy, hisseler, sentiment):
    fiyat_map={h.ticker:h.fiyat for h in hisseler}
    emo={"POZITIF":"🟢","NEGATIF":"🔴","NOTR":"🟡","POZİTİF":"🟢","NEGATİF":"🔴","NÖTR":"🟡"}
    hs=sentiment.get("hisse_sentiment",{})
    kr={"AL":"bold green","SAT":"bold red","TUT":"bold yellow","BEKLE":"bold dim"}
    t=Table(title=f"💼 Portföy — {PORTFOY_BUYUKLUGU:,} TL",border_style="gold1",show_lines=True)
    for col,kw in [("Hisse",{"style":"bold"}),("Karar",{"justify":"center"}),
                   ("Sent.",{"justify":"center"}),("Ağırlık",{"justify":"right"}),
                   ("Tutar ₺",{"justify":"right"}),("Hedef ₺",{"justify":"right"}),
                   ("Stop-Loss",{"justify":"right"}),("Upside",{"justify":"right"}),
                   ("Kelly",{"justify":"right"}),("Kural",{"justify":"right"}),
                   ("Gerekçe",{"max_width":28})]:
        t.add_column(col,**kw)
    for k in sorted(portfoy.get("kararlar",[]),key=lambda x:x.get("agirlik_pct",0),reverse=True):
        ticker=k["ticker"]; karar=k["karar"]; ag=k.get("agirlik_pct",0)
        guncel=fiyat_map.get(ticker,0); hedef=k.get("hedef_fiyat"); stop=k.get("stop_loss")
        upside=round((hedef/guncel-1)*100,1) if hedef and guncel else None
        sv=hs.get(ticker,{}).get("sentiment","NOTR")
        t.add_row(
            ticker,f"[{kr.get(karar,'white')}]{karar}[/]",emo.get(sv,"🟡"),
            f"%{ag}" if ag else "—",
            f"{PORTFOY_BUYUKLUGU*ag/100:,.0f}" if ag else "—",
            f"{hedef:.2f}" if hedef else "—",f"{stop:.2f}" if stop else "—",
            (f"[green]+{upside}%[/]" if upside and upside>0 else f"[red]{upside}%[/]" if upside else "—"),
            f"{k.get('kelly_f','—')}",f"{k.get('kural_puan','—')}",
            k.get("gerekce","")[:55])
    console.print(t)
    toplam_al=sum(k.get("agirlik_pct",0) for k in portfoy.get("kararlar",[]) if k["karar"]=="AL")
    nakit=portfoy.get("nakit_orani_pct",100-toplam_al)
    rprint(f"\n[bold]Strateji:[/] {portfoy.get('strateji','')}")
    rprint(f"[bold]Risk:[/] {portfoy.get('risk_seviyesi')} | [bold]Görüş:[/] {portfoy.get('piyasa_gorusu')}")
    rprint(f"[bold]Yatırım:[/] %{round(toplam_al,1)} → {PORTFOY_BUYUKLUGU*toplam_al/100:,.0f} ₺")
    rprint(f"[bold]Nakit:[/]   %{nakit} → {PORTFOY_BUYUKLUGU*nakit/100:,.0f} ₺")

# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    console.rule("[bold blue]BIST100 AI AJAN SİSTEMİ v4.0[/bold blue]")
    console.print(f"Tarih:{datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                  f"Model:{MODEL} | Hisse:{len(BIST100_TICKERS)}")
    risk_profili_goster()
    console.print()

    # ── 1. Filtre Agent ──────────────────────────────────────
    console.rule("[bold cyan]🔍 FİLTRE AGENT[/bold cyan]")
    ozetler=[]
    with Progress(SpinnerColumn(),TextColumn("{task.description}"),console=console) as prog:
        task=prog.add_task("Taranıyor...",total=len(BIST100_TICKERS))
        for ticker in BIST100_TICKERS:
            prog.update(task,description=f"Tarıyor: {ticker}")
            ozet=hisse_ozet_cek(ticker)
            if ozet:
                ozet.manipulasyon_skoru=manipulasyon_skoru_hesapla(ozet)
                ozet.balon_skoru=balon_skoru_hesapla(ozet)
                ozet.kalite_skoru=kalite_skoru_hesapla(ozet)
                if ozet.manipulasyon_skoru>=MANIPULASYON_ESIK: ozet.kalite_skoru=0
                elif ozet.balon_skoru>=BALON_ESIK: ozet.kalite_skoru=max(0,ozet.kalite_skoru-40)
                ozetler.append(ozet)
            time.sleep(0.2); prog.advance(task)

    ozetler.sort(key=lambda x:x.kalite_skoru,reverse=True)
    # Zorunlu geçiş hisseleri — manipülasyon/balon skoru ne olursa olsun dahil et
    zorunlu_oz  = [o for o in ozetler if _is_zorunlu(o.ticker)]
    normal_oz   = [o for o in ozetler if not _is_zorunlu(o.ticker)
                   and o.manipulasyon_skoru < MANIPULASYON_ESIK
                   and o.balon_skoru < BALON_ESIK]
    # Zorunlular önce, sonra kalite sıralı normal hisseler — toplam FILTRE_LIMIT
    kalan_slot  = FILTRE_LIMIT - len(zorunlu_oz)
    secilen_oz  = zorunlu_oz + normal_oz[:max(kalan_slot, 0)]
    elinen_oz   = [o for o in ozetler if o not in secilen_oz]

    pozitif=sum(1 for o in ozetler if o.degisim_3ay>0)
    bist_ozet=(f"BIST100 Özet ({len(ozetler)} hisse tarandı):\n"
               f"- Pozitif 3Ay: {pozitif}/{len(ozetler)} (%{pozitif/max(len(ozetler),1)*100:.0f})\n"
               f"- Ort 3Ay getiri: %{sum(o.degisim_3ay for o in ozetler)/max(len(ozetler),1):.1f}\n"
               f"- Manipülasyon şüphesi: {sum(1 for o in ozetler if o.manipulasyon_skoru>=MANIPULASYON_ESIK)}\n"
               f"- Balon şüphesi: {sum(1 for o in ozetler if o.balon_skoru>=BALON_ESIK)}\n"
               f"- Derin analize: {len(secilen_oz)}")

    console.print(f"\n[green]✓ {len(ozetler)} tarandı | {len(secilen_oz)} seçildi | {len(elinen_oz)} elindi[/green]\n")
    filtre_tablosu(ozetler,secilen_oz,elinen_oz)

    # ── 2. Derin Veri + Kural Motoru ────────────────────────
    console.print("\n[bold cyan]📡 Derin veri + Kural Motoru çalışıyor...[/bold cyan]")
    secilen_map={o.ticker+".IS":o for o in secilen_oz}
    derin=[]
    with Progress(SpinnerColumn(),TextColumn("{task.description}"),console=console) as prog:
        task=prog.add_task("Çekiliyor...",total=len(secilen_oz))
        for ticker_is,ozet in secilen_map.items():
            prog.update(task,description=f"Derin: {ticker_is}")
            d=hisse_derin_cek(ticker_is,ozet)
            if d: derin.append(d)
            time.sleep(0.3); prog.advance(task)

    console.print(f"\n[green]✓ {len(derin)} hisse hazır.[/green]\n")
    kural_tablosu(derin)
    derin_tablo(derin)

    # ── Korelasyon ──────────────────────────────────────────
    console.print("\n[cyan]📐 Korelasyon matrisi hesaplanıyor...[/cyan]")
    with console.status("[cyan]Korelasyon...[/cyan]"):
        kor_df=korelasyon_matrisi_hesapla(derin)
    if not kor_df.empty:
        yuksek=[(a,b,round(float(kor_df.loc[a,b]),2))
                for a,b in combinations(kor_df.columns,2)
                if abs(float(kor_df.loc[a,b]))>0.8]
        kor_ozet=f"Yüksek korelasyon (>0.8): " + (", ".join([f"{a}-{b}:{c}" for a,b,c in yuksek[:8]]) if yuksek else "Yok")
    else: kor_ozet="Korelasyon hesaplanamadı"
    rprint(f"\n[cyan]{kor_ozet}[/cyan]")

    # ── 3. Agent 3 ───────────────────────────────────────────
    console.print("\n"); console.rule("[bold magenta]🔴 AGENT 3 — Haber & Sentiment[/bold magenta]")
    with console.status("[magenta]Haberler...[/magenta]"):
        tum_h=rss_cek()+resmi_cek()
    console.print(f"[green]✓ {len(tum_h)} haber[/green]")
    secili=[h.ticker for h in derin]
    haber_oz=haber_ozeti(tum_h,secili)
    ajanlar=FinansalAjanlar()
    with console.status("[magenta]Agent 3...[/magenta]"):
        sentiment=ajanlar.agent3(haber_oz,secili)
    sentiment_goster(sentiment,derin)

    # ── 4. Agent 1 ───────────────────────────────────────────
    console.print("\n"); console.rule("[bold blue]🔵 AGENT 1 — Piyasa Analisti[/bold blue]")
    piyasa_oz=piyasa_ozeti_olustur(derin)
    def elenme_sebebi(o):
        if o.manipulasyon_skoru >= MANIPULASYON_ESIK:
            return f"Manipülasyon şüphesi (hacim anomalisi:{o.hacim_anomali:.1f}x, RSI:{o.rsi_14:.0f}, 1ay:{o.degisim_1ay:+.0f}%)"
        if o.balon_skoru >= BALON_ESIK:
            fk = _to_float(o.fk_orani)
            return f"Balon şüphesi (FK:{fk:.0f if fk else 'N/A'}, balon_puan:{o.balon_skoru:.0f})"
        return f"Filtre limit ({FILTRE_LIMIT}) aşıldı — kalite_puan:{o.kalite_skoru:.0f}"

    elinen_bilgi=[{
        "ticker": o.ticker,
        "m_skor": o.manipulasyon_skoru,
        "b_skor": o.balon_skoru,
        "sebep":  elenme_sebebi(o),
    } for o in elinen_oz]
    with console.status("[blue]Agent 1...[/blue]"):
        analiz=ajanlar.agent1(piyasa_oz,sentiment,elinen_bilgi,bist_ozet,kor_ozet)
    console.print(Panel(analiz,title="Agent 1 — Analiz",border_style="blue",padding=(1,2)))

    # ── 5. Agent 2 ───────────────────────────────────────────
    console.print("\n"); console.rule("[bold gold1]🟡 AGENT 2 — Portföy Yöneticisi[/bold gold1]")
    with console.status("[gold1]Agent 2...[/gold1]"):
        portfoy=ajanlar.agent2(analiz,sentiment,derin,kor_df)
    portfoy_goster(portfoy,derin,sentiment)

    # ── 5b. Portföy pozisyonlarını kaydet ────────────────────
    portfoy_kaydet(portfoy, derin)
    console.print(f"[green]✓ Pozisyonlar kaydedildi → {PORTFOY_KAYIT_DOSYA}[/green]")

    # ── 5c. Önceki portföy varsa P&L göster ──────────────────
    console.print("\n"); console.rule("[bold green]📊 PORTFÖY P&L TAKİBİ[/bold green]")
    pnl_hesapla_goster(derin)

    # ── 6. Kaydet ────────────────────────────────────────────
    cikti={"tarih":datetime.now().isoformat(),"bist_ozet":bist_ozet,
           "kural_motoru":[{"ticker":h.ticker,**vars(h.kural_sonuc)} for h in derin if h.kural_sonuc],
           "hisseler":[{k:v for k,v in vars(h).items() if k!="kural_sonuc"} for h in derin],
           "agent3":sentiment,"agent1":analiz,"agent2":portfoy,
           "elinen":[{"ticker":o.ticker,"m":o.manipulasyon_skoru,"b":o.balon_skoru} for o in elinen_oz],
           "haberler":[vars(h) for h in tum_h[:50]]}
    dosya=f"bist_rapor_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(dosya,"w",encoding="utf-8") as f:
        json.dump(cikti,f,ensure_ascii=False,indent=2,default=str)
    console.print(f"\n[green]✓ Rapor → {dosya}[/green]")
    console.rule("[bold]✅ Tamamlandı[/bold]")

if __name__=="__main__":
    main()
