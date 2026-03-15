import json
from pathlib import Path
from datetime import datetime

LOG_DOSYA = "gelistirme_log.json"

log = []
if Path(LOG_DOSYA).exists():
    try:
        log = json.loads(Path(LOG_DOSYA).read_text(encoding="utf-8"))
    except:
        pass

yeni = [
    {
        "id": f"D20260315_001",
        "tarih": datetime.now().strftime("%Y-%m-%d"),
        "kaynak": "kullanici_analiz",
        "oneri": "KAP entegrasyonu: bilanço, temettü, önemli sözleşme haberlerini otomatik çek ve hisse bazlı sentiment'e ekle",
        "durum": "BEKLIYOR",
        "uygulama_tarihi": None,
    },
    {
        "id": f"D20260315_002",
        "tarih": datetime.now().strftime("%Y-%m-%d"),
        "kaynak": "kullanici_analiz",
        "oneri": "Haber-hisse eşleştirme iyileştirmesi: Agent3 haber gerekçelerini boş bırakıyor, LLM prompt'u güçlendir",
        "durum": "BEKLIYOR",
        "uygulama_tarihi": None,
    },
    {
        "id": f"D20260315_003",
        "tarih": datetime.now().strftime("%Y-%m-%d"),
        "kaynak": "kullanici_analiz",
        "oneri": "Altın-DXY ters korelasyon takibi: DXY trendi (yükselen/düşen) altın alarm sinyaline ek faktör olarak ekle",
        "durum": "BEKLIYOR",
        "uygulama_tarihi": None,
    },
]

mevcut_ids = {g["id"] for g in log}
eklendi = []
for g in yeni:
    if g["id"] not in mevcut_ids:
        log.append(g)
        eklendi.append(g["id"])

Path(LOG_DOSYA).write_text(
    json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"Eklendi: {eklendi}")
for g in log:
    print(f"  [{g['durum'][:1]}] {g['id']}: {g['oneri'][:65]}")
