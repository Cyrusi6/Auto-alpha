"""Dataset registry for governed A-share raw data collection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AShareDatasetDefinition:
    dataset: str
    api_name: str
    fields: tuple[str, ...]
    primary_key: tuple[str, ...]
    description: str
    date_field: str | None = None
    availability_date_field: str | None = None
    effective_date_field: str | None = None
    chunk_strategy: str = "static"
    date_param_mode: str = "range"
    single_date_param: str | None = None
    index_param: str | None = None
    default_params: dict[str, str] | None = None
    ts_code_split_recommended: bool = False
    pit_safe: bool = False
    weak_pit: bool = False

    @property
    def field_string(self) -> str:
        return ",".join(self.fields)


CORE_DATASETS: tuple[str, ...] = (
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
    "index_members",
    "corporate_actions",
)

INDEX_INDUSTRY_STATUS_DATASETS: tuple[str, ...] = (
    "index_basic",
    "index_daily_bars",
    "index_daily_basic",
    "industry_classification",
    "industry_members",
    "suspensions",
    "name_changes",
    "new_shares",
)

FINANCIAL_STATEMENT_DATASETS: tuple[str, ...] = (
    "income_statements",
    "balance_sheets",
    "cashflow_statements",
    "earnings_forecasts",
    "earnings_express",
    "disclosure_calendar",
    "financial_audit",
    "main_business",
)

FLOW_MARGIN_TRADING_DATASETS: tuple[str, ...] = (
    "moneyflow",
    "margin_summary",
    "margin_detail",
    "top_list",
    "top_inst",
    "block_trades",
)

HOLDER_EVENT_RISK_DATASETS: tuple[str, ...] = (
    "holder_number",
    "holder_trades",
    "top10_holders",
    "top10_float_holders",
    "pledge_detail",
    "pledge_stat",
    "repurchases",
    "share_unlocks",
    "hk_holdings",
)

FULL_RESEARCH_DATASETS: tuple[str, ...] = (
    *CORE_DATASETS,
    *INDEX_INDUSTRY_STATUS_DATASETS,
    *FINANCIAL_STATEMENT_DATASETS,
    *FLOW_MARGIN_TRADING_DATASETS,
    *HOLDER_EVENT_RISK_DATASETS,
)

EXPANDED_INDEX_CODES: tuple[str, ...] = (
    "000016.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "000906.SH",
    "000985.CSI",
    "932000.CSI",
    "000001.SH",
    "399001.SZ",
    "399006.SZ",
    "399107.SZ",
    "000688.SH",
)


DATASET_DEFINITIONS: dict[str, AShareDatasetDefinition] = {
    "index_basic": AShareDatasetDefinition(
        dataset="index_basic",
        api_name="index_basic",
        fields=("ts_code", "name", "fullname", "market", "publisher", "index_type", "category", "base_date", "base_point", "list_date", "weight_rule", "desc", "exp_date"),
        primary_key=("ts_code",),
        date_field="list_date",
        availability_date_field="list_date",
        effective_date_field="list_date",
        description="Index master data.",
        chunk_strategy="static",
        date_param_mode="none",
        weak_pit=True,
    ),
    "index_daily_bars": AShareDatasetDefinition(
        dataset="index_daily_bars",
        api_name="index_daily",
        fields=("ts_code", "trade_date", "close", "open", "high", "low", "pre_close", "change", "pct_chg", "vol", "amount"),
        primary_key=("ts_code", "trade_date"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Index daily OHLCV bars.",
        chunk_strategy="index_window",
        index_param="ts_code",
    ),
    "index_daily_basic": AShareDatasetDefinition(
        dataset="index_daily_basic",
        api_name="index_dailybasic",
        fields=("ts_code", "trade_date", "total_mv", "float_mv", "total_share", "float_share", "free_share", "turnover_rate", "turnover_rate_f", "pe", "pe_ttm", "pb"),
        primary_key=("ts_code", "trade_date"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Index daily valuation and share indicators.",
        chunk_strategy="index_window",
        index_param="ts_code",
    ),
    "industry_classification": AShareDatasetDefinition(
        dataset="industry_classification",
        api_name="index_classify",
        fields=("index_code", "industry_name", "level", "industry_code", "is_pub", "src"),
        primary_key=("index_code", "industry_code", "level", "src"),
        description="Industry classification tree.",
        chunk_strategy="static",
        date_param_mode="none",
        default_params={"src": "SW2021"},
        weak_pit=True,
    ),
    "industry_members": AShareDatasetDefinition(
        dataset="industry_members",
        api_name="index_member_all",
        fields=("l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name", "ts_code", "name", "in_date", "out_date", "is_new"),
        primary_key=("ts_code", "l1_code", "l2_code", "l3_code", "in_date", "out_date"),
        date_field="in_date",
        availability_date_field="in_date",
        effective_date_field="in_date",
        description="Industry membership history.",
        chunk_strategy="static",
        date_param_mode="none",
        weak_pit=True,
    ),
    "suspensions": AShareDatasetDefinition(
        dataset="suspensions",
        api_name="suspend_d",
        fields=("ts_code", "suspend_date", "resume_date", "ann_date", "suspend_reason", "reason_type"),
        primary_key=("ts_code", "suspend_date", "resume_date"),
        date_field="suspend_date",
        availability_date_field="ann_date",
        effective_date_field="suspend_date",
        description="Suspension and resumption events.",
        chunk_strategy="trade_day",
        date_param_mode="single",
        single_date_param="suspend_date",
        weak_pit=True,
    ),
    "name_changes": AShareDatasetDefinition(
        dataset="name_changes",
        api_name="namechange",
        fields=("ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"),
        primary_key=("ts_code", "start_date", "end_date", "name"),
        date_field="start_date",
        availability_date_field="ann_date",
        effective_date_field="start_date",
        description="Security name and status changes.",
        chunk_strategy="static",
        date_param_mode="none",
        weak_pit=True,
    ),
    "new_shares": AShareDatasetDefinition(
        dataset="new_shares",
        api_name="new_share",
        fields=("ts_code", "sub_code", "name", "ipo_date", "issue_date", "amount", "market_amount", "price", "pe", "limit_amount", "funds", "ballot"),
        primary_key=("ts_code", "ipo_date", "issue_date"),
        date_field="ipo_date",
        availability_date_field="issue_date",
        effective_date_field="ipo_date",
        description="IPO and new-share issuance records.",
        chunk_strategy="window",
        date_param_mode="range",
    ),
    "income_statements": AShareDatasetDefinition(
        dataset="income_statements",
        api_name="income",
        fields=("ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "comp_type", "end_type", "basic_eps", "diluted_eps", "total_revenue", "revenue", "int_income", "prem_earned", "comm_income", "n_commis_income", "n_oth_income", "n_oth_b_income", "prem_income", "out_prem", "une_prem_reser", "reins_income", "n_sec_tb_income", "n_sec_uw_income", "n_asset_mg_income", "oth_b_income", "fv_value_chg_gain", "invest_income", "ass_invest_income", "forex_gain", "total_cogs", "oper_cost", "int_exp", "comm_exp", "biz_tax_surchg", "sell_exp", "admin_exp", "fin_exp", "assets_impair_loss", "total_profit", "income_tax", "n_income", "n_income_attr_p", "minority_gain", "oth_compr_income", "t_compr_income", "compr_inc_attr_p", "compr_inc_attr_m_s", "ebit", "ebitda", "insurance_exp", "undist_profit", "distable_profit", "update_flag"),
        primary_key=("ts_code", "end_date", "ann_date", "report_type"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Full income statements.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "balance_sheets": AShareDatasetDefinition(
        dataset="balance_sheets",
        api_name="balancesheet",
        fields=("ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "comp_type", "end_type", "total_share", "cap_rese", "undistr_porfit", "surplus_rese", "special_rese", "money_cap", "trad_asset", "notes_receiv", "accounts_receiv", "oth_receiv", "prepayment", "div_receiv", "int_receiv", "inventories", "amor_exp", "nca_within_1y", "sett_rsrv", "loanto_oth_bank_fi", "premium_receiv", "reinsur_receiv", "reinsur_res_receiv", "pur_resale_fa", "oth_cur_assets", "total_cur_assets", "fa_avail_for_sale", "htm_invest", "lt_eqt_invest", "invest_real_estate", "time_deposits", "oth_assets", "lt_rec", "fix_assets", "cip", "const_materials", "fixed_assets_disp", "produc_bio_assets", "oil_and_gas_assets", "intan_assets", "r_and_d", "goodwill", "lt_amor_exp", "defer_tax_assets", "decr_in_disbur", "oth_nca", "total_nca", "cash_reser_cb", "depos_in_oth_bfi", "prec_metals", "deriv_assets", "rr_reins_une_prem", "rr_reins_outstd_cla", "rr_reins_lins_liab", "rr_reins_lthins_liab", "refund_depos", "ph_pledge_loans", "refund_cap_depos", "indep_acct_assets", "client_depos", "client_prov", "transac_seat_fee", "invest_as_receiv", "total_assets", "lt_borr", "st_borr", "cb_borr", "depos_ib_deposits", "loan_oth_bank", "trading_fl", "notes_payable", "acct_payable", "adv_receipts", "sold_for_repur_fa", "comm_payable", "payroll_payable", "taxes_payable", "int_payable", "div_payable", "oth_payable", "acc_exp", "deferred_inc", "st_bonds_payable", "payable_to_reinsurer", "rsrv_insur_cont", "acting_trading_sec", "acting_uw_sec", "non_cur_liab_due_1y", "oth_cur_liab", "total_cur_liab", "bond_payable", "lt_payable", "specific_payables", "estimated_liab", "defer_tax_liab", "defer_inc_non_cur_liab", "oth_ncl", "total_ncl", "depos_oth_bfi", "deriv_liab", "depos", "agency_bus_liab", "oth_liab", "prem_receiv_adva", "depos_received", "ph_invest", "reser_une_prem", "reser_outstd_claims", "reser_lins_liab", "reser_lthins_liab", "indept_acc_liab", "pledge_borr", "indem_payable", "policy_div_payable", "total_liab", "treasury_share", "ordin_risk_reser", "forex_differ", "invest_loss_unconf", "minority_int", "total_hldr_eqy_exc_min_int", "total_hldr_eqy_inc_min_int", "total_liab_hldr_eqy", "lt_payroll_payable", "oth_comp_income", "oth_eqt_tools", "oth_eqt_tools_p_shr", "lending_funds", "acc_receivable", "st_fin_payable", "payables", "hfs_assets", "hfs_sales", "update_flag"),
        primary_key=("ts_code", "end_date", "ann_date", "report_type"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Full balance sheets.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "cashflow_statements": AShareDatasetDefinition(
        dataset="cashflow_statements",
        api_name="cashflow",
        fields=("ts_code", "ann_date", "f_ann_date", "end_date", "comp_type", "report_type", "end_type", "net_profit", "finan_exp", "c_fr_sale_sg", "recp_tax_rends", "n_depos_incr_fi", "n_incr_loans_cb", "n_inc_borr_oth_fi", "prem_fr_orig_contr", "n_incr_insured_dep", "n_reinsur_prem", "n_incr_disp_tfa", "ifc_cash_incr", "n_incr_disp_faas", "n_incr_loans_oth_bank", "n_cap_incr_repur", "c_fr_oth_operate_a", "c_inf_fr_operate_a", "c_paid_goods_s", "c_paid_to_for_empl", "c_paid_for_taxes", "n_incr_clt_loan_adv", "n_incr_dep_cbob", "c_pay_claims_orig_inco", "pay_handling_chrg", "pay_comm_insur_plcy", "oth_cash_pay_oper_act", "st_cash_out_act", "n_cashflow_act", "oth_recp_ral_inv_act", "c_disp_withdrwl_invest", "c_recp_return_invest", "n_recp_disp_fiolta", "n_recp_disp_sobu", "stot_inflows_inv_act", "c_pay_acq_const_fiolta", "c_paid_invest", "n_disp_subs_oth_biz", "oth_pay_ral_inv_act", "n_incr_pledge_loan", "stot_out_inv_act", "n_cashflow_inv_act", "c_recp_borrow", "proc_issue_bonds", "oth_cash_recp_ral_fnc_act", "stot_cash_in_fnc_act", "free_cashflow", "c_prepay_amt_borr", "c_pay_dist_dpcp_int_exp", "incl_dvd_profit_paid_sc_ms", "oth_cashpay_ral_fnc_act", "stot_cashout_fnc_act", "n_cash_flows_fnc_act", "eff_fx_flu_cash", "n_incr_cash_cash_equ", "c_cash_equ_beg_period", "c_cash_equ_end_period", "c_recp_cap_contrib", "incl_cash_rec_saims", "uncon_invest_loss", "prov_depr_assets", "depr_fa_coga_dpba", "amort_intang_assets", "lt_amort_deferred_exp", "decr_deferred_exp", "incr_acc_exp", "loss_disp_fiolta", "loss_scr_fa", "loss_fv_chg", "invest_loss", "decr_def_inc_tax_assets", "incr_def_inc_tax_liab", "decr_inventories", "decr_oper_payable", "incr_oper_payable", "others", "im_net_cashflow_oper_act", "conv_debt_into_cap", "conv_copbonds_due_within_1y", "fa_fnc_leases", "im_n_incr_cash_equ", "net_dism_capital_add", "net_cash_rece_sec", "credit_impa_loss", "use_right_asset_dep", "oth_loss_asset", "end_bal_cash", "beg_bal_cash", "end_bal_cash_equ", "beg_bal_cash_equ", "update_flag"),
        primary_key=("ts_code", "end_date", "ann_date", "report_type"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Full cash-flow statements.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "earnings_forecasts": AShareDatasetDefinition(
        dataset="earnings_forecasts",
        api_name="forecast",
        fields=("ts_code", "ann_date", "end_date", "type", "p_change_min", "p_change_max", "net_profit_min", "net_profit_max", "last_parent_net", "first_ann_date", "summary", "change_reason"),
        primary_key=("ts_code", "end_date", "ann_date", "type"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Earnings forecasts.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "earnings_express": AShareDatasetDefinition(
        dataset="earnings_express",
        api_name="express",
        fields=("ts_code", "ann_date", "end_date", "revenue", "operate_profit", "total_profit", "n_income", "total_assets", "total_hldr_eqy_exc_min_int", "diluted_eps", "diluted_roe", "yoy_net_profit", "bps", "yoy_sales", "yoy_op", "yoy_tp", "yoy_dedu_np", "yoy_eps", "yoy_roe", "growth_assets", "yoy_equity", "growth_bps", "or_last_year", "op_last_year", "tp_last_year", "np_last_year", "eps_last_year", "open_net_assets", "open_bps", "perf_summary", "is_audit", "remark"),
        primary_key=("ts_code", "end_date", "ann_date"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Earnings express reports.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "disclosure_calendar": AShareDatasetDefinition(
        dataset="disclosure_calendar",
        api_name="disclosure_date",
        fields=("ts_code", "ann_date", "end_date", "pre_date", "actual_date", "modify_date"),
        primary_key=("ts_code", "end_date", "ann_date"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Financial disclosure calendar.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "financial_audit": AShareDatasetDefinition(
        dataset="financial_audit",
        api_name="fina_audit",
        fields=("ts_code", "ann_date", "end_date", "audit_result", "audit_fees", "audit_agency", "audit_sign"),
        primary_key=("ts_code", "end_date", "ann_date"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Financial audit opinions.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "main_business": AShareDatasetDefinition(
        dataset="main_business",
        api_name="fina_mainbz",
        fields=("ts_code", "end_date", "bz_item", "bz_sales", "bz_profit", "bz_cost", "curr_type", "update_flag"),
        primary_key=("ts_code", "end_date", "bz_item", "curr_type"),
        date_field="end_date",
        availability_date_field=None,
        effective_date_field="end_date",
        description="Main business segment revenue and profit.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        weak_pit=True,
    ),
    "moneyflow": AShareDatasetDefinition(
        dataset="moneyflow",
        api_name="moneyflow",
        fields=("ts_code", "trade_date", "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount", "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount", "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount", "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount", "net_mf_vol", "net_mf_amount"),
        primary_key=("ts_code", "trade_date"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="A-share capital flow by order size.",
        chunk_strategy="trade_day",
        date_param_mode="range",
    ),
    "margin_summary": AShareDatasetDefinition(
        dataset="margin_summary",
        api_name="margin",
        fields=("trade_date", "exchange_id", "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye", "rqyl"),
        primary_key=("trade_date", "exchange_id"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Exchange-level margin trading summary.",
        chunk_strategy="window",
    ),
    "margin_detail": AShareDatasetDefinition(
        dataset="margin_detail",
        api_name="margin_detail",
        fields=("trade_date", "ts_code", "rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye"),
        primary_key=("trade_date", "ts_code"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Security-level margin trading detail.",
        chunk_strategy="trade_day",
    ),
    "top_list": AShareDatasetDefinition(
        dataset="top_list",
        api_name="top_list",
        fields=("trade_date", "ts_code", "name", "close", "pct_change", "turnover_rate", "amount", "l_sell", "l_buy", "l_amount", "net_amount", "net_rate", "amount_rate", "float_values", "reason"),
        primary_key=("trade_date", "ts_code", "reason"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Daily top trading list.",
        chunk_strategy="trade_day",
        date_param_mode="single",
        single_date_param="trade_date",
    ),
    "top_inst": AShareDatasetDefinition(
        dataset="top_inst",
        api_name="top_inst",
        fields=("trade_date", "ts_code", "exalter", "buy", "buy_rate", "sell", "sell_rate", "net_buy", "side", "reason"),
        primary_key=("trade_date", "ts_code", "exalter", "side", "reason"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Top trading list institution seats.",
        chunk_strategy="trade_day",
        date_param_mode="single",
        single_date_param="trade_date",
    ),
    "block_trades": AShareDatasetDefinition(
        dataset="block_trades",
        api_name="block_trade",
        fields=("ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller"),
        primary_key=("trade_date", "ts_code", "buyer", "seller", "price", "vol"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Block trade records.",
        chunk_strategy="trade_day",
        date_param_mode="range",
    ),
    "holder_number": AShareDatasetDefinition(
        dataset="holder_number",
        api_name="stk_holdernumber",
        fields=("ts_code", "ann_date", "end_date", "holder_num"),
        primary_key=("ts_code", "end_date", "ann_date"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Shareholder count.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "holder_trades": AShareDatasetDefinition(
        dataset="holder_trades",
        api_name="stk_holdertrade",
        fields=("ts_code", "ann_date", "holder_name", "holder_type", "in_de", "change_vol", "change_ratio", "after_share", "after_ratio", "avg_price", "total_share", "begin_date", "close_date"),
        primary_key=("ts_code", "ann_date", "holder_name", "begin_date", "close_date"),
        date_field="ann_date",
        availability_date_field="ann_date",
        effective_date_field="close_date",
        description="Major shareholder trading events.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "top10_holders": AShareDatasetDefinition(
        dataset="top10_holders",
        api_name="top10_holders",
        fields=("ts_code", "ann_date", "end_date", "holder_name", "hold_amount", "hold_ratio"),
        primary_key=("ts_code", "end_date", "ann_date", "holder_name"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Top 10 shareholders.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "top10_float_holders": AShareDatasetDefinition(
        dataset="top10_float_holders",
        api_name="top10_floatholders",
        fields=("ts_code", "ann_date", "end_date", "holder_name", "hold_amount", "hold_ratio"),
        primary_key=("ts_code", "end_date", "ann_date", "holder_name"),
        date_field="end_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Top 10 float shareholders.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "pledge_detail": AShareDatasetDefinition(
        dataset="pledge_detail",
        api_name="pledge_detail",
        fields=("ts_code", "ann_date", "holder_name", "pledge_amount", "start_date", "end_date", "is_release", "release_date", "pledgor", "holding_amount", "pledged_amount", "p_total_ratio", "h_total_ratio"),
        primary_key=("ts_code", "ann_date", "holder_name", "start_date", "end_date"),
        date_field="ann_date",
        availability_date_field="ann_date",
        effective_date_field="start_date",
        description="Share pledge detail events.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "pledge_stat": AShareDatasetDefinition(
        dataset="pledge_stat",
        api_name="pledge_stat",
        fields=("ts_code", "end_date", "pledge_count", "unrest_pledge", "rest_pledge", "total_share", "pledge_ratio"),
        primary_key=("ts_code", "end_date"),
        date_field="end_date",
        availability_date_field=None,
        effective_date_field="end_date",
        description="Share pledge statistics.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        weak_pit=True,
    ),
    "repurchases": AShareDatasetDefinition(
        dataset="repurchases",
        api_name="repurchase",
        fields=("ts_code", "ann_date", "end_date", "proc", "exp_date", "vol", "amount", "high_limit", "low_limit"),
        primary_key=("ts_code", "ann_date", "end_date", "proc"),
        date_field="ann_date",
        availability_date_field="ann_date",
        effective_date_field="end_date",
        description="Share repurchase plans and progress.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "share_unlocks": AShareDatasetDefinition(
        dataset="share_unlocks",
        api_name="share_float",
        fields=("ts_code", "ann_date", "float_date", "float_share", "float_ratio", "holder_name", "share_type"),
        primary_key=("ts_code", "float_date", "holder_name", "share_type"),
        date_field="float_date",
        availability_date_field="ann_date",
        effective_date_field="float_date",
        description="Restricted share unlock events.",
        chunk_strategy="window",
        ts_code_split_recommended=True,
        pit_safe=True,
    ),
    "hk_holdings": AShareDatasetDefinition(
        dataset="hk_holdings",
        api_name="hk_hold",
        fields=("trade_date", "ts_code", "name", "vol", "ratio", "exchange"),
        primary_key=("trade_date", "ts_code", "exchange"),
        date_field="trade_date",
        availability_date_field="trade_date",
        effective_date_field="trade_date",
        description="Northbound holdings.",
        chunk_strategy="trade_day",
        date_param_mode="range",
    ),
}


DATASET_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "securities": ("ts_code",),
    "trade_calendar": ("trade_date",),
    "daily_bars": ("ts_code", "trade_date"),
    "daily_basic": ("ts_code", "trade_date"),
    "financial_features": ("ts_code", "report_period", "announce_date"),
    "daily_limits": ("ts_code", "trade_date"),
    "adjustment_factors": ("ts_code", "trade_date"),
    "index_members": ("index_code", "ts_code", "trade_date"),
    "corporate_actions": ("ts_code", "ann_date", "end_date", "ex_date", "div_proc"),
    **{name: definition.primary_key for name, definition in DATASET_DEFINITIONS.items()},
}

WINDOWED_DATASETS: frozenset[str] = frozenset(
    name
    for name, definition in DATASET_DEFINITIONS.items()
    if definition.chunk_strategy in {"window", "trade_day", "index_window"}
) | frozenset({"daily_bars", "daily_basic", "financial_features", "daily_limits", "adjustment_factors", "corporate_actions"})

TRADE_DAY_DATASETS: frozenset[str] = frozenset(
    name for name, definition in DATASET_DEFINITIONS.items() if definition.chunk_strategy == "trade_day"
) | frozenset({"daily_bars", "daily_basic", "daily_limits", "adjustment_factors", "corporate_actions"})

INDEX_CODE_DATASETS: frozenset[str] = frozenset(
    name for name, definition in DATASET_DEFINITIONS.items() if definition.chunk_strategy == "index_window"
) | frozenset({"index_members"})

TS_CODE_SPLIT_DATASETS: frozenset[str] = frozenset(
    name for name, definition in DATASET_DEFINITIONS.items() if definition.ts_code_split_recommended
) | frozenset({"financial_features"})


def dataset_description(dataset: str) -> str:
    descriptions = {
        "securities": "Listed A-share securities.",
        "trade_calendar": "Exchange trading calendar.",
        "daily_bars": "Daily price and volume bars.",
        "daily_basic": "Daily market indicators.",
        "financial_features": "Financial features aligned by announcement date.",
        "daily_limits": "Daily limit up/down prices.",
        "adjustment_factors": "Daily adjustment factors.",
        "index_members": "Index constituent weights.",
        "corporate_actions": "Cash dividend and stock distribution events.",
    }
    if dataset in descriptions:
        return descriptions[dataset]
    return DATASET_DEFINITIONS[dataset].description
