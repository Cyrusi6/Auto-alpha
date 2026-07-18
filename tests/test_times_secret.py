import re
from pathlib import Path


def test_times_py_is_superseded_before_token_lookup_or_transport():
    source = Path("times.py").read_text(encoding="utf-8")

    assert "superseded_by_task055j" in source
    assert not re.search(r"os\.(getenv|environ\.get)\([\"']TUSHARE_TOKEN[\"']\)", source)
    assert not re.search(
        r"(?:TS_TOKEN|TUSHARE_TOKEN)\s*=\s*[\"'][A-Za-z0-9]{32,}[\"']",
        source,
    )
