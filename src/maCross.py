#_*_ coding: utf-8 _*_
from src import channel
import talib

class MACross(channel.ChannelBreakOut):
   def __init__(self):
       super().__init__()

   def judge(self,df_candle_stick, a=0, b=0, c=0, d=0, e=0, f=0):
       """
       バックテスト用の売り買い判断を行う関数．judgementリストは[買いエントリー，売りエントリー，買いクローズ（売り），売りクローズ（買い）]のリスト(つまり「二次元リスト)になっている．リスト内リストの要素は，0（シグナルなし）,シグナル点灯時価格（シグナル点灯時のみ）を取る．
       if Trueの部分にシグナル点灯条件を入れる．
       """
       #EMAの計算
       ema_short = self.calculateEMA(df_candle_stick["close"], self.entryTerm)
       ema_long = self.calculateEMA(df_candle_stick["close"], self.closeTerm)

       judgement = [[0,0,0,0] for i in range(len(ema_short))]
       for i in range(len(ema_short)):
           #ゴールデンクロス
           if ema_short[i-1] > ema_long[i-1] and ema_short[i-2] < ema_long[i-2]:
               judgement[i][0] = df_candle_stick["open"][i] # 始値でロングエントリー
               judgement[i][3] = df_candle_stick["open"][i] # 始値でショートクローズ
           #デッドクロス．ショートエントリー
           if ema_short[i-1] < ema_long[i-1] and ema_short[i-2] > ema_long[i-2]:
               judgement[i][1] = df_candle_stick["open"][i] # 始値でショートエントリー
               judgement[i][2] = df_candle_stick["open"][i] # 始値でロングクローズ
       return judgement

   def calculateEMA(self, close, term):
       """
       終値からEMAを計算する
       :param close: 終値が入ったiterable, term: EMAを計算する期間
       :return: EMAが入ったlist
       """

       ema = [(sum(close[i-term:i]) + close[i])/(term+1) for i in range(len(close))]
       return ema
