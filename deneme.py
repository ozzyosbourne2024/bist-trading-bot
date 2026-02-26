import yfinance as yf

h = yf.Ticker("XU100.IS").history(period="3mo", auto_adjust=True)
print("empty:", h.empty)
print("len:", len(h))

# _fiyat_cek mantığı
sonuc = h if not h.empty else None
print("sonuc None mu:", sonuc is None)