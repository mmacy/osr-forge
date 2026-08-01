"""The eval scorer: synthetic metric tables, alignment edge cases, determinism, and the JN1 pinned baseline."""

import hashlib
import json
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
    MapEdgeProposal,
    MapLevelReading,
    MapReading,
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
    rescore_modules,
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


PERFECT_TRUTH_YAML = """
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

PERFECT_TRUTH = truth_from_yaml(PERFECT_TRUTH_YAML)


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


SEVEN_FAMILY_TRUTH = truth_from_yaml(
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
            doors:
              "2": {kind: door, locked: true}
            treasure:
              present: true
              letters: [B]
          - key: "2"
            encounters: []
            connections: ["1"]
            doors:
              "1": {kind: door, locked: true}
            treasure:
              present: false
      - number: 2
        areas:
          - key: "3"
            encounters: []
            connections: []
            doors: {}
            treasure:
              present: false
    transitions:
      - {from_level: 1, from_key: "2", to_level: 2, to_key: "3", kind: stairs}
    entrance:
      level: 1
      key: "1"
"""
)


def seven_family_workdir(tmp_path: Path) -> Path:
    """An extraction agreeing with `SEVEN_FAMILY_TRUTH` on every family."""
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
                                connections=(
                                    AreaConnection(to_key="2", direction="north", via="door", door_locked=True),
                                ),
                                treasure=("Treasure Type B.",),
                            ),
                            content_area(
                                "2",
                                connections=(
                                    AreaConnection(to_key="1", direction="south", via="door"),
                                    AreaConnection(to_key="3", direction="down", via="stairs"),
                                ),
                            ),
                        ],
                    ),
                    (
                        2,
                        [survey_area("3")],
                        [content_area("3", connections=(AreaConnection(to_key="2", direction="up", via="stairs"),))],
                    ),
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
        # The truth asserts no doors, transitions, or entrance: the new
        # families stay honestly empty rather than inventing agreement.
        assert metrics.encounters.precision == 1.0
        assert metrics.doors.truth_doors == 0 and metrics.doors.recall is None
        assert metrics.transitions.asserted_dungeons == 0 and metrics.transitions.recall is None
        assert metrics.entrances.asserted == 0 and metrics.entrances.accuracy is None

    def test_perfect_seven_family_extraction_scores_ones(self, tmp_path: Path):
        metrics = score_workdir(seven_family_workdir(tmp_path), SEVEN_FAMILY_TRUTH)
        assert metrics.areas.recall == 1.0 and metrics.areas.precision == 1.0
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.precision_denominator == 1
        assert metrics.encounters.precision == 1.0
        assert metrics.connections.truth_edges == 1
        assert metrics.connections.f1 == 1.0
        assert metrics.treasure.presence_agreement == 1.0
        assert metrics.doors.truth_doors == 1
        assert metrics.doors.extracted_doors == 1
        assert metrics.doors.true_positives == 1
        assert metrics.doors.recall == 1.0 and metrics.doors.precision == 1.0
        # Locked was stated on one directed mention only: either side suffices.
        assert metrics.doors.kind_accuracy == 1.0 and metrics.doors.locked_accuracy == 1.0
        assert metrics.transitions.asserted_dungeons == 1
        assert metrics.transitions.truth_transitions == 1
        # The reciprocal cross-level mentions merged into one claim.
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 1
        assert metrics.transitions.recall == 1.0 and metrics.transitions.precision == 1.0
        assert metrics.transitions.kind_accuracy == 1.0
        assert metrics.entrances.asserted == 1
        assert metrics.entrances.matched == 1
        assert metrics.entrances.accuracy == 1.0

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
        encounters = self.CUSTOM_TRUTH.dungeons[0].levels[0].areas[0].encounters
        assert encounters is not None
        (encounter_truth,) = encounters
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


class TestEncounterPrecision:
    """Name-level precision over matched areas whose truth asserts encounters — the asserted-empty convention."""

    def _workdir(self, tmp_path: Path, encounters, resolutions=None):
        return fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1")], [content_area("1", encounters=encounters)])])],
            resolutions=resolutions,
        )

    def test_hallucinated_names_cost_precision(self, tmp_path: Path):
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
"""
        )
        root = self._workdir(
            tmp_path,
            (encounter("orcs", count_fixed=2), encounter("goblin", count_fixed=1)),
            {
                "orcs": MonsterResolution(template_id="orc", method="exact"),
                "goblin": MonsterResolution(template_id="goblin", method="exact"),
            },
        )
        metrics = score_workdir(root, truth)
        assert metrics.encounters.name_recall == 1.0
        assert metrics.encounters.precision_denominator == 2
        assert metrics.encounters.precision_matched == 1
        assert metrics.encounters.precision == 0.5

    def test_unasserted_encounters_leave_both_denominators(self, tmp_path: Path):
        # Omitted asserts nothing: extracted names in the area are neither
        # recall targets nor precision liabilities.
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
        root = self._workdir(tmp_path, (encounter("goblin", count_fixed=1),))
        metrics = score_workdir(root, truth)
        assert metrics.encounters.truth_encounters == 0
        assert metrics.encounters.name_recall is None
        assert metrics.encounters.precision_denominator == 0
        assert metrics.encounters.precision is None

    def test_asserted_empty_makes_extracted_names_false_positives(self, tmp_path: Path):
        # The asserted-empty flip: [] is a complete (empty) list, so any
        # extracted name is provably a hallucination.
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters: []
"""
        )
        root = self._workdir(tmp_path, (encounter("goblin", count_fixed=1),))
        metrics = score_workdir(root, truth)
        assert metrics.encounters.truth_encounters == 0
        assert metrics.encounters.name_recall is None
        assert metrics.encounters.precision_denominator == 1
        assert metrics.encounters.precision_matched == 0
        assert metrics.encounters.precision == 0.0

    def test_precision_counts_distinct_folds_not_mentions(self, tmp_path: Path):
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
"""
        )
        root = self._workdir(tmp_path, (encounter("orc", count_fixed=1), encounter("orcs", count_fixed=2)))
        metrics = score_workdir(root, truth)
        # "orc" and "orcs" fold together: one denominator entry, matched.
        assert metrics.encounters.precision_denominator == 1
        assert metrics.encounters.precision == 1.0


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


class TestTruthAssertionSchema:
    """The phase 9 truth extension's validators: contradictions unrepresentable, assertion-awareness preserved."""

    def test_omitted_encounters_are_unasserted_and_empty_is_asserted(self):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            encounters: []
          - key: "2"
"""
        )
        asserted, omitted = truth.dungeons[0].levels[0].areas
        assert asserted.encounters == ()
        assert omitted.encounters is None

    def test_doors_require_asserted_connections(self):
        with pytest.raises(ValidationError, match="doors asserted without connections"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            doors:
              "2": {kind: door}
"""
            )

    def test_doors_keys_must_appear_in_connections(self):
        with pytest.raises(ValidationError, match="not among its connections"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            doors:
              "9": {kind: door}
          - key: "2"
          - key: "9"
"""
            )

    def test_both_endpoint_door_assertions_must_agree_on_facts(self):
        with pytest.raises(ValidationError, match="contradictory door assertions"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            doors:
              "2": {kind: door}
          - key: "2"
            connections: ["1"]
            doors:
              "1": {kind: secret_door}
"""
            )

    def test_implicit_no_door_against_an_explicit_door_is_a_contradiction(self):
        # Endpoint A asserts a doors set omitting B while B lists A: presence
        # disagreement, rejected the same as a kind or locked mismatch.
        with pytest.raises(ValidationError, match="including omission"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            doors: {}
          - key: "2"
            connections: ["1"]
            doors:
              "1": {kind: door}
"""
            )

    def test_agreeing_door_assertions_round_trip(self):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            doors:
              "2": {kind: secret_door, locked: true}
          - key: "2"
            connections: ["1"]
            doors:
              "1": {kind: secret_door, locked: true}
"""
        )
        doors = truth.dungeons[0].levels[0].areas[0].doors
        assert doors is not None
        assert doors["2"].kind == "secret_door"
        assert doors["2"].locked is True

    def test_one_sided_door_assertion_is_legal(self):
        # Only one endpoint asserts doors: no pair to contradict.
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
            doors:
              "2": {kind: door}
          - key: "2"
            connections: ["1"]
"""
        )
        assert truth.dungeons[0].levels[0].areas[1].doors is None

    def test_transition_endpoints_must_exist(self):
        with pytest.raises(ValidationError, match="does not have"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
    transitions:
      - {from_level: 1, from_key: "1", to_level: 2, to_key: "9", kind: stairs}
"""
            )
        with pytest.raises(ValidationError, match="does not have"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
      - number: 2
        areas:
          - key: "9"
    transitions:
      - {from_level: 1, from_key: "1", to_level: 2, to_key: "99", kind: stairs}
"""
            )

    def test_transitions_link_two_levels(self):
        with pytest.raises(ValidationError, match="links two levels"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
          - key: "2"
    transitions:
      - {from_level: 1, from_key: "1", to_level: 1, to_key: "2", kind: stairs}
"""
            )

    def test_entrance_endpoint_must_exist(self):
        with pytest.raises(ValidationError, match="does not have"):
            truth_from_yaml(
                """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
    entrance:
      level: 1
      key: "9"
"""
            )

    def test_omitted_transitions_and_entrance_assert_nothing(self):
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
        assert truth.dungeons[0].transitions is None
        assert truth.dungeons[0].entrance is None


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


class TestDoorMetrics:
    """The doors family off the edge-fact seam: the pinned dedup rule and the doors-asserting universe."""

    def _workdir(self, tmp_path: Path, connections: dict[str, tuple[AreaConnection, ...]]) -> Path:
        keys = ("1", "2")
        return fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area(key) for key in keys],
                            [content_area(key, connections=connections.get(key, ())) for key in keys],
                        )
                    ],
                )
            ],
        )

    def _truth(self, doors_yaml: str) -> ModuleTruth:
        return truth_from_yaml(
            f"""
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
{doors_yaml}
          - key: "2"
            connections: ["1"]
"""
        )

    def test_either_directed_mention_states_the_door(self, tmp_path: Path):
        truth = self._truth('            doors:\n              "2": {kind: door}')
        root = self._workdir(
            tmp_path,
            {
                "1": (AreaConnection(to_key="2", direction="north", via="passage"),),
                "2": (AreaConnection(to_key="1", direction="south", via="door"),),
            },
        )
        metrics = score_workdir(root, truth)
        assert metrics.doors.truth_doors == 1
        assert metrics.doors.extracted_doors == 1
        assert metrics.doors.true_positives == 1
        assert metrics.doors.recall == 1.0 and metrics.doors.precision == 1.0
        assert metrics.doors.kind_accuracy == 1.0
        assert metrics.doors.locked_accuracy == 1.0
        # The seam feeds the F1 the same edge.
        assert metrics.connections.f1 == 1.0

    def test_kind_conflict_resolves_to_secret_door(self, tmp_path: Path):
        root = self._workdir(
            tmp_path,
            {
                "1": (AreaConnection(to_key="2", direction="north", via="door"),),
                "2": (AreaConnection(to_key="1", direction="south", via="secret_door"),),
            },
        )
        metrics = score_workdir(root, self._truth('            doors:\n              "2": {kind: secret_door}'))
        assert metrics.doors.true_positives == 1
        assert metrics.doors.kind_matched == 1
        assert metrics.doors.kind_accuracy == 1.0

    def test_a_kind_conflict_can_genuinely_miss_a_plain_door(self, tmp_path: Path):
        root = self._workdir(
            tmp_path,
            {
                "1": (AreaConnection(to_key="2", direction="north", via="door"),),
                "2": (AreaConnection(to_key="1", direction="south", via="secret_door"),),
            },
        )
        metrics = score_workdir(root, self._truth('            doors:\n              "2": {kind: door}'))
        # Presence still agrees; the resolved secret_door misses the plain kind.
        assert metrics.doors.true_positives == 1
        assert metrics.doors.kind_accuracy == 0.0

    def test_locked_if_either_side_states_it(self, tmp_path: Path):
        truth = self._truth('            doors:\n              "2": {kind: door, locked: true}')
        root = self._workdir(
            tmp_path,
            {
                "1": (AreaConnection(to_key="2", direction="north", via="door"),),
                "2": (AreaConnection(to_key="1", direction="south", via="door", door_locked=True),),
            },
        )
        metrics = score_workdir(root, truth)
        assert metrics.doors.true_positives == 1
        assert metrics.doors.locked_matched == 1
        assert metrics.doors.locked_accuracy == 1.0

    def test_door_conditions_on_a_non_door_via_contribute_nothing(self, tmp_path: Path):
        # Geometry's discard posture, mirrored: a locked flag on a passage
        # states no door fact, so the asserted door is a plain miss.
        truth = self._truth('            doors:\n              "2": {kind: door, locked: true}')
        root = self._workdir(
            tmp_path,
            {"1": (AreaConnection(to_key="2", direction="north", via="passage", door_locked=True),)},
        )
        metrics = score_workdir(root, truth)
        assert metrics.doors.truth_doors == 1
        assert metrics.doors.extracted_doors == 0
        assert metrics.doors.recall == 0.0
        assert metrics.doors.precision is None

    def test_implicit_no_door_makes_an_extracted_door_a_false_positive(self, tmp_path: Path):
        # An asserting area's door set is complete: doors: {} plus an
        # extracted door via is the hallucination the metric must see.
        truth = self._truth("            doors: {}")
        root = self._workdir(
            tmp_path,
            {"1": (AreaConnection(to_key="2", direction="north", via="door"),)},
        )
        metrics = score_workdir(root, truth)
        assert metrics.doors.truth_doors == 0
        assert metrics.doors.extracted_doors == 1
        assert metrics.doors.true_positives == 0
        assert metrics.doors.precision == 0.0
        assert metrics.doors.recall is None

    def test_edges_outside_the_asserting_universe_are_ignored(self, tmp_path: Path):
        # Neither endpoint asserts doors (the minimod 4/5 shape): the
        # extracted door is outside the universe, not a false positive.
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
          - key: "2"
            connections: ["1"]
"""
        )
        root = self._workdir(
            tmp_path,
            {"1": (AreaConnection(to_key="2", direction="north", via="door"),)},
        )
        metrics = score_workdir(root, truth)
        assert metrics.doors.truth_doors == 0
        assert metrics.doors.extracted_doors == 0
        assert metrics.doors.recall is None and metrics.doors.precision is None
        # The same edge still scores in the connection family.
        assert metrics.connections.f1 == 1.0


class TestTransitionMetrics:
    """The dungeon-scoped transitions family: the four-clause classifier, dedup, and endpoint resolution."""

    COLLAPSED_TRUTH = truth_from_yaml(
        """
dungeons:
  - name: temple
    levels:
      - number: 1
        areas:
          - key: "1"
          - key: "2"
      - number: 2
        areas:
          - key: "9"
          - key: "10"
    transitions:
      - {from_level: 1, from_key: "2", to_level: 2, to_key: "9", kind: stairs}
"""
    )

    def _collapsed(self, tmp_path: Path, connections: dict[str, tuple[AreaConnection, ...]]) -> Path:
        """The JN2 shape: two printed levels the survey collapsed into one extracted level."""
        keys = ("1", "2", "9", "10")
        return fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "temple",
                    [
                        (
                            1,
                            [survey_area(key) for key in keys],
                            [content_area(key, connections=connections.get(key, ())) for key in keys],
                        )
                    ],
                )
            ],
        )

    def test_jn2_collapsed_level_shape_scores_the_printed_stair(self, tmp_path: Path):
        # The printed inter-level stair reads as a same-extracted-level
        # connection; the pairing claims recover the truth levels.
        root = self._collapsed(tmp_path, {"2": (AreaConnection(to_key="9", direction="up", via="stairs"),)})
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        assert metrics.transitions.asserted_dungeons == 1
        assert metrics.transitions.truth_transitions == 1
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 1
        assert metrics.transitions.recall == 1.0 and metrics.transitions.precision == 1.0
        assert metrics.transitions.kind_accuracy == 1.0

    def test_cross_level_keyed_target_resolves_on_a_sibling_pairing(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: mine
    levels:
      - number: 1
        areas:
          - key: "1"
      - number: 2
        areas:
          - key: "3"
    transitions:
      - {from_level: 1, from_key: "1", to_level: 2, to_key: "3", kind: stairs}
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "mine",
                    [
                        (
                            1,
                            [survey_area("1")],
                            [
                                content_area(
                                    "1", connections=(AreaConnection(to_key="3", direction="down", via="stairs"),)
                                )
                            ],
                        ),
                        (2, [survey_area("3")], [content_area("3")]),
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        assert metrics.transitions.true_positives == 1
        assert metrics.transitions.recall == 1.0 and metrics.transitions.precision == 1.0

    def test_level_shaped_stub_matches_on_source_and_level_number(self, tmp_path: Path):
        root = self._collapsed(
            tmp_path, {"2": (AreaConnection(to_key=None, direction="up", via="stairs", to_level=2),)}
        )
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        # The stub claims (source, level 2): the truth stair's landing key is
        # geometry's guess policy, not extraction's claim.
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 1
        assert metrics.transitions.kind_accuracy == 1.0

    def test_a_stub_never_matches_a_transition_from_another_source(self, tmp_path: Path):
        root = self._collapsed(
            tmp_path, {"1": (AreaConnection(to_key=None, direction="up", via="stairs", to_level=2),)}
        )
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        # The truth stair rises from "2"; a stub from "1" is a false positive.
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 0
        assert metrics.transitions.recall == 0.0 and metrics.transitions.precision == 0.0

    def test_no_target_mention_is_dropped(self, tmp_path: Path):
        root = self._collapsed(tmp_path, {"2": (AreaConnection(to_key=None, direction="up", via="stairs"),)})
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        # Geometry's `no target stated` discard, mirrored: no claim, no FP.
        assert metrics.transitions.extracted_transitions == 0
        assert metrics.transitions.recall == 0.0
        assert metrics.transitions.precision is None

    def test_an_unresolved_keyed_target_is_dropped(self, tmp_path: Path):
        root = self._collapsed(tmp_path, {"2": (AreaConnection(to_key="99", direction="up", via="stairs"),)})
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        # The connection F1's conservatism, mirrored: an unmatched target
        # never becomes a false positive.
        assert metrics.transitions.extracted_transitions == 0
        assert metrics.transitions.precision is None

    def test_an_unresolved_key_with_a_level_falls_back_to_stub_form(self, tmp_path: Path):
        root = self._collapsed(
            tmp_path,
            {"2": (AreaConnection(to_key="ninety-nine", direction="up", via="stairs", to_level=2),)},
        )
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 1

    def test_reciprocal_and_repeated_mentions_merge(self, tmp_path: Path):
        root = self._collapsed(
            tmp_path,
            {
                "2": (
                    AreaConnection(to_key="9", direction="up", via="stairs"),
                    AreaConnection(to_key="9", direction="up", via="stairs"),
                ),
                "9": (AreaConnection(to_key="2", direction="down", via="stairs"),),
            },
        )
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 1
        assert metrics.transitions.precision == 1.0

    def test_a_stub_merges_into_the_pair_form_claim_of_the_same_link(self, tmp_path: Path):
        root = self._collapsed(
            tmp_path,
            {
                "2": (
                    AreaConnection(to_key="9", direction="up", via="stairs"),
                    AreaConnection(to_key=None, direction="up", via="stairs", to_level=2),
                ),
            },
        )
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        # Same source endpoint, pair far endpoint on the stub's target level:
        # one physical link, the richer pair form wins.
        assert metrics.transitions.extracted_transitions == 1
        assert metrics.transitions.true_positives == 1

    def test_kind_conflicts_resolve_by_the_pinned_total_order(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: temple
    levels:
      - number: 1
        areas:
          - key: "1"
          - key: "2"
      - number: 2
        areas:
          - key: "9"
          - key: "10"
    transitions:
      - {from_level: 1, from_key: "2", to_level: 2, to_key: "9", kind: chute}
"""
        )
        root = self._collapsed(
            tmp_path,
            {
                "2": (AreaConnection(to_key="9", direction="down", via="chute"),),
                "9": (AreaConnection(to_key="2", direction="up", via="trapdoor"),),
            },
        )
        metrics = score_workdir(root, truth)
        # trapdoor > chute: the resolved attribute misses the printed chute,
        # while endpoint matching still lands the true positive.
        assert metrics.transitions.true_positives == 1
        assert metrics.transitions.kind_matched == 0
        assert metrics.transitions.kind_accuracy == 0.0

    def test_same_truth_level_target_is_a_same_level_edge_only(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2"]
          - key: "2"
    transitions: []
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
                                    "1", connections=(AreaConnection(to_key="2", direction="down", via="stairs"),)
                                ),
                                content_area("2"),
                            ],
                        )
                    ],
                )
            ],
        )
        metrics = score_workdir(root, truth)
        # One extraction claim is never scored in two families: the printed
        # same-level stair is a connection edge and only that.
        assert metrics.connections.extracted_edges == 1
        assert metrics.connections.f1 == 1.0
        assert metrics.transitions.asserted_dungeons == 1
        assert metrics.transitions.extracted_transitions == 0
        assert metrics.transitions.recall is None and metrics.transitions.precision is None

    def test_a_cross_level_target_without_a_vertical_signal_is_neither(self, tmp_path: Path):
        root = self._collapsed(tmp_path, {"2": (AreaConnection(to_key="9", direction="north", via="passage"),)})
        metrics = score_workdir(root, self.COLLAPSED_TRUTH)
        # Crediting it as a transition would score a claim extraction never
        # made; the F1 machinery already ignores the cross-pairing target.
        assert metrics.transitions.extracted_transitions == 0
        assert metrics.transitions.recall == 0.0
        assert metrics.connections.extracted_edges == 0

    def test_an_unasserted_dungeon_contributes_nothing(self, tmp_path: Path):
        truth = truth_from_yaml(
            """
dungeons:
  - name: temple
    levels:
      - number: 1
        areas:
          - key: "1"
          - key: "2"
      - number: 2
        areas:
          - key: "9"
          - key: "10"
"""
        )
        root = self._collapsed(tmp_path, {"2": (AreaConnection(to_key="9", direction="up", via="stairs"),)})
        metrics = score_workdir(root, truth)
        assert metrics.transitions.asserted_dungeons == 0
        assert metrics.transitions.truth_transitions == 0
        assert metrics.transitions.extracted_transitions == 0
        assert metrics.transitions.recall is None and metrics.transitions.precision is None


class TestEntranceMetrics:
    """The entrance family: geometry's positional heuristic reproduced from the survey index alone."""

    ENTRANCE_TRUTH = truth_from_yaml(
        """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
          - key: "2"
    entrance:
      level: 1
      key: "1"
"""
    )

    def test_first_listed_area_of_the_lowest_level_matches(self, tmp_path: Path):
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("1"), survey_area("2")], [content_area("1"), content_area("2")])])],
        )
        metrics = score_workdir(root, self.ENTRANCE_TRUTH)
        assert metrics.entrances.asserted == 1
        assert metrics.entrances.matched == 1
        assert metrics.entrances.accuracy == 1.0

    def test_a_mislisted_first_area_misses(self, tmp_path: Path):
        # The survey lists "2" first: the heuristic picks it, the truth says "1".
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [survey_area("2"), survey_area("1")], [content_area("2"), content_area("1")])])],
        )
        metrics = score_workdir(root, self.ENTRANCE_TRUTH)
        assert metrics.entrances.asserted == 1
        assert metrics.entrances.matched == 0
        assert metrics.entrances.accuracy == 0.0

    def test_unasserted_entrance_scores_nothing(self, tmp_path: Path):
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
        assert metrics.entrances.asserted == 0
        assert metrics.entrances.accuracy is None

    def test_selection_skips_empty_levels(self, tmp_path: Path):
        # Geometry's rule verbatim: the lowest-numbered level *with any
        # areas*; an empty survey level never holds the entrance.
        truth = truth_from_yaml(
            """
dungeons:
  - name: lair
    levels:
      - number: 2
        areas:
          - key: "1"
    entrance:
      level: 2
      key: "1"
"""
        )
        root = fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [("lair", [(1, [], []), (2, [survey_area("1")], [content_area("1")])])],
        )
        metrics = score_workdir(root, truth)
        assert metrics.entrances.asserted == 1
        assert metrics.entrances.matched == 1


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
    # The seven-family fixture exercises every family — doors, transitions,
    # and the entrance included — through the byte-stability check.
    root = seven_family_workdir(tmp_path)
    first = score_workdir(root, SEVEN_FAMILY_TRUTH)
    second = score_workdir(root, SEVEN_FAMILY_TRUTH)
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


STALE_RUN_BLOCK = {
    "date": "2026-07-17",
    "model_id": "gpt-5.4-2026-03-05",
    "osrforge_version": "0.1.0",
    "input_tokens": 307478,
    "output_tokens": 47175,
    "usd": 1.4763,
}


def stale_entry(input_tokens: int = 307478) -> dict[str, object]:
    """A pre-extension scoreboard entry: a metrics shape the current models refuse."""
    return {
        "run": {**STALE_RUN_BLOCK, "input_tokens": input_tokens},
        "truth_sha256": "00" * 32,
        "settings_overrides": ["render_dpi=300"],
        "metrics": {"note": "the pre-phase-9 four-family shape"},
    }


class TestRescore:
    """The offline regeneration mechanism: same runs, re-scored — raw read in, one validated save out."""

    def _corpus(self, tmp_path: Path, entries: dict[str, dict[str, object]]) -> tuple[Path, Path]:
        """A corpus with one member directory (plus truth) per raw board entry."""
        corpus = tmp_path / "corpus"
        corpus.mkdir(parents=True)
        for module_id in entries:
            member = corpus / module_id
            member.mkdir()
            (member / "truth.yaml").write_text(PERFECT_TRUTH_YAML, encoding="utf-8")
        board_path = corpus / "scoreboard.json"
        board_path.write_text(json.dumps({"schema_version": 1, "modules": entries}, indent=2) + "\n", encoding="utf-8")
        return corpus, board_path

    def _target(self, tmp_path: Path, corpus: Path, module_id: str) -> tuple[str, Path, Path]:
        """One rescore target: the module id, a matching fabricated workdir, and its truth path."""
        return (module_id, perfect_workdir(tmp_path / module_id), corpus / module_id / "truth.yaml")

    def test_run_block_carried_verbatim_and_truth_sha_repinned(self, tmp_path: Path):
        corpus, board_path = self._corpus(tmp_path, {"mod": stale_entry()})
        target = self._target(tmp_path, corpus, "mod")
        results = rescore_modules(board_path, [target])
        board = load_scoreboard(board_path)
        entry = board.modules["mod"]
        # The entry stays a record of the run: the run's own date, tokens,
        # cost, model id, and package version — never today's.
        assert entry.run == RunInfo.model_validate(STALE_RUN_BLOCK)
        assert entry.settings_overrides == ("render_dpi=300",)
        assert entry.truth_sha256 == hashlib.sha256(target[2].read_bytes()).hexdigest()
        assert entry.truth_sha256 != "00" * 32
        assert entry.metrics == results["mod"]
        assert results["mod"] == score_workdir(target[1], PERFECT_TRUTH)

    def test_a_stale_board_reads_raw(self, tmp_path: Path):
        corpus, board_path = self._corpus(tmp_path, {"mod": stale_entry()})
        # The stale shape is exactly what the extended models refuse — the
        # raw-JSON read is what makes regeneration possible at all.
        with pytest.raises(ValidationError):
            load_scoreboard(board_path)
        rescore_modules(board_path, [self._target(tmp_path, corpus, "mod")])
        assert load_scoreboard(board_path).modules["mod"].run.date == "2026-07-17"

    def test_a_whole_stale_board_migrates_in_one_save(self, tmp_path: Path):
        # The committed-board migration shape: every entry predates the model
        # extension, so the board can only become valid whole — all three
        # rebuild in one invocation and the board saves once.
        entries = {"mod-a": stale_entry(1), "mod-b": stale_entry(2), "mod-c": stale_entry(3)}
        corpus, board_path = self._corpus(tmp_path, entries)
        targets = [self._target(tmp_path, corpus, module_id) for module_id in ("mod-a", "mod-b", "mod-c")]
        results = rescore_modules(board_path, targets)
        board = load_scoreboard(board_path)
        assert list(results) == ["mod-a", "mod-b", "mod-c"]
        assert set(board.modules) == {"mod-a", "mod-b", "mod-c"}
        for module_id, tokens in (("mod-a", 1), ("mod-b", 2), ("mod-c", 3)):
            # Each entry carried its own run block, not a neighbor's.
            assert board.modules[module_id].run.input_tokens == tokens
            assert board.modules[module_id].metrics == results[module_id]

    def test_a_leftover_stale_entry_refuses_the_save_by_name(self, tmp_path: Path):
        corpus, board_path = self._corpus(
            tmp_path, {"mod": stale_entry(), "other": stale_entry(), "third": stale_entry()}
        )
        before = board_path.read_bytes()
        # "other" and "third" stay stale in the rebuilt board: the loud
        # refusal names them, and the file is untouched — the whole-board
        # pin working, not an obstacle.
        with pytest.raises(ValueError, match="stale entries: other, third"):
            rescore_modules(board_path, [self._target(tmp_path, corpus, "mod")])
        assert board_path.read_bytes() == before

    def test_refuses_a_module_with_no_existing_entry(self, tmp_path: Path):
        corpus, board_path = self._corpus(tmp_path, {"other": stale_entry()})
        (corpus / "mod").mkdir()
        with pytest.raises(ValueError, match="no existing entry"):
            rescore_modules(board_path, [self._target(tmp_path, corpus, "mod")])

    def test_refuses_a_repeated_module_id(self, tmp_path: Path):
        corpus, board_path = self._corpus(tmp_path, {"mod": stale_entry()})
        target = self._target(tmp_path, corpus, "mod")
        with pytest.raises(ValueError, match="name each entry once"):
            rescore_modules(board_path, [target, target])

    def test_refuses_a_missing_board(self, tmp_path: Path):
        corpus, board_path = self._corpus(tmp_path, {"mod": stale_entry()})
        board_path.unlink()
        with pytest.raises(ValueError, match="no scoreboard"):
            rescore_modules(board_path, [self._target(tmp_path, corpus, "mod")])

    def test_saved_board_keeps_the_pinned_byte_format(self, tmp_path: Path):
        corpus, board_path = self._corpus(tmp_path, {"mod": stale_entry()})
        rescore_modules(board_path, [self._target(tmp_path, corpus, "mod")])
        first = board_path.read_bytes()
        save_scoreboard(board_path, load_scoreboard(board_path))
        assert board_path.read_bytes() == first


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

        Optionality (unpinned sha256, absent license, partial truth) exists
        for private BYOM corpora; every committed member must keep the full
        posture — plus the backfilled truth provenance — or the corpus
        scoreboard's meaning silently changes. The phase 9 posture, in full:
        `encounters` asserted on every area (the asserted-empty convention),
        `doors` on every asserted-connections area, and `transitions` and
        `entrance` on every dungeon — a new family that can thin without a
        failure here isn't enforced.
        """
        members = sorted(child.name for child in CORPUS.iterdir() if child.is_dir())
        for member in members:
            manifest = load_manifest(CORPUS / member / "manifest.yaml")
            assert manifest.sha256 is not None, member
            assert manifest.license is not None, member
            assert manifest.truth_provenance is not None, member
            truth = load_truth(CORPUS / member / "truth.yaml")
            for dungeon in truth.dungeons:
                assert dungeon.transitions is not None, (member, dungeon.name)
                assert dungeon.entrance is not None, (member, dungeon.name)
                for level in dungeon.levels:
                    for area in level.areas:
                        assert area.treasure is not None, (member, dungeon.name, area.key)
                        assert area.encounters is not None, (member, dungeon.name, area.key)
                        if area.connections is not None:
                            assert area.doors is not None, (member, dungeon.name, area.key)

    def test_truth_templates_exist_in_the_catalog(self):
        from osrlib.data import load_monsters

        ids = {template.id for template in load_monsters().monsters}
        for member in sorted(child.name for child in CORPUS.iterdir() if child.is_dir()):
            truth = load_truth(CORPUS / member / "truth.yaml")
            for dungeon in truth.dungeons:
                for level in dungeon.levels:
                    for area in level.areas:
                        for creature in area.encounters or ():
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

    # The phase 9 families, blessed over the committed test caches against the
    # phase 9 truth pass (the existing four families above are byte-identical
    # to their pre-phase-9 values — the truth-key freeze makes any movement
    # there a scorer bug by construction). The committed test caches predate
    # phase 6's door extraction, so every via is `passage` and the door family
    # reads zero extracted doors — recall 0.0 with an empty-denominator
    # precision is the honest pin for these caches, not a defect.
    assert metrics.encounters.precision_denominator == 107
    assert metrics.encounters.precision_matched == 100
    assert metrics.encounters.precision == 0.9346
    assert metrics.doors.truth_doors == 21
    assert metrics.doors.extracted_doors == 0
    assert metrics.doors.true_positives == 0
    assert metrics.doors.recall == 0.0
    assert metrics.doors.precision is None
    assert metrics.doors.kind_accuracy is None
    assert metrics.doors.locked_accuracy is None
    assert metrics.transitions.asserted_dungeons == 14
    assert metrics.transitions.truth_transitions == 1
    assert metrics.transitions.extracted_transitions == 1
    assert metrics.transitions.true_positives == 1
    assert metrics.transitions.recall == 1.0
    assert metrics.transitions.precision == 1.0
    assert metrics.transitions.kind_accuracy == 1.0
    assert metrics.entrances.asserted == 14
    assert metrics.entrances.matched == 13
    assert metrics.entrances.accuracy == 0.9286


class TestMapReconciledScoring:
    """The scorer's map-present path: the reconciled fact set entering the edge and entrance families.

    The offline hard check only proves the no-map path drift-free; these pin
    the merge's consumption — adopted doors, map-only pairs, the matched-slug
    filter, and the map-proposed entrance — through `score_workdir` itself,
    since the live sweep's go/no-go numbers flow through exactly this path.
    """

    MAP_TRUTH = truth_from_yaml(
        """
dungeons:
  - name: lair
    levels:
      - number: 1
        areas:
          - key: "1"
            connections: ["2", "3"]
            doors:
              "2": {kind: door}
          - key: "2"
            connections: ["1"]
            doors:
              "1": {kind: door}
          - key: "3"
            connections: ["1"]
            doors: {}
    entrance:
      level: 1
      key: "2"
"""
    )

    def _workdir(self, tmp_path: Path, extra_keys: tuple[str, ...] = ()) -> Path:
        # Prose states only 1→2, as a plain passage: the (1,3) edge, the
        # (1,2) door, and the entrance are exactly what the map must supply.
        keys = ("1", "2", "3", *extra_keys)
        return fabricate_eval_workdir(
            tmp_path / "mod.forge",
            [
                (
                    "lair",
                    [
                        (
                            1,
                            [survey_area(key) for key in keys],
                            [
                                content_area("1", connections=(AreaConnection(to_key="2", direction="north"),)),
                                *[content_area(key) for key in keys if key != "1"],
                            ],
                        )
                    ],
                )
            ],
        )

    def _write_reading(
        self,
        root: Path,
        proposals: tuple[MapEdgeProposal, ...],
        entrance_key: str | None,
    ) -> None:
        write_json_artifact(
            Workdir(root).mapread_json,
            MapReading(
                map_reading="read",
                levels=(
                    MapLevelReading(
                        dungeon_id="lair",
                        level_number=1,
                        map_pages=(4,),
                        status="read",
                        proposals=proposals,
                        entrance_key=entrance_key,
                    ),
                ),
            ),
        )

    def test_adopted_door_map_only_edge_and_map_entrance_complete_the_families(self, tmp_path: Path):
        root = self._workdir(tmp_path)
        # The prose-only baseline first: half the edges, no doors, the
        # positional entrance missing the printed way in.
        baseline = score_workdir(root, self.MAP_TRUTH)
        assert baseline.connections.recall == 0.5
        assert baseline.doors.extracted_doors == 0
        assert baseline.entrances.matched == 0
        self._write_reading(
            root,
            (MapEdgeProposal(a="1", b="2", door="door"), MapEdgeProposal(a="1", b="3", door="none")),
            entrance_key="2",
        )
        metrics = score_workdir(root, self.MAP_TRUTH)
        # The map-only (1,3) pair joins the extracted set inside the
        # asserted universe; the adopted (1,2) door enters the door family.
        assert metrics.connections.extracted_edges == 2
        assert metrics.connections.true_positives == 2
        assert metrics.connections.f1 == 1.0
        assert metrics.doors.extracted_doors == 1
        assert metrics.doors.true_positives == 1
        assert (metrics.doors.recall, metrics.doors.precision, metrics.doors.kind_accuracy) == (1.0, 1.0, 1.0)
        # The map-proposed entrance beats the heuristic and matches truth.
        assert metrics.entrances.matched == 1

    def test_a_map_only_door_the_truth_denies_is_a_scoreable_false_positive(self, tmp_path: Path):
        root = self._workdir(tmp_path)
        self._write_reading(
            root,
            (MapEdgeProposal(a="1", b="2", door="door"), MapEdgeProposal(a="1", b="3", door="secret_door")),
            entrance_key=None,
        )
        metrics = score_workdir(root, self.MAP_TRUTH)
        # (1,3) carries a map door the truth's complete door set denies:
        # recall holds at 1.0, precision drops to 1/2 — the union admitting
        # map-only doors is measured, not masked.
        assert metrics.doors.extracted_doors == 2
        assert metrics.doors.true_positives == 1
        assert (metrics.doors.recall, metrics.doors.precision) == (1.0, 0.5)

    def test_unmatched_endpoints_stay_outside_the_universe(self, tmp_path: Path):
        # "9" exists in the survey but not the truth: a map-only pair touching
        # it resolves at reconcile time yet must not enter the extracted set.
        root = self._workdir(tmp_path, extra_keys=("9",))
        self._write_reading(root, (MapEdgeProposal(a="2", b="9", door="none"),), entrance_key=None)
        metrics = score_workdir(root, self.MAP_TRUTH)
        assert metrics.connections.extracted_edges == 1  # the prose (1,2) alone
        assert metrics.doors.extracted_doors == 0

    def test_an_unread_reading_scores_prose_only(self, tmp_path: Path):
        root = self._workdir(tmp_path)
        baseline = score_workdir(root, self.MAP_TRUTH)
        write_json_artifact(
            Workdir(root).mapread_json,
            MapReading(
                map_reading="read",
                levels=(
                    MapLevelReading(
                        dungeon_id="lair",
                        level_number=1,
                        map_pages=(),
                        status="unread",
                        unread_reason="no_map_pages",
                        entrance_key="2",
                    ),
                ),
            ),
        )
        assert score_workdir(root, self.MAP_TRUTH) == baseline
