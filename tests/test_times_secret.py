import re
from pathlib import Path


def test_times_py_has_no_hardcoded_tushare_token_assignment():
    source = Path("times.py").read_text(encoding="utf-8")

    assert re.search(r"os\.(getenv|environ\.get)\([\"']TUSHARE_TOKEN[\"']\)", source)
    assert not re.search(
        r"(?:TS_TOKEN|TUSHARE_TOKEN)\s*=\s*[\"'][A-Za-z0-9]{32,}[\"']",
        source,
    )
