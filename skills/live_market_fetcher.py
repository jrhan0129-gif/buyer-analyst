import yfinance as yf
import sys
import pandas as pd

def fetch_live_data(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        info = stock.info
        
        print(f"========== 【{ticker_symbol}】 实时市场快照 ==========")
        print(f"公司名称: {info.get('shortName', 'N/A')}")
        print(f"当前价格: {info.get('currentPrice', 'N/A')} {info.get('currency', '')}")
        print(f"52周高点: {info.get('fiftyTwoWeekHigh', 'N/A')} | 52周低点: {info.get('fiftyTwoWeekLow', 'N/A')}")
        print(f"远期市盈率 (Forward PE): {info.get('forwardPE', 'N/A')}")
        print(f"空头占比 (Short % of Float): {info.get('shortPercentOfFloat', 'N/A')}")
        print(f"机构持仓比例: {info.get('heldPercentInstitutions', 'N/A')}")
        
        # 抓取最近一个月的趋势，判断资金动能
        hist = stock.history(period="1mo")
        if not hist.empty:
            start_price = hist['Close'].iloc[0]
            end_price = hist['Close'].iloc[-1]
            trend = ((end_price - start_price) / start_price) * 100
            print(f"\n近一月股价动能: {'🟢 上涨' if trend > 0 else '🔴 下跌'} {trend:.2f}%")
        
    except Exception as e:
        print(f"⚠️ 无法获取 {ticker_symbol} 的数据: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python live_market_fetcher.py <Ticker1> [Ticker2 ...]")
    else:
        for ticker in sys.argv[1:]:
            fetch_live_data(ticker)
