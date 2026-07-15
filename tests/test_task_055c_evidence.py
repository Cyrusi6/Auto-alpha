import json
from pathlib import Path
import pytest
from task_055_c.evidence import Task055CEvidenceError, _classify, canonical_hash
from task_055_c.cascade import _request


def test_vendor_modeled_requires_exact_positive_s_complete_coverage():
    assert _classify("S", "null", "absent", "complete", False)[0] == "VENDOR_DAILY_NON_TRADING_MODELED"
    for event, timing, coverage in (("R", "null", "complete"), ("S+R", "null", "complete"), ("S", "unparsed", "complete"), ("S", "null", "incomplete")):
        assert _classify(event, timing, "absent", coverage, False)[0] != "VENDOR_DAILY_NON_TRADING_MODELED"


def test_transport_identity_excludes_episode_evidence_use():
    left = _request("L2", "suspend_d", {"ts_code":"600000.SH","start_date":"20240101","end_date":"20240131"}, ("ts_code","trade_date","suspend_timing","suspend_type"), ["a"])
    right = _request("L2", "suspend_d", {"ts_code":"600000.SH","start_date":"20240101","end_date":"20240131"}, ("ts_code","trade_date","suspend_timing","suspend_type"), ["b"])
    assert left["transport_hash"] == right["transport_hash"]
    assert left["evidence_use_hash"] != right["evidence_use_hash"]


def test_same_day_suspend_resume_and_wrong_code_fail_closed():
    assert _classify("S+R", "null", "absent", "complete", False) == ("CONFLICT", "same_day_suspend_resume_conflict")
    assert _classify("none", "none", "absent", "complete", False)[0] == "DATA_SOURCE_GAP"


def test_handwritten_replay_counts_are_not_proof():
    fake={"primary_terminal_count":100,"sibling_terminal_count":100,"resume_hit_count":100,"truth_hash_match":True}
    assert canonical_hash(fake) != "0" * 64
