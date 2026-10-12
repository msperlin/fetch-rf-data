from rfbpy import cli


def _write_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[dirs]\nprocessing_dir="{tmp}"\n'
        '[urls]\nbase_url="http://x/"\nmonth="latest"\n'
        '[arquivos]\nfiles_to_download=["Cnaes.zip"]\n'.format(tmp=tmp_path)
    )
    return path


def test_missing_config_returns_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check"]) == 1


def test_months(tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path)
    monkeypatch.setattr(
        "rfbpy.webdav.list_months", lambda *a, **k: ["2026-08", "2026-09"]
    )
    assert cli.main(["--config", str(config), "months"]) == 0
    out = capsys.readouterr().out
    assert "2026-08" in out
    assert "2026-09" in out


def test_check(tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path)
    monkeypatch.setattr(
        "rfbpy.webdav.list_files",
        lambda *a, **k: [
            {"name": "Cnaes.zip", "url": "http://x/Cnaes.zip", "size": 10,
             "etag": "e1", "last_modified": None}
        ],
    )
    assert cli.main(["--config", str(config), "check"]) == 0
    out = capsys.readouterr().out
    assert "To download   : 1" in out


def test_download_dry_run(tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path)
    monkeypatch.setattr(
        "rfbpy.webdav.list_files",
        lambda *a, **k: [
            {"name": "Cnaes.zip", "url": "http://x/Cnaes.zip", "size": 10,
             "etag": "e1", "last_modified": None}
        ],
    )
    assert cli.main(["--config", str(config), "download-and-unzip", "--dry-run"]) == 0
    assert "Cnaes.zip" in capsys.readouterr().out


def test_download_alias(tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path)
    monkeypatch.setattr(
        "rfbpy.webdav.list_files",
        lambda *a, **k: [
            {"name": "Cnaes.zip", "url": "http://x/Cnaes.zip", "size": 10,
             "etag": "e1", "last_modified": None}
        ],
    )
    # the legacy "download" name is kept as an alias
    assert cli.main(["--config", str(config), "download", "--dry-run"]) == 0
    assert "Cnaes.zip" in capsys.readouterr().out
