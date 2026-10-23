# P11-A — fixed risk overlay comparison

Exploratory reuse of disclosed train/validation, not independent validation. Holdout rows were not parsed or evaluated.
3,072 single-asset runs; 768 signal-only baselines checked against the archived metrics (absolute tolerance 1e-8). No selection or optimization.
Full results: metrics.csv/results.json; 2,304 matched nonbaseline differences: paired_deltas.csv. Spread fixed at 2bps; fee 5/10bps × slippage 0/5/10bps.
These arithmetic cross-dataset summaries are NOT portfolio returns. The 8 overlapping market/interval samples are correlated; no significance claim.

Primary cost: fee 5bps / slippage 5bps / full spread 2bps. Equal-weight summaries across 8 separate initial-cash simulation accounts.
Worst return=min net return; worst MDD=max MDD. Win mean excludes datasets without a closed trade (count shown); it is not a pooled portfolio win rate.
Fills/closed are summed descriptive counts across simulations. Exposure is mean close-marked invested asset/equity percentage; idle/warm-up bars count.

## train

|Strategy|Overlay|Mean net %|Worst net %|Worst MDD %|Mean fees KRW|Fills/closed|Mean win % (n/8)|Mean exposure %|
|---|---|---:|---:|---:|---:|---:|---:|---:|
|rsi|signal_only|0.206|-3.366|5.713|6151.028|246/122|56.058 (8/8)|19.941|
|rsi|take_profit_5|0.404|-3.366|5.299|6152.022|246/122|56.058 (8/8)|19.530|
|rsi|stop_5|0.231|-3.377|4.793|6401.153|256/127|59.286 (8/8)|19.676|
|rsi|take_profit_stop_5|0.429|-3.377|4.729|6402.147|256/127|59.286 (8/8)|19.266|
|rsi|take_profit_average_once_5|0.446|-3.219|5.506|6467.977|259/125|58.248 (8/8)|19.742|
|macd|signal_only|1.734|-5.399|10.132|14982.524|599/298|44.094 (8/8)|20.678|
|macd|take_profit_5|0.303|-5.975|7.289|15001.424|600/299|44.378 (8/8)|20.286|
|macd|stop_5|1.470|-6.830|11.435|15331.206|613/305|44.105 (8/8)|20.622|
|macd|take_profit_stop_5|0.120|-7.406|8.690|15350.508|614/306|44.425 (8/8)|20.187|
|macd|take_profit_average_once_5|0.274|-6.494|8.101|15266.886|611/301|45.643 (8/8)|20.428|
|bollinger|signal_only|-0.266|-3.420|5.709|13473.815|539/269|59.947 (8/8)|21.069|
|bollinger|take_profit_5|-0.266|-3.420|5.709|13473.815|539/269|59.947 (8/8)|21.069|
|bollinger|stop_5|-0.227|-3.420|5.480|13774.009|551/275|60.578 (8/8)|21.058|
|bollinger|take_profit_stop_5|-0.227|-3.420|5.480|13774.009|551/275|60.578 (8/8)|21.058|
|bollinger|take_profit_average_once_5|-0.217|-3.420|6.486|13689.750|548/270|60.716 (8/8)|21.243|
|consensus_2of3|signal_only|2.361|-2.496|6.361|2085.130|83/40|49.375 (8/8)|15.496|
|consensus_2of3|take_profit_5|2.154|-2.496|6.361|2110.437|84/41|50.625 (8/8)|15.453|
|consensus_2of3|stop_5|1.796|-4.413|6.329|2132.301|85/41|48.542 (8/8)|13.421|
|consensus_2of3|take_profit_stop_5|1.589|-4.413|6.329|2157.609|86/42|49.732 (8/8)|13.379|
|consensus_2of3|take_profit_average_once_5|1.825|-6.298|6.692|2277.181|91/42|53.304 (8/8)|14.400|
|consensus_3of3|signal_only|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|take_profit_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|stop_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|take_profit_stop_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|take_profit_average_once_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|legacy_composite|signal_only|-1.188|-3.516|6.285|19744.057|790/395|51.174 (8/8)|17.553|
|legacy_composite|take_profit_5|-1.188|-3.516|6.285|19744.057|790/395|51.174 (8/8)|17.553|
|legacy_composite|stop_5|-1.528|-4.403|6.961|20092.352|804/402|52.204 (8/8)|17.527|
|legacy_composite|take_profit_stop_5|-1.528|-4.403|6.961|20092.352|804/402|52.204 (8/8)|17.527|
|legacy_composite|take_profit_average_once_5|-0.944|-3.054|6.519|19960.952|799/396|51.848 (8/8)|17.703|
|buy_hold_40|signal_only|7.474|4.142|10.032|199.900|8/0|N/A (0/8)|40.700|
|buy_hold_100|signal_only|18.686|10.354|20.718|499.750|8/0|N/A (0/8)|98.312|

## validation

|Strategy|Overlay|Mean net %|Worst net %|Worst MDD %|Mean fees KRW|Fills/closed|Mean win % (n/8)|Mean exposure %|
|---|---|---:|---:|---:|---:|---:|---:|---:|
|rsi|signal_only|-0.262|-5.127|6.424|2249.365|90/43|60.372 (8/8)|16.591|
|rsi|take_profit_5|-0.254|-5.127|6.424|2249.404|90/43|60.372 (8/8)|16.578|
|rsi|stop_5|-0.003|-3.920|5.224|2450.656|98/47|64.524 (8/8)|16.436|
|rsi|take_profit_stop_5|0.004|-3.920|5.224|2450.696|98/47|64.524 (8/8)|16.423|
|rsi|take_profit_average_once_5|-0.315|-5.515|6.809|2493.686|100/46|63.854 (8/8)|16.818|
|macd|signal_only|-0.101|-5.208|5.975|4249.274|170/83|54.842 (8/8)|18.030|
|macd|take_profit_5|-0.189|-5.208|5.975|4248.830|170/83|54.842 (8/8)|17.540|
|macd|stop_5|-0.106|-4.357|5.126|4449.248|178/87|55.911 (8/8)|17.972|
|macd|take_profit_stop_5|-0.195|-4.357|5.126|4448.804|178/87|55.911 (8/8)|17.483|
|macd|take_profit_average_once_5|-0.138|-5.160|5.927|4393.761|176/84|55.363 (8/8)|17.706|
|bollinger|signal_only|0.584|-1.664|3.734|4504.523|180/87|75.920 (8/8)|16.001|
|bollinger|take_profit_5|0.584|-1.664|3.734|4504.523|180/87|75.920 (8/8)|16.001|
|bollinger|stop_5|0.584|-1.664|3.734|4504.523|180/87|75.920 (8/8)|16.001|
|bollinger|take_profit_stop_5|0.584|-1.664|3.734|4504.523|180/87|75.920 (8/8)|16.001|
|bollinger|take_profit_average_once_5|0.584|-1.664|3.734|4504.523|180/87|75.920 (8/8)|16.001|
|consensus_2of3|signal_only|1.804|0.511|2.329|733.390|29/12|86.111 (6/8)|14.381|
|consensus_2of3|take_profit_5|1.504|0.511|1.692|807.605|32/14|89.286 (7/8)|9.762|
|consensus_2of3|stop_5|1.804|0.511|2.329|733.390|29/12|86.111 (6/8)|14.381|
|consensus_2of3|take_profit_stop_5|1.504|0.511|1.692|807.605|32/14|89.286 (7/8)|9.762|
|consensus_2of3|take_profit_average_once_5|1.504|0.511|1.692|807.605|32/14|89.286 (7/8)|9.762|
|consensus_3of3|signal_only|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|take_profit_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|stop_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|take_profit_stop_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|consensus_3of3|take_profit_average_once_5|0.000|0.000|0.000|0.000|0/0|N/A (0/8)|0.000|
|legacy_composite|signal_only|-0.802|-6.019|6.754|5372.046|215/105|63.719 (8/8)|15.051|
|legacy_composite|take_profit_5|-0.802|-6.019|6.754|5372.046|215/105|63.719 (8/8)|15.051|
|legacy_composite|stop_5|-0.749|-5.516|6.253|5522.313|221/108|64.557 (8/8)|15.025|
|legacy_composite|take_profit_stop_5|-0.749|-5.516|6.253|5522.313|221/108|64.557 (8/8)|15.025|
|legacy_composite|take_profit_average_once_5|-0.796|-6.312|7.046|5493.109|220/106|63.987 (8/8)|15.162|
|buy_hold_40|signal_only|3.704|2.016|4.779|199.900|8/0|N/A (0/8)|41.493|
|buy_hold_100|signal_only|9.261|5.041|11.834|499.750|8/0|N/A (0/8)|99.855|

## Legacy composite: validation baseline → take-profit + one averaging

|Dataset|Net %|MDD %|Fees KRW|Fills/closed|Win %|Mean exposure %|Average fills|
|---|---:|---:|---:|---:|---:|---:|---:|
|KRW-BTC-15|-1.697 → -1.697|2.241 → 2.241|9392.342 → 9392.342|47/23 → 47/23|43.478 → 43.478|14.692 → 14.692|0|
|KRW-BTC-60|1.044 → 1.044|1.011 → 1.011|2405.219 → 2405.219|12/6 → 12/6|50.000 → 50.000|16.467 → 16.467|0|
|KRW-ETH-15|-2.279 → -2.279|2.609 → 2.609|8191.754 → 8191.754|41/20 → 41/20|50.000 → 50.000|14.846 → 14.846|0|
|KRW-ETH-60|1.309 → 1.309|1.022 → 1.022|3206.548 → 3206.548|16/8 → 16/8|75.000 → 75.000|13.162 → 13.162|0|
|KRW-XRP-15|-6.019 → -6.312|6.754 → 7.046|8174.179 → 8951.552|41/20 → 45/21|55.000 → 57.143|15.071 → 15.383|2|
|KRW-XRP-60|2.787 → 2.787|1.642 → 1.642|2612.668 → 2612.668|13/6 → 13/6|83.333 → 83.333|17.517 → 17.517|0|
|KRW-SOL-15|-3.334 → -2.987|4.305 → 3.958|6984.794 → 7175.924|35/17 → 36/17|52.941 → 52.941|14.927 → 15.501|1|
|KRW-SOL-60|1.771 → 1.771|1.307 → 1.307|2008.862 → 2008.862|10/5 → 10/5|100.000 → 100.000|13.729 → 13.729|0|

## Cost sensitivity (validation, legacy composite, 8-account descriptive mean)

|Fee bps|Slippage bps|Overlay|Mean net %|Worst MDD %|Mean fees KRW|
|---:|---:|---|---:|---:|---:|
|5|0|signal_only|-0.265|6.174|5374.670|
|5|0|take_profit_5|-0.265|6.174|5374.670|
|5|0|stop_5|-0.203|5.642|5524.982|
|5|0|take_profit_stop_5|-0.203|5.642|5524.982|
|5|0|take_profit_average_once_5|-0.236|6.306|5495.777|
|5|5|signal_only|-0.802|6.754|5372.046|
|5|5|take_profit_5|-0.802|6.754|5372.046|
|5|5|stop_5|-0.749|6.253|5522.313|
|5|5|take_profit_stop_5|-0.749|6.253|5522.313|
|5|5|take_profit_average_once_5|-0.796|7.046|5493.109|
|5|10|signal_only|-1.339|7.546|5369.424|
|5|10|take_profit_5|-1.339|7.546|5369.424|
|5|10|stop_5|-1.301|7.125|5519.616|
|5|10|take_profit_stop_5|-1.301|7.125|5519.616|
|5|10|take_profit_average_once_5|-1.344|7.915|5490.391|
|10|0|signal_only|-0.802|6.754|10743.973|
|10|0|take_profit_5|-0.802|6.754|10743.973|
|10|0|stop_5|-0.755|6.302|11044.446|
|10|0|take_profit_stop_5|-0.755|6.302|11044.446|
|10|0|take_profit_average_once_5|-0.785|6.963|10985.994|
|10|5|signal_only|-1.339|7.546|10738.726|
|10|5|take_profit_5|-1.339|7.546|10738.726|
|10|5|stop_5|-1.301|7.125|11039.109|
|10|5|take_profit_stop_5|-1.301|7.125|11039.109|
|10|5|take_profit_average_once_5|-1.344|7.915|10980.660|
|10|10|signal_only|-1.875|8.338|10733.484|
|10|10|take_profit_5|-1.875|8.338|10733.484|
|10|10|stop_5|-1.852|7.997|11033.717|
|10|10|take_profit_stop_5|-1.852|7.997|11033.717|
|10|10|take_profit_average_once_5|-1.892|8.784|10975.226|

## Limits

- Closed-bar decisions execute at next open with fixed costs and assumed full fills. No intrabar path, liquidity/partial-fill/cancel/timeout simulation; a stop is not a guaranteed loss cap.
- Final holdings are marked to close without liquidation costs. Close-only MDD misses intrabar risk. Short, correlated samples and many comparisons do not establish future profitability.
- Existing combined gzip archives are streamed for exactly development rows only. Buffered compressed bytes may include tail data; no byte-level isolation/CRC or whole-archive revalidation claim.
- Operating strategy, orders, PAPER journals, Slack, automatic workers and deployment are unchanged/OFF. Details: ../../docs/P11_STRATEGY_RISK_RESEARCH.md.
