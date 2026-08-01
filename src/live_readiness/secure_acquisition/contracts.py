from __future__ import annotations
CANONICAL_ORIGIN="https://api.tushare.pro"
MAX_DATE="20260630"
GLOBAL_BUDGET=2500
REQUEST_NORMALIZATION_VERSION="task055d_transport_identity_v1"
PROVIDER_API_VERSION="tushare_pro_http.v1"
ENDPOINT_CAPS={"daily":6000,"suspend_d":5000}
DAILY_FIELDS=("ts_code","trade_date","open","high","low","close","pre_close","vol","amount")
SUSPEND_FIELDS=("ts_code","trade_date","suspend_timing","suspend_type")
BLOCKED="task055d_secure_acquisition_or_valuation_or_fee_closure_blocked"
