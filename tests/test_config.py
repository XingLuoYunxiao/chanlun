from chanlun.config import PROJECT_ROOT, load_config


def test_default_config_has_port_8888():
    cfg = load_config()
    assert cfg.web.port == 8888
    assert cfg.web.host == "127.0.0.1"
    assert cfg.bs_adjust == "2"
    assert cfg.periods == ["day", "30", "5"]


def test_config_override_by_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('periods = ["day"]\n\n[web]\nport = 9999\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.web.port == 9999
    assert cfg.periods == ["day"]


def test_data_root_is_absolute_and_resolved_against_project_root():
    cfg = load_config()
    assert cfg.data.root.is_absolute()
    assert cfg.data.root == PROJECT_ROOT / "data"
    assert cfg.data.meta_db.name == "meta.db"


def test_missing_config_falls_back_to_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.web.port == 8888
