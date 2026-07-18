"""Custom-template emission: the refusal ladder, the mapping anchors, bundling, and the override kind."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from osrlib.core.monsters import MonsterHitDice
from osrlib.core.tables import monster_xp, thac0_for_hd
from osrlib.data import load_classes, load_combat_tables, load_monsters
from osrlib.versioning import check_document

from osrforge.assemble import assemble, emit_custom_templates, map_stat_block, usable_stat_block
from osrforge.contracts.overrides import Overrides
from osrforge.contracts.report import ExtractionReport
from osrforge.contracts.run import Stage
from osrforge.contracts.stages import MonsterResolution, MonsterResolutions, RawStatBlock, StatBlocks
from osrforge.errors import OverrideError
from osrforge.overrides import apply_template_overrides, plan_template_overrides
from osrforge.workdir import Workdir, write_json_artifact
from test_assemble import assembled_workdir, encounter, make_area, make_level

COMBAT_TABLES = load_combat_tables()
CATALOG_IDS = frozenset(template.id for template in load_monsters().monsters)


def block(**overrides: Any) -> RawStatBlock:
    """An OSE-shaped usable block; override fields per case."""
    base: dict[str, Any] = {
        "ac": "5 [14]",
        "ac_notation": "dual",
        "hit_dice": "3+1",
        "hp": 14,
        "attacks": ["1 bite (1d6)"],
        "movement": "120' (40')",
        "saves": "D12 W13 P14 B16 S18 (2)",
        "morale": 8,
        "alignment": "Chaotic",
        "xp": 75,
        "number_appearing": "1d6 (2d6)",
        "special": [],
        "confidence": 0.9,
        "source_pages": [7],
    }
    base.update(overrides)
    return RawStatBlock.model_validate(base)


class TestUsability:
    """The refusal ladder's eligibility predicate: AC plus HD-or-class-level."""

    def test_absent_marker_refuses(self):
        assert usable_stat_block(None) is False

    def test_no_ac_refuses(self):
        assert usable_stat_block(block(ac=None)) is False

    def test_unparseable_ac_refuses(self):
        assert usable_stat_block(block(ac="see below")) is False

    def test_no_hd_and_no_class_level_refuses(self):
        assert usable_stat_block(block(hit_dice=None, class_level=None)) is False

    def test_ac_plus_hd_is_usable(self):
        assert usable_stat_block(block()) is True

    def test_ac_plus_class_level_is_usable(self):
        assert usable_stat_block(block(hit_dice=None, class_level="F 3")) is True

    @pytest.mark.parametrize("notation", ["F 0", "T0", "0th-level cleric"])
    def test_zero_level_notation_refuses_in_both_forms(self, notation: str):
        # A 0-level notation carries no combat math to derive: it must fall to
        # the refusal ladder, never into mapping (which is total only over
        # accepted parses) — the duck-found "F 0" crash, pinned.
        assert usable_stat_block(block(hit_dice=None, class_level=notation)) is False


class TestAcAnchors:
    def test_dual_notation_is_printed_verbatim(self):
        template, derived = map_stat_block("worm", "worm", block())
        assert (template.ac, template.ac_ascending) == (5, 14)
        assert "ac" not in derived

    def test_descending_complements_by_19(self):
        template, derived = map_stat_block("worm", "worm", block(ac="5", ac_notation="descending"))
        assert (template.ac, template.ac_ascending) == (5, 14)
        assert "ac" in derived

    def test_ascending_complements_by_19(self):
        template, derived = map_stat_block("worm", "worm", block(ac="14", ac_notation="ascending"))
        assert (template.ac, template.ac_ascending) == (5, 14)
        assert "ac" in derived

    def test_unclassified_single_value_reads_descending(self):
        template, derived = map_stat_block("worm", "worm", block(ac="6", ac_notation=None))
        assert (template.ac, template.ac_ascending) == (6, 13)
        assert "ac" in derived


class TestHitDiceAnchors:
    def test_plain_count_rolls_d8_with_printed_hp_as_average(self):
        template, _ = map_stat_block("worm", "worm", block(hit_dice="3", hp=13))
        assert template.hit_dice == MonsterHitDice(count=3, die=8, average_hp=13)

    def test_modifiers_carry_as_printed(self):
        template, _ = map_stat_block("worm", "worm", block(hit_dice="1-1", hp=None))
        assert (template.hit_dice.count, template.hit_dice.modifier) == (1, -1)

    def test_fractional_maps_to_count_zero_with_printed_hp(self):
        template, derived = map_stat_block("worm", "worm", block(hit_dice="½", hp=2))
        assert (template.hit_dice.count, template.hit_dice.fixed_hp) == (0, 2)
        assert "fixed_hp" not in derived

    def test_fractional_without_hp_defaults_fixed_hp_to_3(self):
        template, derived = map_stat_block("worm", "worm", block(hit_dice="1/2", hp=None))
        assert template.hit_dice.fixed_hp == 3
        assert "fixed_hp" in derived

    def test_uncarriable_die_keeps_count_on_d8_with_printed_hp_fixed(self):
        template, derived = map_stat_block("worm", "worm", block(hit_dice="3d6", hp=11))
        assert template.hit_dice == MonsterHitDice(count=3, die=8, fixed_hp=11)
        assert "hit_dice" in derived

    def test_printed_d4_carries(self):
        template, _ = map_stat_block("worm", "worm", block(hit_dice="1d4", hp=None))
        assert template.hit_dice.die == 4

    def test_printed_asterisks_win_over_special_lines(self):
        template, _ = map_stat_block("worm", "worm", block(hit_dice="3*", special=["a", "b"]))
        assert template.hit_dice.asterisks == 1

    def test_special_lines_supply_asterisks_when_unprinted(self):
        template, _ = map_stat_block("worm", "worm", block(hit_dice="3", special=["paralysis", "acid"]))
        assert template.hit_dice.asterisks == 2


class TestClassLevelAnchors:
    def test_class_level_maps_as_hd_equal_to_level(self):
        template, derived = map_stat_block("npc", "npc", block(hit_dice=None, class_level="F 3", hp=15, saves=None))
        assert template.hit_dice == MonsterHitDice(count=3, die=8, fixed_hp=15)
        assert "hit_dice" in derived
        assert template.thac0 == thac0_for_hd(3)[0]
        row = next(row for row in load_classes().get("fighter").progression if row.level == 3)
        assert template.saves.values == row.saves
        assert template.saves.save_as == "F3"
        assert "saves" in derived

    @pytest.mark.parametrize(
        ("notation", "class_id", "level", "save_as"),
        [
            ("C3", "cleric", 3, "C3"),
            ("mu 4", "magic_user", 4, "MU4"),
            ("3rd-level cleric", "cleric", 3, "C3"),
            ("human magic-user 4", "magic_user", 4, "MU4"),
            ("Thief 2", "thief", 2, "T2"),
        ],
    )
    def test_printed_notations(self, notation: str, class_id: str, level: int, save_as: str):
        template, _ = map_stat_block("npc", "npc", block(hit_dice=None, class_level=notation, saves=None))
        assert template.hit_dice.count == level
        row = next(row for row in load_classes().get(class_id).progression if row.level == level)
        assert template.saves.values == row.saves
        assert template.saves.save_as == save_as

    def test_xp_derives_from_the_monster_tables_on_the_level(self):
        template, derived = map_stat_block("npc", "npc", block(hit_dice=None, class_level="F 3", xp=None, hp=15))
        assert template.xp == monster_xp(COMBAT_TABLES, template.hit_dice)
        assert "xp" in derived


class TestThac0Anchors:
    @pytest.mark.parametrize(
        ("text", "thac0", "bonus"), [("17", 17, 2), ("+2", 17, 2), ("19 [+0]", 19, 0), ("AB +2", 17, 2)]
    )
    def test_printed_forms(self, text: str, thac0: int, bonus: int):
        template, derived = map_stat_block("worm", "worm", block(thac0=text))
        assert (template.thac0, template.attack_bonus) == (thac0, bonus)
        assert "thac0" not in derived

    def test_unprinted_derives_from_hd_with_the_bonus_modifier_rule(self):
        template, derived = map_stat_block("worm", "worm", block(hit_dice="3+1", thac0=None))
        assert (template.thac0, template.attack_bonus) == thac0_for_hd(3, bonus_modifier=True)
        assert "thac0" in derived


class TestSaveAnchors:
    def test_printed_values_with_printed_save_as(self):
        template, derived = map_stat_block("worm", "worm", block())
        assert (template.saves.values.death, template.saves.values.spells) == (12, 18)
        assert template.saves.save_as == "2"
        assert "saves" not in derived

    def test_printed_values_without_save_as_derive_the_band_label(self):
        template, derived = map_stat_block("worm", "worm", block(saves="D12 W13 P14 B16 S18"))
        assert template.saves.save_as == "1–3"  # noqa: RUF001 — osrlib's band labels print en dashes
        assert "save_as" in derived

    def test_save_as_class_form_uses_the_class_table(self):
        template, _ = map_stat_block("worm", "worm", block(saves="save as F2"))
        row = next(row for row in load_classes().get("fighter").progression if row.level == 2)
        assert template.saves.values == row.saves
        assert template.saves.save_as == "F2"

    @pytest.mark.parametrize("text", ["Sv F2", "Saves: F2", "F2"])
    def test_labelled_and_bare_bfrpg_forms_are_printed_save_as(self, text: str):
        template, derived = map_stat_block("worm", "worm", block(saves=text))
        row = next(row for row in load_classes().get("fighter").progression if row.level == 2)
        assert template.saves.values == row.saves
        assert template.saves.save_as == "F2"
        assert "saves" not in derived

    def test_fort_ref_will_is_discarded_and_derived_from_hd(self):
        template, derived = map_stat_block("worm", "worm", block(saves="Fort +2, Ref +4, Will +1", hit_dice="5"))
        band = next(band for band in COMBAT_TABLES.monster_saves if band.label == "4–6")  # noqa: RUF001
        assert template.saves.values == band.saves
        assert template.saves.save_as == "4–6"  # noqa: RUF001
        assert "saves" in derived

    def test_absent_saves_derive_from_hd(self):
        template, derived = map_stat_block("worm", "worm", block(saves=None, hit_dice="7"))
        assert template.saves.save_as == "7–9"  # noqa: RUF001
        assert "saves" in derived


class TestRemainingAnchors:
    def test_morale_defaults_to_7(self):
        template, derived = map_stat_block("worm", "worm", block(morale=None))
        assert template.morale == 7
        assert "morale" in derived

    @pytest.mark.parametrize(("text", "value"), [("C", "chaotic"), ("Lawful", "lawful"), ("N", "neutral")])
    def test_alignment_letters(self, text: str, value: str):
        template, derived = map_stat_block("worm", "worm", block(alignment=text))
        assert template.alignment.options[0].value == value
        assert "alignment" not in derived

    def test_unparseable_alignment_defaults_to_neutral(self):
        template, derived = map_stat_block("worm", "worm", block(alignment="any"))
        assert template.alignment.options[0].value == "neutral"
        assert "alignment" in derived

    def test_xp_derives_with_one_asterisk_per_special_line(self):
        template, derived = map_stat_block("worm", "worm", block(xp=None, hit_dice="3", special=["acid touch"]))
        assert template.xp == monster_xp(COMBAT_TABLES, MonsterHitDice(count=3, asterisks=1))
        assert "xp" in derived

    def test_number_appearing_pair_fills_dungeon_then_lair(self):
        template, _ = map_stat_block("worm", "worm", block(number_appearing="1d6 (3d6)"))
        assert template.number_appearing.dungeon.dice == "1d6"
        assert template.number_appearing.lair.dice == "3d6"

    def test_number_appearing_range_converts_like_attack_ranges(self):
        template, _ = map_stat_block("worm", "worm", block(number_appearing="2-8"))
        assert template.number_appearing.dungeon.dice == "2d4"
        assert template.number_appearing.lair.dice == "2d4"

    def test_unprinted_number_appearing_fills_from_the_max_fixed_keyed_count(self):
        template, derived = map_stat_block("worm", "worm", block(number_appearing=None), max_keyed_count=4)
        assert template.number_appearing.dungeon.fixed == 4
        assert template.number_appearing.lair.fixed == 4
        assert "number_appearing" in derived

    def test_movement_pair_is_printed_verbatim(self):
        template, derived = map_stat_block("worm", "worm", block())
        assert (template.movement[0].rate_feet, template.movement[0].encounter_rate_feet) == (120, 40)
        assert "movement" not in derived

    def test_lone_rate_derives_the_encounter_rate_by_thirds(self):
        template, derived = map_stat_block("worm", "worm", block(movement="30'"))
        assert (template.movement[0].rate_feet, template.movement[0].encounter_rate_feet) == (30, 10)
        assert "movement" in derived

    def test_descriptor_mode_keeps_the_printed_word(self):
        template, _ = map_stat_block("worm", "worm", block(movement="Fly 180' (60')"))
        assert template.movement[0].descriptor == "fly"

    def test_no_movement_defaults_to_the_human_norm(self):
        template, derived = map_stat_block("worm", "worm", block(movement=None))
        assert (template.movement[0].rate_feet, template.movement[0].encounter_rate_feet) == (120, 40)
        assert "movement" in derived

    def test_attack_line_with_two_items_is_one_routine(self):
        template, _ = map_stat_block("worm", "worm", block(attacks=["2 claws (1d4), 1 bite (1d8)"]))
        (routine,) = template.attacks
        assert [(attack.count, attack.name, attack.damage) for attack in routine.attacks] == [
            (2, "claws", "1d4"),
            (1, "bite", "1d8"),
        ]

    def test_or_separates_alternative_routines(self):
        template, _ = map_stat_block("worm", "worm", block(attacks=["1 weapon (1d8) or 2 arrows (1d6)"]))
        assert len(template.attacks) == 2

    def test_range_damage_converts_to_dice(self):
        template, _ = map_stat_block("worm", "worm", block(attacks=["1 sting (1-10)"]))
        assert template.attacks[0].attacks[0].damage == "1d10"

    def test_offset_range_damage_converts_to_the_exact_uniform_form(self):
        # The TSR 2-7 print is 1d6+1 exactly — a conversion, not a guess.
        template, derived = map_stat_block("worm", "worm", block(attacks=["1 claw (2-7)"]))
        assert template.attacks[0].attacks[0].damage == "1d6+1"
        assert "attacks" not in derived

    def test_unconvertible_range_damage_is_flagged_never_truncated(self):
        # 3-7 fits neither conversion rule: the attack keeps its printed name
        # with no damage and the miss is recorded — the low end never becomes
        # silent flat damage.
        template, derived = map_stat_block("worm", "worm", block(attacks=["1 claw (3-7)"]))
        attack = template.attacks[0].attacks[0]
        assert (attack.damage, attack.fixed_damage) == (None, None)
        assert "attacks" in derived

    def test_bfrpg_dam_form_parses(self):
        template, derived = map_stat_block("worm", "worm", block(attacks=["1 bite, Dam 1d8"]))
        (routine,) = template.attacks
        assert (routine.attacks[0].count, routine.attacks[0].name, routine.attacks[0].damage) == (1, "bite", "1d8")
        assert "attacks" not in derived

    def test_printed_effect_keywords_land_on_the_attack(self):
        template, _ = map_stat_block("worm", "worm", block(attacks=["6 tentacles, Dam paralysis"]))
        attack = template.attacks[0].attacks[0]
        assert attack.damage is None
        assert attack.effects == ("paralysis",)

    def test_bare_counted_attack_keeps_the_name_and_records_the_missing_damage(self):
        template, derived = map_stat_block("worm", "worm", block(attacks=["1 spear"]))
        attack = template.attacks[0].attacks[0]
        assert (attack.name, attack.damage, attack.fixed_damage) == ("spear", None, None)
        assert "attacks" in derived

    def test_no_parseable_attack_maps_with_empty_attacks_and_a_derived_record(self):
        template, derived = map_stat_block("worm", "worm", block(attacks=["see below"]))
        assert template.attacks == ()
        assert "attacks" in derived

    def test_page_is_the_first_source_page(self):
        template, _ = map_stat_block("worm", "worm", block(source_pages=[7, 9]))
        assert template.page == "p. 7"

    def test_treasure_is_always_the_empty_ref_and_recorded_derived(self):
        template, derived = map_stat_block("worm", "worm", block())
        assert template.treasure.letters == ()
        assert "treasure" in derived

    def test_special_lines_become_manual_abilities(self):
        template, _ = map_stat_block("worm", "worm", block(special=["Paralysing touch: save or freeze"]))
        (ability,) = template.abilities
        assert ability.manual is True
        assert "Paralysing touch" in ability.prose

    def test_unusable_block_is_programmer_misuse(self):
        with pytest.raises(ValueError, match="usable_stat_block"):
            map_stat_block("worm", "worm", block(ac=None))


class TestEmission:
    def test_unresolved_name_with_usable_block_emits_and_resolves_custom(self):
        resolutions = MonsterResolutions(
            resolutions={"tentacle worm": MonsterResolution(template_id=None, method="unresolved")}
        )
        replaced, emitted = emit_custom_templates(resolutions, {"tentacle worm": block()}, (), (), CATALOG_IDS)
        assert replaced.resolutions["tentacle worm"] == MonsterResolution(template_id="tentacle_worm", method="custom")
        assert emitted["tentacle worm"].template.id == "tentacle_worm"
        assert emitted["tentacle worm"].source_pages == (7,)

    def test_absent_and_unusable_blocks_stay_unresolved(self):
        resolutions = MonsterResolutions(
            resolutions={
                "gone": MonsterResolution(template_id=None, method="unresolved"),
                "weak": MonsterResolution(template_id=None, method="unresolved"),
            }
        )
        replaced, emitted = emit_custom_templates(
            resolutions, {"gone": None, "weak": block(ac=None)}, (), (), CATALOG_IDS
        )
        assert emitted == {}
        assert replaced.resolutions["gone"].template_id is None
        assert replaced.resolutions["weak"].template_id is None

    def test_id_collision_with_the_catalog_appends_a_numeric_suffix(self):
        resolutions = MonsterResolutions(
            resolutions={"goblin!": MonsterResolution(template_id=None, method="unresolved")}
        )
        replaced, _ = emit_custom_templates(resolutions, {"goblin!": block()}, (), (), CATALOG_IDS)
        assert replaced.resolutions["goblin!"].template_id == "goblin_2"

    def test_sibling_collisions_suffix_deterministically(self):
        resolutions = MonsterResolutions(
            resolutions={
                "ice-worm": MonsterResolution(template_id=None, method="unresolved"),
                "ice worm": MonsterResolution(template_id=None, method="unresolved"),
            }
        )
        replaced, _ = emit_custom_templates(
            resolutions, {"ice-worm": block(), "ice worm": block()}, (), (), CATALOG_IDS
        )
        # Population is sorted: "ice worm" precedes "ice-worm".
        assert replaced.resolutions["ice worm"].template_id == "ice_worm"
        assert replaced.resolutions["ice-worm"].template_id == "ice_worm_2"

    def test_forced_resolved_name_emits_over_the_pick(self):
        resolutions = MonsterResolutions(
            resolutions={"primordial titan": MonsterResolution(template_id="normal_human", method="llm")}
        )
        replaced, emitted = emit_custom_templates(
            resolutions, {"primordial titan": block()}, ("primordial titan",), (), CATALOG_IDS
        )
        assert replaced.resolutions["primordial titan"].method == "custom"
        assert "primordial titan" in emitted

    def test_forced_name_with_unusable_block_drops_the_rejected_pick(self):
        resolutions = MonsterResolutions(
            resolutions={"primordial titan": MonsterResolution(template_id="normal_human", method="llm")}
        )
        replaced, emitted = emit_custom_templates(
            resolutions, {"primordial titan": block(ac=None)}, ("primordial titan",), (), CATALOG_IDS
        )
        assert emitted == {}
        assert replaced.resolutions["primordial titan"] == MonsterResolution(template_id=None, method="unresolved")


class TestTemplateOverrides:
    def cache(self, **names: MonsterResolution) -> MonsterResolutions:
        return MonsterResolutions(resolutions=dict(names))

    def test_patch_applies_pre_mapping(self):
        entries = plan_template_overrides(
            Overrides.model_validate(
                {"monster_templates": {"worm": {"ac": "3 [16]", "reason": "printed AC is 3, extraction read 8"}}}
            ),
            self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
        )
        blocks = apply_template_overrides({"worm": block(ac="8", ac_notation="descending")}, entries)
        patched = blocks["worm"]
        assert patched is not None and patched.ac == "3 [16]"
        assert patched.hit_dice == "3+1"  # untouched fields keep the extracted values

    def test_null_clears_a_field_back_to_unprinted(self):
        entries = plan_template_overrides(
            Overrides.model_validate({"monster_templates": {"worm": {"xp": None, "reason": "printed XP is wrong"}}}),
            self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
        )
        blocks = apply_template_overrides({"worm": block(xp=75)}, entries)
        patched = blocks["worm"]
        assert patched is not None and patched.xp is None

    def test_supplying_a_block_over_an_absent_marker_enables_emission(self):
        entries = plan_template_overrides(
            Overrides.model_validate(
                {
                    "monster_templates": {
                        "worm": {"ac": "5 [14]", "hit_dice": "2", "reason": "block printed in the appendix"}
                    }
                }
            ),
            self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
        )
        blocks = apply_template_overrides({"worm": None}, entries)
        assert usable_stat_block(blocks["worm"])

    def test_same_name_in_both_kinds_is_contradictory(self):
        with pytest.raises(OverrideError, match="contradictory"):
            plan_template_overrides(
                Overrides.model_validate(
                    {
                        "monsters": {"worm": {"template_id": "goblin", "reason": "remap"}},
                        "monster_templates": {"Worm": {"ac": "5", "reason": "patch"}},
                    }
                ),
                self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
            )

    def test_two_keys_normalizing_to_one_name_are_contradictory(self):
        with pytest.raises(OverrideError, match="both normalize"):
            plan_template_overrides(
                Overrides.model_validate(
                    {
                        "monster_templates": {
                            "worm": {"ac": "5", "reason": "one"},
                            "WORM": {"ac": "6", "reason": "two"},
                        }
                    }
                ),
                self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
            )

    def test_key_matching_no_extracted_name_is_loud(self):
        with pytest.raises(OverrideError, match="matches no extracted name"):
            plan_template_overrides(
                Overrides.model_validate({"monster_templates": {"wyrm": {"ac": "5", "reason": "typo"}}}),
                self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
            )

    def test_entry_with_only_a_reason_replaces_nothing(self):
        with pytest.raises(OverrideError, match="replaces nothing"):
            plan_template_overrides(
                Overrides.model_validate({"monster_templates": {"worm": {"reason": "no fields"}}}),
                self.cache(worm=MonsterResolution(template_id=None, method="unresolved")),
            )


def emission_workdir(root: Path, statblocks: StatBlocks | None, overrides_text: str | None = None) -> Workdir:
    """A workdir whose one level keys a resolved goblin and an unresolved tentacle worm."""
    workdir = assembled_workdir(root)
    write_json_artifact(
        workdir.areas_json("lair", 1),
        make_level([make_area("1", encounters=[encounter("goblin", fixed=2), encounter("Tentacle Worm", fixed=3)])]),
    )
    write_json_artifact(
        workdir.monsters_json,
        MonsterResolutions(
            resolutions={
                "goblin": MonsterResolution(template_id="goblin", method="exact"),
                "tentacle worm": MonsterResolution(template_id=None, method="unresolved"),
            }
        ),
    )
    if statblocks is not None:
        write_json_artifact(workdir.statblocks_json, statblocks)
    if overrides_text is not None:
        workdir.overrides_yaml.write_text(overrides_text, encoding="utf-8")
    return workdir


class TestAssemblyIntegration:
    def test_emitted_draft_bundles_validates_and_reports(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": block()})
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks)
        result = assemble(workdir.root)
        assert [template.id for template in result.adventure.monsters] == ["tentacle_worm"]
        assert result.report.validation.passed is True
        assert result.report.monsters.resolved == 2
        assert result.report.monsters.unresolved == ()
        (record,) = result.report.monsters.custom
        assert record.id == "tentacle_worm"
        assert record.name == "tentacle worm"
        assert record.source_pages == (7,)
        assert "thac0" in record.derived
        area_report = result.report.areas[0]
        assert "monster_custom:tentacle worm" in area_report.flags
        assert not any(flag.startswith("monster_unresolved") for flag in area_report.flags)
        document = json.loads(workdir.adventure_json.read_text(encoding="utf-8"))
        payload = cast(dict[str, Any], check_document(document, "adventure"))
        assert payload["monsters"][0]["id"] == "tentacle_worm"

    def test_missing_statblock_cache_assembles_without_emission(self, tmp_path: Path):
        # The pre-phase-7 workdir: no statblocks.json, no error, stand-in as before.
        workdir = emission_workdir(tmp_path / "mod.forge", None)
        result = assemble(workdir.root)
        assert result.adventure.monsters == ()
        assert result.report.monsters.unresolved == ("tentacle worm",)

    def test_off_echo_assembles_without_emission(self, tmp_path: Path):
        workdir = emission_workdir(tmp_path / "mod.forge", StatBlocks(custom_monsters="off", blocks={}))
        result = assemble(workdir.root)
        assert result.adventure.monsters == ()
        assert result.report.monsters.unresolved == ("tentacle worm",)
        assert result.report.monsters.custom == ()

    def test_emit_echo_with_a_missing_unresolved_name_is_stale(self, tmp_path: Path):
        workdir = emission_workdir(tmp_path / "mod.forge", StatBlocks(custom_monsters="emit", blocks={}))
        with pytest.raises(ValueError, match="stat-block cache is stale"):
            assemble(workdir.root)
        assert workdir.read_run().stages[Stage.GEOMETRY].status == "pending"

    def test_absent_marker_falls_to_the_stand_in_machinery(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": None})
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks)
        result = assemble(workdir.root)
        assert result.adventure.monsters == ()
        assert result.report.monsters.unresolved == ("tentacle worm",)
        assert any(flag.startswith("monster_unresolved:tentacle worm") for flag in result.report.areas[0].flags)

    def test_unreferenced_emission_stays_out_of_the_bundle(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": block()})
        overrides_text = (
            "areas:\n"
            "  lair/1/1:\n"
            "    encounter: {monsters: [{template_id: goblin, count_fixed: 1}]}\n"
            "    reason: the printed room keys goblins only\n"
        )
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks, overrides_text)
        result = assemble(workdir.root)
        assert result.adventure.monsters == ()
        assert result.report.monsters.custom == ()

    def test_template_override_forces_emission_over_a_resolved_name(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": block()})
        overrides_text = (
            "monster_templates:\n"
            "  goblin:\n"
            '    ac: "6 [13]"\n'
            '    hit_dice: "1"\n'
            "    reason: the module prints its own goblin variant\n"
        )
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks, overrides_text)
        result = assemble(workdir.root)
        assert sorted(template.id for template in result.adventure.monsters) == ["goblin_2", "tentacle_worm"]
        assert {record.id for record in result.report.monsters.custom} == {"goblin_2", "tentacle_worm"}

    def test_template_override_without_the_cache_is_loud(self, tmp_path: Path):
        overrides_text = 'monster_templates:\n  tentacle worm:\n    ac: "5"\n    reason: supply\n'
        workdir = emission_workdir(tmp_path / "mod.forge", None, overrides_text)
        with pytest.raises(OverrideError, match="re-run monsters"):
            assemble(workdir.root)

    def test_template_override_under_off_names_the_knob(self, tmp_path: Path):
        overrides_text = 'monster_templates:\n  tentacle worm:\n    ac: "5"\n    reason: supply\n'
        workdir = emission_workdir(tmp_path / "mod.forge", StatBlocks(custom_monsters="off", blocks={}), overrides_text)
        with pytest.raises(OverrideError, match="custom_monsters"):
            assemble(workdir.root)

    def test_remap_to_an_emitted_id_resolves(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": block()})
        overrides_text = (
            "monsters:\n  goblin:\n    template_id: tentacle_worm\n    reason: the goblins here are worms in disguise\n"
        )
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks, overrides_text)
        result = assemble(workdir.root)
        assert [template.id for template in result.adventure.monsters] == ["tentacle_worm"]
        assert result.report.validation.passed is True

    def test_emission_is_pure_and_byte_stable(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": block()})
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks)
        assemble(workdir.root)
        first_adventure = workdir.adventure_json.read_bytes()
        first_report = workdir.report_json.read_bytes()
        assemble(workdir.root)
        assert workdir.adventure_json.read_bytes() == first_adventure
        assert workdir.report_json.read_bytes() == first_report

    def test_supplied_block_reaches_emission(self, tmp_path: Path):
        statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": None})
        overrides_text = (
            "monster_templates:\n"
            "  tentacle worm:\n"
            '    ac: "5 [14]"\n'
            '    hit_dice: "3+1"\n'
            "    hp: 14\n"
            "    reason: the block is printed on p. 7; the pass missed it\n"
        )
        workdir = emission_workdir(tmp_path / "mod.forge", statblocks, overrides_text)
        result = assemble(workdir.root)
        assert [template.id for template in result.adventure.monsters] == ["tentacle_worm"]
        (record,) = result.report.monsters.custom
        assert record.source_pages == ()  # a supplied block has no transcription pages


def test_report_validates_under_the_contract(tmp_path: Path):
    statblocks = StatBlocks(custom_monsters="emit", blocks={"tentacle worm": block()})
    workdir = emission_workdir(tmp_path / "mod.forge", statblocks)
    assemble(workdir.root)
    report = ExtractionReport.model_validate_json(workdir.report_json.read_text(encoding="utf-8"))
    assert report.monsters.custom[0].id == "tentacle_worm"
