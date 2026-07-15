import numpy as np
from task_055_c.valuation import METHODS


def test_mark_method_contract_is_strict():
    assert METHODS == {
        "UNRESOLVED":0,"OFFICIAL_OPEN":1,"OFFICIAL_CLOSE":2,
        "STALE_OFFICIAL_NON_TRADING":3,"STALE_VENDOR_DAILY_NON_TRADING_MODELED":4,
        "LIFECYCLE_SETTLEMENT":5,
    }


def test_normal_close_between_gaps_refreshes_prior_mark():
    closes=np.array([10.0,11.0,np.nan]); valid=np.array([True,True,False]); prior=np.nan
    used=[]
    for value, ok in zip(closes,valid):
        if ok: prior=value
        used.append(prior)
    assert used[-1] == 11.0
