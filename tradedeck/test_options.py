import pandas as pd

def test_fyers():
    url = "https://public.fyers.in/sym_details/NSE_FO.csv"
    df = pd.read_csv(url, names=[
        "FyToken", "SymbolDetails", "ExchangeInstrumentId", "MinimumLotSize",
        "TickSize", "ISIN", "TradingSession", "LastUpdateDate", "ExpiryDate",
        "SymbolTicker", "Exchange", "Segment", "ScripCode", "UnderlyingScripCode",
        "StrikePrice", "OptionType", "UnderlyingFyToken"
    ])
    
    nifty = df[df["UnderlyingScripCode"] == "NIFTY"]
    opts = nifty[(nifty["OptionType"] == "CE") | (nifty["OptionType"] == "PE")]
    
    with open("nifty_options_sample.txt", "w", encoding="utf-8") as f:
        f.write(f"Total Nifty Options: {len(opts)}\n")
        f.write("Sample Nifty Options:\n")
        for sym in opts["SymbolTicker"].head(10).tolist():
            f.write(f"{sym}\n")

if __name__ == "__main__":
    test_fyers()
