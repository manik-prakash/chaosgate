"""ReportGenerator: writes a self-contained HTML report for every gate run."""
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from chaos_engine import ExperimentResult
from config import Config

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass
class GateReport:
    rollout: str
    namespace: str
    decision: str  # PROMOTED | ABORTED
    reason: str
    experiments: list[ExperimentResult]
    slo_service: str
    max_burn: float
    threshold: float
    slo_ok: bool
    slo_no_data: bool = False


class ReportGenerator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html", "j2"])
        )

    def generate(self, report: GateReport) -> Path:
        html = self.env.get_template("report.html.j2").render(
            r=report,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            grafana_url=self.cfg.grafana_dashboard_url,
        )
        out_dir = Path(self.cfg.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report.rollout}-{int(time.time())}.html"
        path.write_text(html, encoding="utf-8")
        return path
