"""Read-only access to the HTML reports in REPORT_DIR (used by the API)."""
import re
from pathlib import Path

REPORT_NAME = re.compile(r"^[A-Za-z0-9._-]+-(\d+)\.html$")
DECISION = re.compile(r'class="decision decision-(promoted|aborted)"')


def decision_of(path: Path) -> str:
    try:
        m = DECISION.search(path.read_text(encoding="utf-8"))
    except OSError:
        return "UNKNOWN"
    return m.group(1).upper() if m else "UNKNOWN"


def list_reports(report_dir: Path) -> list[dict]:
    """Newest first: [{file, timestamp, decision}]."""
    if not report_dir.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in report_dir.iterdir() if (m := REPORT_NAME.match(p.name))]
    return [
        {"file": p.name, "timestamp": ts, "decision": decision_of(p)}
        for ts, p in sorted(found, key=lambda x: x[0], reverse=True)
    ]


def read_report(report_dir: Path, name: str) -> str | None:
    """Return the report text, or None unless `name` is a plain report filename that exists."""
    if not REPORT_NAME.match(name):  # rejects slashes, '..', other extensions
        return None
    f = report_dir / name
    return f.read_text(encoding="utf-8") if f.is_file() else None
