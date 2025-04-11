import requests
import time
import schedule
import json
import os
from datetime import datetime
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()


# ✅ 改為從環境變數讀取 Token 與 Chat ID
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("❗ 請設定環境變數 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID")
    exit(1)

ALLOWED_CHAT_ID = TELEGRAM_CHAT_ID  # 限制 /top 指令來源

TEST_MODE = True
price_5min_ago = defaultdict(float)
price_15min_ago = defaultdict(float)
bybit_extra_symbols = set()

BINANCE_SYMBOL_FILE = "binance_symbols.json"
BYBIT_SYMBOL_FILE = "bybit_symbols.json"
UPBIT_SYMBOL_FILE = "upbit_symbols.json"
last_update_id = 0  # 用於 /top 指令追蹤

def send_telegram_message(message, chat_id=TELEGRAM_CHAT_ID):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message
    }
    try:
        res = requests.post(url, data=data)
        if res.status_code != 200:
            print("❗Telegram 發送失敗：", res.text)
    except Exception as e:
        print("❗Telegram 發送錯誤：", e)
def get_binance_symbols():
    res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    return [s['symbol'] for s in res.json()['symbols']
            if s['contractType'] == 'PERPETUAL' and s['quoteAsset'] == 'USDT']

def get_bybit_symbols():
    res = requests.get("https://api.bybit.com/v5/market/tickers?category=linear")
    return [item['symbol'] for item in res.json()['result']['list']
            if item['symbol'].endswith("USDT")]

def get_upbit_krw_symbols():
    res = requests.get("https://api.upbit.com/v1/market/all")
    return [item["market"] for item in res.json() if item["market"].startswith("KRW-")]

def init_symbols():
    global bybit_extra_symbols
    binance_symbols = get_binance_symbols()
    bybit_symbols = get_bybit_symbols()
    bybit_extra_symbols = set(bybit_symbols) - set(binance_symbols)
    print(f"✅ Binance 合約數：{len(binance_symbols)}")
    print(f"✅ Bybit 額外合約數：{len(bybit_extra_symbols)}")
def get_top_movers_text():
    def fetch_binance():
        try:
            exchange_info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo").json()
            valid_symbols = {
                s['symbol']
                for s in exchange_info['symbols']
                if s['contractType'] == 'PERPETUAL' and s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING'
            }

            tickers = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr").json()
            return {
                item['symbol']: float(item['priceChangePercent'])
                for item in tickers
                if item['symbol'] in valid_symbols
            }

        except Exception as e:
            print("❗ Binance 資料抓取錯誤：", e)
            return {}

    def fetch_bybit():
        try:
            res = requests.get("https://api.bybit.com/v5/market/tickers?category=linear").json()
            return {
                item['symbol']: float(item['price24hPcnt']) * 100
                for item in res['result']['list']
                if item['symbol'].endswith("USDT")
            }
        except Exception as e:
            print("❗ Bybit 資料抓取錯誤：", e)
            return {}

    binance_data = fetch_binance()
    bybit_data = fetch_bybit()
    all_data = {}

    for symbol, change in binance_data.items():
        short = symbol.replace("USDT", "")
        all_data[short] = (change, "Binance")

    for symbol, change in bybit_data.items():
        if symbol in bybit_extra_symbols:
            short = symbol.replace("USDT", "")
            if short not in all_data:
                all_data[short] = (change, "Bybit")
            else:
                prev_change, prev_source = all_data[short]
                if abs(change) > abs(prev_change):
                    all_data[short] = (change, "Bybit")

    top_gainers = sorted(all_data.items(), key=lambda x: x[1][0], reverse=True)[:10]
    top_losers = sorted(all_data.items(), key=lambda x: x[1][0])[:10]

    def format_ranked_list(title, data):
        lines = [title]
        for i, (symbol, (change, source)) in enumerate(data, start=1):
            pct = f"{change:.2f}%"
            if change > 0:
                pct = f"+{pct}"
            lines.append(f"{i}. {symbol} {pct}（{source}）")
        return "\n".join(lines)

    return (
        format_ranked_list("📊 24H 漲幅榜 TOP 10（Binance + Bybit 額外）:", top_gainers) +
        "\n\n" +
        format_ranked_list("📉 24H 跌幅榜 TOP 10（Binance + Bybit 額外）:", top_losers)
    )
def check_daily_top_movers():
    print(f"[{datetime.now()}] ☀️ 推播每日榜單")
    msg = get_top_movers_text()
    send_telegram_message(msg)

def check_telegram_commands():
    global last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_update_id + 1}"
        res = requests.get(url).json()
        if not res["ok"]:
            print("❗ 無法獲取 Telegram 訊息")
            return

        for update in res["result"]:
            last_update_id = update["update_id"]
            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id"))
            text = message.get("text", "")

            if chat_id != str(ALLOWED_CHAT_ID):
                print(f"⚠️ 忽略來自未授權 chat_id：{chat_id}")
                continue

            if text.strip().lower() == "/top":
                print(f"📩 收到 /top 指令，回覆榜單")
                msg = get_top_movers_text()
                send_telegram_message(msg, chat_id=chat_id)

    except Exception as e:
        print("❗ 檢查指令錯誤：", e)
def update_price_15min_ago():
    print(f"[{datetime.now()}] 🔁 更新 15 分鐘前價格...")
    try:
        res = requests.get("https://fapi.binance.com/fapi/v1/ticker/price")
        for item in res.json():
            symbol = item["symbol"]
            if symbol.endswith("USDT"):
                price_15min_ago[f"binance_{symbol}"] = float(item["price"])
    except:
        pass

    try:
        data = requests.get("https://api.bybit.com/v5/market/tickers?category=linear").json()
        for item in data["result"]["list"]:
            symbol = item["symbol"]
            if symbol in bybit_extra_symbols:
                price_15min_ago[f"bybit_{symbol}"] = float(item["lastPrice"])
    except:
        pass

def check_price_change():
    global TEST_MODE
    print(f"\n[{datetime.now()}] 📈 檢查價格變化中...")
    binance_5m, binance_15m, bybit_5m, bybit_15m = [], [], [], []

    try:
        binance_data = requests.get("https://fapi.binance.com/fapi/v1/ticker/price").json()
    except:
        binance_data = []

    for item in binance_data:
        symbol = item['symbol']
        if not symbol.endswith("USDT"): continue
        short = symbol.replace("USDT", "")
        price = float(item['price'])
        old_5 = price_5min_ago.get(f"binance_{symbol}", 0)
        if old_5 > 0 and (price - old_5) / old_5 * 100 >= 5:
            binance_5m.append(f"{short} +{(price - old_5) / old_5 * 100:.2f}%")
        old_15 = price_15min_ago.get(f"binance_{symbol}", 0)
        if old_15 > 0 and (price - old_15) / old_15 * 100 >= 5:
            binance_15m.append(f"{short} +{(price - old_15) / old_15 * 100:.2f}%")
        price_5min_ago[f"binance_{symbol}"] = price

    try:
        bybit_data = requests.get("https://api.bybit.com/v5/market/tickers?category=linear").json()['result']['list']
    except:
        bybit_data = []

    for item in bybit_data:
        symbol = item['symbol']
        if symbol not in bybit_extra_symbols: continue
        short = symbol.replace("USDT", "")
        price = float(item['lastPrice'])
        old_5 = price_5min_ago.get(f"bybit_{symbol}", 0)
        if old_5 > 0 and (price - old_5) / old_5 * 100 >= 5:
            bybit_5m.append(f"{short} +{(price - old_5) / old_5 * 100:.2f}%")
        old_15 = price_15min_ago.get(f"bybit_{symbol}", 0)
        if old_15 > 0 and (price - old_15) / old_15 * 100 >= 5:
            bybit_15m.append(f"{short} +{(price - old_15) / old_15 * 100:.2f}%")
        price_5min_ago[f"bybit_{symbol}"] = price

    if TEST_MODE:
        binance_5m.append("TEST +6.00%")
        TEST_MODE = False

    msg = ""
    if binance_5m:
        msg += "📈 Binance（5分鐘）:\n" + "\n".join(binance_5m) + "\n\n"
    if binance_15m:
        msg += "📈 Binance（15分鐘）:\n" + "\n".join(binance_15m) + "\n\n"
    if bybit_5m:
        msg += "📈 Bybit（5分鐘）:\n" + "\n".join(bybit_5m) + "\n\n"
    if bybit_15m:
        msg += "📈 Bybit（15分鐘）:\n" + "\n".join(bybit_15m) + "\n\n"

    if msg:
        send_telegram_message("🚨 發現漲幅超過 5% 的合約：\n\n" + msg.strip())
        print("✅ 已發送通知")
    else:
        print("ℹ️ 沒有符合條件的交易對")
def detect_new_contracts(file, new_list, source_name):
    def load_symbols(file):
        if os.path.exists(file):
            with open(file, "r") as f:
                return set(json.load(f))
        return set()

    def save_symbols(file, symbols):
        with open(file, "w") as f:
            json.dump(sorted(symbols), f)

    old_set = load_symbols(file)
    new_set = set(new_list)
    diff = new_set - old_set
    save_symbols(file, list(new_list))

    if diff:
        if "Upbit" in source_name:
            cleaned = [s.replace("KRW-", "") for s in sorted(diff)]
            msg = "📢 Upbit 新增標的\n" + "\n".join(cleaned)
        else:
            msg = f"📢 {source_name} 新增合約：\n" + "\n".join(f"- {s}" for s in sorted(diff))

        send_telegram_message(msg)
        print(f"✅ 發送 {source_name} 新增通知")
    else:
        print(f"ℹ️ {source_name} 沒有新合約")

def check_new_all_contracts():
    print(f"\n[{datetime.now()}] 🔍 檢查是否有新合約...")
    try:
        detect_new_contracts(BINANCE_SYMBOL_FILE, get_binance_symbols(), "Binance")
        detect_new_contracts(BYBIT_SYMBOL_FILE, get_bybit_symbols(), "Bybit")
        detect_new_contracts(UPBIT_SYMBOL_FILE, get_upbit_krw_symbols(), "Upbit (KRW 現貨)")
    except Exception as e:
        print("❗ 新合約偵測錯誤：", e)

# ✅ 初始化與排程
init_symbols()
schedule.every(5).minutes.do(check_price_change)
schedule.every(15).minutes.do(update_price_15min_ago)
schedule.every().hour.at(":00").do(check_new_all_contracts)
schedule.every().day.at("00:00").do(check_daily_top_movers)

print("✅ 系統已啟動，開始監控...\n")

# ✅ 主迴圈
while True:
    schedule.run_pending()
    check_telegram_commands()
    time.sleep(2)
