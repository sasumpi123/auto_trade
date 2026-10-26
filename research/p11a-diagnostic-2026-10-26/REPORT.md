# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-BTC; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|3.301358|0.261096|-2.776129|3.299709|0.260966|-2.774743|
|net_return_pct|0.261227|-2.777515|-5.813222|-2.777517|-5.813221|-8.845894|
|mdd_pct|1.750964|3.288255|6.228884|3.288256|6.228886|9.241276|
|win_rate_pct|65.789474|46.052632|32.894737|46.052632|32.894737|21.052632|
|average_net_sell_pnl|34.371947|-365.462532|-764.897615|-365.462707|-764.897551|-1163.933399|
|expectancy_per_sell|34.371947|-365.462532|-764.897615|-365.462707|-764.897551|-1163.933399|
|net_realized_pnl|2612.267977|-27775.152404|-58132.218754|-27775.165768|-58132.213899|-88458.938339|
|unrealized_pnl|-0.000000|-0.000000|0.000000|0.000000|0.000000|0.000000|
|buy_hold_100_net_pct|12.480694|12.424488|12.368337|12.424510|12.368332|12.312209|
|buy_hold_40_net_pct|4.992278|4.969795|4.947335|4.969804|4.947333|4.924884|
|fees|30401.314387|30386.113077|30370.926950|60772.257831|60741.870396|60711.513314|
|slippage_cost|0.000000|30386.124799|60741.896861|0.000000|30370.946914|60711.556253|
|half_spread_cost|6080.263268|6077.224960|6074.189686|6077.226174|6074.189383|6071.155625|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|4984|4984|4984|4984|4984|4984|
|analyzer_calls|4984|4984|4984|4984|4984|4984|
|raw_BUY|1016|1016|1016|1016|1016|1016|
|raw_SELL|1211|1211|1211|1211|1211|1211|
|raw_HOLD|2757|2757|2757|2757|2757|2757|
|effective_BUY|659|659|659|659|659|659|
|effective_SELL|882|882|882|882|882|882|
|effective_HOLD|3443|3443|3443|3443|3443|3443|
|volume_blocked|1316|1316|1316|1316|1316|1316|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|76|76|76|76|76|76|
|sell_attempts|76|76|76|76|76|76|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|583|583|583|583|583|583|
|sell_without_position|806|806|806|806|806|806|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|76|76|76|76|76|76|
|sells|76|76|76|76|76|76|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|540|4444|
|rsi_rising|2478|2506|
|rsi_gt_70|552|4432|
|rsi_falling|2494|2490|
|macd_gt_signal|2447|2537|
|macd_negative|2581|2403|
|macd_rising|2477|2507|
|macd_lt_signal|2537|2447|
|macd_positive|2403|2581|
|macd_falling|2507|2477|
|below_lower|276|4708|
|rsi_or_macd_rising|3327|1657|
|above_upper|269|4715|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|4444|
|rsi_sell|rsi_gt_70|4432|
|macd_buy|macd_gt_signal|2537|
|macd_sell|macd_lt_signal|2447|
|bb_buy|below_lower|4708|
|bb_sell|above_upper|4715|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-BTC; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|0.181760|-0.757554|-1.695931|0.181669|-0.757176|-1.695083|
|net_return_pct|-0.757935|-1.696790|-2.634707|-1.696782|-2.634709|-3.571698|
|mdd_pct|1.522688|2.241024|2.958930|2.241025|2.958930|3.763640|
|win_rate_pct|69.565217|43.478261|34.782609|43.478261|34.782609|30.434783|
|average_net_sell_pnl|-256.337908|-655.881822|-1055.026631|-655.881997|-1055.026567|-1453.772431|
|expectancy_per_sell|-256.337908|-655.881822|-1055.026631|-655.881997|-1055.026567|-1453.772431|
|net_realized_pnl|-5895.771874|-15085.281898|-24265.612510|-15085.285940|-24265.611042|-33436.765906|
|unrealized_pnl|-1683.578347|-1882.617134|-2081.457102|-1882.537598|-2081.476966|-2280.217613|
|buy_hold_100_net_pct|8.915501|8.861076|8.806706|8.861098|8.806700|8.752357|
|buy_hold_40_net_pct|3.566201|3.544431|3.522682|3.544439|3.522680|3.500943|
|fees|9396.952989|9392.355936|9387.763474|18784.518413|18775.328899|18766.148563|
|slippage_cost|0.000000|9392.237609|18775.093301|0.000000|9387.546182|18765.715132|
|half_spread_cost|1879.386652|1878.447522|1877.509330|1878.447897|1877.509236|1876.571513|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1727|1727|1727|1727|1727|1727|
|analyzer_calls|1727|1727|1727|1727|1727|1727|
|raw_BUY|294|294|294|294|294|294|
|raw_SELL|527|527|527|527|527|527|
|raw_HOLD|906|906|906|906|906|906|
|effective_BUY|209|209|209|209|209|209|
|effective_SELL|380|380|380|380|380|380|
|effective_HOLD|1138|1138|1138|1138|1138|1138|
|volume_blocked|409|409|409|409|409|409|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|24|24|24|24|24|24|
|sell_attempts|23|23|23|23|23|23|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|185|185|185|185|185|185|
|sell_without_position|357|357|357|357|357|357|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|24|24|24|24|24|24|
|sells|23|23|23|23|23|23|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|159|1568|
|rsi_rising|884|843|
|rsi_gt_70|208|1519|
|rsi_falling|842|885|
|macd_gt_signal|823|904|
|macd_negative|742|985|
|macd_rising|853|874|
|macd_lt_signal|904|823|
|macd_positive|985|742|
|macd_falling|874|853|
|below_lower|82|1645|
|rsi_or_macd_rising|1170|557|
|above_upper|120|1607|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|1568|
|rsi_sell|rsi_gt_70|1519|
|macd_buy|macd_gt_signal|904|
|macd_sell|macd_lt_signal|823|
|bb_buy|below_lower|1645|
|bb_sell|above_upper|1607|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-BTC; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|3.317292|2.514776|1.713061|3.315635|2.513520|1.712206|
|net_return_pct|2.516034|1.713919|0.912605|1.713918|0.912605|0.112092|
|mdd_pct|1.458130|1.464547|1.590849|1.464547|1.590849|1.827828|
|win_rate_pct|70.000000|60.000000|55.000000|60.000000|55.000000|30.000000|
|average_net_sell_pnl|1258.016791|856.959279|456.302384|856.959103|456.302448|56.046009|
|expectancy_per_sell|1258.016791|856.959279|456.302384|856.959103|456.302448|56.046009|
|net_realized_pnl|25160.335822|17139.185580|9126.047675|17139.182052|9126.048956|1120.920188|
|unrealized_pnl|0.000000|0.000000|0.000000|0.000000|-0.000000|-0.000000|
|buy_hold_100_net_pct|15.848749|15.790860|15.733028|15.790883|15.733022|15.675219|
|buy_hold_40_net_pct|6.339500|6.316344|6.293211|6.316353|6.293209|6.270088|
|fees|8012.588461|8008.575880|8004.567306|16017.172338|16009.151184|16001.138042|
|slippage_cost|0.000000|8008.586307|16009.172828|0.000000|8004.586014|16001.176238|
|half_spread_cost|1602.518040|1601.717261|1600.917283|1601.717581|1600.917203|1600.117624|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1096|1096|1096|1096|1096|1096|
|analyzer_calls|1095|1095|1095|1095|1095|1095|
|raw_BUY|212|212|212|212|212|212|
|raw_SELL|236|236|236|236|236|236|
|raw_HOLD|648|648|648|648|648|648|
|effective_BUY|131|131|131|131|131|131|
|effective_SELL|191|191|191|191|191|191|
|effective_HOLD|773|773|773|773|773|773|
|volume_blocked|279|279|279|279|279|279|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|20|20|20|20|20|20|
|sell_attempts|20|20|20|20|20|20|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|111|111|111|111|111|111|
|sell_without_position|172|172|172|172|172|172|
|risk_bypassed_analyzer|1|1|1|1|1|1|
|buys|20|20|20|20|20|20|
|sells|20|20|20|20|20|20|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|108|988|
|rsi_rising|543|553|
|rsi_gt_70|114|982|
|rsi_falling|551|545|
|macd_gt_signal|567|529|
|macd_negative|573|523|
|macd_rising|533|563|
|macd_lt_signal|529|567|
|macd_positive|523|573|
|macd_falling|563|533|
|below_lower|61|1035|
|rsi_or_macd_rising|725|371|
|above_upper|65|1031|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|988|
|rsi_sell|rsi_gt_70|982|
|macd_buy|macd_gt_signal|529|
|macd_sell|macd_lt_signal|567|
|bb_buy|below_lower|1035|
|bb_sell|above_upper|1031|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-BTC; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|1.525983|1.284698|1.043653|1.525221|1.284056|1.043132|
|net_return_pct|1.285340|1.044175|0.803251|1.044175|0.803251|0.562568|
|mdd_pct|0.984072|1.010757|1.169656|1.010758|1.169655|1.328584|
|win_rate_pct|50.000000|50.000000|50.000000|50.000000|50.000000|50.000000|
|average_net_sell_pnl|2142.233509|1740.292222|1338.752435|1740.292045|1338.752499|937.614051|
|expectancy_per_sell|2142.233509|1740.292222|1338.752435|1740.292045|1338.752499|937.614051|
|net_realized_pnl|12853.401054|10441.753332|8032.514608|10441.752272|8032.514994|5625.684305|
|unrealized_pnl|-0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|
|buy_hold_100_net_pct|8.910343|8.855921|8.801552|8.855942|8.801547|8.747206|
|buy_hold_40_net_pct|3.564137|3.542368|3.520621|3.542377|3.520619|3.498882|
|fees|2406.430515|2405.224088|2404.018866|4810.457004|4808.045356|4805.636116|
|slippage_cost|0.000000|2405.228808|4808.055031|0.000000|2404.027395|4805.653405|
|half_spread_cost|481.286261|481.045762|480.805503|481.045858|480.805479|480.565340|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|431|431|431|431|431|431|
|analyzer_calls|431|431|431|431|431|431|
|raw_BUY|74|74|74|74|74|74|
|raw_SELL|146|146|146|146|146|146|
|raw_HOLD|211|211|211|211|211|211|
|effective_BUY|44|44|44|44|44|44|
|effective_SELL|112|112|112|112|112|112|
|effective_HOLD|275|275|275|275|275|275|
|volume_blocked|107|107|107|107|107|107|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|6|6|6|6|6|6|
|sell_attempts|6|6|6|6|6|6|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|38|38|38|38|38|38|
|sell_without_position|106|106|106|106|106|106|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|6|6|6|6|6|6|
|sells|6|6|6|6|6|6|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|43|388|
|rsi_rising|211|220|
|rsi_gt_70|81|350|
|rsi_falling|220|211|
|macd_gt_signal|221|210|
|macd_negative|184|247|
|macd_rising|219|212|
|macd_lt_signal|210|221|
|macd_positive|247|184|
|macd_falling|212|219|
|below_lower|18|413|
|rsi_or_macd_rising|292|139|
|above_upper|29|402|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|388|
|rsi_sell|rsi_gt_70|350|
|macd_buy|macd_gt_signal|210|
|macd_sell|macd_lt_signal|221|
|bb_buy|below_lower|413|
|bb_sell|above_upper|402|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-ETH; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|6.634216|3.430782|0.230548|6.630902|3.429068|0.230433|
|net_return_pct|3.432498|0.230666|-2.967968|0.230664|-2.967967|-6.163404|
|mdd_pct|2.099964|2.303952|3.775097|2.303952|3.775098|6.910105|
|win_rate_pct|62.500000|52.500000|45.000000|52.500000|45.000000|32.500000|
|average_net_sell_pnl|429.062201|28.833229|-370.995953|28.833053|-370.995889|-770.425442|
|expectancy_per_sell|429.062201|28.833229|-370.995953|28.833053|-370.995889|-770.425442|
|net_realized_pnl|34324.976071|2306.658332|-29679.676270|2306.644251|-29679.671155|-61634.035391|
|unrealized_pnl|-0.000000|0.000000|0.000000|-0.000000|0.000000|0.000000|
|buy_hold_100_net_pct|24.585793|24.523537|24.461344|24.523562|24.461338|24.399175|
|buy_hold_40_net_pct|9.834317|9.809415|9.784538|9.809425|9.784535|9.759670|
|fees|32017.179074|32001.161906|31985.160738|64002.372953|63970.354620|63938.368269|
|slippage_cost|0.000000|32001.183719|63970.401417|0.000000|31985.199112|63938.448169|
|half_spread_cost|6403.436542|6400.236744|6397.040142|6400.238022|6397.039822|6393.844817|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|4984|4984|4984|4984|4984|4984|
|analyzer_calls|4984|4984|4984|4984|4984|4984|
|raw_BUY|898|898|898|898|898|898|
|raw_SELL|1282|1282|1282|1282|1282|1282|
|raw_HOLD|2804|2804|2804|2804|2804|2804|
|effective_BUY|589|589|589|589|589|589|
|effective_SELL|928|928|928|928|928|928|
|effective_HOLD|3467|3467|3467|3467|3467|3467|
|volume_blocked|1408|1408|1408|1408|1408|1408|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|80|80|80|80|80|80|
|sell_attempts|80|80|80|80|80|80|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|509|509|509|509|509|509|
|sell_without_position|848|848|848|848|848|848|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|80|80|80|80|80|80|
|sells|80|80|80|80|80|80|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|445|4539|
|rsi_rising|2352|2632|
|rsi_gt_70|535|4449|
|rsi_falling|2328|2656|
|macd_gt_signal|2482|2502|
|macd_negative|2367|2617|
|macd_rising|2461|2523|
|macd_lt_signal|2502|2482|
|macd_positive|2617|2367|
|macd_falling|2523|2461|
|below_lower|235|4749|
|rsi_or_macd_rising|3264|1720|
|above_upper|281|4703|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|4539|
|rsi_sell|rsi_gt_70|4449|
|macd_buy|macd_gt_signal|2502|
|macd_sell|macd_lt_signal|2482|
|bb_buy|below_lower|4749|
|bb_sell|above_upper|4703|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-ETH; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|-0.641217|-1.460060|-2.278086|-0.640897|-1.459331|-2.276948|
|net_return_pct|-1.460792|-2.279236|-3.096862|-2.279228|-3.096864|-3.913682|
|mdd_pct|2.044761|2.609150|3.405414|2.609150|3.405414|4.201201|
|win_rate_pct|55.000000|50.000000|50.000000|50.000000|50.000000|35.000000|
|average_net_sell_pnl|-414.979151|-814.364503|-1213.350909|-814.364679|-1213.350845|-1611.938463|
|expectancy_per_sell|-414.979151|-814.364503|-1213.350909|-814.364679|-1213.350845|-1611.938463|
|net_realized_pnl|-8299.583022|-16287.290065|-24267.018177|-16287.293578|-24267.016901|-32238.769270|
|unrealized_pnl|-6308.340942|-6505.068735|-6701.600016|-6504.990122|-6701.619649|-6898.052763|
|buy_hold_100_net_pct|7.009176|6.955703|6.902284|6.955724|6.902279|6.848886|
|buy_hold_40_net_pct|2.803670|2.782281|2.760914|2.782290|2.760911|2.739554|
|fees|8195.750183|8191.754331|8187.762471|16383.312802|16375.325091|16367.345359|
|slippage_cost|0.000000|8191.634851|16375.087069|0.000000|8187.543126|16366.907705|
|half_spread_cost|1639.146052|1638.326970|1637.508707|1638.327298|1637.508625|1636.690770|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1727|1727|1727|1727|1727|1727|
|analyzer_calls|1727|1727|1727|1727|1727|1727|
|raw_BUY|306|306|306|306|306|306|
|raw_SELL|475|475|475|475|475|475|
|raw_HOLD|946|946|946|946|946|946|
|effective_BUY|198|198|198|198|198|198|
|effective_SELL|358|358|358|358|358|358|
|effective_HOLD|1171|1171|1171|1171|1171|1171|
|volume_blocked|430|430|430|430|430|430|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|21|21|21|21|21|21|
|sell_attempts|20|20|20|20|20|20|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|177|177|177|177|177|177|
|sell_without_position|338|338|338|338|338|338|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|21|21|21|21|21|21|
|sells|20|20|20|20|20|20|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|128|1599|
|rsi_rising|834|893|
|rsi_gt_70|167|1560|
|rsi_falling|802|925|
|macd_gt_signal|844|883|
|macd_negative|764|963|
|macd_rising|893|834|
|macd_lt_signal|883|844|
|macd_positive|963|764|
|macd_falling|834|893|
|below_lower|85|1642|
|rsi_or_macd_rising|1155|572|
|above_upper|101|1626|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|1599|
|rsi_sell|rsi_gt_70|1560|
|macd_buy|macd_gt_signal|883|
|macd_sell|macd_lt_signal|844|
|bb_buy|below_lower|1642|
|bb_sell|above_upper|1626|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-ETH; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|2.778838|1.976860|1.175683|2.777450|1.975873|1.175096|
|net_return_pct|1.977849|1.176272|0.375495|1.176271|0.375495|-0.424481|
|mdd_pct|1.538769|1.782120|2.193727|1.782120|2.193727|2.825593|
|win_rate_pct|50.000000|45.000000|45.000000|45.000000|45.000000|35.000000|
|average_net_sell_pnl|988.924438|588.135884|187.747678|588.135708|187.747742|-212.240276|
|expectancy_per_sell|988.924438|588.135884|187.747678|588.135708|187.747742|-212.240276|
|net_realized_pnl|19778.488764|11762.717680|3754.953559|11762.714155|3754.954840|-4244.805515|
|unrealized_pnl|0.000000|-0.000000|0.000000|-0.000000|0.000000|0.000000|
|buy_hold_100_net_pct|26.066203|26.003208|25.940276|26.003233|25.940269|25.877368|
|buy_hold_40_net_pct|10.426481|10.401283|10.376110|10.401293|10.376108|10.350947|
|fees|8009.896191|8005.886301|8001.880416|16011.790489|16003.774714|15995.766945|
|slippage_cost|0.000000|8005.895114|16003.793129|0.000000|8001.896165|15995.799226|
|half_spread_cost|1601.979532|1601.179023|1600.379313|1601.179343|1600.379233|1599.579923|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1096|1096|1096|1096|1096|1096|
|analyzer_calls|1096|1096|1096|1096|1096|1096|
|raw_BUY|190|190|190|190|190|190|
|raw_SELL|272|272|272|272|272|272|
|raw_HOLD|634|634|634|634|634|634|
|effective_BUY|124|124|124|124|124|124|
|effective_SELL|208|208|208|208|208|208|
|effective_HOLD|764|764|764|764|764|764|
|volume_blocked|268|268|268|268|268|268|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|20|20|20|20|20|20|
|sell_attempts|20|20|20|20|20|20|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|104|104|104|104|104|104|
|sell_without_position|188|188|188|188|188|188|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|20|20|20|20|20|20|
|sells|20|20|20|20|20|20|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|89|1007|
|rsi_rising|524|572|
|rsi_gt_70|99|997|
|rsi_falling|534|562|
|macd_gt_signal|539|557|
|macd_negative|504|592|
|macd_rising|551|545|
|macd_lt_signal|557|539|
|macd_positive|592|504|
|macd_falling|545|551|
|below_lower|52|1044|
|rsi_or_macd_rising|723|373|
|above_upper|61|1035|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|1007|
|rsi_sell|rsi_gt_70|997|
|macd_buy|macd_gt_signal|557|
|macd_sell|macd_lt_signal|539|
|bb_buy|below_lower|1044|
|bb_sell|above_upper|1035|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-ETH; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|1.951105|1.629475|1.308166|1.950131|1.628661|1.307512|
|net_return_pct|1.630290|1.308820|0.987672|1.308820|0.987672|0.666844|
|mdd_pct|1.021882|1.021576|1.091459|1.021576|1.091459|1.251405|
|win_rate_pct|75.000000|75.000000|75.000000|75.000000|75.000000|62.500000|
|average_net_sell_pnl|2037.862073|1636.025106|1234.589533|1636.024929|1234.589598|833.555260|
|expectancy_per_sell|2037.862073|1636.025106|1234.589533|1636.024929|1234.589598|833.555260|
|net_realized_pnl|16302.896587|13088.200845|9876.716267|13088.199431|9876.716781|6668.442084|
|unrealized_pnl|0.000000|-0.000000|-0.000000|0.000000|0.000000|0.000000|
|buy_hold_100_net_pct|6.600744|6.547475|6.494260|6.547496|6.494255|6.441066|
|buy_hold_40_net_pct|2.640297|2.618990|2.597704|2.618999|2.597702|2.576426|
|fees|3208.156326|3206.548174|3204.941629|6413.107701|6409.893003|6406.681517|
|slippage_cost|0.000000|3206.554217|6409.905403|0.000000|3204.952541|6406.703652|
|half_spread_cost|641.631467|641.310843|640.990540|641.310972|640.990508|640.670365|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|431|431|431|431|431|431|
|analyzer_calls|431|431|431|431|431|431|
|raw_BUY|74|74|74|74|74|74|
|raw_SELL|126|126|126|126|126|126|
|raw_HOLD|231|231|231|231|231|231|
|effective_BUY|46|46|46|46|46|46|
|effective_SELL|94|94|94|94|94|94|
|effective_HOLD|291|291|291|291|291|291|
|volume_blocked|108|108|108|108|108|108|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|8|8|8|8|8|8|
|sell_attempts|8|8|8|8|8|8|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|38|38|38|38|38|38|
|sell_without_position|86|86|86|86|86|86|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|8|8|8|8|8|8|
|sells|8|8|8|8|8|8|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|45|386|
|rsi_rising|201|230|
|rsi_gt_70|69|362|
|rsi_falling|223|208|
|macd_gt_signal|226|205|
|macd_negative|179|252|
|macd_rising|220|211|
|macd_lt_signal|205|226|
|macd_positive|252|179|
|macd_falling|211|220|
|below_lower|18|413|
|rsi_or_macd_rising|292|139|
|above_upper|22|409|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|386|
|rsi_sell|rsi_gt_70|362|
|macd_buy|macd_gt_signal|205|
|macd_sell|macd_lt_signal|226|
|bb_buy|below_lower|413|
|bb_sell|above_upper|409|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-XRP; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|4.548137|1.400214|-1.809114|4.545852|1.399535|-1.808125|
|net_return_pct|1.410588|-1.735742|-4.943511|-1.726054|-4.869188|-8.073731|
|mdd_pct|5.442530|6.458841|7.519940|6.449446|7.514418|9.465379|
|win_rate_pct|64.935065|51.948052|48.051948|51.948052|48.051948|40.259740|
|average_net_sell_pnl|183.193295|-225.420986|-642.014405|-224.162900|-632.362037|-1048.536546|
|expectancy_per_sell|183.193295|-225.420986|-642.014405|-224.162900|-632.362037|-1048.536546|
|net_realized_pnl|14105.883705|-17357.415901|-49435.109216|-17260.543263|-48691.876827|-80737.314009|
|unrealized_pnl|0.000000|-0.000000|-0.000000|0.000000|0.000000|0.000000|
|buy_hold_100_net_pct|19.903083|19.843168|19.783312|19.843192|19.783306|19.723480|
|buy_hold_40_net_pct|7.961233|7.937267|7.913325|7.937277|7.913322|7.889392|
|fees|31375.485726|31359.559053|31343.971605|62719.059889|62687.222972|62656.063844|
|slippage_cost|0.000000|31359.574543|62687.999162|0.000000|31343.626968|62656.119768|
|half_spread_cost|6275.097663|6271.914909|6268.799916|6271.906506|6268.725394|6265.611977|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|4984|4984|4984|4984|4984|4984|
|analyzer_calls|4980|4980|4980|4980|4980|4980|
|raw_BUY|1053|1053|1053|1053|1053|1053|
|raw_SELL|1082|1082|1082|1082|1082|1082|
|raw_HOLD|2849|2849|2849|2849|2849|2849|
|effective_BUY|692|692|692|692|692|692|
|effective_SELL|826|826|826|826|826|826|
|effective_HOLD|3462|3462|3462|3462|3462|3462|
|volume_blocked|1140|1140|1140|1140|1140|1140|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|80|80|80|80|80|80|
|sell_attempts|77|77|77|77|77|77|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|615|615|615|615|615|615|
|sell_without_position|750|750|750|750|750|750|
|risk_bypassed_analyzer|4|4|4|4|4|4|
|buys|80|80|80|80|80|80|
|sells|77|77|77|77|77|77|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|479|4505|
|rsi_rising|2243|2741|
|rsi_gt_70|501|4483|
|rsi_falling|2219|2765|
|macd_gt_signal|2569|2415|
|macd_negative|2722|2262|
|macd_rising|2522|2462|
|macd_lt_signal|2415|2569|
|macd_positive|2262|2722|
|macd_falling|2462|2522|
|below_lower|259|4725|
|rsi_or_macd_rising|3225|1759|
|above_upper|244|4740|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|4505|
|rsi_sell|rsi_gt_70|4483|
|macd_buy|macd_gt_signal|2415|
|macd_sell|macd_lt_signal|2569|
|bb_buy|below_lower|4725|
|bb_sell|above_upper|4740|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-XRP; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|-4.440793|-5.417251|-6.309151|-4.438554|-5.414464|-6.305899|
|net_return_pct|-5.336373|-6.312417|-7.203862|-6.228781|-7.203863|-8.094389|
|mdd_pct|6.305815|7.046158|7.915422|6.962857|7.915422|8.784130|
|win_rate_pct|61.904762|57.142857|57.142857|57.142857|57.142857|42.857143|
|average_net_sell_pnl|-2132.735127|-2588.204087|-3003.396746|-2548.380989|-3003.396674|-3418.156419|
|expectancy_per_sell|-2132.735127|-2588.204087|-3003.396746|-2548.380989|-3003.396674|-3418.156419|
|net_realized_pnl|-44787.437677|-54352.285821|-63071.331657|-53516.000760|-63071.330144|-71781.284809|
|unrealized_pnl|-8576.289898|-8771.884396|-8967.283515|-8771.806237|-8967.303036|-9162.604552|
|buy_hold_100_net_pct|5.147953|5.095411|5.042921|5.095432|5.042916|4.990452|
|buy_hold_40_net_pct|2.059181|2.038164|2.017168|2.038173|2.017166|1.996181|
|fees|8955.793406|8951.655844|8947.104877|17902.262633|17893.991602|17884.894590|
|slippage_cost|0.000000|8951.525446|17893.731868|0.000000|8946.865469|17884.416945|
|half_spread_cost|1791.154341|1790.305089|1789.373187|1790.221925|1789.373094|1788.441694|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1727|1727|1727|1727|1727|1727|
|analyzer_calls|1724|1724|1724|1724|1724|1724|
|raw_BUY|355|355|355|355|355|355|
|raw_SELL|482|482|482|482|482|482|
|raw_HOLD|890|890|890|890|890|890|
|effective_BUY|243|243|243|243|243|243|
|effective_SELL|359|359|359|359|359|359|
|effective_HOLD|1122|1122|1122|1122|1122|1122|
|volume_blocked|439|438|438|439|438|438|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|24|24|24|24|24|24|
|sell_attempts|21|21|21|21|21|21|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|221|221|221|221|221|221|
|sell_without_position|339|339|339|339|339|339|
|risk_bypassed_analyzer|3|3|3|3|3|3|
|buys|24|24|24|24|24|24|
|sells|21|21|21|21|21|21|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|168|1559|
|rsi_rising|822|905|
|rsi_gt_70|199|1528|
|rsi_falling|814|913|
|macd_gt_signal|849|878|
|macd_negative|862|865|
|macd_rising|875|852|
|macd_lt_signal|878|849|
|macd_positive|865|862|
|macd_falling|852|875|
|below_lower|75|1652|
|rsi_or_macd_rising|1155|572|
|above_upper|124|1603|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|1559|
|rsi_sell|rsi_gt_70|1528|
|macd_buy|macd_gt_signal|878|
|macd_sell|macd_lt_signal|849|
|bb_buy|below_lower|1652|
|bb_sell|above_upper|1603|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-XRP; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|-1.410425|-2.107546|-3.718786|-1.410232|-2.106975|-3.717079|
|net_return_pct|-2.087166|-2.783941|-4.434531|-2.763000|-3.459050|-5.147816|
|mdd_pct|4.570892|4.709035|6.233694|4.708195|5.263184|6.827214|
|win_rate_pct|50.000000|50.000000|47.058824|50.000000|43.750000|47.058824|
|average_net_sell_pnl|-1304.478474|-1739.962990|-2608.547781|-1726.874733|-2161.906502|-3028.127330|
|expectancy_per_sell|-1304.478474|-1739.962990|-2608.547781|-1726.874733|-2161.906502|-3028.127330|
|net_realized_pnl|-20871.655591|-27839.407836|-44345.312273|-27629.995724|-34590.504039|-51478.164612|
|unrealized_pnl|-0.000000|0.000000|0.000000|0.000000|-0.000000|0.000000|
|buy_hold_100_net_pct|24.892548|24.830140|24.767793|24.830165|24.767787|24.705472|
|buy_hold_40_net_pct|9.957019|9.932056|9.907117|9.932066|9.907115|9.882189|
|fees|6767.409182|6763.944165|7157.454830|13527.675502|13520.749210|14307.378973|
|slippage_cost|0.000000|6763.940277|14314.886074|0.000000|6760.370718|14307.355397|
|half_spread_cost|1353.481709|1352.788055|1431.488607|1352.767423|1352.074144|1430.735540|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1096|1096|1096|1096|1096|1096|
|analyzer_calls|1094|1094|1093|1094|1094|1093|
|raw_BUY|267|267|267|267|267|267|
|raw_SELL|197|197|197|197|197|197|
|raw_HOLD|632|632|632|632|632|632|
|effective_BUY|169|169|169|169|169|169|
|effective_SELL|153|153|153|153|153|153|
|effective_HOLD|772|772|771|772|772|771|
|volume_blocked|254|254|254|254|254|254|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|18|18|19|18|18|19|
|sell_attempts|16|16|17|16|16|17|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|153|153|152|153|153|152|
|sell_without_position|137|137|137|137|137|137|
|risk_bypassed_analyzer|2|2|3|2|2|3|
|buys|18|18|19|18|18|19|
|sells|16|16|17|16|16|17|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|139|957|
|rsi_rising|504|592|
|rsi_gt_70|109|987|
|rsi_falling|539|557|
|macd_gt_signal|549|547|
|macd_negative|699|397|
|macd_rising|542|554|
|macd_lt_signal|547|549|
|macd_positive|397|699|
|macd_falling|554|542|
|below_lower|74|1022|
|rsi_or_macd_rising|717|379|
|above_upper|62|1034|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|957|
|rsi_sell|rsi_gt_70|987|
|macd_buy|macd_gt_signal|547|
|macd_sell|macd_lt_signal|549|
|bb_buy|below_lower|1022|
|bb_sell|above_upper|1034|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-XRP; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|3.311329|3.048419|2.785771|3.309675|3.046896|2.784380|
|net_return_pct|3.049941|2.787152|2.524626|2.787160|2.524624|2.262350|
|mdd_pct|1.640246|1.641705|1.643167|1.641705|1.643166|1.644630|
|win_rate_pct|100.000000|83.333333|83.333333|83.333333|83.333333|83.333333|
|average_net_sell_pnl|4657.991097|4253.535310|3849.483534|4253.535132|3849.483598|3445.835672|
|expectancy_per_sell|4657.991097|4253.535310|3849.483534|4253.535132|3849.483598|3445.835672|
|net_realized_pnl|27947.946583|25521.211858|23096.901202|25521.210791|23096.901590|20675.014033|
|unrealized_pnl|2551.466449|2350.311409|2149.357303|2350.391790|2149.337227|1948.483498|
|buy_hold_100_net_pct|5.093189|5.040674|4.988211|5.040695|4.988206|4.935769|
|buy_hold_40_net_pct|2.037276|2.016269|1.995284|2.016278|1.995282|1.974308|
|fees|2613.881614|2612.667639|2611.454878|5225.151957|5222.725221|5220.300909|
|slippage_cost|0.000000|2612.557020|5222.504354|0.000000|2611.252047|5219.895710|
|half_spread_cost|522.772634|522.511404|522.250435|522.511508|522.250409|521.989571|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|431|431|431|431|431|431|
|analyzer_calls|431|431|431|431|431|431|
|raw_BUY|85|85|85|85|85|85|
|raw_SELL|121|121|121|121|121|121|
|raw_HOLD|225|225|225|225|225|225|
|effective_BUY|57|57|57|57|57|57|
|effective_SELL|88|88|88|88|88|88|
|effective_HOLD|286|286|286|286|286|286|
|volume_blocked|112|112|112|112|112|112|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|7|7|7|7|7|7|
|sell_attempts|6|6|6|6|6|6|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|50|50|50|50|50|50|
|sell_without_position|82|82|82|82|82|82|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|7|7|7|7|7|7|
|sells|6|6|6|6|6|6|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|56|375|
|rsi_rising|202|229|
|rsi_gt_70|64|367|
|rsi_falling|217|214|
|macd_gt_signal|213|218|
|macd_negative|199|232|
|macd_rising|226|205|
|macd_lt_signal|218|213|
|macd_positive|232|199|
|macd_falling|205|226|
|below_lower|20|411|
|rsi_or_macd_rising|291|140|
|above_upper|23|408|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|375|
|rsi_sell|rsi_gt_70|367|
|macd_buy|macd_gt_signal|218|
|macd_sell|macd_lt_signal|213|
|bb_buy|below_lower|411|
|bb_sell|above_upper|408|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-SOL; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|5.818747|3.132402|0.113151|5.815840|3.130680|0.112946|
|net_return_pct|2.817336|0.113358|-2.904374|-0.183982|-2.904374|-5.919071|
|mdd_pct|3.078699|3.488676|4.587615|3.777532|4.587615|6.480254|
|win_rate_pct|68.000000|56.000000|48.000000|56.000000|48.000000|40.000000|
|average_net_sell_pnl|375.644851|15.114381|-387.249931|-24.530906|-387.249868|-789.209462|
|expectancy_per_sell|375.644851|15.114381|-387.249931|-24.530906|-387.249868|-789.209462|
|net_realized_pnl|28173.363809|1133.578550|-29043.744814|-1839.817971|-29043.740127|-59190.709625|
|unrealized_pnl|0.000000|-0.000000|0.000000|0.000000|-0.000000|0.000000|
|buy_hold_100_net_pct|27.363723|27.300079|27.236499|27.300104|27.236493|27.172944|
|buy_hold_40_net_pct|10.945489|10.920032|10.894600|10.920042|10.894597|10.869178|
|fees|30014.101229|30190.443666|30175.252626|59998.218340|60350.535979|60320.169267|
|slippage_cost|0.000000|30190.463932|60350.579521|0.000000|30175.288245|60320.243497|
|half_spread_cost|6002.820888|6038.092786|6035.057952|5999.822476|6035.057649|6032.024350|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|4984|4984|4984|4984|4984|4984|
|analyzer_calls|4984|4983|4983|4984|4983|4983|
|raw_BUY|910|910|910|910|910|910|
|raw_SELL|1269|1269|1269|1269|1269|1269|
|raw_HOLD|2805|2805|2805|2805|2805|2805|
|effective_BUY|570|570|570|570|570|570|
|effective_SELL|884|884|884|884|884|884|
|effective_HOLD|3530|3529|3529|3530|3529|3529|
|volume_blocked|1529|1529|1529|1529|1529|1529|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|75|76|76|75|76|76|
|sell_attempts|75|75|75|75|75|75|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|495|495|495|495|495|495|
|sell_without_position|809|809|809|809|809|809|
|risk_bypassed_analyzer|0|1|1|0|1|1|
|buys|75|76|76|75|76|76|
|sells|75|75|75|75|75|75|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|377|4607|
|rsi_rising|2164|2820|
|rsi_gt_70|539|4445|
|rsi_falling|2193|2791|
|macd_gt_signal|2506|2478|
|macd_negative|2419|2565|
|macd_rising|2526|2458|
|macd_lt_signal|2478|2506|
|macd_positive|2565|2419|
|macd_falling|2458|2526|
|below_lower|262|4722|
|rsi_or_macd_rising|3211|1773|
|above_upper|279|4705|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|4607|
|rsi_sell|rsi_gt_70|4445|
|macd_buy|macd_gt_signal|2478|
|macd_sell|macd_lt_signal|2506|
|bb_buy|below_lower|4722|
|bb_sell|above_upper|4705|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-SOL; minutes: 15; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|-1.552879|-2.269876|-2.986137|-1.552296|-2.268925|-2.984819|
|net_return_pct|-2.270830|-2.987469|-3.703372|-2.987461|-3.703374|-4.418553|
|mdd_pct|3.461821|3.957987|4.453836|3.957987|4.453836|4.949368|
|win_rate_pct|58.823529|52.941176|41.176471|52.941176|41.176471|41.176471|
|average_net_sell_pnl|-1162.491435|-1572.372545|-1981.832893|-1572.372685|-1981.832837|-2390.872664|
|expectancy_per_sell|-1162.491435|-1572.372545|-1981.832893|-1572.372685|-1981.832837|-2390.872664|
|net_realized_pnl|-19762.354389|-26730.333273|-33691.159183|-26730.335644|-33691.158234|-40644.835285|
|unrealized_pnl|-2945.944594|-3144.352577|-3342.562369|-3144.273293|-3342.582171|-3540.692958|
|buy_hold_100_net_pct|16.464412|16.406214|16.348075|16.406238|16.348069|16.289959|
|buy_hold_40_net_pct|6.585765|6.562486|6.539230|6.562495|6.539228|6.515984|
|fees|7179.504862|7175.924442|7172.347695|14351.646356|14344.489284|14337.339555|
|slippage_cost|0.000000|7175.801159|14344.243576|0.000000|7172.121420|14336.887964|
|half_spread_cost|1435.896861|1435.160232|1434.424358|1435.160526|1434.424284|1433.688796|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1727|1727|1727|1727|1727|1727|
|analyzer_calls|1726|1726|1726|1726|1726|1726|
|raw_BUY|300|300|300|300|300|300|
|raw_SELL|508|508|508|508|508|508|
|raw_HOLD|919|919|919|919|919|919|
|effective_BUY|190|190|190|190|190|190|
|effective_SELL|394|394|394|394|394|394|
|effective_HOLD|1142|1142|1142|1142|1142|1142|
|volume_blocked|438|438|438|438|438|438|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|19|19|19|19|19|19|
|sell_attempts|17|17|17|17|17|17|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|172|172|172|172|172|172|
|sell_without_position|377|377|377|377|377|377|
|risk_bypassed_analyzer|1|1|1|1|1|1|
|buys|19|19|19|19|19|19|
|sells|17|17|17|17|17|17|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|116|1611|
|rsi_rising|808|919|
|rsi_gt_70|194|1533|
|rsi_falling|817|910|
|macd_gt_signal|852|875|
|macd_negative|758|969|
|macd_rising|878|849|
|macd_lt_signal|875|852|
|macd_positive|969|758|
|macd_falling|849|878|
|below_lower|83|1644|
|rsi_or_macd_rising|1153|574|
|above_upper|106|1621|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|1611|
|rsi_sell|rsi_gt_70|1533|
|macd_buy|macd_gt_signal|875|
|macd_sell|macd_lt_signal|852|
|bb_buy|below_lower|1644|
|bb_sell|above_upper|1621|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: train
UTC period: [2026-07-23T00:00:00+00:00, 2026-09-15T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-SOL; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|1.944360|1.443622|0.943402|1.943062|1.442583|0.942623|
|net_return_pct|1.444662|0.944183|0.444223|0.944183|0.444223|-0.055218|
|mdd_pct|2.842522|2.859939|2.877448|2.859936|2.877449|2.971472|
|win_rate_pct|66.666667|58.333333|41.666667|58.333333|41.666667|41.666667|
|average_net_sell_pnl|1203.884768|786.818833|370.185807|786.818752|370.185849|-46.014602|
|expectancy_per_sell|1203.884768|786.818833|370.185807|786.818752|370.185849|-46.014602|
|net_realized_pnl|14446.617216|9441.825993|4442.229685|9441.825027|4442.230185|-552.175226|
|unrealized_pnl|0.000000|-0.000000|-0.000000|-0.000000|-0.000000|-0.000000|
|buy_hold_100_net_pct|31.730479|31.664653|31.598893|31.664679|31.598887|31.533159|
|buy_hold_40_net_pct|12.692192|12.665861|12.639557|12.665872|12.639555|12.613264|
|fees|4996.987722|4994.389252|4991.793475|9988.790791|9983.596640|9978.407873|
|slippage_cost|0.000000|4994.395381|9983.609407|0.000000|4991.804445|9978.430316|
|half_spread_cost|999.397749|998.879076|998.360941|998.879283|998.360889|997.843032|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|1096|1096|1096|1096|1096|1096|
|analyzer_calls|1095|1095|1095|1095|1095|1095|
|raw_BUY|160|160|160|160|160|160|
|raw_SELL|307|307|307|307|307|307|
|raw_HOLD|629|629|629|629|629|629|
|effective_BUY|108|108|108|108|108|108|
|effective_SELL|226|226|226|226|226|226|
|effective_HOLD|761|761|761|761|761|761|
|volume_blocked|270|270|270|270|270|270|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|13|13|13|13|13|13|
|sell_attempts|12|12|12|12|12|12|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|96|96|96|96|96|96|
|sell_without_position|214|214|214|214|214|214|
|risk_bypassed_analyzer|1|1|1|1|1|1|
|buys|13|13|13|13|13|13|
|sells|12|12|12|12|12|12|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|72|1024|
|rsi_rising|513|583|
|rsi_gt_70|150|946|
|rsi_falling|497|599|
|macd_gt_signal|538|558|
|macd_negative|481|615|
|macd_rising|548|548|
|macd_lt_signal|558|538|
|macd_positive|615|481|
|macd_falling|548|548|
|below_lower|52|1044|
|rsi_or_macd_rising|732|364|
|above_upper|55|1041|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|1024|
|rsi_sell|rsi_gt_70|946|
|macd_buy|macd_gt_signal|558|
|macd_sell|macd_lt_signal|538|
|bb_buy|below_lower|1044|
|bb_sell|above_upper|1041|


# Legacy strategy offline diagnostic
Status: EVALUATED; minute1: INSUFFICIENT_REAL_DATA
Source: VERIFIED_ARCHIVED_REAL_DATA; split: validation
UTC period: [2026-09-15T00:00:00+00:00, 2026-10-03T00:00:00+00:00)
Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.

Market: KRW-SOL; minutes: 60; spread: 2bps

Performance (fee-addback is NOT a no-cost counterfactual)

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|gross_fee_addback_return_pct|2.174294|1.972321|1.770549|2.173208|1.971335|1.769665|
|net_return_pct|1.973307|1.771434|1.569764|1.771434|1.569764|1.368294|
|mdd_pct|1.267138|1.307201|1.347304|1.307201|1.347304|1.387446|
|win_rate_pct|100.000000|100.000000|80.000000|100.000000|80.000000|80.000000|
|average_net_sell_pnl|3946.613432|3542.868667|3139.527203|3542.868490|3139.527268|2736.588943|
|expectancy_per_sell|3946.613432|3542.868667|3139.527203|3542.868490|3139.527268|2736.588943|
|net_realized_pnl|19733.067162|17714.343336|15697.636015|17714.342448|15697.636338|13682.944717|
|unrealized_pnl|-0.000000|-0.000000|-0.000000|0.000000|-0.000000|0.000000|
|buy_hold_100_net_pct|16.380382|16.322227|16.264130|16.322250|16.264124|16.206056|
|buy_hold_40_net_pct|6.552153|6.528891|6.505652|6.528900|6.505650|6.482422|
|fees|2009.871969|2008.862102|2007.853244|4017.736075|4015.717350|4013.700641|
|slippage_cost|0.000000|2008.868743|4015.730824|0.000000|2007.865312|4013.724964|
|half_spread_cost|401.974615|401.773749|401.573082|401.773829|401.573062|401.372496|
|na_reason|N/A|N/A|N/A|N/A|N/A|N/A|

Decision and confirmed fill counts

|Metric|fee5/slip0|fee5/slip5|fee5/slip10|fee10/slip0|fee10/slip5|fee10/slip10|
|---|---:|---:|---:|---:|---:|---:|
|evaluations|431|431|431|431|431|431|
|analyzer_calls|431|431|431|431|431|431|
|raw_BUY|65|65|65|65|65|65|
|raw_SELL|159|159|159|159|159|159|
|raw_HOLD|207|207|207|207|207|207|
|effective_BUY|45|45|45|45|45|45|
|effective_SELL|122|122|122|122|122|122|
|effective_HOLD|264|264|264|264|264|264|
|volume_blocked|104|104|104|104|104|104|
|cooldown_blocked|0|0|0|0|0|0|
|cooldown_blocked_actionable|0|0|0|0|0|0|
|nonfinite_skipped|0|0|0|0|0|0|
|duplicate_evaluations_suppressed|0|0|0|0|0|0|
|repeat_signal_candidates|0|0|0|0|0|0|
|buy_attempts|5|5|5|5|5|5|
|sell_attempts|5|5|5|5|5|5|
|buy_rejected|0|0|0|0|0|0|
|sell_rejected|0|0|0|0|0|0|
|reject_position_limit|0|0|0|0|0|0|
|reject_cash_below_minimum|0|0|0|0|0|0|
|reject_minimum_order|0|0|0|0|0|0|
|buy_while_held|40|40|40|40|40|40|
|sell_without_position|117|117|117|117|117|117|
|risk_bypassed_analyzer|0|0|0|0|0|0|
|buys|5|5|5|5|5|5|
|sells|5|5|5|5|5|5|

Atomic predicates (overlap; do not sum as HOLD count)

|Predicate|Met|Not met|
|---|---:|---:|
|rsi_lt_30|43|388|
|rsi_rising|206|225|
|rsi_gt_70|92|339|
|rsi_falling|211|220|
|macd_gt_signal|214|217|
|macd_negative|169|262|
|macd_rising|212|219|
|macd_lt_signal|217|214|
|macd_positive|262|169|
|macd_falling|219|212|
|below_lower|14|417|
|rsi_or_macd_rising|286|145|
|above_upper|35|396|

First failed condition per rule (full counts retained in JSON)

|Rule|Most frequent first failure|Count|
|---|---|---:|
|rsi_buy|rsi_lt_30|388|
|rsi_sell|rsi_gt_70|339|
|macd_buy|macd_gt_signal|217|
|macd_sell|macd_lt_signal|214|
|bb_buy|below_lower|417|
|bb_sell|above_upper|396|
