"""The eval scorer: synthetic metric tables, alignment edge cases, determinism, and the JN1 pinned baseline."""

import hashlib
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
    RawStatBlock,
    StatBlocks,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
    TownInfo,
)
from osrforge.evals import (
    ByomScoreboard,
    CorpusManifest,
    ModuleScore,
    ModuleTruth,
    RunInfo,
    Scoreboard,
    TruthEncounter,
    _match_fold,
    corpus_means,
    enforce_source_integrity,
    load_byom_scoreboard,
    load_manifest,
    load_scoreboard,
    load_truth,
    publish_module,
    save_byom_scoreboard,
    save_scoreboard,
    score_workdir,
    settings_overrides,
    sidecar_path,
    verify_source,
)
from osrforge.settings import ConversionSettings
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
        assert metrics.areas.truth_dungeons == 1
        assert metrics.areas.extracted_dungeons == 1
        assert metrics.areas.matched_dungeons == 1
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


class TestCustomAssertions:
    """The custom metric pair: `custom: true` scored against the stat-block cache via the shared predicate."""

    CUSTOM_TRUTH = truth_from_yaml(
        """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: tentacle worm
                custom: true
"""
    )

    def _workdir(self, tmp_path: Path, blocks: dict[str, RawStatBlock | None] | None) -> Path:
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1", encounters=(encounter("tentacle worms", 1),))])])],
            resolutions={"tentacle worms": MonsterResolution(template_id=None, method="unresolved")},
        )
        if blocks is not None:
            write_json_artifact(Workdir(root).statblocks_json, StatBlocks(custom_monsters="emit", blocks=blocks))
        return root

    def test_custom_is_rejected_beside_a_template(self):
        with pytest.raises(ValidationError, match="legal only when template is omitted"):
            TruthEncounter(name="orc chief", template="orc", custom=True)

    def test_custom_round_trips_through_truth_yaml(self):
        (encounter_truth,) = self.CUSTOM_TRUTH.dungeons[0].levels[0].areas[0].encounters
        assert encounter_truth.custom is True
        assert encounter_truth.template is None

    def test_usable_block_scores_a_match(self, tmp_path: Path):
        block = RawStatBlock(ac="5 [14]", ac_notation="dual", hit_dice="3", hp=12)
        root = self._workdir(tmp_path, {"tentacle worms": block})
        metrics = score_workdir(root, self.CUSTOM_TRUTH)
        assert metrics.encounters.custom_denominator == 1
        assert metrics.encounters.custom_matched == 1
        assert metrics.encounters.custom_accuracy == 1.0
        # The custom-asserted encounter left non_srd and the resolution family.
        assert metrics.encounters.non_srd == 0
        assert metrics.encounters.resolution_denominator == 0

    def test_extracted_but_unusable_block_scores_a_miss(self, tmp_path: Path):
        # No AC: assembly's refusal ladder would refuse this emission, so the
        # shared predicate must refuse the match too.
        block = RawStatBlock(hit_dice="3", hp=12)
        root = self._workdir(tmp_path, {"tentacle worms": block})
        metrics = score_workdir(root, self.CUSTOM_TRUTH)
        assert metrics.encounters.custom_denominator == 1
        assert metrics.encounters.custom_matched == 0
        assert metrics.encounters.custom_accuracy == 0.0

    def test_absent_marker_scores_a_miss(self, tmp_path: Path):
        root = self._workdir(tmp_path, {"tentacle worms": None})
        metrics = score_workdir(root, self.CUSTOM_TRUTH)
        assert metrics.encounters.custom_matched == 0

    def test_missing_statblock_cache_pins_matches_at_zero(self, tmp_path: Path):
        # The pre-phase-7 workdir state: the assertion still counts, honestly unmatched.
        root = self._workdir(tmp_path, None)
        metrics = score_workdir(root, self.CUSTOM_TRUTH)
        assert metrics.encounters.custom_denominator == 1
        assert metrics.encounters.custom_matched == 0

    def test_wrongly_resolved_bespoke_creature_scores_a_miss(self, tmp_path: Path):
        # An SRD-resolved name never entered the stat-block pass, so it has no
        # block — the right verdict for a bespoke creature the LLM mis-picked.
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1", encounters=(encounter("tentacle worms", 1),))])])],
            resolutions={"tentacle worms": MonsterResolution(template_id="carcass_crawler", method="llm")},
        )
        write_json_artifact(Workdir(root).statblocks_json, StatBlocks(custom_monsters="emit", blocks={}))
        metrics = score_workdir(root, self.CUSTOM_TRUTH)
        assert metrics.encounters.custom_denominator == 1
        assert metrics.encounters.custom_matched == 0

    def test_omitted_without_custom_stays_non_srd(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: tentacle worm
"""
        )
        block = RawStatBlock(ac="5 [14]", ac_notation="dual", hit_dice="3", hp=12)
        root = self._workdir(tmp_path, {"tentacle worms": block})
        metrics = score_workdir(root, truth)
        assert metrics.encounters.non_srd == 1
        assert metrics.encounters.custom_denominator == 0
        assert metrics.encounters.custom_accuracy is None


class TestMatchFolding:
    @pytest.mark.parametrize(
        ("name", "folded"),
        [
            ("kobolds", "kobold"),
            ("zombies", "zombie"),
            ("nixies", "nixie"),
            ("necrotic oozes", "necrotic ooze"),
            ("lizard men", "lizard man"),
            ("giant acid worms", "giant acid worm"),
            ("kobold", "kobold"),
            ("lizard man", "lizard man"),
            ("boss", "boss"),
            ("giant octopus", "giant octopus"),
            ("gas", "gas"),
            ("rats", "rat"),
            # Documented misses — the fold is conservative, never falsely crediting:
            ("cronies", "cronie"),
            ("bosses", "bosse"),
            ("wolves", "wolve"),
            ("mermen", "mermen"),
        ],
    )
    def test_fold_table(self, name: str, folded: str):
        assert _match_fold(name) == folded

    def _workdir(self, tmp_path: Path, encounters, resolutions):
        return fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1", encounters=encounters)])])],
            resolutions=resolutions,
        )

    def test_singular_truth_matches_extracted_plural_across_all_three_metrics(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: kobold
                template: kobold
                count: 4
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("Kobolds", count_fixed=4),),
            {"kobolds": MonsterResolution(template_id="kobold", method="exact")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.count_accuracy == 1.0
        assert metrics.encounters.resolution_accuracy == 1.0

    def test_counts_sum_across_the_fold_group(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: skeleton
                template: skeleton
                count: 3
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("skeleton", count_fixed=1), encounter("skeletons", count_fixed=2)),
            {"skeleton": MonsterResolution(template_id="skeleton", method="exact")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.count_denominator == 1
        assert metrics.encounters.count_matched == 1
        # Resolution requires every fold-matched name resolved to the asserted
        # template; "skeletons" carries no resolution entry here, so the
        # encounter scores a resolution miss while name and count match.
        assert metrics.encounters.resolution_matched == 0

    def test_resolution_needs_every_fold_matched_name_on_the_asserted_template(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: skeleton
                template: skeleton
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("skeleton", count_fixed=1), encounter("skeletons", count_fixed=2)),
            resolutions={
                "skeleton": MonsterResolution(template_id="skeleton", method="exact"),
                "skeletons": MonsterResolution(template_id="zombie", method="llm"),
            },
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.resolution_denominator == 1
        assert metrics.encounters.resolution_matched == 0

    def test_a_dice_count_sibling_disqualifies_the_fold_group(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: stirge
                template: stirge
                count: 3
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("stirge", count_fixed=1), encounter("stirges", count_dice="1d6")),
            {"stirge": MonsterResolution(template_id="stirge", method="exact")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.count_denominator == 1
        assert metrics.encounters.count_matched == 0

    def test_duplicate_truth_names_each_score_against_the_whole_group(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: native
                count: 2
              - name: native
                count: 10
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("natives", count_fixed=12),),
            {"natives": MonsterResolution(template_id=None, method="unresolved")},
        )
        metrics = score_workdir(root, truth)
        # Both truth entries match the one extracted group; each count compares
        # against the group sum (12), so both miss — the known conservative
        # shape score_workdir's docstring records.
        assert metrics.encounters.name_matched == 2
        assert metrics.encounters.count_denominator == 2
        assert metrics.encounters.count_matched == 0
        assert metrics.encounters.non_srd == 2

    def test_token_subsets_and_rank_variants_stay_misses(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: hobgoblin
                template: hobgoblin
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("hobgoblin chief", count_fixed=1),),
            {"hobgoblin chief": MonsterResolution(template_id="hobgoblin", method="llm")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_matched == 0

    def test_folded_non_srd_match_counts_as_non_srd(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters:
              - name: giant acid worm
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("giant acid worms", count_fixed=2),),
            {"giant acid worms": MonsterResolution(template_id=None, method="unresolved")},
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.non_srd == 1
        assert metrics.encounters.resolution_denominator == 0


class TestTreasureAssertion:
    def test_unasserted_treasure_leaves_both_denominators(self, tmp_path: Path):
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
              letters: [B]
          - key: "2"
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
                                content_area("1", treasure=("Treasure Type B.",)),
                                # Area 2's treasure is unasserted: whatever extraction
                                # saw here is outside both denominators.
                                content_area("2", treasure=("500 gp",)),
                            ],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 2
        assert metrics.treasure.presence_denominator == 1
        assert metrics.treasure.presence_agreement == 1.0
        assert metrics.treasure.letters_denominator == 1
        assert metrics.treasure.letter_accuracy == 1.0

    def test_asserted_empty_still_disagrees_with_an_extracted_signal(self, tmp_path: Path):
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
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1", treasure=("a ruby worth 100 gp",))])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.treasure.presence_denominator == 1
        assert metrics.treasure.presence_agreement == 0.0

    def test_all_unasserted_yields_empty_denominator(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1")])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.treasure.presence_denominator == 0
        assert metrics.treasure.presence_agreement is None


class TestDungeonCounts:
    def test_mode_flip_shape_is_legible(self, tmp_path: Path):
        """The phase 4 hazard's shape: many truth dungeons collapsing into one extracted dungeon."""
        truth = truth_from_yaml(
            """
dungeons:
  - name: cave a
    levels:
      - number: 1
        areas:
          - key: "1"
  - name: cave b
    levels:
      - number: 1
        areas:
          - key: "2"
  - name: cave c
    levels:
      - number: 1
        areas:
          - key: "3"
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "caves",
                    [
                        (
                            1,
                            [survey_area(key) for key in ("1", "2", "3")],
                            [content_area(key) for key in ("1", "2", "3")],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.truth_dungeons == 3
        assert metrics.areas.extracted_dungeons == 1
        # Greedy alignment gives the one extracted dungeon to the first truth
        # dungeon with overlap; the other two go unmatched.
        assert metrics.areas.matched_dungeons == 1

    def test_phantom_extracted_dungeon_is_visible(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                ("lair", [(1, [survey_area("1")], [content_area("1")])]),
                ("phantom", [(1, [survey_area("99")], [content_area("99")])]),
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.truth_dungeons == 1
        assert metrics.areas.extracted_dungeons == 2
        assert metrics.areas.matched_dungeons == 1


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

    def test_level_targeted_connections_are_outside_the_edge_universe(self, tmp_path: Path):
        """A to_key-null connection is skipped before canonical_slug — no crash, no denominator movement."""
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
                                content_area(
                                    "1",
                                    connections=(
                                        AreaConnection(to_key="2", direction="north"),
                                        AreaConnection(to_key=None, direction="down", via="stairs", to_level=2),
                                    ),
                                ),
                                content_area("2"),
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


class TestLevelAlignment:
    """Level alignment by maximal area-key overlap, many-to-one from the truth side (the B4 fix)."""

    def test_equal_numbered_levels_still_pair(self, tmp_path: Path):
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

    def test_many_truth_levels_pair_with_one_extracted_level(self, tmp_path: Path):
        """The B4 shape: printed tiers grouped by extraction into one coarse level all still score."""
        truth = truth_from_yaml(
            """
dungeons:
  - name: city
    levels:
      - number: 1
        areas:
          - key: "1"
            treasure:
              present: false
          - key: "2"
            treasure:
              present: false
      - number: 2
        areas:
          - key: "3"
            treasure:
              present: false
      - number: 3
        areas:
          - key: "4"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "city",
                    [
                        (
                            1,
                            [survey_area(key) for key in ("1", "2", "3", "4")],
                            [content_area(key) for key in ("1", "2", "3", "4")],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 4
        assert metrics.areas.recall == 1.0
        assert metrics.areas.precision == 1.0

    def test_a_split_truth_level_pairs_with_the_larger_share(self, tmp_path: Path):
        """The reverse split: extraction divided one printed level across two; maximal overlap picks the larger."""
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
                (
                    "lair",
                    [
                        (1, [survey_area("1")], [content_area("1")]),
                        (2, [survey_area("2"), survey_area("3")], [content_area("2"), content_area("3")]),
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        # The truth level pairs with extracted level 2 (overlap 2 beats 1);
        # extracted level 1's area is unmatched and costs precision only.
        assert metrics.areas.matched == 2
        assert metrics.areas.precision == round(2 / 3, 4)

    def test_a_zero_overlap_truth_level_stays_unmatched(self, tmp_path: Path):
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
            [("lair", [(1, [survey_area("1")], [content_area("1")])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 1
        assert metrics.areas.recall == 0.5

    def test_a_claimed_key_is_not_claimed_again(self, tmp_path: Path):
        """Two truth levels sharing a key against one extracted level: the first pairing claims it."""
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
          - key: "1"
            treasure:
              present: false
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1")])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.areas.matched == 1
        assert metrics.areas.recall == 0.5

    def test_equal_overlap_breaks_by_number_distance_then_lower_number(self):
        from osrforge.contracts.stages import SurveyDungeon, SurveyLevel
        from osrforge.evals import _align_levels

        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 2
        areas:
          - key: x
      - number: 3
        areas:
          - key: y
"""
        )
        extracted = SurveyDungeon(
            id="lair",
            name="lair",
            levels=(
                SurveyLevel(number=1, map_pages=(), areas=(survey_area("x"),)),
                SurveyLevel(number=3, map_pages=(), areas=(survey_area("x"),)),
                SurveyLevel(number=2, map_pages=(), areas=(survey_area("y"),)),
                SurveyLevel(number=4, map_pages=(), areas=(survey_area("y"),)),
            ),
        )
        matches = _align_levels(truth.dungeons[0], extracted)
        # Truth 2 ("x"): extracted 1 and 3 tie on overlap; distance 1 both; lower number wins.
        assert matches[2] == 1
        # Truth 3 ("y"): extracted 2 and 4 tie on overlap; distance 1 both; lower number wins.
        assert matches[3] == 2


@pytest.mark.parametrize(
    ("member", "caches"),
    [("jn1-chaotic-caves", "chaotic-caves/stages"), ("minimod", "minimod/expected")],
)
def test_committed_caches_pair_levels_by_equal_number(member: str, caches: str):
    """The committed-corpus property: overlap alignment reproduces the old number rule's outcome as data.

    No number-alignment implementation survives phase 6; this pins the
    verified property (no cross-level key overlap in any committed dungeon)
    that makes the JN1 baseline's numbers carry over the alignment rewrite.
    JN2 has no in-repo caches, so the property covers the two members that do.
    """
    from osrforge.evals import _align_dungeons, _align_levels

    truth = load_truth(CORPUS / member / "truth.yaml")
    index = SurveyIndex.model_validate_json((ASSETS / caches / "survey.json").read_text(encoding="utf-8"))
    matches = _align_dungeons(truth, index)
    assert len(matches) == len(truth.dungeons)
    for truth_position, extracted_position in matches.items():
        level_matches = _align_levels(truth.dungeons[truth_position], index.dungeons[extracted_position])
        assert level_matches, truth.dungeons[truth_position].name
        for truth_number, extracted_number in level_matches.items():
            assert truth_number == extracted_number, truth.dungeons[truth_position].name


def test_scoring_is_deterministic(tmp_path: Path):
    root = perfect_workdir(tmp_path)
    first = score_workdir(root, PERFECT_TRUTH)
    second = score_workdir(root, PERFECT_TRUTH)
    assert first == second
    score = ModuleScore(
        run=RunInfo(
            date="2026-07-10", model_id="gpt-5.4", osrforge_version="0.1.0", input_tokens=1, output_tokens=1, usd=0.01
        ),
        truth_sha256="ab" * 32,
        metrics=first,
    )
    board = Scoreboard(modules={"synthetic": score})
    out = tmp_path / "scoreboard.json"
    save_scoreboard(out, board)
    first_bytes = out.read_bytes()
    save_scoreboard(
        out,
        Scoreboard(modules={"synthetic": ModuleScore(run=score.run, truth_sha256="ab" * 32, metrics=second)}),
    )
    assert out.read_bytes() == first_bytes


UNPINNED_MANIFEST = {
    "title": "Some Retail Module",
    "source_url": "purchased retail; not redistributable",
    "pages": 36,
    "publisher": "Some Publisher",
    "edition": "2nd printing, 1981",
}


class TestHarnessPlumbing:
    def test_manifest_sha256_refusal(self, tmp_path: Path):
        manifest = load_manifest(CORPUS / "minimod" / "manifest.yaml")
        module_dir = tmp_path / "minimod"
        module_dir.mkdir()
        doctored = tmp_path / "doctored.pdf"
        doctored.write_bytes((ASSETS / "minimod" / "minimod.pdf").read_bytes() + b" ")
        with pytest.raises(ValueError, match="authored against"):
            verify_source(manifest, module_dir, doctored)
        # A pinned manifest never touches the sidecar.
        assert not sidecar_path(module_dir).exists()
        # The genuine file passes without seeding anything.
        assert verify_source(manifest, module_dir, ASSETS / "minimod" / "minimod.pdf") is False
        assert not sidecar_path(module_dir).exists()

    def test_unpinned_manifest_round_trips(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.safe_dump(UNPINNED_MANIFEST), encoding="utf-8")
        manifest = load_manifest(path)
        assert manifest.sha256 is None
        assert manifest.license is None
        assert manifest.truth_provenance is None
        assert manifest.publisher == "Some Publisher"
        assert manifest.edition == "2nd printing, 1981"

    def test_first_convert_seeds_the_sidecar_and_a_doctored_file_is_refused(self, tmp_path: Path):
        manifest = CorpusManifest.model_validate(UNPINNED_MANIFEST)
        module_dir = tmp_path / "some-retail-module"
        module_dir.mkdir()
        pdf = tmp_path / "owned-copy.pdf"
        pdf.write_bytes(b"%PDF-1.4 watermarked for one customer")
        assert verify_source(manifest, module_dir, pdf) is True
        recorded = sidecar_path(module_dir).read_text(encoding="utf-8").strip()
        assert recorded == hashlib.sha256(pdf.read_bytes()).hexdigest()
        # The same file passes on every later sight.
        assert verify_source(manifest, module_dir, pdf) is False
        # A doctored file is refused against the sidecar, with the authored-against message.
        doctored = tmp_path / "doctored.pdf"
        doctored.write_bytes(pdf.read_bytes() + b" ")
        with pytest.raises(ValueError, match="authored against"):
            verify_source(manifest, module_dir, doctored)

    def test_harness_external_workdir_seeds_the_sidecar_at_first_score(self, tmp_path: Path):
        # score's integrity check runs over run.json's recorded source hash;
        # the driver passes that digest here.
        manifest = CorpusManifest.model_validate(UNPINNED_MANIFEST)
        module_dir = tmp_path / "some-retail-module"
        module_dir.mkdir()
        digest = hashlib.sha256(b"the owner's copy").hexdigest()
        assert enforce_source_integrity(manifest, module_dir, digest, "wd (run.json)") is True
        assert sidecar_path(module_dir).read_text(encoding="utf-8").strip() == digest
        # A later workdir over a different source is refused.
        other = hashlib.sha256(b"a different copy").hexdigest()
        with pytest.raises(ValueError, match="authored against"):
            enforce_source_integrity(manifest, module_dir, other, "wd2 (run.json)")

    def test_a_manifest_pin_beats_the_sidecar(self, tmp_path: Path):
        pinned = CorpusManifest.model_validate(
            {**UNPINNED_MANIFEST, "sha256": hashlib.sha256(b"the pinned release").hexdigest()}
        )
        module_dir = tmp_path / "mod"
        module_dir.mkdir()
        sidecar_path(module_dir).write_text(hashlib.sha256(b"something else").hexdigest() + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="manifest"):
            enforce_source_integrity(
                manifest=pinned,
                module_dir=module_dir,
                digest=hashlib.sha256(b"something else").hexdigest(),
                described="wd (run.json)",
            )

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
            modules={
                "minimod": ModuleScore(
                    run=run, truth_sha256="ab" * 32, metrics=score_workdir(perfect_workdir(tmp_path), PERFECT_TRUTH)
                )
            }
        )
        save_scoreboard(path, board)
        assert load_scoreboard(path) == board
        means = corpus_means(board)
        assert means["area_recall"] == 1.0


class TestSettingsOverrides:
    def test_defaults_echo_nothing(self):
        assert settings_overrides(ConversionSettings()) == ()

    def test_non_default_knobs_echo_as_yaml_parseable_pairs(self):
        settings = ConversionSettings(blank_page_renders=(21,), render_dpi=300, unresolved_fallback="omit")
        assert settings_overrides(settings) == (
            "render_dpi=300",
            "blank_page_renders=[21]",
            "unresolved_fallback=omit",
        )


def private_module_fixtures(tmp_path: Path) -> tuple[Path, CorpusManifest, ModuleScore, str]:
    """A fabricated private corpus member with a scored entry and provenance."""
    module_dir = tmp_path / "some-retail-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    truth_path = module_dir / "truth.yaml"
    truth_path.write_text(
        "dungeons:\n  - name: lair\n    levels:\n      - number: 1\n        areas:\n          - key: '1'\n",
        encoding="utf-8",
    )
    manifest = CorpusManifest.model_validate(
        {
            **UNPINNED_MANIFEST,
            "truth_provenance": {
                "authored": "2026-07-10",
                "instrument": "Claude (Anthropic)",
                "verified": "adversarial pass 2026-07-10; owner sampled 10 areas",
            },
        }
    )
    truth_sha256 = hashlib.sha256(truth_path.read_bytes()).hexdigest()
    score = ModuleScore(
        run=RunInfo(
            date="2026-07-10", model_id="gpt-5.4", osrforge_version="0.1.0", input_tokens=10, output_tokens=2, usd=0.5
        ),
        truth_sha256=truth_sha256,
        settings_overrides=("blank_page_renders=[21]",),
        metrics=score_workdir(perfect_workdir(tmp_path), PERFECT_TRUTH),
    )
    return module_dir, manifest, score, truth_sha256


class TestPublish:
    def test_round_trip_carries_identity_truth_hash_and_overrides(self, tmp_path: Path):
        _, manifest, score, truth_sha256 = private_module_fixtures(tmp_path)
        board = publish_module(
            board=ByomScoreboard(),
            module_id="some-retail-module",
            manifest=manifest,
            private_board=Scoreboard(modules={"some-retail-module": score}),
            current_truth_sha256=truth_sha256,
            committed_ids={"minimod", "jn1-chaotic-caves", "jn2-monkey-isle"},
        )
        entry = board.modules["some-retail-module"]
        assert entry.title == "Some Retail Module"
        assert entry.publisher == "Some Publisher"
        assert entry.edition == "2nd printing, 1981"
        assert entry.pages == 36
        assert entry.truth_sha256 == truth_sha256
        assert entry.settings_overrides == ("blank_page_renders=[21]",)
        assert entry.run == score.run
        assert entry.metrics == score.metrics
        # Byte stability under the pinned artifact writer.
        out = tmp_path / "byom-scoreboard.json"
        save_byom_scoreboard(out, board)
        first = out.read_bytes()
        save_byom_scoreboard(out, load_byom_scoreboard(out))
        assert out.read_bytes() == first

    def test_refuses_an_unscored_module(self, tmp_path: Path):
        _, manifest, _, truth_sha256 = private_module_fixtures(tmp_path)
        with pytest.raises(ValueError, match="no scored entry"):
            publish_module(
                board=ByomScoreboard(),
                module_id="some-retail-module",
                manifest=manifest,
                private_board=Scoreboard(),
                current_truth_sha256=truth_sha256,
                committed_ids=set(),
            )

    def test_refuses_missing_provenance(self, tmp_path: Path):
        _, _, score, truth_sha256 = private_module_fixtures(tmp_path)
        bare = CorpusManifest.model_validate(UNPINNED_MANIFEST)
        with pytest.raises(ValueError, match="truth_provenance"):
            publish_module(
                board=ByomScoreboard(),
                module_id="some-retail-module",
                manifest=bare,
                private_board=Scoreboard(modules={"some-retail-module": score}),
                current_truth_sha256=truth_sha256,
                committed_ids=set(),
            )

    def test_refuses_a_committed_corpus_id_collision(self, tmp_path: Path):
        _, manifest, score, truth_sha256 = private_module_fixtures(tmp_path)
        with pytest.raises(ValueError, match="collides"):
            publish_module(
                board=ByomScoreboard(),
                module_id="minimod",
                manifest=manifest,
                private_board=Scoreboard(modules={"minimod": score}),
                current_truth_sha256=truth_sha256,
                committed_ids={"minimod"},
            )

    def test_refuses_a_title_mismatch_on_update(self, tmp_path: Path):
        _, manifest, score, truth_sha256 = private_module_fixtures(tmp_path)
        private = Scoreboard(modules={"some-retail-module": score})
        board = publish_module(
            board=ByomScoreboard(),
            module_id="some-retail-module",
            manifest=manifest,
            private_board=private,
            current_truth_sha256=truth_sha256,
            committed_ids=set(),
        )
        imposter = manifest.model_copy(update={"title": "A Different Module"})
        with pytest.raises(ValueError, match="cannot share one id"):
            publish_module(
                board=board,
                module_id="some-retail-module",
                manifest=imposter,
                private_board=private,
                current_truth_sha256=truth_sha256,
                committed_ids=set(),
            )
        # A same-title update replaces the entry — with a re-scored entry
        # whose score-time hash matches the truth as it stands now.
        rescored = score.model_copy(update={"truth_sha256": "ff" * 32})
        updated = publish_module(
            board=board,
            module_id="some-retail-module",
            manifest=manifest,
            private_board=Scoreboard(modules={"some-retail-module": rescored}),
            current_truth_sha256="ff" * 32,
            committed_ids=set(),
        )
        assert updated.modules["some-retail-module"].truth_sha256 == "ff" * 32

    def test_refuses_a_truth_edited_after_scoring(self, tmp_path: Path):
        _, manifest, score, _ = private_module_fixtures(tmp_path)
        with pytest.raises(ValueError, match="changed since"):
            publish_module(
                board=ByomScoreboard(),
                module_id="some-retail-module",
                manifest=manifest,
                private_board=Scoreboard(modules={"some-retail-module": score}),
                current_truth_sha256="ee" * 32,
                committed_ids=set(),
            )


class TestCommittedCorpus:
    def test_every_committed_member_validates(self):
        members = sorted(child.name for child in CORPUS.iterdir() if child.is_dir())
        assert members == ["jn1-chaotic-caves", "jn2-monkey-isle", "minimod"]
        for member in members:
            manifest = load_manifest(CORPUS / member / "manifest.yaml")
            truth = load_truth(CORPUS / member / "truth.yaml")
            assert manifest.pages >= 1
            assert truth.dungeons

    def test_committed_members_stay_fully_pinned_and_asserted(self):
        """The gating corpus never gets thinner by accident.

        Optionality (unpinned sha256, absent license, partial treasure truth)
        exists for private BYOM corpora; every committed member must keep the
        full posture — plus the backfilled truth provenance — or the corpus
        scoreboard's meaning silently changes.
        """
        members = sorted(child.name for child in CORPUS.iterdir() if child.is_dir())
        for member in members:
            manifest = load_manifest(CORPUS / member / "manifest.yaml")
            assert manifest.sha256 is not None, member
            assert manifest.license is not None, member
            assert manifest.truth_provenance is not None, member
            truth = load_truth(CORPUS / member / "truth.yaml")
            for dungeon in truth.dungeons:
                for level in dungeon.levels:
                    for area in level.areas:
                        assert area.treasure is not None, (member, dungeon.name, area.key)

    def test_truth_templates_exist_in_the_catalog(self):
        from osrlib.data import load_monsters

        ids = {template.id for template in load_monsters().monsters}
        for member in sorted(child.name for child in CORPUS.iterdir() if child.is_dir()):
            truth = load_truth(CORPUS / member / "truth.yaml")
            for dungeon in truth.dungeons:
                for level in dungeon.levels:
                    for area in level.areas:
                        for creature in area.encounters:
                            if creature.template is not None:
                                assert creature.template in ids, (member, creature.name, creature.template)

    def test_truth_connections_reference_same_level_keys(self):
        from osrforge.survey import canonical_slug

        for member in sorted(child.name for child in CORPUS.iterdir() if child.is_dir()):
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

    # The milestone extraction surveyed every keyed site and area the truth records.
    assert metrics.areas.truth_dungeons == 14
    assert metrics.areas.extracted_dungeons == 14
    assert metrics.areas.matched_dungeons == 14
    assert metrics.areas.truth_areas == 137
    assert metrics.areas.extracted_areas == 137
    assert metrics.areas.matched == 137
    assert metrics.areas.recall == 1.0
    assert metrics.areas.precision == 1.0

    assert metrics.encounters.truth_encounters == 109
    assert metrics.encounters.name_matched == 100
    assert metrics.encounters.name_recall == 0.9174
    assert metrics.encounters.count_denominator == 100
    assert metrics.encounters.count_matched == 99
    assert metrics.encounters.count_accuracy == 0.99
    # The phase 7 monsters re-record (null-hardened prompt) moved one LLM
    # answer: gray jelly now resolves grey_ooze — agreeing with the truth and
    # with the phase 3 correction session's remap — where the old recording
    # picked ochre_jelly.
    assert metrics.encounters.resolution_denominator == 85
    assert metrics.encounters.resolution_matched == 75
    assert metrics.encounters.resolution_accuracy == 0.8824
    # The phase 7 truth edits assert emission on all 15 name-matched
    # template-omitted encounters, moving them out of non_srd into the custom
    # pair. Four match against the committed caches — the names the tiers
    # left unresolved with usable transcribed blocks (elven thief, human
    # cleric 3, human magic-user 4, orc war leader); the eleven misses are
    # bespoke variants the recorded LLM pass resolved to SRD picks, which is
    # the phase 5 wrong-pick asymmetry made visible on its own metric.
    assert metrics.encounters.custom_denominator == 15
    assert metrics.encounters.custom_matched == 4
    assert metrics.encounters.custom_accuracy == 0.2667
    assert metrics.encounters.non_srd == 0

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
