import json
from pathlib import Path

parcalar = []

try:
    log = json.loads(Path('bist_alarm_log.json').read_text(encoding='utf-8'))
    son = log[-1] if isinstance(log, list) else log
    parcalar.append(f"BIST SISTEM — {son['tarih']}")
    parcalar.append(f"BIST: {son['skor']}/5 — {son['karar']}")
    for k, v in son.get('sinyaller', {}).items():
        parcalar.append(f"  {k}: {v['detay']}")
except Exception as e:
    parcalar.append(f"bist_alarm hata: {e}")

try:
    poz = json.loads(Path('portfoy_pozisyonlar.json').read_text(encoding='utf-8'))
    parcalar.append("\nPORTFOY:")
    for k, v in poz.get('pozisyonlar', {}).items():
        parcalar.append(f"  {k}: giris:{v.get('giris_fiyati','?')} hedef:{v.get('hedef','?')} stop:{v.get('stop','?')}")
except Exception as e:
    parcalar.append(f"portfoy hata: {e}")

metin = "\n".join(parcalar)
Path('son_mesaj.txt').write_text(metin, encoding='utf-8')
print("son_mesaj.txt olusturuldu")
print(metin[:300])
