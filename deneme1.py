python -c "
import yfinance as yf, warnings
warnings.filterwarnings('ignore')
for t in ['DX=F', 'DX-Y.NYB', 'UUP']:
    df = yf.Ticker(t).history(period='30d', interval='1d')
    if df is not None and len(df) > 0:
        print(f'OK: {t} son={df[\"Close\"].iloc[-1]:.2f} n={len(df)}')
    else:
        print(f'BOŞ: {t}')
"