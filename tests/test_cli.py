"""The CLI: parsing, defaults, provider rejection, exit codes, and the overrides template — no network."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from osrforge import cli
from osrforge.contracts.overrides import Overrides, load_overrides
from osrforge.contracts.report import (
    ExtractionReport,
    LintCheck,
    LintFinding,
    ModuleInfo,
    MonsterSummary,
    ValidationResult,
)
from osrforge.contracts.run import RunMeta, Stage, StageStatus, TokenUsage
from osrforge.errors import OsrForgeError
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact


class TestParsing:
    def test_convert_defaults(self):
        args = cli.build_parser().parse_args(["convert", "some/module.pdf"])
        assert args.pdf == Path("some/module.pdf")
        assert args.workdir is None
        assert args.provider == "foundry"
        assert args.set is None

    def test_assemble_check_and_preview_default_to_discovery(self):
        # None means the handler discovers the workdir; an explicit flag bypasses discovery.
        assert cli.build_parser().parse_args(["assemble"]).workdir is None
        assert cli.build_parser().parse_args(["preview"]).workdir is None
        assert cli.build_parser().parse_args(["check"]).workdir is None

    def test_unknown_provider_rejected(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit) as excinfo:
            cli.build_parser().parse_args(["convert", "m.pdf", "--provider", "anthropic"])
        assert excinfo.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_unknown_subcommand_rejected(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["evaluate", "m.pdf"])

    def test_rerun_stage_choices_are_the_runnable_stages(self, capsys: pytest.CaptureFixture[str]):
        args = cli.build_parser().parse_args(["rerun", "assemble"])
        assert args.stage == "assemble"
        assert args.workdir is None
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["rerun", "geometry"])
        assert "invalid choice" in capsys.readouterr().err

    def test_set_is_repeatable_on_convert_and_rerun(self):
        args = cli.build_parser().parse_args(
            ["rerun", "preprocess", "--set", "blank_page_renders=[21]", "--set", "render_dpi=100"]
        )
        assert args.set == ["blank_page_renders=[21]", "render_dpi=100"]
        args = cli.build_parser().parse_args(["convert", "m.pdf", "--set", "unresolved_fallback=omit"])
        assert args.set == ["unresolved_fallback=omit"]

    def test_parse_set_coerces_values_through_yaml(self):
        updates = cli.parse_set_values(
            ["max_pages=21", "blank_page_renders=[21, 30]", "unresolved_fallback=best-effort"]
        )
        assert updates == {"max_pages": 21, "blank_page_renders": [21, 30], "unresolved_fallback": "best-effort"}

    def test_parse_set_rejects_missing_equals(self):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            cli.parse_set_values(["render_dpi"])

    def test_version_prints_and_exits_zero(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert capsys.readouterr().out.startswith("osrforge ")


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
            cli.main(["preview", "--workdir", "mod.forge"])
        assert str(excinfo.value) == "osrforge: no cache"

    def test_broken_overrides_yaml_exits_one_line(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            cli, "assemble", lambda workdir: (_ for _ in ()).throw(yaml.YAMLError("bad syntax on line 3"))
        )
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["assemble", "--workdir", "mod.forge"])
        assert str(excinfo.value) == "osrforge: bad syntax on line 3"


def fake_result(passed: bool = True, errors: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(report=SimpleNamespace(validation=SimpleNamespace(passed=passed, errors=errors)))


class TestConvertTemplate:
    def run_convert(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        workdir = tmp_path / "mod.forge"
        workdir.mkdir(exist_ok=True)
        monkeypatch.setattr(cli, "_build_provider", lambda name: object())
        monkeypatch.setattr(cli, "convert", lambda *args, **kwargs: fake_result())
        cli.main(["convert", str(tmp_path / "mod.pdf"), "--workdir", str(workdir)])
        return workdir / "overrides.yaml"

    def test_convert_writes_the_template_once(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        template_path = self.run_convert(monkeypatch, tmp_path)
        assert template_path.read_text(encoding="utf-8") == cli.OVERRIDES_TEMPLATE
        # Comments-only: it loads as the empty overrides set.
        assert load_overrides(template_path) == Overrides()

    def test_convert_never_touches_an_existing_overrides_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        workdir = tmp_path / "mod.forge"
        workdir.mkdir()
        existing = "monsters: {}\n"
        (workdir / "overrides.yaml").write_text(existing, encoding="utf-8")
        template_path = self.run_convert(monkeypatch, tmp_path)
        assert template_path.read_text(encoding="utf-8") == existing


def write_report(root: Path, passed: bool) -> None:
    workdir = Workdir(root)
    workdir.root.mkdir(parents=True, exist_ok=True)
    report = ExtractionReport(
        module=ModuleInfo(title="T", pages=1),
        validation=ValidationResult(passed=passed, errors=() if passed else ("dangling id",)),
        monsters=MonsterSummary(resolved=0),
        usage=TokenUsage(),
    )
    write_json_artifact(workdir.report_json, report)


def finding(severity: str) -> LintFinding:
    return LintFinding(
        id=LintCheck.EDGE_INVALID if severity == "error" else LintCheck.SECRET_ONLY_ACCESS,
        severity=severity,  # pyright: ignore[reportArgumentType]
        location="lair/1",
        message="synthetic finding",
    )


class TestCheckExitCodes:
    def run_check(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        findings: tuple[LintFinding, ...],
        passed: bool,
    ) -> tuple[int | str | None, str]:
        root = tmp_path / "mod.forge"
        write_report(root, passed)
        monkeypatch.setattr(cli, "check", lambda workdir: findings)
        code: int | str | None = 0
        try:
            cli.main(["check", "--workdir", str(root)])
        except SystemExit as excinfo:
            code = excinfo.code
        return code, ""

    def test_clean_check_exits_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        code, _ = self.run_check(monkeypatch, tmp_path, (), passed=True)
        assert code == 0

    def test_warnings_only_exits_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        code, _ = self.run_check(monkeypatch, tmp_path, (finding("warning"),), passed=True)
        assert code == 0

    def test_error_finding_exits_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        code, _ = self.run_check(monkeypatch, tmp_path, (finding("warning"), finding("error")), passed=True)
        assert code == 1

    def test_failed_validation_exits_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        code, _ = self.run_check(monkeypatch, tmp_path, (), passed=False)
        assert code == 1

    def test_check_prints_one_line_per_finding(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        self.run_check(monkeypatch, tmp_path, (finding("warning"),), passed=True)
        out = capsys.readouterr().out
        assert "validation: passed" in out
        assert "warning secret_only_access lair/1 synthetic finding" in out


class TestRerunCommand:
    def test_rerun_assemble_builds_no_provider(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        seen: dict[str, object] = {}

        def fake_rerun(workdir, stage, provider=None, settings_updates=None, on_progress=None):
            seen.update(stage=stage, provider=provider, settings_updates=settings_updates)
            return fake_result()

        monkeypatch.setattr(cli, "rerun", fake_rerun)
        monkeypatch.setattr(cli, "_build_provider", lambda name: pytest.fail("provider built for assemble"))
        cli.main(["rerun", "assemble", "--workdir", str(tmp_path), "--set", "unresolved_fallback=omit"])
        assert seen["stage"] is Stage.ASSEMBLE
        assert seen["provider"] is None
        assert seen["settings_updates"] == {"unresolved_fallback": "omit"}

    def test_rerun_model_stage_builds_the_provider(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        sentinel = object()
        seen: dict[str, object] = {}

        def fake_rerun(workdir, stage, provider=None, settings_updates=None, on_progress=None):
            seen.update(stage=stage, provider=provider)
            return fake_result()

        monkeypatch.setattr(cli, "rerun", fake_rerun)
        monkeypatch.setattr(cli, "_build_provider", lambda name: sentinel)
        cli.main(["rerun", "survey", "--workdir", str(tmp_path)])
        assert seen["stage"] is Stage.SURVEY
        assert seen["provider"] is sentinel


class TestWorkdirDiscovery:
    def stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        seen: dict[str, object] = {}
        monkeypatch.setattr(cli, "render_previews", lambda workdir: seen.update(workdir=workdir) or [])
        return seen

    def test_explicit_workdir_bypasses_discovery(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # Two *.forge directories would be ambiguous; the explicit flag never looks.
        (tmp_path / "a.forge").mkdir()
        (tmp_path / "b.forge").mkdir()
        monkeypatch.chdir(tmp_path)
        seen = self.stub_preview(monkeypatch)
        cli.main(["preview", "--workdir", "b.forge"])
        assert seen["workdir"] == Path("b.forge")

    def test_the_working_directory_wins_when_it_is_a_workdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "run.json").write_text("{}", encoding="utf-8")
        (tmp_path / "other.forge").mkdir()  # run.json outranks any *.forge sibling
        monkeypatch.chdir(tmp_path)
        seen = self.stub_preview(monkeypatch)
        cli.main(["preview"])
        assert seen["workdir"] == Path(".")

    def test_the_unique_forge_directory_is_discovered(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "mod.forge").mkdir()
        monkeypatch.chdir(tmp_path)
        seen = self.stub_preview(monkeypatch)
        cli.main(["preview"])
        assert seen["workdir"] == Path("mod.forge")

    def test_a_forge_file_is_not_a_workdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "notes.forge").write_text("not a directory", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["preview"])
        assert "contains no *.forge directory" in str(excinfo.value)

    def test_multiple_forge_directories_are_a_loud_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "a.forge").mkdir()
        (tmp_path / "b.forge").mkdir()
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["preview"])
        message = str(excinfo.value)
        assert "multiple workdirs" in message
        assert "a.forge, b.forge" in message
        assert "pass --workdir" in message

    def test_absence_is_a_loud_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["preview"])
        message = str(excinfo.value)
        assert "not a workdir (no run.json)" in message
        assert "contains no *.forge directory" in message
        assert "pass --workdir" in message

    def test_assemble_and_rerun_share_discovery(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "mod.forge").mkdir()
        monkeypatch.chdir(tmp_path)
        seen: dict[str, object] = {}
        monkeypatch.setattr(cli, "assemble", lambda workdir: seen.update(assemble=workdir) or fake_result())

        def fake_rerun(workdir, stage, provider=None, settings_updates=None, on_progress=None):
            seen["rerun"] = workdir
            return fake_result()

        monkeypatch.setattr(cli, "rerun", fake_rerun)
        cli.main(["assemble"])
        cli.main(["rerun", "assemble"])
        assert seen["assemble"] == Path("mod.forge")
        assert seen["rerun"] == Path("mod.forge")

    def test_check_shares_discovery(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        write_report(tmp_path / "mod.forge", passed=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli, "check", lambda workdir: ())
        cli.main(["check"])  # exit 0: discovery found mod.forge and its passing report


def write_failed_run(root: Path, failed: Stage) -> None:
    stages = {stage: StageStatus() for stage in Stage}
    stages[failed] = StageStatus(status="failed", error="boom")
    run = RunMeta(source_sha256="00" * 32, source_bytes=1, page_count=1, settings=ConversionSettings(), stages=stages)
    root.mkdir(parents=True, exist_ok=True)
    Workdir(root).write_run(run)


class TestResumeHint:
    def test_convert_failure_prints_the_resume_command(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        workdir = tmp_path / "mod.forge"

        def fake_convert(pdf_path, wd, provider, settings=None, on_progress=None):
            write_failed_run(workdir, Stage.SURVEY)
            raise OsrForgeError("survey exploded")

        monkeypatch.setattr(cli, "_build_provider", lambda name: object())
        monkeypatch.setattr(cli, "convert", fake_convert)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(tmp_path / "mod.pdf"), "--workdir", str(workdir)])
        assert str(excinfo.value) == (
            f"osrforge: survey exploded\nresume with: osrforge rerun survey --workdir {workdir}"
        )

    def test_rerun_failure_prints_the_resume_command(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        workdir = tmp_path / "mod.forge"

        def fake_rerun(wd, stage, provider=None, settings_updates=None, on_progress=None):
            write_failed_run(workdir, Stage.MONSTERS)
            raise OsrForgeError("monsters exploded")

        monkeypatch.setattr(cli, "_build_provider", lambda name: object())
        monkeypatch.setattr(cli, "rerun", fake_rerun)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["rerun", "monsters", "--workdir", str(workdir)])
        assert str(excinfo.value) == (
            f"osrforge: monsters exploded\nresume with: osrforge rerun monsters --workdir {workdir}"
        )

    def test_a_geometry_failure_resumes_at_assemble(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # Geometry has no independent run — it fails inside assembly, and the
        # resume command is the one the user can actually type.
        workdir = tmp_path / "mod.forge"

        def fake_rerun(wd, stage, provider=None, settings_updates=None, on_progress=None):
            write_failed_run(workdir, Stage.GEOMETRY)
            raise OsrForgeError("geometry exploded")

        monkeypatch.setattr(cli, "rerun", fake_rerun)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["rerun", "assemble", "--workdir", str(workdir)])
        assert str(excinfo.value).endswith(f"resume with: osrforge rerun assemble --workdir {workdir}")

    def test_no_hint_without_a_failed_stage(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # A failure before any stage records `failed` (here: a missing run.json)
        # keeps the one-line contract.
        monkeypatch.setattr(cli, "_build_provider", lambda name: object())
        monkeypatch.setattr(cli, "convert", lambda *args, **kwargs: (_ for _ in ()).throw(OsrForgeError("early")))
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(tmp_path / "mod.pdf"), "--workdir", str(tmp_path / "mod.forge")])
        assert str(excinfo.value) == "osrforge: early"

    def test_the_pure_commands_never_hint(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        # `assemble` failing over a workdir with a failed stage stays one line —
        # the hint belongs to the chain commands.
        workdir = tmp_path / "mod.forge"
        write_failed_run(workdir, Stage.MONSTERS)
        monkeypatch.setattr(cli, "assemble", lambda wd: (_ for _ in ()).throw(OsrForgeError("no cache")))
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["assemble", "--workdir", str(workdir)])
        assert str(excinfo.value) == "osrforge: no cache"


class TestEstimateCommand:
    def test_estimate_prints_the_table_and_defaults_the_workdir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        from osrforge.estimate import CostEstimate

        seen: dict[str, object] = {}

        def fake_estimate(pdf_path, workdir, settings=None):
            seen["workdir"] = workdir
            return CostEstimate(
                page_count=200,
                text_tokens=100,
                image_tokens=181_000,
                survey_window_count=2,
                survey_input_tokens=187_100,
                survey_output_tokens=14_000,
                content_input_tokens=226_375,
                content_output_tokens=110_000,
                monsters_input_tokens=5_000,
                monsters_output_tokens=500,
                input_tokens=418_475,
                output_tokens=124_500,
                usd=2.9,
            )

        monkeypatch.setattr(cli, "estimate", fake_estimate)
        cli.main(["estimate", str(tmp_path / "huge.pdf")])
        assert seen["workdir"] == Path("huge.forge")
        out = capsys.readouterr().out
        assert "pages: 200" in out
        assert "(2 windows)" in out
        assert "estimated cost: $2.90" in out
        # The guard warning is gone from the table — larger sources chunk.
        assert "survey guard" not in out
