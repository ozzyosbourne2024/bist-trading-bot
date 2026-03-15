"""
Gelistirme logunu guncelle — stop-loss ATR×1.5 YAPILDI olarak isaretle
"""
import json
from pathlib import Path
from datetime import datetime

LOG_DOSYA = "gelistirme_log.json"

if not Path(LOG_DOSYA).exists():
    print("Log dosyasi bulunamadi")
    exit()

log = json.loads(Path(LOG_DOSYA).read_text(encoding="utf-8"))

guncellendi = []
for g in log:
    oneri = g.get("oneri", "").lower()
    if g["durum"] == "BEKLIYOR" and any(x in oneri for x in [
        "stop", "atr", "stop-loss", "stop loss"
    ]):
        g["durum"] = "YAPILDI"
        g["uygulama_tarihi"] = datetime.now().strftime("%Y-%m-%d")
        g["uygulama_notu"] = "ATR x2 → ATR x1.5, fallback %93, tavan %88"
        guncellendi.append(g["id"])

Path(LOG_DOSYA).write_text(
    json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
)

print(f"Guncellendi: {guncellendi}")
for g in log:
    print(f"  [{g['durum']}] {g['id']}: {g['oneri'][:60]}")
