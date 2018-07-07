# coding: utf-8
from datetime import datetime, timedelta
import time, calendar, pytz, requests
import pandas as pd

ALLOWED_PERIOD = {
    "1m": ["1m", 1,    1],  "3m": ["1m",  3,   3],
    "5m": ["5m", 1,    5], "15m": ["5m",  3,  15], "30m": ["5m", 6, 30],
    "1h": ["1h", 1,   60],  "2h": ["1h",  2, 120],
    "3h": ["1h", 3,  180],  "4h": ["1h",  4, 240],
    "6h": ["1h", 6,  360], "12h": ["1h", 12, 720],
    "1d": ["1d", 1, 1440],
    # not support yet '3d', '1w', '2w', '1m'
}

#---------------------------------------------------------------------
# メイン処理
#---------------------------------------------------------------------
def main():
    # DataFrameにてOHLCV取得
    # df_ohlcv = fetch_ohlcv_df(period="1m", count=10, reverse=False, partial=False, tstype="ST")
    # print(df_ohlcv)

    # ListにてOHLCV取得
    # lst_ohlcv = fetch_ohlcv_lst(period="1m", count=60*24*10, reverse=False, partial=False, tstype="ST")
    lst_ohlcv = fetch_ohlcv_lst(period="1m", count=60*24*150, reverse=False, partial=False, tstype="ST")
    for ohlcv in lst_ohlcv:
        print(str(ohlcv[0]) + "," + str(ohlcv[1]) + "," + str(ohlcv[2]) + "," + str(ohlcv[3]) + "," + str(ohlcv[4]) + "," + str(ohlcv[5]))

#---------------------------------------------------------------------
# 指定された時間足のOHLCVデータをBitMEX REST APIより取得
#---------------------------------------------------------------------
# [@param]
#     period  ="1m"     時間足(1m, 3m, 5m, 15m, 30m, 1h, 2h, 3h, 4h, 6h, 12h, 1d)
#     symbol  ="XBTUSD" 通貨ペア
#     count   =1000     取得期間 ※
#     reverse =True     並び順(True:新->古, False:古->新)
#     partial =False    最新未確定足を取得するか(True:含む, False:含まない)
#     tstype  ="UTMS"   時刻形式(以下の5パターン)
#              "UTMS":UnixTime(ミリ秒), "UTS":UnixTime(秒), "DT":datetime,
#              "STMS":日付文字列(%Y-%m-%dT%H:%M:%S.%fZ), "STS":日付文字列(%Y-%m-%dT%H:%M:%S)
# [return]
#     DataFrame : [[timestamp, open, high, low, close, volume], ・・・]
#     List      : [[timestamp, open, high, low, close, volume], ・・・]
#     ※戻り値データ型 (List or DataFrame) によって取得関数を2パターンに分けてある
#---------------------------------------------------------------------
# ※ 1m, 5m, 1h, 1dを基準にmergeしているため、それ以外を取得する場合は件数に注意
#    (3m : 1m * 3期間より、3mを10期間取得するなら、APIでは 10 * 3 = 30件になる)
#    APIは1回でMAX10000件までの取得としている
#    30mや4h、6h、12hなどを取得する場合、件数を大きくするとAPI制限にかかる可能性がある
#---------------------------------------------------------------------
# [usage] lst_ohlcv = fetch_ohlcv_lst(period="15m", count=1000, tstype="DT")
#         df_ohlcv = fetch_ohlcv_df(period="5m", count=1000, partial=True)
#---------------------------------------------------------------------
# ListでOHLCVを取得
def fetch_ohlcv_lst(period="1m", symbol="XBTUSD", count=1000, reverse=True, partial=False, tstype="UTMS"):
    df = fetch_ohlcv_df(period, symbol, count, reverse, partial, tstype)
    if df is None:
        return None
    return [[int(x[0]),x[1],x[2],x[3],x[4],x[5]] if tstype=="UTS" or tstype=="UTMS" else x for x in df.values.tolist()]

# DataFrameでOHLCVを取得
def fetch_ohlcv_df(period="1m", symbol="XBTUSD", count=1000, reverse=True, partial=False, tstype="UTMS"):
    if period not in ALLOWED_PERIOD:
        return None
    period_params = ALLOWED_PERIOD[period]
    need_count = (count + 1) * period_params[1] # マージ状況により、不足が発生する可能性があるため、多めに取得

    # REST APIリクエストでOHLCVデータ取得
    df_ohlcv = __get_ohlcv_paged(symbol=symbol, period=period_params[0], count=need_count)

    # DataFrame化して指定時間にリサンプリング
    if period_params[1] > 1:
        minutes = ALLOWED_PERIOD[period][2]
        offset = str(minutes) + "T"
        if 60 <= minutes < 1440:
            offset = str(minutes / 60) + "H"
        elif 1440 <= minutes:
            offset = str(minutes / 1440) + "D"
        df_ohlcv = df_ohlcv.resample(offset).agg({
                        "timestamp": "first",
                        "open":      "first",
                        "high":      "max",
                        "low":       "min",
                        "close":     "last",
                        "volume":    "sum",
                    })
    # 未確定の最新足を除去
    if partial == False:
        df_ohlcv = df_ohlcv.iloc[:-1]
    # マージした結果、余分に取得している場合、古い足から除去
    if len(df_ohlcv) > count:
        df_ohlcv = df_ohlcv.iloc[len(df_ohlcv)-count:]
    # index解除
    df_ohlcv.reset_index(inplace=True)
    # 並び順を反転
    if reverse == True:
        df_ohlcv = df_ohlcv.iloc[::-1]
    # timestampを期間終わり時刻にするため、datetimeをシフト
    df_ohlcv["datetime"] += timedelta(minutes=ALLOWED_PERIOD[period][2])
    # timestamp変換
    __convert_timestamp(df_ohlcv, tstype)
    # datetime列を削除
    df_ohlcv.drop("datetime", axis=1, inplace=True)
    # indexリセット
    df_ohlcv.reset_index(inplace=True, drop=True)
    return df_ohlcv

# private
def __convert_timestamp(df_ohlcv, timestamp="UTMS"):
    if timestamp == "UTS":
        # df_ohlcv["timestamp"] = pd.Series([int(dt.timestamp()) for dt in df_ohlcv["datetime"]])
        df_ohlcv["timestamp"] = pd.Series([int(time.mktime(dt.timetuple())) for dt in df_ohlcv["datetime"]])
    elif timestamp == "UTMS":
        # df_ohlcv["timestamp"] = pd.Series([int(dt.timestamp()) * 1000 for dt in df_ohlcv["datetime"]])
        df_ohlcv["timestamp"] = pd.Series([int(time.mktime(dt.timetuple())) * 1000 for dt in df_ohlcv["datetime"]])
    elif timestamp == "DT":
        df_ohlcv["timestamp"] = df_ohlcv["datetime"]
    elif timestamp == "STS":
        df_ohlcv["timestamp"] = pd.Series([dt.strftime("%Y-%m-%d %H:%M:%S") for dt in df_ohlcv["datetime"]])
    elif timestamp == "STMS":
        df_ohlcv["timestamp"] = pd.Series([dt.strftime("%Y-%m-%d %H:%M:%S.%fZ") for dt in df_ohlcv["datetime"]])
    elif timestamp == "ST":
        df_ohlcv["timestamp"] = pd.Series([dt.strftime("%Y-%m-%d %H:%M:%S") for dt in df_ohlcv["datetime"]])
        # df_ohlcv["timestamp"] = pd.Series([dt.strftime("%Y/%m/%d %H:%M") for dt in df_ohlcv["datetime"]])
    else:
        df_ohlcv["timestamp"] = df_ohlcv["datetime"]

def __get_ohlcv_paged(symbol="XBTUSD", period="1m", count=1000):
    ohlcv_list = []
    utc_now = datetime.now(pytz.utc)
    to_time = int(utc_now.timestamp())
    #to_time = int(time.mktime(utc_now.timetuple()))
    from_time = to_time - ALLOWED_PERIOD[period][2] * 60 * count
    start = from_time
    end = to_time
    if count > 10000:
        end = from_time + ALLOWED_PERIOD[period][2] * 60 * 10000
    while start <= to_time:
        ohlcv_list += __fetch_ohlcv_list(symbol=symbol, period=period, start=start, end=end)
        start = end + ALLOWED_PERIOD[period][2] * 60
        end = start + ALLOWED_PERIOD[period][2] * 60 * 10000
        if end > to_time:
            end = to_time
    df_ohlcv = pd.DataFrame(ohlcv_list,
                            columns=["timestamp", "open", "high", "low", "close", "volume"])
    df_ohlcv["datetime"] = pd.to_datetime(df_ohlcv["timestamp"], unit="s")
    df_ohlcv = df_ohlcv.set_index("datetime")
    pd.to_datetime(df_ohlcv.index, utc=True)
    return df_ohlcv

def __fetch_ohlcv_list(symbol="XBTUSD", period="1m", start=0, end=0):
    param = {"period": ALLOWED_PERIOD[period][2], "from": start, "to": end}
    url = "https://www.bitmex.com/api/udf/history?symbol=XBTUSD&resolution={period}&from={from}&to={to}".format(**param)
    res = requests.get(url)
    data = res.json()
    return [list(ohlcv) for ohlcv in zip(data["t"], data["o"], data["h"], data["l"], data["c"], data["v"])]

if __name__ == "__main__":
    main()