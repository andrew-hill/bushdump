import pytest

from bushdump import config


def _write(path, text):
    path.write_text(text)
    return path


def test_load_config_two_cameras_with_defaults(tmp_path):
    cfg_path = _write(
        tmp_path / "config.toml",
        'output_dir = "~/Pictures/BushDump"\n'
        'password = "defaultpw"\n'
        'camera_host = "192.168.1.8"\n'
        "\n"
        "[cameras.frontgate]\n"
        'ble_address = "AAA-111"\n'
        'ssid = "Trail Cam Pro 1234"\n'
        "\n"
        "[cameras.backwoods]\n"
        'ble_address = "BBB-222"\n'
        'ssid = "Trail Cam Pro 5678"\n'
        'password = "secret"\n',
    )
    cfg = config.load_config(cfg_path)
    assert set(cfg.cameras) == {"frontgate", "backwoods"}

    front = cfg.cameras["frontgate"]
    assert front.ssid == "Trail Cam Pro 1234"
    assert front.ble_address == "AAA-111"
    assert front.password == "defaultpw"  # inherits top-level default
    assert front.camera_host == "192.168.1.8"
    # default output dir is <base>/<name>, expanded
    assert front.output_dir.name == "frontgate"
    assert "~" not in str(front.output_dir)

    assert cfg.cameras["backwoods"].password == "secret"  # per-camera override


def test_load_config_per_camera_output_override(tmp_path):
    cfg_path = _write(
        tmp_path / "config.toml",
        '[cameras.cam]\nssid = "S"\noutput_dir = "~/somewhere/else"\n',
    )
    cam = config.load_config(cfg_path).cameras["cam"]
    assert cam.output_dir.name == "else"
    assert "~" not in str(cam.output_dir)


def test_load_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load_config(tmp_path / "absent.toml")


def test_add_camera_appends_and_round_trips(tmp_path):
    cfg_path = tmp_path / "config.toml"
    config.write_config_template(cfg_path)
    config.add_camera("frontgate", ble_address="AAA-111", ssid="Trail Cam Pro 1234", path=cfg_path)

    cam = config.load_config(cfg_path).cameras["frontgate"]
    assert cam.ble_address == "AAA-111"
    assert cam.ssid == "Trail Cam Pro 1234"


def test_add_camera_duplicate_raises(tmp_path):
    cfg_path = tmp_path / "config.toml"
    config.add_camera("cam", ble_address="A", ssid="S", path=cfg_path)
    with pytest.raises(ValueError):
        config.add_camera("cam", ble_address="B", ssid="S2", path=cfg_path)


def test_add_camera_escapes_quotes_in_ssid(tmp_path):
    cfg_path = tmp_path / "config.toml"
    tricky = 'Trail "Cam" \\ Pro'
    config.add_camera("cam", ble_address="A", ssid=tricky, path=cfg_path)
    assert config.load_config(cfg_path).cameras["cam"].ssid == tricky


def test_camera_exists(tmp_path):
    cfg_path = tmp_path / "config.toml"
    assert config.camera_exists("cam", cfg_path) is False  # no file yet
    config.add_camera("cam", ble_address="A", ssid="S", path=cfg_path)
    assert config.camera_exists("cam", cfg_path) is True
    assert config.camera_exists("other", cfg_path) is False


def test_write_config_template_creates_then_is_noop(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    assert config.write_config_template(path) is True
    assert path.exists()
    assert config.write_config_template(path) is False


def test_state_round_trip_nested(tmp_path):
    path = tmp_path / "state.json"
    state = {
        "frontgate": {"Photo": "2026-05-10 13:00:01", "Video": "2026-05-10 14:00:00"},
        "backwoods": {"Photo": "2026-04-01 08:30:00"},
    }
    config.save_state(state, path)
    assert config.load_state(path) == state


def test_load_state_missing_returns_empty(tmp_path):
    assert config.load_state(tmp_path / "nope.json") == {}


def test_backups_round_trip(tmp_path):
    path = tmp_path / "backups.json"
    backups = {
        "east": {"Photo": "2026-05-31 18:00:01", "Video": "2026-05-30 12:00:00"},
        "west": {"Photo": "2026-04-01 08:30:00"},
    }
    config.save_backups(backups, path)
    assert config.load_backups(path) == backups


def test_load_backups_missing_returns_empty(tmp_path):
    assert config.load_backups(tmp_path / "nope.json") == {}


def test_backup_section_target_and_args(tmp_path):
    cfg_path = _write(
        tmp_path / "config.toml",
        '[cameras.east]\nssid = "CAM8Z8_ABC"\n'
        "\n"
        '[backup]\ntarget = "nas:/backup/"\nargs = ["--chown=andrew:users"]\n'
        'rsync_bin = "/opt/homebrew/bin/rsync"\n',
    )
    cfg = config.load_config(cfg_path)
    assert cfg.backup.target == "nas:/backup/"
    assert cfg.backup.args == ["--chown=andrew:users"]
    assert cfg.backup.rsync_bin == "/opt/homebrew/bin/rsync"


def test_backup_section_absent_gives_defaults(tmp_path):
    cfg_path = _write(
        tmp_path / "config.toml",
        '[cameras.east]\nssid = "CAM8Z8_ABC"\n',
    )
    cfg = config.load_config(cfg_path)
    assert cfg.backup.target is None
    assert cfg.backup.args == []
    assert cfg.backup.rsync_bin == "rsync"
