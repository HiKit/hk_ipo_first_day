# HK IPO First Day

Usage:

```
python main.py 20260309
```

Input:
- Date format: `YYYYMMDD` (e.g. `20260309`)

Output (plain text, per stock):

```
股票名称 股票代码
招股价：X.XX
暗盘：开X.XX(+/-X.XX%)，收X.XX(+/-X.XX%)，最高X.XX(+/-X.XX%)，最低X.XX(+/-X.XX%)
首日：开X.XX(+/-X.XX%)，收X.XX(+/-X.XX%)，最高X.XX(+/-X.XX%)，最低X.XX(+/-X.XX%)
```
```
今日无上市股票时，输出：今日无上市股票
```

Notes:
- Percentages are vs offer price.
- Run time can be slow due to browser automation.
