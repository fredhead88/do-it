import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_nh", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_projects_unresolved_needs_human_from_store(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    nh = sl.LEDGER_DIR / "needs-human"
    nh.mkdir(parents=True, exist_ok=True)
    (nh / "200-x.yml").write_text(
        "spec_id: 200-x\nreason: TASTE\nnote: color looks off\nresolved: false\n"
    )
    (nh / "201-y.yml").write_text(
        "spec_id: 201-y\nreason: stale\nnote: done\nresolved: true\n"
    )
    body = sl.render(sl.load_records(), include_all=False)
    assert "NEEDS-HUMAN" in body
    assert "200-x" in body and "color looks off" in body
    assert "201-y" not in body  # resolved -> dropped


def test_render_surfaces_liveness_flags(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    fl = sl.LEDGER_DIR / "liveness"
    fl.mkdir(parents=True, exist_ok=True)
    (fl / "VERIFIER_DOWN").write_text("2026-06-08T00:00:00Z PROGRESS.jsonl stale 200m")
    body = sl.render(sl.load_records(), include_all=False)
    assert "VERIFIER_DOWN" in body and "🚨" in body
