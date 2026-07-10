"""The CLI: parsing, defaults, provider rejection, and error-to-exit-code mapping — no network."""

from pathlib import Path

import pytest

from osrforge import cli
from osrforge.errors import OsrForgeError


class TestParsing:
    def test_convert_defaults(self):
        args = cli.build_parser().parse_args(["convert", "some/module.pdf"])
        assert args.pdf == Path("some/module.pdf")
        assert args.workdir is None
        assert args.provider == "foundry"

    def test_assemble_and_preview_default_to_the_current_directory(self):
        assert cli.build_parser().parse_args(["assemble"]).workdir == Path(".")
        assert cli.build_parser().parse_args(["preview"]).workdir == Path(".")

    def test_unknown_provider_rejected(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit) as excinfo:
            cli.build_parser().parse_args(["convert", "m.pdf", "--provider", "anthropic"])
        assert excinfo.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_unknown_subcommand_rejected(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["estimate", "m.pdf"])


class TestErrorMapping:
    def test_missing_foundry_env_is_a_one_line_exit(self, monkeypatch: pytest.MonkeyPatch):
        for name in ("OSRFORGE_FOUNDRY_ENDPOINT", "OSRFORGE_FOUNDRY_DEPLOYMENT", "OSRFORGE_FOUNDRY_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", "m.pdf"])
        assert "OSRFORGE_FOUNDRY_ENDPOINT" in str(excinfo.value)

    def test_convert_workdir_defaults_to_pdf_stem(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        seen: dict[str, object] = {}

        def fake_build_provider(name: str) -> object:
            return object()

        def fake_convert(pdf_path, workdir, provider, settings=None, on_progress=None):
            seen["workdir"] = workdir
            raise OsrForgeError("stubbed")

        monkeypatch.setattr(cli, "_build_provider", fake_build_provider)
        monkeypatch.setattr(cli, "convert", fake_convert)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(tmp_path / "barrow.pdf")])
        assert seen["workdir"] == Path("barrow.forge")
        assert str(excinfo.value) == "osrforge: stubbed"

    def test_runtime_failure_exits_one_line(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cli, "assemble", lambda workdir: (_ for _ in ()).throw(OsrForgeError("boom")))
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["assemble", "--workdir", "nowhere.forge"])
        assert str(excinfo.value) == "osrforge: boom"

    def test_malformed_workdir_exits_one_line(self, tmp_path: Path):
        # A directory with no run.json is a user mistake, not a traceback.
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["assemble", "--workdir", str(tmp_path)])
        assert str(excinfo.value).startswith("osrforge: ")

    def test_misuse_valueerror_exits_one_line(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cli, "render_previews", lambda workdir: (_ for _ in ()).throw(ValueError("no cache")))
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["preview"])
        assert str(excinfo.value) == "osrforge: no cache"
