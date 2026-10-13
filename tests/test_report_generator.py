import re
from html.parser import HTMLParser

from chaos_engine import ExperimentResult
from report_generator import GateReport, ReportGenerator


def make_report(decision, **kw):
    base = dict(
        rollout="web",
        namespace="default",
        decision=decision,
        reason="all chaos experiments passed",
        experiments=[
            ExperimentResult("pod-delete", 60, "Pass"),
            ExperimentResult("pod-cpu-hog-exec", 60, "Fail" if decision == "ABORTED" else "Pass"),
        ],
        slo_service="backend-api",
        max_burn=0.034,
        threshold=0.02,
        slo_ok=decision == "PROMOTED",
        slo_no_data=False,
    )
    base.update(kw)
    return GateReport(**base)


class Validator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []
        self.void = {"meta", "br", "link", "hr", "img", "input"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.void:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(tag)


def assert_valid(html):
    v = Validator()
    v.feed(html)
    assert not v.errors and not v.stack
    assert html.lstrip().lower().startswith("<!doctype html")


def test_promoted_report(cfg, tmp_path):
    import dataclasses

    cfg = dataclasses.replace(cfg, report_dir=str(tmp_path))
    path = ReportGenerator(cfg).generate(make_report("PROMOTED"))
    html = open(path, encoding="utf-8").read()
    assert re.fullmatch(r"web-\d+\.html", path.name)
    assert_valid(html)
    for s in ("web", "default", "PROMOTED", "pod-delete", "pod-cpu-hog-exec", "backend-api", "0.034", "0.02", "Z"):
        assert s in html
    assert 'class="decision decision-promoted"' in html


def test_aborted_report_shows_reason_and_no_grafana(cfg, tmp_path):
    import dataclasses

    cfg = dataclasses.replace(cfg, report_dir=str(tmp_path), grafana_dashboard_url="")
    r = make_report("ABORTED", reason="pod-cpu-hog-exec returned Fail")
    path = ReportGenerator(cfg).generate(r)
    html = path.read_text(encoding="utf-8")
    assert_valid(html)
    assert "ABORTED" in html and 'class="decision decision-aborted"' in html
    assert "pod-cpu-hog-exec returned Fail" in html
    assert "Grafana" not in html


def test_grafana_link_included_when_set(cfg, tmp_path):
    import dataclasses

    cfg = dataclasses.replace(cfg, report_dir=str(tmp_path), grafana_dashboard_url="http://g/d/1")
    html = ReportGenerator(cfg).generate(make_report("PROMOTED")).read_text(encoding="utf-8")
    assert 'href="http://g/d/1"' in html


def test_html_is_escaped(cfg, tmp_path):
    import dataclasses

    cfg = dataclasses.replace(cfg, report_dir=str(tmp_path))
    html = ReportGenerator(cfg).generate(make_report("ABORTED", reason="<script>x</script>")).read_text(encoding="utf-8")
    assert "<script>x</script>" not in html


def test_no_slo_data_is_flagged(cfg, tmp_path):
    import dataclasses

    cfg = dataclasses.replace(cfg, report_dir=str(tmp_path))
    html = ReportGenerator(cfg).generate(make_report("PROMOTED", slo_no_data=True, max_burn=0.0)).read_text(encoding="utf-8")
    assert "No SLO data" in html
