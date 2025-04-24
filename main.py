import requests
import time
import schedule
import json
import os
from datetime import datetime
from collections import defaultdict
from pytz import timezone

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

            if text.strip().lower() == "/t":
                print(f"📩 收到 /t 指令，回覆榜單")
                msg = get_top_movers_text()
                send_telegram_message(msg, chat_id=chat_id)

            elif text.strip().lower() == "/5":
                print("📩 收到 /5 指令，回傳 5 分鐘紀錄")
                parts = load_price_spike_log("5")
                msg = "🕔 5分鐘漲幅紀錄\n\n"
                for source, records in parts:
                    msg += f"📍 {source}：\n"
                    if records:
                        msg += "\n".join([f"- {r['symbol']} @ {r['datetime']}" for r in records]) + "\n\n"
                    else:
                        msg += "（無紀錄）\n\n"
                send_telegram_message(msg.strip(), chat_id=chat_id)

            elif text.strip().lower() == "/15":
                print("📩 收到 /15 指令，回傳 15 分鐘紀錄")
                parts = load_price_spike_log("15")
                msg = "🕒 15分鐘漲幅紀錄\n\n"
                for source, records in parts:
                    msg += f"📍 {source}：\n"
                    if records:
                        msg += "\n".join([f"- {r['symbol']} @ {r['datetime']}" for r in records]) + "\n\n"
                    else:
                        msg += "（無紀錄）\n\n"
                send_telegram_message(msg.strip(), chat_id=chat_id)
            elif text.strip().lower() == "/f":
                print("📩 收到 /f 指令，回傳資金費率排行榜")
                msg = get_funding_rate_ranking_text()
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
            save_price_spike_log("binance", "5", short)
        old_15 = price_15min_ago.get(f"binance_{symbol}", 0)
        if old_15 > 0 and (price - old_15) / old_15 * 100 >= 5:
            binance_15m.append(f"{short} +{(price - old_15) / old_15 * 100:.2f}%")
            save_price_spike_log("binance", "15", short)
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
            save_price_spike_log("bybit", "5", short)
        old_15 = price_15min_ago.get(f"bybit_{symbol}", 0)
        if old_15 > 0 and (price - old_15) / old_15 * 100 >= 5:
            bybit_15m.append(f"{short} +{(price - old_15) / old_15 * 100:.2f}%")
            save_price_spike_log("bybit", "15", short)
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
LOG_FILES = {
    "binance_5": "binance_5min_log.json",
    "binance_15": "binance_15min_log.json",
    "bybit_5": "bybit_5min_log.json",
    "bybit_15": "bybit_15min_log.json"
}

def save_price_spike_log(source, interval, symbol):
    key = f"{source}_{interval}"
    file = LOG_FILES.get(key)
    if not file:
        return

    now = datetime.now(timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")

    if os.path.exists(file):
        with open(file, "r") as f:
            data = json.load(f)
    else:
        data = []

    # 如果已有 symbol 紀錄，略過
    for entry in data:
        if entry["symbol"] == symbol:
            return

    data.insert(0, {"symbol": symbol, "datetime": now})
    data = data[:30]

    with open(file, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_price_spike_log(interval):
    result = []
    for source in ["binance", "bybit"]:
        key = f"{source}_{interval}"
        file = LOG_FILES.get(key)
        if os.path.exists(file):
            with open(file, "r") as f:
                data = json.load(f)
            result.append((source.capitalize(), data))
        else:
            result.append((source.capitalize(), []))
    return result
def get_funding_rate_ranking_text():
    def fetch_binance_funding():
        try:
            url = "https://fapi.binance.com/fapi/v1/premiumIndex"
            res = requests.get(url).json()
            return {
                item["symbol"]: float(item["lastFundingRate"]) * 100
                for item in res
                if item["symbol"].endswith("USDT")
            }
        except Exception as e:
            print("❗ Binance 資金費率錯誤：", e)
            return {}

    def format_rate(rank_data, title):
        lines = [title]
        for i, (symbol, rate) in enumerate(rank_data, start=1):
            pct = f"{rate:.4f}%"
            if rate > 0:
                pct = f"+{pct}"
            lines.append(f"{i}. {symbol.replace('USDT','')} {pct}")
        return "\n".join(lines)

    binance_data = fetch_binance_funding()

    def sort_and_split(data):
        items = sorted(data.items(), key=lambda x: x[1], reverse=True)
        return items[:10], sorted(items[-10:], key=lambda x: x[1])

    b_top, b_bottom = sort_and_split(binance_data)

    return (
        format_rate(b_top, "📈 Binance 資金費率 Top 10") + "\n\n" +
        format_rate(b_bottom, "📉 Binance 資金費率 Bottom 10")
    )


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
