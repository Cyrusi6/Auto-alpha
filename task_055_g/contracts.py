from __future__ import annotations

EXPECTED_BASELINE = "9eeb79b1bc4f55bb93c24818f0455059d5b082b7"
MAX_DATE = "20260630"
SIMULATION_START = "20160104"
SIMULATION_END = "20240530"

ACCESS_PLAN_SCHEMA = "task055g_immutable_access_plan_v1"
ACCESS_LEDGER_SCHEMA = "task055g_attempted_access_ledger_v1"
TRUTH_SCHEMA = "task055g_security_date_truth_v2"
CAUSAL_SCHEMA = "task055g_fee_aware_causal_frontier_v1"
SEMANTIC_VERIFICATION_SCHEMA = "task055g_independent_semantic_verification_v1"
FINAL_REPORT_SCHEMA = "task055g_engineering_report_v1"

FINAL_WAITING_STATUS = "task055g_fee_aware_frontier_sealed_waiting_for_network_authorization"
FINAL_BLOCKED_STATUS = "task055g_offline_engineering_baseline_blocked"

TASK055E_REPORT_CONTENT_HASH = "202d84acc3ad245ad2b7c0b24e3e5eedafa5138a2e2f9b3d296086ea1f03b676"
TASK055F_REPORT_CONTENT_HASH = "922e74e3aa26c9069956ece53ec588e47e39bea8cbb190a9ed927ec4dab5139c"
TASK055F_READ_LEDGER_CONTENT_HASH = "2ded8f6958c3bddd512bccf7c1821d68c6b247cdd060bd42f79b15dbc8040391"
TASK055A_BUNDLE_CONTENT_HASH = "0be5ee96c4fddbed4202f1f4de1124dd87cb7871d18ef5be44f5859b93454e58"
TASK055A_POLICY_SEAL_HASH = "fe2c2712095b5fd13b7e853e625dce8d517c1a1ad002ee743acbf7159fc02a3b"

BOOTSTRAP_INPUTS = (
    {
        "relative_path": "validation_runs/task_055_f_20260716_v6/read_ledger/generations/read_ledger_2ded8f6958c3bddd512bccf7/read_ledger_manifest.json",
        "dataset_role": "task055f_read_ledger_manifest",
        "expected_sha256": "107945d4f83c5694496a596beac8609a8d72ef3ebce865ee02a581b0db1d435d",
        "read_mode": "json",
        "date_parser": "manifest_metadata",
    },
    {
        "relative_path": "validation_runs/task_055_f_20260716_v6/read_ledger/generations/read_ledger_2ded8f6958c3bddd512bccf7/read_ledger.jsonl",
        "dataset_role": "task055f_read_ledger_rows",
        "expected_sha256": "5684738e3124adb609b32ca272a433292a59d0420cfdd68098720f94e4d8d35e",
        "read_mode": "jsonl",
        "date_parser": "read_ledger_rows",
        "declared_max_date": MAX_DATE,
    },
    {
        "relative_path": "validation_runs/task_055_f_20260716_v6/final/generations/task055f_report_922e74e3aa26c9069956ece5/task055f_report.json",
        "dataset_role": "task055f_parent_report",
        "expected_sha256": "67b639a6b36846a79a3276706442665f88ec9c424bacbf3403ec099ce5e755c9",
        "read_mode": "json",
        "date_parser": "task055f_report",
        "declared_max_date": MAX_DATE,
    },
    {
        "relative_path": "validation_runs/task_055_e_offline_20260716/final/generations/offline_report_202d84acc3ad245ad2b7c0b2/task055e_offline_report.json",
        "dataset_role": "task055e_parent_report",
        "expected_sha256": "2187b6342e4048d74c4a9dfdaa72f5169ffceecca2573df33eb63f78cc297603",
        "read_mode": "json",
        "date_parser": "task055e_report",
        "declared_max_date": MAX_DATE,
    },
    {
        "relative_path": "validation_runs/task_055_e_offline_20260716/task055e_offline_config.json",
        "dataset_role": "task055e_parent_config",
        "expected_sha256": "fc3a9043283908bb2b10871324c346fdbeb1f381627f97d8cd0782b5d6a4354e",
        "read_mode": "json",
        "date_parser": "config_dates",
        "declared_max_date": MAX_DATE,
    },
    {
        "relative_path": "validation_runs/task_055_a_20260715/simulation_bundles/generations/simulation_bundle_0be5ee96c4fddbed4202f1f4/simulation_bundle_manifest.json",
        "dataset_role": "task055a_simulation_bundle_manifest",
        "expected_sha256": "ea61cc6b949822e066e670d94f457dc74a163c260c2ed65e9465dfca569566d7",
        "read_mode": "json",
        "date_parser": "simulation_bundle_manifest",
        "declared_max_date": SIMULATION_END,
    },
    {
        "relative_path": "validation_runs/task_055_a_20260715/simulator_replay_v3/policy_seal/generations/policy_seal_fe2c2712095b5fd13b7e853e/policy_seal.json",
        "dataset_role": "task055a_policy_seal",
        "expected_sha256": "0a38aa3d95703cbdc954d481df118f7b692a86b7604786c8a6422cf5b4fad1e9",
        "read_mode": "json",
        "date_parser": "policy_seal",
        "declared_max_date": SIMULATION_END,
    },
)
