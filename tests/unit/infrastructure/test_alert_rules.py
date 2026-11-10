"""obs/alerts.yml + obs/alertmanager.yml (#342): the files parse, every
condition the ticket names has a rule, every metric a rule reads exists
(engines: src/shared/observability.py; api: health backup gauge; cAdvisor),
and Prometheus points at them.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
ALERTS = yaml.safe_load((ROOT / "obs" / "alerts.yml").read_text(encoding="utf-8"))
AM = yaml.safe_load((ROOT / "obs" / "alertmanager.yml").read_text(encoding="utf-8"))
PROM = yaml.safe_load((ROOT / "obs" / "prometheus.yml").read_text(encoding="utf-8"))

KNOWN_METRICS = {
    # engines (src/shared/observability.py)
    "engine_last_cycle_timestamp_seconds", "ib_session_connected", "engine_cycles_total",
    "history_prune_failures_total", "db_writer_queue_dropped_total", "reconciliation_breaks_open", "db_writer_dead_letter_total",
    "order_submit_latency_seconds", "fill_to_publish_seconds",
    # api (src/api/routers/health.py)
    "fxvol_postgres_backup_age_seconds",
    # prometheus / cadvisor
    "up", "container_start_time_seconds", "container_fs_usage_bytes", "container_fs_limit_bytes",
    "container_memory_working_set_bytes", "container_spec_memory_limit_bytes",
}


def _rules() -> list[dict]:
    return [r for g in ALERTS["groups"] for r in g["rules"]]


def test_every_condition_the_ticket_names_has_a_rule() -> None:
    names = {r["alert"] for r in _rules()}
    for required in ("ContainerCrashLoop", "DiskUsageHigh", "IBSessionDown", "DbWriterLag", "BackupStale",
                     "EngineDown", "EngineCycleStalled", "ApiDown"):
        assert required in names, required


def test_rules_only_reference_metrics_that_exist_and_carry_severity_and_text() -> None:
    for r in _rules():
        expr = r["expr"]
        metrics = set(re.findall(r"\b([a-z_]+_(?:seconds|bytes|total|connected|time_seconds))\b|\b(up)\{", expr))
        found = {m[0] or m[1] for m in metrics}
        unknown = found - KNOWN_METRICS
        assert not unknown, (r["alert"], unknown)
        assert r["labels"]["severity"] in ("critical", "warning"), r["alert"]
        assert r["annotations"]["summary"] and r["annotations"]["description"], r["alert"]


def test_engine_labels_in_rules_match_the_code() -> None:
    src = (ROOT / "src").rglob("*.py")
    labels: set[str] = set()
    for p in src:
        labels |= set(re.findall(r'observed_cycle\("([a-z_]+)"', p.read_text(encoding="utf-8", errors="replace")))
    stalled = next(r for r in _rules() if r["alert"] == "EngineCycleStalled")["expr"]
    for lbl in re.findall(r'engine=~"([a-z_|]+)"', stalled):
        for e in lbl.split("|"):
            assert e in labels, e


def test_prometheus_loads_the_rules_and_talks_to_alertmanager() -> None:
    assert "/etc/prometheus/alerts.yml" in PROM["rule_files"]
    targets = PROM["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
    assert "alertmanager:9093" in targets


def test_alertmanager_routes_to_the_sns_topic_with_resolve() -> None:
    recv = AM["receivers"][0]
    assert recv["name"] == AM["route"]["receiver"]
    sns = recv["sns_configs"][0]
    assert sns["topic_arn"].endswith(":fxvol-alarms") and sns["sigv4"]["region"] == "eu-west-1"
    assert sns["send_resolved"] is True
