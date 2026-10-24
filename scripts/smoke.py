"""Accept consistent coverage and reject inflated or below-threshold reports."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for name, threshold, expected in [
    ("valid.xml", "0.6", 0),
    ("inflated.xml", "0.6", 1),
    ("valid.xml", "0.8", 1),
]:
    source = root / "examples" / name
    digest = hashlib.sha256(source.read_bytes()).digest()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "cobertura_audit",
            str(source),
            "--min-line-rate",
            threshold,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected, result.stderr
    report = json.loads(result.stdout)
    assert report["computed"]["lines_covered"] == 2
    assert report["computed"]["lines_valid"] == 3
    assert report["passed"] == (expected == 0)
    assert hashlib.sha256(source.read_bytes()).digest() == digest
print("Consistent report accepted; inflated summary and threshold failure rejected")
