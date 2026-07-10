"""The eval scorer: synthetic metric tables, alignment edge cases, determinism, and the JN1 pinned baseline."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from osrforge.contracts.stages import (
    AreaConnection,
    AreaContent,
    AreaEncounter,
    LevelContent,
    MonsterResolution,
    MonsterResolutions,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
    TownInfo,
)
from osrforge.evals import (
    CorpusManifest,
    ModuleScore,
    ModuleTruth,
    RunInfo,
    Scoreboard,
    corpus_means,
    load_manifest,
    load_scoreboard,
    load_truth,
    save_scoreboard,
    score_workdir,
    verify_source,
)
from osrforge.workdir import Workdir, write_json_artifact

ASSETS = Path(__file__).parent / "assets"
CORPUS = Path(__file__).parent.parent / "tools" / "eval" / "corpus"


def survey_area(key: str, name: str = "Somewhere") -> SurveyArea:
    return SurveyArea(key=key, name=name, kind="room", source_pages=(1,))


def content_area(
    key: str,
    encounters: tuple[AreaEncounter, ...] = (),
    connections: tuple[AreaConnection, ...] = (),
    treasure: tuple[str, ...] = (),
) -> AreaContent:
    return AreaContent(
        key=key,
        description="A room.",
        encounters=encounters,
        treasure=treasure,
        features=(),
        connections=connections,
        source_pages=(1,),
        confidence=0.9,
    )


def encounter(monster: str, count_fixed: int | None = None, count_dice: str | None = None) -> AreaEncounter:
    return AreaEncounter(monster=monster, count_fixed=count_fixed, count_dice=count_dice)


def fabricate_eval_workdir(
    root: Path,
    dungeons: list[tuple[str, list[tuple[int, list[SurveyArea], list[AreaContent]]]]],
    resolutions: dict[str, MonsterResolution] | None = None,
) -> Path:
    """Write survey.json, areas caches, and monsters.json for a synthetic extraction."""
    workdir = Workdir(root)
    workdir.stages_dir.mkdir(parents=True)
    survey_dungeons = []
    for name, levels in dungeons:
        survey_levels = []
        for number, areas, cached in levels:
            survey_levels.append(SurveyLevel(number=number, map_pages=(), areas=tuple(areas)))
            dungeon_id = name  # callers pass canonical ids as names for simplicity
            write_json_artifact(
                workdir.areas_json(dungeon_id, number),
                LevelContent(dungeon_id=dungeon_id, level_number=number, areas=tuple(cached)),
            )
        survey_dungeons.append(SurveyDungeon(id=name, name=name, levels=tuple(survey_levels)))
    index = SurveyIndex(
        title="Synthetic",
        hooks=(),
        town=TownInfo(name="", description=""),
        dungeons=tuple(survey_dungeons),
        monster_names=(),
    )
    write_json_artifact(workdir.survey_json, index)
    write_json_artifact(workdir.monsters_json, MonsterResolutions(resolutions=resolutions or {}))
    return root


def truth_from_yaml(text: str) -> ModuleTruth:
    return ModuleTruth.model_validate(yaml.safe_load(text))


PERFECT_TRUTH = truth_from_yaml(
    """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: orc
                template: orc
                count: 2
            connections: ["2"]
            treasure:
              present: true
              letters: [B]
          - key: "2"
            connections: ["1"]
            treasure:
              present: false
"""
)


def perfect_workdir(tmp_path: Path) -> Path:
    return fabricate_eval_workdir(
        tmp_path / "mod.forge",
        [
            (
                "lair",
                [
                    (
                        1,
                        [survey_area("1"), survey_area("2")],
                        [
                            content_area(
                                "1",
                                encounters=(encounter("orc", count_fixed=2),),
                                connections=(AreaConnection(to_key="2", direction="north"),),
                                treasure=("Treasure Type B.",),
                            ),
                            content_area("2"),
                        ],
                    )
                ],
            )
        ],
        resolutions={"orc": MonsterResolution(template_id="orc", method="exact")},
    )


class TestMetricFamilies:
    def test_perfect_extraction_scores_ones(self, tmp_path: Path):
        metrics = score_workdir(perfect_workdir(tmp_path), PERFECT_TRUTH)
        assert metrics.areas.recall == 1.0 and metrics.areas.precision == 1.0
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.count_accuracy == 1.0
        assert metrics.encounters.resolution_accuracy == 1.0
        assert metrics.encounters.non_srd == 0
        assert metrics.connections.f1 == 1.0
        assert metrics.treasure.presence_agreement == 1.0
        assert metrics.treasure.letter_accuracy == 1.0

    def test_empty_extraction_scores_zero_recall(self, tmp_path: Path):
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("other", [(1, [survey_area("9")], [content_area("9")])])],
        )
        metrics = score_workdir(root, PERFECT_TRUTH)
        # No key overlap: the dungeon doesn't align, so every truth area is a miss.
        assert metrics.areas.matched == 0 and metrics.areas.recall == 0.0
        assert metrics.encounters.name_recall == 0.0
        # No matched areas: connection and treasure denominators are empty.
        assert metrics.connections.f1 is None
        assert metrics.treasure.presence_agreement is None

    def test_partial_extraction(self, tmp_path: Path):
        # Area 2 missing: recall 0.5; a phantom area drops precision to 0.5.
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area("1"), survey_area("99")],
                            [
                                content_area("1", encounters=(encounter("orc", count_fixed=3),), treasure=()),
                                content_area("99"),
                            ],
                        )
                    ],
                )
            ],
            resolutions={"orc": MonsterResolution(template_id="hobgoblin", method="fuzzy")},
        )
        metrics = score_workdir(root, PERFECT_TRUTH)
        assert metrics.areas.recall == 0.5 and metrics.areas.precision == 0.5
        # Name matched, but the count disagrees and the resolution is wrong.
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.count_accuracy == 0.0
        assert metrics.encounters.resolution_accuracy == 0.0
        # Truth asserts 1-2 but area 2 is unmatched: no scoreable edges.
        assert metrics.connections.truth_edges == 0
        # Area 1's treasure signal is empty but truth says present: disagreement.
        assert metrics.treasure.presence_agreement == 0.0
        assert metrics.treasure.letter_accuracy == 0.0

    def test_count_sums_same_name_encounters_and_dice_disqualify(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: orc
                template: orc
                count: 6
            treasure:
              present: false
          - key: "2"
            encounters:
              - name: wolf
                template: normal_wolf
                count: 2
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area("1"), survey_area("2")],
                            [
                                content_area("1", encounters=(encounter("orc", 4), encounter("orc", 2))),
                                content_area("2", encounters=(encounter("wolf", count_dice="1d4"),)),
                            ],
                        )
                    ],
                )
            ],
            resolutions={
                "orc": MonsterResolution(template_id="orc", method="exact"),
                "wolf": MonsterResolution(template_id="normal_wolf", method="alias"),
            },
        )
        metrics = score_workdir(root, truth)
        # 4 + 2 sums to the truth count; the dice-counted wolf has no comparable count.
        assert metrics.encounters.count_denominator == 2
        assert metrics.encounters.count_matched == 1

    def test_treasure_presence_counts_unparsed_strings_as_seen(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            treasure:
              present: true
          - key: "2"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area("1"), survey_area("2")],
                            [
                                # Unparseable prose still counts as "extraction saw it".
                                content_area("1", treasure=("each orc carries 1d6 sp",)),
                                content_area("2", treasure=("   ",)),
                            ],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.treasure.presence_agreement == 1.0

    def test_non_srd_encounters_leave_the_resolution_denominator(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: orc chief
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [(1, [survey_area("1")], [content_area("1", encounters=(encounter("orc chief", 1),))])],
                )
            ],
            resolutions={"orc chief": MonsterResolution(template_id=None, method="unresolved")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.resolution_denominator == 0
        assert metrics.encounters.resolution_accuracy is None
        assert metrics.encounters.non_srd == 1


class TestConnectionUniverse:
    def test_unasserted_areas_never_produce_false_positives(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            treasure:
              present: false
          - key: "2"
            treasure:
              present: false
          - key: "3"
            treasure:
              present: false
          - key: "4"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area(key) for key in ("1", "2", "3", "4")],
                            [
                                content_area("1", connections=(AreaConnection(to_key="2", direction="north"),)),
                                content_area("2"),
                                # 3-4 is between two unasserted areas: out of the universe.
                                content_area("3", connections=(AreaConnection(to_key="4", direction="east"),)),
                                content_area("4"),
                            ],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.connections.truth_edges == 1
        assert metrics.connections.extracted_edges == 1
        assert metrics.connections.f1 == 1.0

    def test_an_asserted_area_makes_incident_extra_edges_false_positives(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            treasure:
              present: false
          - key: "2"
            treasure:
              present: false
          - key: "3"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area(key) for key in ("1", "2", "3")],
                            [
                                content_area(
                                    "1",
                                    connections=(
                                        AreaConnection(to_key="2", direction="north"),
                                        AreaConnection(to_key="3", direction="south"),
                                    ),
                                ),
                                content_area("2"),
                                content_area("3"),
                            ],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        # 1's asserted list is complete: 1-3 is a false positive.
        assert metrics.connections.extracted_edges == 2
        assert metrics.connections.true_positives == 1
        assert metrics.connections.precision == 0.5
        assert metrics.connections.recall == 1.0

    def test_edges_are_undirected_and_deduplicated(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            treasure:
              present: false
          - key: "2"
            connections: ["1"]
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area("1"), survey_area("2")],
                            [
                                content_area("1", connections=(AreaConnection(to_key="2", direction="north"),)),
                                content_area("2", connections=(AreaConnection(to_key="1", direction="south"),)),
                            ],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.connections.truth_edges == 1
        assert metrics.connections.extracted_edges == 1
        assert metrics.connections.f1 == 1.0


class TestAlignment:
    def test_missed_dungeon_counts_all_its_areas_as_misses(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: found
    levels:
      - number: 1
        areas:
          - key: "1"
            treasure:
              present: false
  - name: missed
    levels:
      - number: 1
        areas:
          - key: "8"
            treasure:
              present: false
          - key: "9"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("found", [(1, [survey_area("1")], [content_area("1")])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.truth_areas == 3
        assert metrics.areas.matched == 1
        assert metrics.areas.recall == round(1 / 3, 4)

    def test_bumped_keys_do_not_match_their_parent(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "5"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("5"), survey_area("5-2")], [content_area("5"), content_area("5-2")])])],
        )
        metrics = score_workdir(root, truth)
        # "5" matches; the reserve-then-bump sibling "5-2" is a distinct, unmatched area.
        assert metrics.areas.matched == 1
        assert metrics.areas.extracted_areas == 2
        assert metrics.areas.precision == 0.5

    def test_ties_break_by_name_ratio_then_document_order(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: Orc Lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: orc
                template: orc
            treasure:
              present: false
"""
        )
        # Two extracted dungeons with identical key overlap; the name-slug
        # ratio prefers orc-lair over rat-warren.
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                ("rat-warren", [(1, [survey_area("1")], [content_area("1")])]),
                ("orc-lair", [(1, [survey_area("1")], [content_area("1", encounters=(encounter("orc", 1),))])]),
            ],
            resolutions={"orc": MonsterResolution(template_id="orc", method="exact")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0

    def test_greedy_overlap_prefers_the_larger_intersection(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            treasure:
              present: false
          - key: "2"
            treasure:
              present: false
          - key: "3"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                ("partial", [(1, [survey_area("1")], [content_area("1")])]),
                (
                    "fuller",
                    [
                        (
                            1,
                            [survey_area(key) for key in ("1", "2", "3")],
                            [content_area(key) for key in ("1", "2", "3")],
                        )
                    ],
                ),
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 3

    def test_levels_align_by_number(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            treasure:
              present: false
      - number: 2
        areas:
          - key: "9"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (1, [survey_area("1")], [content_area("1")]),
                        (2, [survey_area("9")], [content_area("9")]),
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 2

    def test_non_ascii_truth_keys_take_the_positional_fallback(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "洞窟"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("area-1")], [content_area("area-1")])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 1


def test_scoring_is_deterministic(tmp_path: Path):
    root = perfect_workdir(tmp_path)
    first = score_workdir(root, PERFECT_TRUTH)
    second = score_workdir(root, PERFECT_TRUTH)
    assert first == second
    score = ModuleScore(
        run=RunInfo(
            date="2026-07-10", model_id="gpt-5.4", osrforge_version="0.1.0", input_tokens=1, output_tokens=1, usd=0.01
        ),
        metrics=first,
    )
    board = Scoreboard(modules={"synthetic": score})
    out = tmp_path / "scoreboard.json"
    save_scoreboard(out, board)
    first_bytes = out.read_bytes()
    save_scoreboard(out, Scoreboard(modules={"synthetic": ModuleScore(run=score.run, metrics=second)}))
    assert out.read_bytes() == first_bytes


class TestHarnessPlumbing:
    def test_manifest_sha256_refusal(self, tmp_path: Path):
        manifest = load_manifest(CORPUS / "minimod" / "manifest.yaml")
        doctored = tmp_path / "doctored.pdf"
        doctored.write_bytes((ASSETS / "minimod" / "minimod.pdf").read_bytes() + b" ")
        with pytest.raises(ValueError, match="sha256"):
            verify_source(manifest, doctored)
        # The genuine file passes.
        verify_source(manifest, ASSETS / "minimod" / "minimod.pdf")

    def test_truth_files_reject_duplicate_key_slugs_per_level(self):
        with pytest.raises(ValidationError, match="unique per level"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "4a"
            treasure:
              present: false
          - key: "4A"
            treasure:
              present: false
"""
            )

    def test_truth_files_reject_duplicate_level_numbers(self):
        with pytest.raises(ValidationError, match="unique per dungeon"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            treasure:
              present: false
      - number: 1
        areas:
          - key: "2"
            treasure:
              present: false
"""
            )

    def test_truth_files_reject_unknown_keys(self):
        with pytest.raises(ValidationError):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            monsters: []
            treasure:
              present: false
"""
            )

    def test_manifest_rejects_bad_digest(self):
        with pytest.raises(ValidationError):
            CorpusManifest.model_validate(
                {
                    "title": "x",
                    "source_url": "https://example.invalid",
                    "sha256": "nothex",
                    "pages": 1,
                    "license": {"spdx": "CC0-1.0", "verified": "note"},
                }
            )

    def test_scoreboard_round_trip_with_injected_run_metadata(self, tmp_path: Path):
        path = tmp_path / "scoreboard.json"
        assert load_scoreboard(path) == Scoreboard()
        run = RunInfo(
            date="2026-07-10", model_id="gpt-5.4", osrforge_version="0.1.0", input_tokens=10, output_tokens=2, usd=0.5
        )
        board = Scoreboard(
            modules={"minimod": ModuleScore(run=run, metrics=score_workdir(perfect_workdir(tmp_path), PERFECT_TRUTH))}
        )
        save_scoreboard(path, board)
        assert load_scoreboard(path) == board
        means = corpus_means(board)
        assert means["area_recall"] == 1.0


class TestCommittedCorpus:
    def test_every_committed_member_validates(self):
        members = sorted(child.name for child in CORPUS.iterdir() if child.is_dir())
        assert members == ["jn1-chaotic-caves", "jn2-monkey-isle", "minimod"]
        for member in members:
            manifest = load_manifest(CORPUS / member / "manifest.yaml")
            truth = load_truth(CORPUS / member / "truth.yaml")
            assert manifest.pages >= 1
            assert truth.dungeons

    def test_truth_templates_exist_in_the_catalog(self):
        from osrlib.data import load_monsters

        ids = {template.id for template in load_monsters().monsters}
        for member in ("minimod", "jn1-chaotic-caves", "jn2-monkey-isle"):
            truth = load_truth(CORPUS / member / "truth.yaml")
            for dungeon in truth.dungeons:
                for level in dungeon.levels:
                    for area in level.areas:
                        for creature in area.encounters:
                            if creature.template is not None:
                                assert creature.template in ids, (member, creature.name, creature.template)

    def test_truth_connections_reference_same_level_keys(self):
        from osrforge.survey import canonical_slug

        for member in ("minimod", "jn1-chaotic-caves", "jn2-monkey-isle"):
            truth = load_truth(CORPUS / member / "truth.yaml")
            for dungeon in truth.dungeons:
                for level in dungeon.levels:
                    slugs = {canonical_slug(area.key) for area in level.areas}
                    for area in level.areas:
                        for neighbor in area.connections or ():
                            assert canonical_slug(neighbor) in slugs, (member, dungeon.name, area.key, neighbor)


JN1_STAGES = ASSETS / "chaotic-caves" / "stages"


def jn1_workdir(root: Path) -> Path:
    workdir = Workdir(root)
    workdir.stages_dir.mkdir(parents=True)
    for path in JN1_STAGES.iterdir():
        shutil.copyfile(path, workdir.stages_dir / path.name)
    return root


def test_jn1_pinned_baseline_over_the_committed_caches(tmp_path: Path):
    """The scorer's behavior as a golden: exact numbers over the committed JN1 caches and truth.

    These re-bless only when the caches or the truth file deliberately change.
    """
    truth = load_truth(CORPUS / "jn1-chaotic-caves" / "truth.yaml")
    metrics = score_workdir(jn1_workdir(tmp_path / "jn1.forge"), truth)

    # The milestone extraction surveyed every keyed area the truth records.
    assert metrics.areas.truth_areas == 137
    assert metrics.areas.extracted_areas == 137
    assert metrics.areas.matched == 137
    assert metrics.areas.recall == 1.0
    assert metrics.areas.precision == 1.0

    assert metrics.encounters.truth_encounters == 109
    assert metrics.encounters.name_matched == 72
    assert metrics.encounters.name_recall == 0.6606
    assert metrics.encounters.count_denominator == 72
    assert metrics.encounters.count_matched == 71
    assert metrics.encounters.count_accuracy == 0.9861
    assert metrics.encounters.resolution_denominator == 58
    assert metrics.encounters.resolution_matched == 48
    assert metrics.encounters.resolution_accuracy == 0.8276
    assert metrics.encounters.non_srd == 14

    assert metrics.connections.truth_edges == 39
    assert metrics.connections.extracted_edges == 51
    assert metrics.connections.true_positives == 30
    assert metrics.connections.precision == 0.5882
    assert metrics.connections.recall == 0.7692
    assert metrics.connections.f1 == 0.6666

    assert metrics.treasure.presence_denominator == 137
    assert metrics.treasure.presence_matched == 130
    assert metrics.treasure.presence_agreement == 0.9489
    assert metrics.treasure.letter_accuracy is None
