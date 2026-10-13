"""Centralised configuration. The only module that reads environment variables."""
import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Config:
    namespace: str = "default"
    chaos_gate_step_index: int = 1
    prometheus_url: str = "http://prometheus-operated:9090"
    slo_service_name: str = "backend-api"
    error_budget_threshold: float = 0.02
    chaos_duration_seconds: int = 180
    poll_interval_seconds: float = 10
    chaos_app_label: str = "app=backend"
    chaos_app_namespace: str = "default"
    chaos_service_account: str = "litmus-admin"
    chaos_result_timeout_seconds: int = 300
    chaos_result_poll_seconds: float = 10
    report_dir: str = "/reports"
    grafana_dashboard_url: str = ""
    max_concurrent_gates: int = 3
    http_port: int = 8080  # dashboard + API; -1 disables
    demo_mode: bool = False  # enables /api/demo/* endpoints
    experiment_duration_seconds: int = 60
    experiments: str = "pod-delete,pod-cpu-hog-exec,pod-network-latency"  # names from chaos_engine.CATALOG
    chaos_app_port: int = 8080  # container port, used by pod-http-latency
    chaos_probe_url: str = ""  # if set, every experiment fails when this URL stops returning 200 during chaos
    static_dir: str = ""  # built React bundle; empty = <orchestrator>/static

    @classmethod
    def from_env(cls) -> "Config":
        d = cls()
        return cls(
            namespace=_env("NAMESPACE", d.namespace),
            chaos_gate_step_index=int(_env("CHAOS_GATE_STEP_INDEX", str(d.chaos_gate_step_index))),
            prometheus_url=_env("PROMETHEUS_URL", d.prometheus_url),
            slo_service_name=_env("SLO_SERVICE_NAME", d.slo_service_name),
            error_budget_threshold=float(_env("ERROR_BUDGET_THRESHOLD", str(d.error_budget_threshold))),
            chaos_duration_seconds=int(_env("CHAOS_DURATION_SECONDS", str(d.chaos_duration_seconds))),
            poll_interval_seconds=float(_env("POLL_INTERVAL_SECONDS", str(d.poll_interval_seconds))),
            chaos_app_label=_env("CHAOS_APP_LABEL", d.chaos_app_label),
            chaos_app_namespace=_env("CHAOS_APP_NAMESPACE", d.chaos_app_namespace),
            chaos_service_account=_env("CHAOS_SERVICE_ACCOUNT", d.chaos_service_account),
            chaos_result_timeout_seconds=int(
                _env("CHAOS_RESULT_TIMEOUT_SECONDS", str(d.chaos_result_timeout_seconds))
            ),
            report_dir=_env("REPORT_DIR", d.report_dir),
            grafana_dashboard_url=_env("GRAFANA_DASHBOARD_URL", d.grafana_dashboard_url),
            max_concurrent_gates=int(_env("MAX_CONCURRENT_GATES", str(d.max_concurrent_gates))),
            http_port=int(_env("HTTP_PORT", _env("VIEWER_PORT", str(d.http_port)))),
            demo_mode=_env("DEMO_MODE", "false").lower() in ("1", "true", "yes"),
            experiment_duration_seconds=int(
                _env("EXPERIMENT_DURATION_SECONDS", str(d.experiment_duration_seconds))
            ),
            static_dir=_env("STATIC_DIR", d.static_dir),
            experiments=_env("EXPERIMENTS", d.experiments),
            chaos_app_port=int(_env("CHAOS_APP_PORT", str(d.chaos_app_port))),
            chaos_probe_url=_env("CHAOS_PROBE_URL", d.chaos_probe_url),
        )
