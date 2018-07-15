#_*_ coding: utf-8 _*_
#https://sshuhei.com

import json
import logging
import time
import itertools
from src import channel
from src import maCross
from hyperopt import fmin, tpe, hp

import math

def describe(params):
    # パラメータごとに呼ばれる

    i, j, k, l, m, cost, mlMode, fileName, n, o, p, q, r, s, t = params

    # ファイル読み込みは最初の１回だけ
    global channelBreakOut
    if channelBreakOut:
        pass
    else:
        channelBreakOut = channel.ChannelBreakOut()
        # channelBreakOut = maCross.MACross()

    channelBreakOut.entryTerm = i[0]
    channelBreakOut.closeTerm = i[1]
    channelBreakOut.rangeTh = j[0]
    channelBreakOut.rangeTerm = j[1]
    channelBreakOut.waitTerm = k[0]
    channelBreakOut.waitTh = k[1]
    channelBreakOut.rangePercent = l[0]
    channelBreakOut.rangePercentTerm = l[1]
    channelBreakOut.candleTerm = str(m) + "T"
    channelBreakOut.cost = cost
    channelBreakOut.fileName = fileName
    channelBreakOut.method = n
    channelBreakOut.price_range_limit = o
    channelBreakOut.tmp2 = p
    channelBreakOut.tmp1 = q
    channelBreakOut.filtter_param = r
    channelBreakOut.filtter = s
    channelBreakOut.offset_raitio = t
    channelBreakOut.showFigure = False

    logging.info("===========Test pattern===========")
    logging.info('fileName:%s',channelBreakOut.fileName)
    logging.info('method:%s',channelBreakOut.method)
    logging.info('candleTerm:%s',channelBreakOut.candleTerm)
    logging.info('rangePercent:%s rangePercentTerm:%s',channelBreakOut.rangePercent,channelBreakOut.rangePercentTerm)
    logging.info('entryTerm:%s closeTerm:%s',channelBreakOut.entryTerm,channelBreakOut.closeTerm)
    logging.info('gyakubari:%s',channelBreakOut.gyakubari)
    logging.info('filtter_param:%s',channelBreakOut.filtter_param)
    logging.info('tmp1:%s',channelBreakOut.tmp1)
    logging.info('tmp2:%s',channelBreakOut.tmp2)
    logging.info('cost:%s',channelBreakOut.cost)
    logging.info('filtter:%s',channelBreakOut.filtter)
    logging.info('mlMode:%s',mlMode)
    logging.info('trailingStop:%s',channelBreakOut.trailingStop)
    logging.info('offset_raitio:%s',channelBreakOut.offset_raitio)
    logging.info("===========Backtest===========")
    channelBreakOut.readFile()
    channelBreakOut.calcPriceRange()

    pl, profitFactor, maxLoss, winPer, ev, nOfTrade, winTotal, loseTotal = channelBreakOut.describeResult()

    a = None
    b = None
    if "PFDD" in mlMode:
        result = profitFactor/maxLoss
    elif "PL" in mlMode:
        result = -pl
    elif "PF" in mlMode:
        result = -profitFactor
    elif "DD" in mlMode:
        result = maxLoss
    elif "WIN" in mlMode:
        result = -winPer
    elif "EV" in mlMode:
        result = -ev
    elif "winTotal" in mlMode:
        result = -winTotal
    elif "SAITO":
        if nOfTrade/channelBreakOut.nissuu < 1:
            a = nOfTrade/channelBreakOut.nissuu * nOfTrade/channelBreakOut.nissuu
        else:
            a = math.sqrt(math.sqrt(math.sqrt(nOfTrade/channelBreakOut.nissuu)))/5
        
        if channelBreakOut.candleTerm == "1T":
            b = profitFactor
        elif channelBreakOut.candleTerm == "5T":
            b = math.sqrt(profitFactor)
        elif channelBreakOut.candleTerm == "15T":
            b = math.sqrt(math.sqrt(profitFactor))

        result = - (pl/channelBreakOut.nissuu/5000 + a + b)
        
    logging.info("===========Assessment===========")
    logging.info('1day pl:%s',pl/channelBreakOut.nissuu/5000)
    logging.info('1day trade:%s',a)
    logging.info('PF:%s',b)
    logging.info('Result:%s',result)
    return result

def optimization(cost, fileName, hyperopt, mlMode, showTradeDetail):
    #optimizeList.jsonの読み込み
    f = open('config/optimizeList.json', 'r', encoding="utf-8")
    config = json.load(f)
    entryAndCloseTerm = config["entryAndCloseTerm"]
    rangeThAndrangeTerm = config["rangeThAndrangeTerm"]
    waitTermAndwaitTh = config["waitTermAndwaitTh"]
    rangePercentList = config["rangePercentList"]
    linePattern = config["linePattern"]
    termUpper = config["termUpper"]
    candleTerm  = config["candleTerm"]
    method  = config["method"]
    price_range_limit = config["price_range_limit"]
    tmp2 = config["tmp2"]
    tmp1 = config["tmp1"]
    filtter_param = config["filtter_param"]
    filtter = config["filtter"]
    trailingStop = config["trailingStop"]
    offset_raitio = config["offset_raitio"]

    if "COMB" in linePattern:
        if trailingStop == 'True':
            # クローズロジックないので1に固定
            entryAndCloseTerm = list(itertools.product(range(2,termUpper[0], 2), range(2,3)))
        else:
            entryAndCloseTerm = list(itertools.product(range(2,termUpper[0], 2), range(2,termUpper[1], 2)))

        entryAndCloseTerm = [i for i in entryAndCloseTerm if i[0] >= i[1]]

    total = len(entryAndCloseTerm) * len(rangeThAndrangeTerm) * len(waitTermAndwaitTh) * len(rangePercentList) * len(candleTerm) * len(method) * len(price_range_limit) * len(tmp2) * len(tmp1) * len(filtter_param) * len(filtter) * len(offset_raitio)

    logging.info('Total pattern:%s Searches:%s',total,hyperopt)
    logging.info("======Optimization start======")
    #hyperoptによる最適値の算出
    space = [hp.choice('i',entryAndCloseTerm), hp.choice('j',rangeThAndrangeTerm), hp.choice('k',waitTermAndwaitTh), hp.choice('l',rangePercentList), hp.choice('m',candleTerm), cost, mlMode, fileName, hp.choice('n',method), hp.choice('o',price_range_limit), hp.choice('p',tmp2), hp.choice('q',tmp1), hp.choice('r',filtter_param), hp.choice('s',filtter), hp.choice('t',offset_raitio)]
    result = fmin(describe,space,algo=tpe.suggest,max_evals=hyperopt)

    logging.info("======Optimization finished======")
    channelBreakOut = channel.ChannelBreakOut()
    channelBreakOut.entryTerm = entryAndCloseTerm[result['i']][0]
    channelBreakOut.closeTerm = entryAndCloseTerm[result['i']][1]
    channelBreakOut.rangeTh = rangeThAndrangeTerm[result['j']][0]
    channelBreakOut.rangeTerm = rangeThAndrangeTerm[result['j']][1]
    channelBreakOut.waitTerm = waitTermAndwaitTh[result['k']][0]
    channelBreakOut.waitTh = waitTermAndwaitTh[result['k']][1]
    channelBreakOut.rangePercent = rangePercentList[result['l']][0]
    channelBreakOut.rangePercentTerm = rangePercentList[result['l']][1]
    channelBreakOut.candleTerm = str(candleTerm[result['m']]) + "T"
    channelBreakOut.cost = cost
    channelBreakOut.fileName = fileName
    channelBreakOut.showTradeDetail = showTradeDetail
    logging.info("======Best pattern======")
    logging.info('candleTerm:%s mlMode:%s',channelBreakOut.candleTerm,mlMode)
    logging.info('entryTerm:%s closeTerm:%s',channelBreakOut.entryTerm,channelBreakOut.closeTerm)
    logging.info('rangePercent:%s rangePercentTerm:%s',channelBreakOut.rangePercent,channelBreakOut.rangePercentTerm)
    logging.info('rangeTerm:%s rangeTh:%s',channelBreakOut.rangeTerm,channelBreakOut.rangeTh)
    logging.info('waitTerm:%s waitTh:%s',channelBreakOut.waitTerm,channelBreakOut.waitTh)
    logging.info("======Backtest======")
    channelBreakOut.describeResult()

    #config.json設定用ログ
    print("======config======")
    print("    \"entryTerm\" : ", channelBreakOut.entryTerm, ",", sep="")
    print("    \"closeTerm\" : ", channelBreakOut.closeTerm, ",", sep="")
    if channelBreakOut.rangePercent is None:
        print("    \"rangePercent\" : ", "null,", sep="")
    else:
        print("    \"rangePercent\" : ", channelBreakOut.rangePercent, ",", sep="")
    if channelBreakOut.rangePercentTerm is None:
        print("    \"rangePercentTerm\" : ", "null,", sep="")
    else:
        print("    \"rangePercentTerm\" : ", channelBreakOut.rangePercentTerm, ",", sep="")
    if channelBreakOut.rangeTerm is None:
        print("    \"rangeTerm\" : ", "null,", sep="")
    else:
        print("    \"rangeTerm\" : ", channelBreakOut.rangeTerm, ",", sep="")
    if channelBreakOut.rangeTh is None:
        print("    \"rangeTh\" : ", "null,", sep="")
    else:
        print("    \"rangeTh\" : ", channelBreakOut.rangeTh, ",", sep="")
    print("    \"waitTerm\" : ", channelBreakOut.waitTerm, ",", sep="")
    print("    \"waitTh\" : ", channelBreakOut.waitTh, ",", sep="")
    print("    \"candleTerm\" : \"", channelBreakOut.candleTerm, "\",", sep="")
    print("==================")

if __name__ == '__main__':
    #logging設定
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    logfile=logging.handlers.TimedRotatingFileHandler(
        filename = 'log/optimization_2.log',
        when = 'midnight'
    )
    logfile.setLevel(logging.INFO)
    logfile.setFormatter(logging.Formatter(
        fmt='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'))
    logging.getLogger('').addHandler(logfile)
    logging.info('Wait...')

    #config.jsonの読み込み
    f = open('config/config.json', 'r', encoding="utf-8")
    config = json.load(f)
    logging.info('cost:%s mlMode:%s fileName:%s',config["cost"],config["mlMode"],config["fileName"])

    # ファイル読み込みは最初の１回だけにするため
    channelBreakOut = False

    #最適化
    start = time.time()
    optimization(cost=config["cost"], fileName=config["fileName"], hyperopt=config["hyperopt"], mlMode=config["mlMode"], showTradeDetail=config["showTradeDetail"])
    logging.info('total processing time: %s', time.time() - start)
