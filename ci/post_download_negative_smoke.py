"""Expected-blocked real-path smoke for local CI."""
from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from artifact_schema.writer import write_json_artifact
from post_download_orchestrator.run_post_download import main as post_download_main


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--output-dir",required=True); args=parser.parse_args(argv)
    root=Path(tempfile.mkdtemp(prefix="auto_alpha_ci_real_path_")); data=root/"governed"; data.mkdir(parents=True)
    os.environ["ASHARE_REAL_DATA_ROOT_PREFIX"]=str(root)
    readiness=root/"readiness.json"
    write_json_artifact(readiness,{"status":"raw_ready_for_freeze","decision":{"status":"raw_ready_for_freeze","core_ready":True,"can_create_freeze":True,"can_build_matrix":True,"required_remediations":[]},"summary":{"failed_job_count":0,"quarantined_job_count":0}},"research_data_readiness_report","ci")
    output=Path(args.output_dir)
    captured=io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        rc=post_download_main(["run","--data-dir",str(data),"--output-dir",str(output),"--readiness-report-path",str(readiness),"--execute"])
    reports=list(output.glob("post_download_step_runs.jsonl")); text=reports[0].read_text() if reports else ""
    passed=rc==1 and "real data mutation steps require --allow-real-data-path" in text
    result={"status":"expected_blocked" if passed else "failed","credential_present":False,"source_type":"none"}
    output.mkdir(parents=True,exist_ok=True)
    (output/"post_download_real_path_negative_smoke.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result))
    return 0 if passed else 1

if __name__=="__main__": raise SystemExit(main())
