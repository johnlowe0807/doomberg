"""Offline market and rate identifiers shared by discovery and views."""
BOARDS = {
    "WEI": [("^GSPC", "S&P 500"), ("^DJI", "Dow Jones"), ("^IXIC", "Nasdaq Comp"), ("^RUT", "Russell 2000"),
            ("^VIX", "VIX"), ("^GSPTSE", "TSX"), ("^BVSP", "Bovespa"), ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"),
            ("^FCHI", "CAC 40"), ("^STOXX50E", "Euro Stoxx 50"), ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"),
            ("000001.SS", "Shanghai"), ("^KS11", "KOSPI"), ("^AXJO", "ASX 200"), ("^BSESN", "Sensex")],
    "CMDTY": [("CL=F", "WTI Crude"), ("BZ=F", "Brent"), ("NG=F", "Nat Gas"), ("RB=F", "RBOB Gas"), ("HO=F", "Heating Oil"),
              ("GC=F", "Gold"), ("SI=F", "Silver"), ("HG=F", "Copper"), ("PL=F", "Platinum"), ("PA=F", "Palladium"),
              ("ZC=F", "Corn"), ("ZW=F", "Wheat"), ("ZS=F", "Soybeans"), ("KC=F", "Coffee"), ("SB=F", "Sugar"),
              ("CC=F", "Cocoa"), ("CT=F", "Cotton"), ("LE=F", "Live Cattle"),
              ("ALI=F", "Aluminum"), ("LBR=F", "Lumber"), ("KE=F", "KC HRW Wheat"), ("ZO=F", "Oats"),
              ("ZR=F", "Rough Rice"), ("ZM=F", "Soybean Meal"), ("ZL=F", "Soybean Oil"),
              ("GF=F", "Feeder Cattle"), ("HE=F", "Lean Hogs"), ("OJ=F", "Orange Juice")],
    "CRYPTO": [(f"{c}-USD", c) for c in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "BCH")],
    "FXL": [("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("JPY=X", "USD/JPY"), ("CHF=X", "USD/CHF"),
            ("AUDUSD=X", "AUD/USD"), ("CAD=X", "USD/CAD"), ("NZDUSD=X", "NZD/USD"), ("CNY=X", "USD/CNY"),
            ("MXN=X", "USD/MXN"), ("DX-Y.NYB", "Dollar Index")],
    "MOSTU": [(s, s) for s in "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V LLY UNH XOM MA COST HD PG JNJ ABBV NFLX "
              "CRM AMD ORCL BAC KO PEP MRK WMT ADBE CSCO INTC QCOM TXN MU PLTR UBER COIN SMCI BA DIS".split()],
}
TAPE = [("^GSPC", "SPX"), ("^DJI", "INDU"), ("^IXIC", "CCMP"), ("^RUT", "RTY"), ("^VIX", "VIX"), ("CL=F", "CL1"),
        ("GC=F", "GOLD"), ("EURUSD=X", "EUR"), ("BTC-USD", "BTC")]


TENORS = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"), ("2Y", "DGS2"), ("3Y", "DGS3"),
          ("5Y", "DGS5"), ("7Y", "DGS7"), ("10Y", "DGS10"), ("20Y", "DGS20"), ("30Y", "DGS30")]
