#_*_ coding: utf-8 _*_
#https://sshuhei.com

import json
import requests
import csv
import math
import pandas as pd
import numpy as np
import time
import datetime
import logging
import websocket
from pubnub.callbacks import SubscribeCallback
from pubnub.enums import PNStatusCategory
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub_tornado import PubNubTornado
from pubnub.pnconfiguration import PNReconnectionPolicy
from tornado import gen
import threading
from collections import deque
from . import bforder
from . import cryptowatch
import talib
from talib import abstract

class ChannelBreakOut:
    def __init__(self):
        #config.jsonの読み込み
        f = open('config/config.json', 'r', encoding="utf-8")
        config = json.load(f)
        self.cryptowatch = cryptowatch.CryptoWatch()
        #取得した約定履歴を保存するリスト（基本的に不要．）
        self._executions = deque(maxlen=300)
        self._spotExecutions = deque(maxlen=300)
        self._lot = 0.01
        self._product_code = config["product_code"]
        #各パラメタ．
        self._entryTerm = 10
        self._closeTerm = 5
        self._rangeTerm = 15
        self._rangeTh = 5000
        self._waitTerm = 5
        self._waitTh = 20000
        self.rangePercent = None
        self.rangePercentTerm = None
        self._candleTerm = "1T"
        #現在のポジション．1ならロング．-1ならショート．0ならポジションなし．
        self._pos = 0
        #注文執行コスト．遅延などでこの値幅を最初から取られていると仮定する
        self._cost = 3000
        self.order = bforder.BFOrder()
        #取引所のヘルスチェック
        self.healthCheck = config["healthCheck"]
        #ラインに稼働状況を通知
        self.line_notify_token = config["line_notify_token"]
        self.line_notify_api = 'https://notify-api.line.me/api/notify'
        # グラフ表示
        self.showFigure = False
        # バックテスト結果のグラフをLineで送る
        self.sendFigure = False
        # バックテストのトレード詳細をログ出力する
        self.showTradeDetail = False
        # optimization用のOHLCcsvファイル
        self.fileName = None
        # 現物とFXの価格差がSFDの許容値を超えた場合にエントリーを制限
        self.sfdLimit = True

        self.method = config["method"]
        self.price_range_limit = config["price_range_limit"]
        self.tmp2 = config["tmp2"]
        self.tmp1 = config["tmp1"]
        self.gyakubari = config["gyakubari"]
        self.filtter = config["filtter"]
        self.filtter_param = config["filtter_param"]
        self.offset_raitio = config["offset_raitio"]
        self.trailingStop = config["trailingStop"]
        self.myOHLC = config["myOHLC"]

        self.df_candleStick = False
        self.priceRange = False

        self.ma_short = []
        self.ma_long = []
        
    @property
    def cost(self):
        return self._cost

    @cost.setter
    def cost(self, value):
        self._cost = value

    @property
    def candleTerm(self):
        return self._candleTerm
    @candleTerm.setter
    def candleTerm(self, val):
        """
        valは"5T"，"1H"などのString
        """
        self._candleTerm = val

    @property
    def waitTh(self):
        return self._waitTh
    @waitTh.setter
    def waitTh(self, val):
        self._waitTh = val

    @property
    def waitTerm(self):
        return self._waitTerm
    @waitTerm.setter
    def waitTerm(self, val):
        self._waitTerm = val

    @property
    def rangeTh(self):
        return self._rangeTh
    @rangeTh.setter
    def rangeTh(self,val):
        self._rangeTh = val

    @property
    def rangeTerm(self):
        return self._rangeTerm
    @rangeTerm.setter
    def rangeTerm(self,val):
        self._rangeTerm = val

    @property
    def executions(self):
        return self._executions
    @executions.setter
    def executions(self, val):
        self._executions = val

    @property
    def spotExecutions(self):
        return self._spotExecutions
    @spotExecutions.setter
    def spotExecutions(self, val):
        self._spotExecutions = val

    @property
    def pos(self):
        return self._pos
    @pos.setter
    def pos(self, val):
        self._pos = int(val)

    @property
    def lot(self):
        return self._lot
    @lot.setter
    def lot(self, val):
        self._lot = round(val,3)

    @property
    def product_code(self):
        return self._product_code
    @product_code.setter
    def product_code(self, val):
        self._product_code = val

    @property
    def entryTerm(self):
        return self._entryTerm
    @entryTerm.setter
    def entryTerm(self, val):
        self._entryTerm = int(val)

    @property
    def closeTerm(self):
        return self._closeTerm
    @closeTerm.setter
    def closeTerm(self, val):
        self._closeTerm = int(val)

    def calculateLot(self, margin):
        """
        証拠金からロットを計算する関数．
        """
        lot = math.floor(margin*10**(-4))*10**(-2)
        return round(lot,2)

    # 約定履歴ファイル
    def writeorderhistory(self, priceOrderd, lotOrderd, profitRange, currentPos):
        with open('log/orderhistory.csv', 'a') as orderhistoryfile:
            orderhistorycsv = csv.writer(orderhistoryfile, lineterminator='\n')
            orderhistorycsv.writerow([datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S"), priceOrderd, lotOrderd, profitRange, currentPos ])

    def calculateLines(self, df_candleStick, term, rangePercent, rangePercentTerm, EntryOrClose, priceRange):
        """
        期間高値・安値を計算する．
        candleStickはcryptowatchのローソク足．termは安値，高値を計算する期間．（5ならローソク足5本文の安値，高値．)
        """
        lowLine = [0 for i in range(len(df_candleStick.index))]
        highLine = [0 for i in range(len(df_candleStick.index))]
        _maximum = 0.2
        self.loss_cut_list = [0 for i in range(len(df_candleStick.index))]

        if self.method == 'line_uki_sma':
            priceRangeMean = self.calculateMA(priceRange, term, 'SMA')
        elif self.method == 'line_uki_ema':
            priceRangeMean = self.calculateMA(priceRange, term, 'EMA')
        elif self.method == 'line_uki_wma':
            priceRangeMean = self.calculateMA(priceRange, term, 'WMA')
        elif self.method == "line_RSI":
            # RSI = abstract.RSI(df_candleStick, timeperiod=14)
            if EntryOrClose == 'entry':
                lowLine  = self.calcRSI(50-self.tmp1, 'down', 'open')
                highLine = self.calcRSI(50+self.tmp1, 'up', 'open')
            if EntryOrClose == 'close':
                highLine  = self.calcRSI(50-self.tmp2, 'up', 'close')
                lowLine = self.calcRSI(50+self.tmp2, 'down', 'close')
            lowLine = [int(i) for i in lowLine]
            highLine = [int(i) for i in highLine]
            return (lowLine, highLine)

        len_df = len(df_candleStick.index) # 高速化のため
        for i in range(len_df):
            if i == 0:
                continue
            if i-1 < term:
                lowLine[i] = df_candleStick["low"][i-1]
                highLine[i] = df_candleStick["high"][i-1]
            else:
                if self.method == 'line_simple':
                    # シンプルチャネルブレイクアウト戦略
                    low = min([price for price in df_candleStick["low"][i-term:i]])
                    high = max([price for price in df_candleStick["high"][i-term:i]])

                elif 'line_uki' in self.method:
                    # ukiさんロジック
                    low = df_candleStick["close"][i-1] - priceRangeMean[i-1] * rangePercent
                    high = df_candleStick["close"][i-1] + priceRangeMean[i-1] * rangePercent
                elif self.method == 'line_my':
                    # 高値・安値からレンジパーセント
                    low_range  = abs(df_candleStick["close"][i-1] - min([price for price in df_candleStick["low"][i-term:i]]))
                    low_range  = low_range * rangePercent
                    low        = df_candleStick["close"][i-1] - low_range

                    high_range = abs(df_candleStick["close"][i-1] - max([price for price in df_candleStick["high"][i-term:i]]))
                    high_range = high_range * rangePercent
                    high       = df_candleStick["close"][i-1] + high_range

                elif self.method == "line_BBANDS":
                    # ぼりんじゃーバンド
                    df_candleStick['close'] = pd.to_numeric(df_candleStick['close']).astype(float)
                    upper, middle, lower = talib.BBANDS(np.array(df_candleStick["close"][i-term:i]), timeperiod=term)

                    high = upper[-1]
                    low = lower[-1]

                else:
                    return lowLine, highLine

                # bitmex用
                # # 切り上げ
                # high = (int(high * 2)) / 2 + 0.5
                # # 切り捨て
                # low = (int(low * 2)) / 2

                lowLine[i] = int(low)
                highLine[i] = int(high)

        return lowLine, highLine

    def calculatePriceRange(self, df_candleStick, term):
        """
        termの期間の値幅を計算．
        """
        if term == 1:
            #low = [min([df_candleStick["close"][i].min(), df_candleStick["open"][i].min()]) for i in range(len(df_candleStick.index))]
            #high = [max([df_candleStick["close"][i].max(), df_candleStick["open"][i].max()]) for i in range(len(df_candleStick.index))]
            low = [df_candleStick["low"][i] for i in range(len(df_candleStick.index))]
            high = [df_candleStick["high"][i] for i in range(len(df_candleStick.index))]
            low = pd.Series(low)
            high = pd.Series(high)
            priceRange = [high.iloc[i]-low.iloc[i] for i in range(len(df_candleStick.index))]
        else:
            #low = [min([df_candleStick["close"][i-term+1:i].min(), df_candleStick["open"][i-term+1:i].min()]) for i in range(len(df_candleStick.index))]
            #high = [max([df_candleStick["close"][i-term+1:i].max(), df_candleStick["open"][i-term+1:i].max()]) for i in range(len(df_candleStick.index))]
            low = [df_candleStick["low"][i-term+1:i].min() for i in range(len(df_candleStick.index))]
            high = [df_candleStick["high"][i-term+1:i].max() for i in range(len(df_candleStick.index))]
            low = pd.Series(low)
            high = pd.Series(high)
            priceRange = [high.iloc[i]-low.iloc[i] for i in range(len(df_candleStick.index))]
        return priceRange

    def isRange(self, df_candleStick, term, th):
        """
        レンジ相場かどうかをTrue,Falseの配列で返す．termは期間高値・安値の計算期間．thはレンジ判定閾値．
        """
        #値幅での判定．
        if th != None:
            priceRange = self.calculatePriceRange(df_candleStick, term)
            isRange = [th > i for i in priceRange]
        #終値の標準偏差の差分が正か負かでの判定．
        elif th == None and term != None:
            df_candleStick["std"] = [df_candleStick["close"][i-term+1:i].std() for i in range(len(df_candleStick.index))]
            df_candleStick["std_slope"] = [df_candleStick["std"][i]-df_candleStick["std"][i-1] for i in range(len(df_candleStick.index))]
            isRange = [i > 0 for i in df_candleStick["std_slope"]]
        else:
            isRange = [False for i in df_candleStick.index]
        return isRange

    def judge(self, df_candleStick, entryHighLine, entryLowLine, closeHighLine, closeLowLine, entryTerm, closeTerm):
        """
        売り買い判断．ローソク足の高値が期間高値を上抜けたら買いエントリー．（2）ローソク足の安値が期間安値を下抜けたら売りエントリー．judgementリストは[買いエントリー，売りエントリー，買いクローズ（売り），売りクローズ（買い）]のリストになっている．（二次元リスト）リスト内リストはの要素は，0（シグナルなし）,価格（シグナル点灯）を取る．
        """

        # ポジションとか何も考えずとりあえず価格を入れる
        # 実際にはポジションの関係で注文しないものもある
        judgement = [[0,0,0,0] for i in range(len(df_candleStick.index))]
        judgement_RSI = [[True,True,False,False] for i in range(len(df_candleStick.index))]
        judgement_BBANDS = [[True,True,False,False] for i in range(len(df_candleStick.index))]
        judgement_CCI = [[True,True,False,False] for i in range(len(df_candleStick.index))]
        judgement_ROC = [[True,True,False,False] for i in range(len(df_candleStick.index))]
        judgement_LAST = [[True,True,False,False] for i in range(len(df_candleStick.index))]

        if 'MA' in self.method:
            close = pd.to_numeric(df_candleStick['close']).astype(float)
            # self.cross_price = self.test_calculateCROSS()
            self.ma_short = self.calculateMA(close, entryTerm, self.method)
            self.ma_long = self.calculateMA(close, closeTerm, self.method)
            nariyuki_cost = 500

            for i in range(len(df_candleStick.index)):
                if i-1 < entryTerm or i < closeTerm:
                    continue

                if self.gyakubari:
                    # 次足の始値でエントリーする
                    # ゴールデンクロスで売り
                    if self.ma_short[i-2] <= self.ma_long[i-2] and self.ma_short[i-1] > self.ma_long[i-1]:
                            judgement[i][2] = (df_candleStick["open"][i]*2 + df_candleStick["high"][i])/3 # 買いクローズ（売り）
                            judgement[i][1] = (df_candleStick["open"][i]*2 + df_candleStick["high"][i])/3 # 売りエントリー

                    #デッドクロスで買い
                    if self.ma_short[i-2] >= self.ma_long[i-2] and self.ma_short[i-1] < self.ma_long[i-1]:
                            judgement[i][3] = (df_candleStick["open"][i]*2 + df_candleStick["low"][i])/3 # 売りクローズ（買い）
                            judgement[i][0] = (df_candleStick["open"][i]*2 + df_candleStick["low"][i])/3 # 買いエントリー
                # 順張り
                else:
                    # 次足の始値でエントリーする
                    # ゴールデンクロスで買い
                    if self.ma_short[i-2] <= self.ma_long[i-2] and self.ma_short[i-1] > self.ma_long[i-1]:
                        judgement[i][3] = df_candleStick["open"][i] # 売りクローズ（買い）
                        judgement[i][0] = df_candleStick["open"][i] # 買いエントリー

                    #デッドクロスで売り
                    if self.ma_short[i-2] >= self.ma_long[i-2] and self.ma_short[i-1] < self.ma_long[i-1]:
                        judgement[i][2] = df_candleStick["open"][i] # 買いクローズ（売り）
                        judgement[i][1] = df_candleStick["open"][i] # 売りエントリー
        
        if 'BBANDS' in self.method:
            judgement_BBANDS = [[False,False,False,False] for i in range(len(df_candleStick.index))]
            df_candleStick['close'] = pd.to_numeric(df_candleStick['close']).astype(float)
            upper, middle, lower = talib.BBANDS(np.array(df_candleStick["close"]), timeperiod=14, nbdevup=2.5, nbdevdn=2.5)
            for i in range(len(df_candleStick.index)):

                if self.gyakubari:
                    if df_candleStick['close'][i-1] < lower[i-1]:
                        judgement_BBANDS[i][0] = True # 買いエントリー
                    if df_candleStick['close'][i-1] > upper[i-1]:
                        judgement_BBANDS[i][1] = True # 売りエントリー
                    if df_candleStick['close'][i-1] > middle[i-1]:
                        judgement_BBANDS[i][2] = True # 買いクローズ
                    if df_candleStick['close'][i-1] < middle[i-1]:
                        judgement_BBANDS[i][3] = True # 売りクローズ
                else:
                    pass
            
        if 'CCI' in self.method:
            judgement_CCI = [[False,False,False,False] for i in range(len(df_candleStick.index))]
            CCI = abstract.CCI(df_candleStick, timeperiod=14)
            for i in range(len(df_candleStick.index)):

                if i < 16:
                    continue

                if self.gyakubari:
                    if CCI[i-1] < -100:
                    # if CCI[i-1] > -100 and CCI[i-2] <= -100:
                        judgement_CCI[i][0] = True # 買いエントリー
                    if CCI[i-1] > 100:
                    # if CCI[i-1] < 100 and CCI[i-2] >= 100:
                        judgement_CCI[i][1] = True # 売りエントリー
                    if CCI[i-1] > -50:
                        judgement_CCI[i][2] = True # 買いクローズ
                    if CCI[i-1] < 50:
                        judgement_CCI[i][3] = True # 売りクローズ
                else:
                    pass
        
        if 'MFI' in self.method:
            self.MFI = abstract.MFI(df_candleStick, timeperiod=14)
            self.RSI = abstract.RSI(df_candleStick, timeperiod=14)
            for i in range(len(df_candleStick.index)):

                if i < 16:
                    continue

                if self.gyakubari:
                    # 30を上抜いたら買い
                    if self.MFI[i-1] < 30 and self.RSI[i-1] < 30:
                        judgement[i][0] = df_candleStick["open"][i] # 買いエントリー
                    # 50を上抜いたら売り（決済）
                    if self.RSI[i-1] >= 35:
                        judgement[i][2] = df_candleStick["open"][i] # 買いクローズ
                    # 70を下抜いたら売り
                    if self.MFI[i-1] > 70 and self.RSI[i-1] > 70:
                        judgement[i][1] = df_candleStick["open"][i] # 売りエントリー
                    # 50を下抜いたら売り
                    if self.MFI[i-1] <= 70:
                        judgement[i][3] = df_candleStick["open"][i] # 売りクローズ
                else:
                    pass

        if 'ROC' in self.method:
            judgement_ROC = [[False,False,False,False] for i in range(len(df_candleStick.index))]
            self.ROC = abstract.ROC(df_candleStick, timeperiod=1)
            for i in range(len(df_candleStick.index)):

                if i < 16:
                    continue

                if self.gyakubari:
                    pass
                else:
                    if self.ROC[i-1] > 0.15:
                        judgement_ROC[i][0] = True # 買いエントリー
                    if self.ROC[i-1] < 0:
                        judgement_ROC[i][2] = True # 買いクローズ
                    if self.ROC[i-1] < -0.15:
                        judgement_ROC[i][1] = True # 売りエントリー
                    if self.ROC[i-1] > 0:
                        judgement_ROC[i][3] = True # 売りクローズ

        if 'SAR' in self.method:
            SAR = self.psar(df_candleStick, acceleration=0.02, maximum=0.2)
            for i in range(len(df_candleStick.index)):
                if i < 16:
                    continue

                if self.gyakubari:
                    tmp = i-1
                    if SAR[tmp] >= df_candleStick["close"][i-1]:
                        judgement[i][0] = df_candleStick["open"][i] # 買いエントリー
                        judgement[i][3] = df_candleStick["open"][i] # 売りクローズ
                    if SAR[tmp] < df_candleStick["close"][i-1]:
                        judgement[i][2] = df_candleStick["open"][i] # 買いクローズ
                        judgement[i][1] = df_candleStick["open"][i] # 売りエントリ
                else:
                    tmp = i-1
                    if SAR[tmp] <= df_candleStick["close"][i-1]:
                        judgement[i][0] = df_candleStick["open"][i] # 買いエントリー
                        judgement[i][3] = df_candleStick["open"][i] # 売りクローズ
                    if SAR[tmp] > df_candleStick["close"][i-1]:
                        judgement[i][2] = df_candleStick["open"][i] # 買いクローズ
                        judgement[i][1] = df_candleStick["open"][i] # 売りエントリ
        
        if 'AROON' in self.method:
            high = pd.to_numeric(df_candleStick['high']).astype(float)
            high = np.array(high)
            low = pd.to_numeric(df_candleStick['low']).astype(float)
            low = np.array(low)
            aroondown, aroonup = talib.AROON(high, low, timeperiod=14)

            for i in range(len(df_candleStick.index)):
                if i < 16:
                    continue

                if self.gyakubari:
                    pass
                else:
                    # ゴールデンクロスで買い
                    if aroonup[i-2] <= aroondown[i-2] and aroonup[i-1] > aroondown[i-1]:
                        judgement[i][0] = df_candleStick["open"][i] # 買いエントリー
                        judgement[i][3] = df_candleStick["open"][i] # 売りクローズ
                    # デッドクロスで売り
                    if aroonup[i-2] >= aroondown[i-2] and aroonup[i-1] < aroondown[i-1]:
                        judgement[i][1] = df_candleStick["open"][i] # 売りエントリー
                        judgement[i][2] = df_candleStick["open"][i] # 買いクローズ

        if 'LAST' in self.method:
            '直近の足で大きく上げてる or 下げてる'
            judgement_LAST = [[False,False,False,False] for i in range(len(df_candleStick.index))]
            priceRangeMean = self.calculateMA(self.priceRange, 5, 'EMA')
            for i in range(len(df_candleStick.index)):
                if i < 2:
                    continue

                if self.gyakubari:
                    if df_candleStick['close'][i-1] > df_candleStick['close'][i-2]:
                        judgement_LAST[i][0] = True # 買いエントリー
                    if df_candleStick['close'][i-1] < df_candleStick['close'][i-2]:
                        judgement_LAST[i][1] = True # 売りエントリー
                else:
                    pass

        if 'line_' in self.method:
            for i in range(len(df_candleStick.index)):
                
                if i-1 < entryTerm:
                    continue

                if i < closeTerm:
                    continue

                if self.gyakubari:

                    # ショートエントリー
                    if df_candleStick["high"][i] > entryHighLine[i]:
                        judgement[i][1] = entryHighLine[i]
                        
                    # ロングエントリー
                    if df_candleStick["low"][i] < entryLowLine[i]:
                        judgement[i][0] = entryLowLine[i]
                    # ショートクローズ
                    if df_candleStick["low"][i] < closeLowLine[i]:
                        judgement[i][3] = closeLowLine[i]
                    # ロングクローズ
                    if df_candleStick["high"][i] > closeHighLine[i]:
                        judgement[i][2] = closeHighLine[i]

                # トレンドフォロー
                else:
                    # 変なヒゲに翻弄されないように、次の足の始値で仕掛けるようにする
                    nextOpen = False
                    if nextOpen:
                        if df_candleStick["high"][i] > entryHighLine[i]+0.5:
                            judgement[i][0] = df_candleStick["open"][i+1]
                        #下抜けでショートエントリー
                        if df_candleStick["low"][i] < entryLowLine[i]-0.5:
                            judgement[i][1] = df_candleStick["open"][i+1]
                        #下抜けでロングクローズ
                        if df_candleStick["low"][i] < closeLowLine[i]+0.5:
                            judgement[i][2] = df_candleStick["open"][i+1]
                        #上抜けでショートクローズ
                        if df_candleStick["high"][i] > closeHighLine[i]-0.5:
                            judgement[i][3] = df_candleStick["open"][i+1]
                        else:
                            pass
                    else:
                        # 0.5はstoplimitのトリガ価格分を想定
                        #上抜けでロングエントリー                    
                        if df_candleStick["high"][i] > entryHighLine[i]+0.5:
                            judgement[i][0] = round((df_candleStick["high"][i] + entryHighLine[i]*2) / 3)
                            if abs(judgement[i][0] - entryHighLine[i]) > 1000:
                                judgement[i][0] = entryHighLine[i] + 1000
                            # BBANDSは終値からの算出じゃないので、急な動きの場合、始値がすでにエントリーラインより
                            # 大幅に上下している場合があるのでその考慮
                            if judgement[i][0] < df_candleStick["open"][i]:
                                judgement[i][0] = df_candleStick["open"][i]
                        #下抜けでショートエントリー
                        if df_candleStick["low"][i] < entryLowLine[i]-0.5:
                            judgement[i][1] = round((df_candleStick["low"][i] + entryLowLine[i]*2) / 3)
                            if abs(judgement[i][1] - entryLowLine[i]) > 1000:
                                judgement[i][1] = entryLowLine[i] - 1000
                            # judgement[i][1] = entryLowLine[i]
                            if judgement[i][1] > df_candleStick["open"][i]:
                                judgement[i][1] = df_candleStick["open"][i]
                        # # ショートクローズ
                        # if df_candleStick["high"][i] > closeHighLine[i]:
                        #     judgement[i][3] = round((df_candleStick["high"][i] + closeHighLine[i]*2) / 3)
                        #     if abs(judgement[i][3] - entryLowLine[i]) > 1000:
                        #         judgement[i][3] = closeHighLine[i] + 1000
                        #     # judgement[i][3] = closeHighLine[i]
                        #     if judgement[i][3] < df_candleStick["open"][i]:
                        #         judgement[i][3] = df_candleStick["open"][i]
                        # # ロングクローズ
                        # if df_candleStick["low"][i] < closeLowLine[i]:
                        #     judgement[i][2] = round((df_candleStick["low"][i] + closeLowLine[i]*2) / 3)
                        #     if abs(judgement[i][2] - closeLowLine[i]) > 1000:
                        #         judgement[i][2] = closeLowLine[i] - 1000
                        #     # judgement[i][2] = closeLowLine[i]
                        #     if judgement[i][2] > df_candleStick["open"][i]:
                        #         judgement[i][2] = df_candleStick["open"][i]

        else:
            pass
            # for i in range(len(df_candleStick.index)):
            #     if i < 2:
            #         continue
            #     if judgement_RSI[i][0] and judgement_BBANDS[i][0] and judgement_CCI[i][0] and judgement_LAST[i][0] and judgement_ROC[i][0]:
            #         judgement[i][0] = df_candleStick["open"][i] # 買いエントリー
            #     if judgement_RSI[i][1] and judgement_BBANDS[i][1] and judgement_CCI[i][1] and judgement_LAST[i][1] and judgement_ROC[i][1]:
            #         judgement[i][1] = df_candleStick["open"][i] # 売りエントリー
            #     if judgement_RSI[i][2] or judgement_BBANDS[i][2] or judgement_CCI[i][2] or judgement_LAST[i][2] or judgement_ROC[i][2]:
            #         judgement[i][2] = df_candleStick["open"][i] # 買いクローズ
            #     if judgement_RSI[i][3] or judgement_BBANDS[i][3] or judgement_CCI[i][3] or judgement_LAST[i][3] or judgement_ROC[i][3]:
            #         judgement[i][3] = df_candleStick["open"][i] # 売りクローズ

        t1 = time.time()

        # 長期EMAのトレンドフィルタ
        # 弱かったのでコメントアウト
        # close = pd.to_numeric(self.df_candleStick_1H['close']).astype(float)
        # self.ma_short = self.calculateMA(close, 12, 'EMA')
        # self.ma_long = self.calculateMA(close, 26, 'EMA')

        # long_trend_1H = [None for i in range(len(self.df_candleStick_1H.index))]
        # for i in range(len(self.df_candleStick_1H.index)):
        #     if self.ma_short[i] >= self.ma_long[i]:
        #         long_trend_1H[i] = 'UP'
        #     elif self.ma_short[i] < self.ma_long[i]:
        #         long_trend_1H[i] = 'DOWN'

        # long_trend = [None for i in range(len(df_candleStick.index))]
        # one_term = len(long_trend) / len(long_trend_1H)
        # for i in range(len(df_candleStick.index)):
        #     long_trend[i] = long_trend_1H[int(i/one_term)]

        # for i in range(len(df_candleStick.index)):
        #     if i == len(df_candleStick.index)-1:
        #         continue
        #     if i == 'None':
        #         continue
        #     if long_trend[i-1] == 'UP':
        #             judgement[i][0] = 0 # 買い新規しない
        #     elif long_trend[i-1] == 'DOWN':
        #             judgement[i][1] = 0 # 売り新規しない

        # トレンドの強弱のフィルタ
        self.trend_adx = [True for i in range(len(df_candleStick.index))]
        self.ADX = abstract.ADX(df_candleStick, timeperiod=14)
        if "ADX" in self.filtter:
            for i in range(len(df_candleStick.index)):
                if i < 14:
                    continue
                if i >= len(df_candleStick)-1:
                    continue
                # トレンドあり
                if self.ADX[i-1] > self.filtter_param:
                    if self.gyakubari:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
                    else:
                        pass
                # トレンドなし
                else:
                    if self.gyakubari:
                        pass
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'SAR' in self.filtter:
            SAR = self.psar(df_candleStick, acceleration=0.02, maximum=0.2)
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                # 上昇トレンド
                if SAR[i-1] <= df_candleStick["close"][i-1] and self.trend_adx[i-1]:
                    judgement[i][1] = 0 # 売り新規しない
                # 下降トレンド
                elif SAR[i-1] > df_candleStick["close"][i-1] and self.trend_adx[i-1]:
                    judgement[i][0] = 0 # 買い新規しない
                else:
                    print(SAR[i-1])
                    print('なにかがおかしい。。')
        if 'CCI' in self.filtter:
            # 最新足の影響大
            self.CCI = abstract.CCI(df_candleStick, timeperiod=14)
            Q1 = self.CCI.quantile(self.filtter_param)
            Q3 = self.CCI.quantile(1-self.filtter_param)
            # Q1 = -165
            # Q3 = 165
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                # 上昇トレンド
                if self.CCI[i-1] > Q3 and self.trend_adx[i-1]:
                    judgement[i][1] = 0 # 売り新規しない
                # 下降トレンド
                if self.CCI[i-1] < Q1 and self.trend_adx[i-1]:
                    judgement[i][0] = 0 # 買い新規しない
                else:
                    # 逆張りはトレンドないときに注文する
                    if self.gyakubari:
                        pass
                    # トレンドフォローはトレンドないときは注文しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'CMO' in self.filtter:
            # 205, 445126JPY, 4.852
            self.CMO = abstract.CMO(df_candleStick, timeperiod=14)
            Q1 = self.CMO.quantile(self.filtter_param)
            Q3 = self.CMO.quantile(1-self.filtter_param)
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                # 上昇トレンド
                if self.CMO[i-1] > Q3 and self.trend_adx[i-1]:
                    judgement[i][1] = 0 # 売り新規しない
                # 下降トレンド
                if self.CMO[i-1] < Q1 and self.trend_adx[i-1]:
                    judgement[i][0] = 0 # 買い新規しない
                else:
                    # 逆張りはトレンドないときに注文する
                    if self.gyakubari:
                        pass
                    # トレンドフォローはトレンドないときは注文しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'MFI' in self.filtter:
            # 180, 260660JPY, 3.703
            self.MFI = abstract.MFI(df_candleStick, timeperiod=14)
            # Q1 = self.MFI.quantile(self.filtter_param)
            # Q3 = self.MFI.quantile(1-self.filtter_param)
            Q1 = self.filtter_param
            Q3 = 100 - self.filtter_param
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                # 上昇トレンド
                if self.MFI[i-1] > Q3 and self.trend_adx[i-1]:
                    judgement[i][0] = 0 # 買い新規しない
                    judgement[i][1] = 0 # 売り新規しない
                # 下降トレンド
                if self.MFI[i-1] < Q1 and self.trend_adx[i-1]:
                    judgement[i][0] = 0 # 買い新規しない
                    judgement[i][1] = 0 # 売り新規しない
                else:
                    # 逆張りはトレンドないときに注文する
                    if self.gyakubari:
                        pass
                    # トレンドフォローはトレンドないときは注文しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'ROC' in self.filtter:
            '''
            オシレーターとはちょっと違うのでいったんコメントアウト
            '''
            # 105, 296372JPY, 5.55
            self.ROC = abstract.ROC(df_candleStick, timeperiod=6)
            time.sleep(100)
            # Q1 = self.ROC.quantile(self.filtter_param)
            # Q3 = self.ROC.quantile(1-self.filtter_param)
            Q1 = -self.filtter_param
            Q3 = self.filtter_param

            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                # 上昇トレンド
                if self.ROC[i-1] > Q3 and self.trend_adx[i-1]:
                    if self.gyakubari:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
                    else:
                        judgement[i][1] = 0 # 売り新規しない
                # 下降トレンド
                if self.ROC[i-1] < Q1 and self.trend_adx[i-1]:
                    if self.gyakubari:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                else:
                    # 逆張りはトレンドないときに注文する
                    if self.gyakubari:
                        pass
                    # トレンドフォローはトレンドないときは注文しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'RSI' in self.filtter:
            # 273, 459359JPY, 3.933
            self.RSI = abstract.RSI(df_candleStick[-30:], timeperiod=14)
            # Q1 = self.RSI.quantile(self.filtter_param)
            # Q3 = self.RSI.quantile(1-self.filtter_param)
            Q1 = self.filtter_param
            Q3 = 100-self.filtter_param
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                if self.RSI[i-1] > 70:
                    judgement[i][0] = 0 # 買い新規しない
                if self.RSI[i-1] > 65:
                    judgement[i][0] = 0 # 買い新規しない
                    
                # 売られすぎ
                elif self.RSI[i-1] < Q1 and self.trend_adx[i-1]:
                    judgement[i][1] = 0 # 売り新規しない
                else:
                    # 逆張りはトレンドないときに注文する
                    if self.gyakubari:
                        pass
                    # トレンドフォローはトレンドないときは注文しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'AROONOSC' in self.filtter:
            high = pd.to_numeric(df_candleStick['high']).astype(float)
            high = np.array(high)
            low = pd.to_numeric(df_candleStick['low']).astype(float)
            low = np.array(low)
            self.AROONOSC = talib.AROONOSC(high, low, timeperiod=14)
            # pandasじゃなくてnumpyで返ってくるので4分位できない。。
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                if i < 16:
                    continue
                # 上昇トレンド
                if self.AROONOSC[i-1] > 60 and self.trend_adx[i-1]:
                    judgement[i][1] = 0 # 売り新規しない
                # 下降トレンド
                elif self.AROONOSC[i-1] < -60 and self.trend_adx[i-1]:
                    judgement[i][0] = 0 # 買い新規しない
                else:
                    # 逆張りはトレンドないときに注文する
                    if self.gyakubari:
                        pass
                    # トレンドフォローはトレンドないときは注文しない
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'NATR' in self.filtter:
            Q1 = 0
            Q3 = 0
            # close = pd.to_numeric(self.df_candleStick_1H['close']).astype(float)
            # self.ma_short = self.calculateMA(close, 12, 'EMA')
            # df_candleStick["std_30"] = [df_candleStick["close"][i-30+1:i].std() for i in range(len(df_candleStick.index))]
            # df_candleStick["std_10"] = [df_candleStick["close"][i-10+1:i].std() for i in range(len(df_candleStick.index))]
            self.NATR = abstract.NATR(df_candleStick, timeperiod=14)

            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                if i < 14:
                    continue
                # ボラがない
                if self.NATR[i-1] < 0.05:
                    if self.gyakubari:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
                    else:
                        pass
                # ボラめっちゃある
                elif self.NATR[i-1] > 0.1:
                    if self.gyakubari:
                        pass
                    else:
                        judgement[i][0] = 0 # 買い新規しない
                        judgement[i][1] = 0 # 売り新規しない
        if 'ore' in self.filtter:
            for i in range(len(df_candleStick.index)):
                if i == len(df_candleStick.index)-1:
                    continue
                if i < 14:
                    continue

                if df_candleStick['close'][i-1] > df_candleStick['open'][i-1] and \
                   df_candleStick['close'][i-2] > df_candleStick['open'][i-2]:
                    pass
                else:
                    judgement[i][0] = 0 # 買い新規しない

                if df_candleStick['close'][i-1] < df_candleStick['open'][i-1] and \
                   df_candleStick['close'][i-2] < df_candleStick['open'][i-2]:
                    pass
                else:
                    judgement[i][1] = 0 # 売り新規しない

        t2 = time.time()
        print('フィルタの計算：{}'.format(t2-t1))

        return judgement

    def calcRSI(self, target, up_or_down, open_or_close):
        term = 14
        target = target/100
        length = len(self.df_candleStick.index)
        target_lines = [0 for i in range(len(self.df_candleStick.index))]
        close_price_range = [x - y for (x, y) in zip(pd.Series.tolist(self.df_candleStick['close'][1:]),  pd.Series.tolist(self.df_candleStick['close'][:-1]))]
        close_price_range.insert(0, 0)
        close_price_range_abs = [abs(i) for i in close_price_range]
        for i in range(0,length):
            if i < term+1:
                continue
            positive_value = [i for i in close_price_range[i-term:i] if i > 0]

            # 上昇してtarget_lineにあたるか下降してあたるかで計算式が異なる
            if up_or_down == 'up':
                now_price_range = (target * sum(close_price_range_abs[i-term:i]) - sum(positive_value)) / (1 - target)
                if now_price_range < 0:
                    if open_or_close == 'open':
                        now_price_range = 9999999
                    elif open_or_close == 'close':
                        now_price_range = 0
            elif up_or_down == 'down':
                now_price_range = (sum(positive_value) - target * sum(close_price_range_abs[i-term:i])) / target
                if now_price_range > 0:
                    now_price_range = -now_price_range
            target_lines[i] = self.df_candleStick['close'][i-1] + now_price_range

        return target_lines

    # 自作パラボリックSAR
    def psar(self, df_candleStick, acceleration = 0.02, maximum = 0.2):
        length = len(df_candleStick.index)
        high = df_candleStick['high']
        low = df_candleStick['low']
        close = df_candleStick['close']
        psar = [0 for i in range(len(df_candleStick.index))]
        trend = [True for i in range(len(df_candleStick.index))]
        af = acceleration
        ep = [0 for i in range(len(df_candleStick.index))]

        for i in range(1,length):

            # 最初は終値でトレンド判定
            if i == 1:
                if close[1] > close[0]:
                    trend[1] = True
                    ep[1] = high[1]
                    psar[1] = high[0]
                else:
                    trend[1] = False
                    ep[1] = low[1]
                    psar[1] = low[0]
                continue
            
            # トレンドを更新
            if trend[i-1]:
                if low[i] < psar[i-1]:
                    trend[i] = False
                else:
                    trend[i] = trend[i-1]
            else:
                if high[i] > psar[i-1]:
                    trend[i] = True
                else:
                    trend[i] = trend[i-1]

            # ep（最大・最小）を更新する
            if trend[i]:
                if low[i] < psar[i-1]:
                    ep[i] = low[i]
                else:
                    ep[i] = max(high[i], ep[i-1])
            else:
                if high[i] > psar[i-1]:
                    ep[i] = high[i]
                else:
                    ep[i] = min(low[i], ep[i-1])
            
            # パラボリックSAR値を計算する
            if trend[i] == trend[i-1]:
                if ep[i] != ep[i-1]:
                    af = min(af + acceleration, maximum)
                psar[i] = psar[i - 1] + af * (ep[i] - psar[i - 1])
            else:
                af = acceleration
                psar[i] = ep[i-1]

        return psar

    def calculateMA(self, close, term, method):
        """
        終値からEMAを計算する
        :param close: 終値が入ったiterable, term: EMAを計算する期間
        :return: EMAが入ったlist
        """

        try:
            ma = eval('talib.' + method + '(np.array(close), term)')
        except:
            close = close.astype(float)
            ma = eval('talib.' + method + '(close, term)')
        return ma

    def test_calculateCROSS(self):
        '''
        クロス価格関数のテストを行う
        '''
        close = np.arange(1, 5, 1)
        tmp = np.arange(5, 1, -1)
        close = np.hstack((close, tmp))
        tmp = np.arange(1, 6, 1)
        close = np.hstack((close, tmp))
        # print(close)
        # [1 2 3 4 5 4 3 2 1 2 3 4 5]
        shortTerm = 4
        longTerm = 6
        ma_short = self.calculateMA(close, shortTerm, self.method)
        ma_long = self.calculateMA(close, longTerm, self.method)
        CROSS = self.calculateCROSS(close, shortTerm, longTerm)
        print(CROSS)

        # 正解リスト
        ac_list = [0, 0, 0, 0, 0, -3.0, 2.0, 9.0, 12.0, 9.0, 4.0, -3.0, -6.0]
        if CROSS == ac_list:
            print('OK')
        else:
            print('NG')

    def calculateCROSS(self, close, shortTerm, longTerm):
        '''
        次の足の中で価格が何円になれば短期MAと長期MAがクロスするかを計算する
        これを使えば指値が使えるので
        '''
        cross_price = [0 for i in range(len(close))]
        for i in range(len(close)):
            if i < longTerm-1:
                cross_price[i] = 0
                continue
            short_sum = sum(close[i-shortTerm+2:i+1])
            long_sum = sum(close[i-longTerm+2:i+1])
            cross_price[i] = (shortTerm*long_sum - longTerm*short_sum)/(longTerm-shortTerm)
        return cross_price

    #エントリーラインおよびクローズラインで約定すると仮定する．
    def backtest(self, judgement, df_candleStick, lot, rangeTh, rangeTerm, originalWaitTerm=10, waitTh=10000, cost = 0, priceRange=None):

        #エントリーポイント，クローズポイントを入れるリスト
        buyEntrySignals = []
        sellEntrySignals = []
        buyCloseSignals = []
        sellCloseSignals = []
        nOfTrade = 0
        pos = 0
        # pl = []
        pl = [0 for i in range(len(df_candleStick.index))]
        # pl.append(0)
        #トレードごとの損益
        plPerTrade = []
        #取引時の価格を入れる配列．この価格でバックテストのplを計算する．（ので，どの価格で約定するかはテストのパフォーマンスに大きく影響を与える．）
        buy_entry = []
        buy_close = []
        sell_entry = []
        sell_close = []
        #各ローソク足について，レンジ相場かどうかの判定が入っている配列
        isRange =  self.isRange(df_candleStick, rangeTerm, rangeTh)
        #基本ロット．勝ちトレードの直後はロットを落とす．
        originalLot = lot
        #勝ちトレード後，何回のトレードでロットを落とすか．
        waitTerm = 0
        # 取引履歴 [time, order, price, profit]
        trade_log = []
        # 損切後の休憩時間
        sonngiri_wait_term = 0
        max_or_min_price = 9999999
        flg_trail = False

        for i in range(len(df_candleStick.index)):
            if i == 0:
                continue
            if i == len(judgement)-1:
                continue

            if sonngiri_wait_term != 0:
                sonngiri_wait_term -= 1
                pl[i] = pl[i-1]
                continue

            # 損切ロジック
            sonngiri_range = 0
            if sonngiri_range != 0:
                if pos == 1 and buy_entry[-1]-sonngiri_range > df_candleStick["close"][i-1]:
                    nOfTrade += 1
                    pos -= 1
                    # 実際は始値よりブレるだろうけととりあえず。。
                    buy_close.append(df_candleStick["open"][i])
                    #値幅
                    plRange = buy_close[-1] - buy_entry[-1]
                    pl[i] = pl[i-1] + (buy_close[-1] - df_candleStick['close'][i-1])
                    pl[i] = pl[i] - self.cost * lot
                    buyCloseSignals.append(df_candleStick.index[i])
                    plPerTrade.append((plRange-self.cost)*lot)
                    trade_log.append([df_candleStick.index[i], 'buy  close', buy_close[-1] , (plRange-self.cost)*lot])
                    sonngiri_wait_term = 5
                    continue
                elif pos == -1 and sell_entry[-1]+sonngiri_range < df_candleStick["close"][i-1]:
                    nOfTrade += 1
                    pos += 1
                    # 実際は始値よりブレるだろうけととりあえず。。
                    sell_close.append(df_candleStick["open"][i])
                    plRange = sell_entry[-1] - sell_close[-1]
                    pl[i] = pl[i-1] + (df_candleStick['close'][i-1] - sell_close[-1])
                    pl[i] = pl[i] - self.cost * lot
                    sellCloseSignals.append(df_candleStick.index[i])
                    plPerTrade.append((plRange-self.cost)*lot)
                    trade_log.append([df_candleStick.index[i], 'sell close', sell_close[-1], (plRange-self.cost)*lot])
                    sonngiri_wait_term = 5
                    continue

            # トレーリング幅の計算
            priceRangeMean = sum(priceRange[i-self.entryTerm:i]) / self.entryTerm
            offset = 1000000
            offset_cost = 1000

            #エントリーロジック
            if pos == 0 and not isRange[i]:

                # 買いと売りの両方約定した場合
                # 実際は先にショートエントリーの可能性もあるが損益に影響ないのでロングエントリーとみなす
                if judgement[i][0] != 0 and judgement[i][1] != 0:
                    # 順張りのトレーディングストップの場合は先にTRAIL注文が刺さるので両方約定しない想定
                    if self.gyakubari == False and self.trailingStop == True:
                        pass
                    else:
                        #ロングエントリー
                        buy_entry.append(judgement[i][0])
                        buyEntrySignals.append(df_candleStick.index[i])
                        trade_log.append([df_candleStick.index[i], 'buy  entry', judgement[i][0]])

                        # ロングクローズロジック（本来はショートエントリー）
                        nOfTrade += 1
                        buy_close.append(judgement[i][1])
                        plRange = buy_close[-1] - buy_entry[-1]
                        buyCloseSignals.append(df_candleStick.index[i])
                        plPerTrade.append((plRange-self.cost)*lot)
                        trade_log.append([df_candleStick.index[i], 'buy  close2', judgement[i][1], (plRange-self.cost)*lot])

                        pl[i] = pl[i-1] + (judgement[i][1] - judgement[i][0])
                        pl[i] = pl[i] - self.cost * lot # 新規
                        pl[i] = pl[i] - self.cost * lot # 決済

                #ロングエントリー
                elif judgement[i][0] != 0:
                    pos += 1
                    buy_entry.append(judgement[i][0])

                    if self.trailingStop:

                        # エントリーした足の中でトレーリングストップにかかった場合
                        if self.gyakubari:
                            if  judgement[i][0] - offset > df_candleStick['low'][i]:
                                # judgement[i][2] = ((judgement[i][0] - offset)*2 + df_candleStick['low'][i]*1)/3
                                judgement[i][2] = judgement[i][0] - offset - offset_cost
                                flg_trail = True
                                sonngiri_wait_term = 0
                        else:
                            if  df_candleStick['high'][i] - offset > df_candleStick['close'][i]:
                                # judgement[i][2] = ((df_candleStick['high'][i] - offset)*2 + df_candleStick['low'][i]*1)/3
                                judgement[i][2] = df_candleStick['high'][i] - offset - offset_cost
                                flg_trail = True
                                sonngiri_wait_term = 5

                        if flg_trail:

                            # 買いの処理
                            buyEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'buy  entry', buy_entry[-1]])

                            # 売りの処理
                            buy_close.append(judgement[i][2])
                            buyCloseSignals.append(df_candleStick.index[i])

                            # 損益計算
                            plRange = buy_close[-1] - buy_entry[-1]
                            pl[i] = pl[i-1] + plRange - self.cost * lot
                            plPerTrade.append((plRange-self.cost)*lot)
                            trade_log.append([df_candleStick.index[i], 'buy  close', buy_close[-1], (plRange-self.cost)*lot])

                            nOfTrade += 1
                            pos -= 1
                            max_or_min_price = 9999999
                            flg_trail = False

                        # かからなかった場合
                        else:
                            pl[i] = pl[i-1] + (df_candleStick['close'][i] - buy_entry[-1])
                            pl[i] = pl[i] - self.cost * lot
                            buyEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'buy  entry', judgement[i][0]])
                            if self.gyakubari:
                                max_or_min_price = judgement[i][0]
                            else:
                                max_or_min_price = df_candleStick['high'][i]

                    # シンプル指値
                    else:
                        # 同じ足の中で売りエントリーが刺さった場合
                        if judgement[i][1] != 0:
                            nOfTrade += 1
                            pos -= 1
                            buy_close.append(judgement[i][1])
                            #値幅
                            plRange = buy_close[-1] - buy_entry[-1]
                            pl[i] = pl[i-1] + plRange - self.cost * lot
                            buyCloseSignals.append(df_candleStick.index[i])
                            plPerTrade.append((plRange-self.cost)*lot)
                            trade_log.append([df_candleStick.index[i], 'buy  close', judgement[i][1], (plRange-self.cost)*lot])

                        # 刺さらなかった場合
                        else:
                            pl[i] = pl[i-1] + (df_candleStick['close'][i] - buy_entry[-1])
                            pl[i] = pl[i] - self.cost * lot
                            buyEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'buy  entry', judgement[i][0]])

                    continue

                #ショートエントリー
                elif judgement[i][1] != 0:
                    pos -= 1
                    sell_entry.append(judgement[i][1])

                    if self.trailingStop:

                        # エントリーした足の中でトレーリングストップにかかった場合
                        if self.gyakubari:
                            if  judgement[i][1] + offset < df_candleStick['high'][i]:
                                # judgement[i][3] = ((judgement[i][1] + offset)*2 + df_candleStick['high'][i]*1)/3
                                judgement[i][3] = judgement[i][1] + offset + offset_cost
                                flg_trail = True
                                sonngiri_wait_term = 0
                        else:
                            if  df_candleStick['low'][i] + offset < df_candleStick['close'][i]:
                                # judgement[i][3] = ((df_candleStick['low'][i] + offset)*2 + df_candleStick['high'][i]*1)/3
                                judgement[i][3] = df_candleStick['low'][i] + offset + offset_cost
                                flg_trail = True
                                sonngiri_wait_term = 0

                        if flg_trail:

                            # 売りの処理
                            sellEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'sell  entry', judgement[i][1]])

                            # 買いの処理
                            sell_close.append(judgement[i][3])
                            sellCloseSignals.append(df_candleStick.index[i])

                            # 損益計算
                            plRange = sell_entry[-1] - sell_close[-1]
                            pl[i] = pl[i-1] + plRange - self.cost * lot
                            plPerTrade.append((plRange-self.cost)*lot)
                            trade_log.append([df_candleStick.index[i], 'sell  close', sell_close[-1], (plRange-self.cost)*lot])

                            nOfTrade += 1
                            pos += 1
                            max_or_min_price = 9999999
                            flg_trail = False

                        # かからなかった場合
                        else:
                            pl[i] = pl[i-1] + (sell_entry[-1] - df_candleStick['close'][i])
                            pl[i] = pl[i] - self.cost * lot
                            sellEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'sell entry', judgement[i][1]])
                            if self.gyakubari:
                                max_or_min_price = judgement[i][1]
                            else:
                                max_or_min_price = df_candleStick['low'][i]

                    # シンプル指値
                    else:
                        pl[i] = pl[i-1] + (sell_entry[-1] - df_candleStick['close'][i])
                        pl[i] = pl[i] - self.cost * lot
                        sellEntrySignals.append(df_candleStick.index[i])
                        trade_log.append([df_candleStick.index[i], 'sell entry', judgement[i][1]])

                else:
                    # ノーポジ継続
                    pl[i] = pl[i-1]

            #ロングクポジ
            elif pos == 1:

                # トレーリングストップ
                if self.trailingStop:
                    if  max_or_min_price - offset > df_candleStick['low'][i]:
                        flg_trail = True
                        sonngiri_wait_term = 0
                        judgement[i][2] = max_or_min_price - offset - offset_cost

                #ロングクローズ
                if judgement[i][2] != 0 or flg_trail:
                    flg_trail = False
                    nOfTrade += 1
                    pos -= 1
                    buy_close.append(judgement[i][2])
                    #値幅
                    plRange = buy_close[-1] - buy_entry[-1]
                    pl[i] = pl[i-1] + (buy_close[-1] - df_candleStick['close'][i-1])
                    pl[i] = pl[i] - self.cost * lot
                    buyCloseSignals.append(df_candleStick.index[i])
                    plPerTrade.append((plRange-self.cost)*lot)
                    trade_log.append([df_candleStick.index[i], 'buy  close', judgement[i][2], (plRange-self.cost)*lot])
                    max_or_min_price = 9999999

                    #さらに，クローズしたと同時にエントリーシグナルが出ていた場合のロジック．
                    # 逆張りは指値なので無理
                    if self.gyakubari or self.trailingStop:
                        pass
                    else:
                        # ショートエントリー
                        if judgement[i][1] != 0:
                            pos -= 1
                            sell_entry.append(judgement[i][1])
                            pl[i] = pl[i-1] + (buy_close[-1] - df_candleStick['close'][i-1])
                            pl[i] = pl[i] + (sell_entry[-1] - df_candleStick['close'][i])
                            pl[i] = pl[i] - self.cost * lot
                            sellEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'sell entry2', judgement[i][1]])

                else:
                    # ロング継続
                    pl[i] = pl[i-1] + (df_candleStick['close'][i] - df_candleStick['close'][i-1])
                    if max_or_min_price < df_candleStick['high'][i]:
                        max_or_min_price = df_candleStick['high'][i]

            #ショートポジ
            elif pos == -1:

                # トレーリングストップ
                if self.trailingStop:
                    if  max_or_min_price + offset < df_candleStick['high'][i]:
                            flg_trail = True
                            sonngiri_wait_term = 0
                            judgement[i][3] = max_or_min_price + offset + offset_cost

                #ショートクローズ
                if judgement[i][3] != 0 or flg_trail:
                    flg_trail = False
                    nOfTrade += 1
                    pos += 1
                    sell_close.append(judgement[i][3])
                    plRange = sell_entry[-1] - sell_close[-1]
                    pl[i] = pl[i-1] + (df_candleStick['close'][i-1] - sell_close[-1])
                    pl[i] = pl[i] - self.cost * lot
                    sellCloseSignals.append(df_candleStick.index[i])
                    plPerTrade.append((plRange-self.cost)*lot)
                    trade_log.append([df_candleStick.index[i], 'sell close', judgement[i][3], (plRange-self.cost)*lot])
                    max_or_min_price = 9999999

                    #さらに，クローズしたと同時にエントリーシグナルが出ていた場合のロジック
                    # 逆張りは指値なので無理
                    if self.gyakubari or self.trailingStop:
                        pass
                    else:
                        # ロングエントリー
                        if judgement[i][0] != 0:
                            pos += 1
                            buy_entry.append(judgement[i][0])
                            pl[i] = pl[i-1] + (df_candleStick['close'][i-1] - sell_close[-1])
                            pl[i] = pl[i] + (df_candleStick['close'][i] - buy_entry[-1])
                            pl[i] = pl[i] - self.cost * lot
                            buyEntrySignals.append(df_candleStick.index[i])
                            trade_log.append([df_candleStick.index[i], 'buy  entry', judgement[i][0]])

                else:
                    # ショート継続
                    pl[i] = pl[i-1] + (df_candleStick['close'][i-1] - df_candleStick['close'][i])
                    if max_or_min_price > df_candleStick['low'][i]:
                        max_or_min_price = df_candleStick['low'][i]

        #最後にポジションを持っていたら，期間最後のローソク足の終値で反対売買．
        if pos == 1:
            buy_close.append(df_candleStick["close"][-1])
            plRange = buy_close[-1] - buy_entry[-1]
            pl[-1] = pl[-2] + (buy_close[-1] - df_candleStick['close'][-2])
            pl[-1] = pl[-1] - self.cost * lot
            pos -= 1
            buyCloseSignals.append(df_candleStick.index[-1])
            nOfTrade += 1
            plPerTrade.append(plRange*lot)
            trade_log.append([df_candleStick.index[-1], 'buy  close', df_candleStick["close"][-1], plRange*lot])
        elif pos ==-1:
            sell_close.append(df_candleStick["close"][-1])
            plRange = sell_entry[-1] - sell_close[-1]
            pl[-1] = pl[-2] + (df_candleStick['close'][-2] - sell_close[-1])
            pl[-1] = pl[-1] - self.cost * lot
            pos +=1
            sellCloseSignals.append(df_candleStick.index[-1])
            nOfTrade += 1
            plPerTrade.append(plRange*lot)
            trade_log.append([df_candleStick.index[-1], 'sell close', df_candleStick["close"][-1], plRange*lot])
        else:
            pl[-1] = pl[-2]

        return (pl, buyEntrySignals, sellEntrySignals, buyCloseSignals, sellCloseSignals, nOfTrade, plPerTrade, trade_log)

    def readFile(self):
        # ファイル読み込みと足変換は最初の１回だけ行う
        # dfはエラーになるが、エラー＝df存在とみなす
        try:
            if self.df_candleStick:
                return
        except:
            return

        t1 = time.time()
        if self.fileName == None:
            if "H" in self.candleTerm:
                candleStick = self.cryptowatch.getSpecifiedCandlestick(2000, "3600")
            elif "30T" in self.candleTerm:
                candleStick = self.cryptowatch.getSpecifiedCandlestick(4000, "1800")
            elif "15T" in self.candleTerm:
                candleStick = self.cryptowatch.getSpecifiedCandlestick(5999, "900")
            elif "5T" in self.candleTerm:
                candleStick = self.cryptowatch.getSpecifiedCandlestick(5999, "300")
            elif "3T" in self.candleTerm:
                candleStick = self.cryptowatch.getSpecifiedCandlestick(5999, "180")
            else:
                candleStick = self.cryptowatch.getSpecifiedCandlestick(5999, "60")
        else:
            candleStick = self.readDataFromFile(self.fileName)

        t2 = time.time()
        print('ファイル読み込み：{}'.format(t2-t1))
        if self.candleTerm != None:
            self.df_candleStick = self.processCandleStick(candleStick, self.candleTerm)
            self.df_candleStick_1H = self.processCandleStick(candleStick, '1H')
        else:
            self.df_candleStick = self.fromListToDF(candleStick)

        # モデル評価のため、テスト日数を取得する
        df_len = len(self.df_candleStick)
        if "60T" in self.candleTerm:
            self.nissuu = df_len/24
        elif "30T" in self.candleTerm:
            self.nissuu = df_len/2/24
        elif "15T" in self.candleTerm:
            self.nissuu = df_len/4/24
        elif "5T" in self.candleTerm:
            self.nissuu = df_len/12/24
        elif "1T" in self.candleTerm:
            self.nissuu = df_len/60/24
        else:
            print('self.candleTermが不正です！:{}'.format(self.candleTerm))
            sys.exit()

        t3 = time.time()
        print('足変換：{}'.format(t3-t2))

    def calcPriceRange(self):
        try:
            if self.priceRange:
                return
        except:
            return

        t1 = time.time()
        self.priceRange = self.calculatePriceRange(self.df_candleStick, 1)
        t2 = time.time()
        print('値幅の計算：{}'.format(t2-t1))

    def describeResult(self):
        """
        signalsは買い，売り，中立が入った配列
        """

        t3 = time.time()

        entryLowLine, entryHighLine = self.calculateLines(self.df_candleStick, self.entryTerm, self.rangePercent, self.rangePercentTerm, 'entry', self.priceRange)

        t4 = time.time()
        print('エントリーライン生成：{}'.format(t4-t3))

        closeLowLine, closeHighLine = self.calculateLines(self.df_candleStick, self.closeTerm, self.rangePercent, self.rangePercentTerm, 'close', self.priceRange)

        t5 = time.time()
        print('クローズライン生成：{}'.format(t5-t4))

        judgement = self.judge(self.df_candleStick, entryHighLine, entryLowLine, closeHighLine, closeLowLine, self.entryTerm, self.closeTerm)

        t6 = time.time()
        print('売買判定：{}'.format(t6-t5))

        pl, buyEntrySignals, sellEntrySignals, buyCloseSignals, sellCloseSignals, nOfTrade, plPerTrade, tradeLog = self.backtest(judgement, self.df_candleStick, 1, self.rangeTh, self.rangeTerm, self.waitTerm, self.waitTh, self.cost, self.priceRange)

        t7 = time.time()
        print('バックテスト実行：{}'.format(t7-t6))

        if self.showFigure:
            from src import candle_plot
            candle_plot.show(self.df_candleStick, pl, buyEntrySignals, sellEntrySignals, buyCloseSignals, sellCloseSignals, self.ma_short, self.ma_long, self.myOHLC)
        elif self.sendFigure:
            from src import candle_plot
            # save as png
            today = datetime.datetime.now().strftime('%Y%m%d')
            number = "_" + str(len(pl))
            fileName = "png/" + today + number + ".png"
            candle_plot.save(self.df_candleStick, pl, buyEntrySignals, sellEntrySignals, buyCloseSignals, sellCloseSignals, fileName)
            self.lineNotify("Result of backtest",fileName)
        else:
            pass

        #各統計量の計算および表示．
        winTrade = sum([1 for i in plPerTrade if i > 0])
        loseTrade = sum([1 for i in plPerTrade if i < 0])
        try:
            winPer = round(winTrade/(winTrade+loseTrade) * 100,2)
        except:
            winPer = 100

        winTotal = sum([i for i in plPerTrade if i > 0])
        loseTotal = sum([i for i in plPerTrade if i < 0])
        try:
            profitFactor = round(winTotal/-loseTotal, 3)
        except:
            # profitFactor = float("inf")
            profitFactor = 0

        maxProfit = max(plPerTrade, default=0)
        maxLoss = min(plPerTrade, default=0)

        try:
            winAve = winTotal / winTrade
        except:
            winAve = 0

        try:
            loseAve = loseTotal / loseTrade
        except:
            loseAve = 0

        winDec = winPer / 100
        ev = round(winDec * winAve + (1-winDec) * loseAve, 3)

        logging.info('showFigure :%s, sendFigure :%s',self.showFigure, self.sendFigure)
        logging.info('Period: %s > %s', self.df_candleStick.index[0], self.df_candleStick.index[-1])
        logging.info("Total pl: {}".format(int(pl[-1])))
        logging.info("pl per 1-day: {}".format(int(pl[-1]/self.nissuu)))
        logging.info("The number of Trades: {}".format(nOfTrade))
        logging.info("trades per 1-day: {}".format(round(nOfTrade/self.nissuu, 2)))
        logging.info("The Winning percentage: {}%".format(winPer))
        logging.info("Expected value: {}".format(ev))
        logging.info("The profitFactor: {}".format(profitFactor))
        logging.info("The maximum Profit and Loss: {}JPY, {}JPY".format(maxProfit, maxLoss))
        if self.showTradeDetail:
            logging.info("==Trade detail==")
            for log in tradeLog:
                profit = log[3] if len(log) > 3 else ''
                logging.info("%s %s %s %s", log[0], log[1], log[2], profit)
            logging.info("============")

        return pl[-1], profitFactor, maxLoss, winPer, ev, nOfTrade, winTotal, loseTotal

    def fromListToDF(self, candleStick):
        """
        Listのローソク足をpandasデータフレームへ．
        """
        date = [price[0] for price in candleStick]
        priceOpen = [float(price[1]) for price in candleStick]
        priceHigh = [float(price[2]) for price in candleStick]
        priceLow = [float(price[3]) for price in candleStick]
        priceClose = [float(price[4]) for price in candleStick]
        if self.myOHLC:
            pass
        else:
            volume = [float(price[5]) for price in candleStick]

        # 完了時刻⇒開始時刻に変換
        # これをしないとresampleで1分ズレるので
        # 約定履歴から生成したOHLCは開始時刻になってるのでcryptowatchが対象
        if self.fileName == 'chart_new.csv':
            del date[-1]
            del priceOpen[0]
            del priceHigh[0]
            del priceLow[0]
            del priceClose[0]
            if self.myOHLC:
                pass
            else:
                del volume[0]

        date_datetime = map(datetime.datetime.fromtimestamp, date)
        dti = pd.DatetimeIndex(date_datetime)
        if self.myOHLC:
            df_candleStick = pd.DataFrame({"open" : priceOpen, "high" : priceHigh, "low": priceLow, "close" : priceClose}, index=dti)
        else:
            df_candleStick = pd.DataFrame({"open" : priceOpen, "high" : priceHigh, "low": priceLow, "close" : priceClose, "volume" : volume}, index=dti)
        return df_candleStick

    def processCandleStick(self, candleStick, timeScale):
        """
        1分足データから各時間軸のデータを作成.timeScaleには5T（5分），H（1時間）などの文字列を入れる
        """
        df_candleStick = self.fromListToDF(candleStick)
        if self.myOHLC:
            processed_candleStick = df_candleStick.resample(timeScale).agg({'open': 'first','high': 'max','low': 'min','close': 'last'})
        else:
            processed_candleStick = df_candleStick.resample(timeScale).agg({'open': 'first','high': 'max','low': 'min','close': 'last',"volume" : "sum"})
        processed_candleStick = processed_candleStick.dropna()
        processed_candleStick.to_csv('resample.csv')
        return processed_candleStick

    #csvファイル（ヘッダなし）からohlcデータを作成．
    def readDataFromFile(self, filename):
        with open(filename, 'r', encoding="utf-16") as f:
            reader = csv.reader(f)
            header = next(reader)
            for row in reader:
                candleStick = [row for row in reader if row[4] != "0"]
        try:
            dtDate = [datetime.datetime.strptime(data[0], '%Y-%m-%d %H:%M:%S') for data in candleStick]
            dtTimeStamp = [dt.timestamp() for dt in dtDate]
        # myOHLC
        except:
            dtTimeStamp = [data[0] for data in candleStick]
        for i in range(len(candleStick)):
            candleStick[i][0] = dtTimeStamp[i]
        candleStick = [[float(i) for i in data] for data in candleStick]
        return candleStick

    def lineNotify(self, message, fileName=None):
        payload = {'message': message}
        headers = {'Authorization': 'Bearer ' + self.line_notify_token}
        if fileName == None:
            try:
                requests.post(self.line_notify_api, data=payload, headers=headers)
            except:
                pass
        else:
            try:
                files = {"imageFile": open(fileName, "rb")}
                requests.post(self.line_notify_api, data=payload, headers=headers, files = files)
            except:
                pass

    def describePLForNotification(self, pl, df_candleStick):
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            close = df_candleStick["close"]
            index = range(len(pl))
            # figure
            fig = plt.figure(figsize=(20,12))
            #for price
            ax = fig.add_subplot(2, 1, 1)
            ax.plot(df_candleStick.index, close)
            ax.set_xlabel('Time')
            # y axis
            ax.set_ylabel('The price[JPY]')
            #for PLcurve
            ax = fig.add_subplot(2, 1, 2)
            # plot
            ax.plot(index, pl, color='b', label='The PL curve')
            ax.plot(index, [0]*len(pl), color='b',)
            # x axis
            ax.set_xlabel('The number of Trade')
            # y axis
            ax.set_ylabel('The estimated Profit/Loss(JPY)')
            # legend and title
            ax.legend(loc='best')
            ax.set_title('The PL curve(Time span:{})'.format(self.candleTerm))
            # save as png
            today = datetime.datetime.now().strftime('%Y%m%d')
            number = "_" + str(len(pl))
            fileName = "png/" + today + number + ".png"
            plt.savefig(fileName)
            plt.close()
        except:
            fileName = ""
        return fileName

    def executionsProcess(self):
        """
        pubnubで価格を取得する場合の処理（基本的に不要．）
        """
        channels = ["lightning_executions_FX_BTC_JPY"]
        executions = self.executions
        class BFSubscriberCallback(SubscribeCallback):
            def message(self, pubnub, message):
                execution = message.message
                for i in execution:
                    executions.append(i)

        config = PNConfiguration()
        config.subscribe_key = 'sub-c-52a9ab50-291b-11e5-baaa-0619f8945a4f'
        config.reconnect_policy = PNReconnectionPolicy.EXPONENTIAL
        config.ssl = False
        config.set_presence_timeout(60)
        pubnub = PubNubTornado(config)
        listener = BFSubscriberCallback()
        pubnub.add_listener(listener)
        pubnub.subscribe().channels(channels).execute()
        pubnubThread = threading.Thread(target=pubnub.start)
        pubnubThread.start()

    def executionsWebsocket(self):
        """
        Websocketで価格を取得する場合の処理
        """
        executions = self.executions
        def on_message(ws, message):
            messages = json.loads(message)
            execution = messages["params"]["message"]
            for i in execution:
                executions.append(i)

        def on_error(ws, error):
            logging.error(error)

        def on_close(ws):
            while True:
                time.sleep(3)
                try:
                    ws = websocket.WebSocketApp("wss://ws.lightstream.bitflyer.com/json-rpc",
                                                on_message = on_message,
                                                on_error = on_error,
                                                on_close = on_close)
                    ws.on_open = on_open
                    ws.run_forever()
                except Exception as e:
                    logging.error(e)

        def on_open(ws):
            ws.send(json.dumps({"method": "subscribe", "params": {"channel": "lightning_executions_FX_BTC_JPY"}}))

        ws = websocket.WebSocketApp("wss://ws.lightstream.bitflyer.com/json-rpc",
                                    on_message = on_message,
                                    on_error = on_error,
                                    on_close = on_close)
        ws.on_open = on_open
        websocketThread = threading.Thread(target=ws.run_forever)
        websocketThread.start()

    def spotExecutionsWebsocket(self):
        """
        Websocketで価格を取得する場合の処理
        """
        spotExecutions = self.spotExecutions
        def on_message(ws, message):
            messages = json.loads(message)
            spotExecution = messages["params"]["message"]
            for i in spotExecution:
                spotExecutions.append(i)

        def on_error(ws, error):
            logging.error(error)

        def on_close(ws):
            while True:
                time.sleep(3)
                try:
                    ws = websocket.WebSocketApp("wss://ws.lightstream.bitflyer.com/json-rpc",
                                                on_message = on_message,
                                                on_error = on_error,
                                                on_close = on_close)
                    ws.on_open = on_open
                    ws.run_forever()
                except Exception as e:
                    logging.error(e)

        def on_open(ws):
            ws.send(json.dumps({"method": "subscribe", "params": {"channel": "lightning_executions_BTC_JPY"}}))

        ws = websocket.WebSocketApp("wss://ws.lightstream.bitflyer.com/json-rpc",
                                    on_message = on_message,
                                    on_error = on_error,
                                    on_close = on_close)
        ws.on_open = on_open
        websocketThread = threading.Thread(target=ws.run_forever)
        websocketThread.start()
