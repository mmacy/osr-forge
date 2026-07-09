"""Replay the spike's replay-grade fixtures through FixtureProvider — zero network.

Replay-grade fixtures are reconstructable from the fixture file alone plus the
committed page assets: text parts and the schema live verbatim in the request
digest, and image parts resolve by sha256 against tests/assets/chaotic-caves/pages/.
Evidence-grade fixtures (the image-count, DPI, and context boundary probes)
reference uncommitted workdir renders and are deliberately not replayed.
"""

import hashlib
import json
from pathlib import Path

from osrforge.providers.base import ImagePart, ModelRequest, TextPart
from osrforge.providers.fixtures import FixtureProvider

MODULE_DIR = Path(__file__).parent / "assets" / "chaotic-caves"
FIXTURES_DIR = MODULE_DIR / "fixtures"
PAGES_DIR = MODULE_DIR / "pages"

REPLAY_GRADE_TAGS = {
    "probe.trivial",
    "probe.survey-schema",
    "probe.schema-stress-e16-d2",
    "probe.schema-stress-e128-d4",
    "probe.schema-stress-e512-d8",
    "probe.extract-survey",
    "probe.extract-content",
    "probe.auth-key",
    "probe.auth-entra",
}


def committed_images_by_sha() -> dict[str, bytes]:
    images = {}
    for path in PAGES_DIR.glob("*.png"):
        data = path.read_bytes()
        images[hashlib.sha256(data).hexdigest()] = data
    return images


def reconstruct_request(fixture: dict, images: dict[str, bytes]) -> ModelRequest:
    parts: list[TextPart | ImagePart] = []
    for part in fixture["request"]["parts"]:
        if "text" in part:
            parts.append(TextPart(text=part["text"]))
        else:
            parts.append(ImagePart(png=images[part["sha256"]]))
    return ModelRequest(
        tag=fixture["tag"],
        system=fixture["request"]["system"],
        parts=tuple(parts),
        schema=fixture["request"]["schema"],
    )


def test_every_replay_grade_fixture_replays():
    images = committed_images_by_sha()
    provider = FixtureProvider(FIXTURES_DIR)
    replayed = set()
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture["tag"] not in REPLAY_GRADE_TAGS:
            continue
        request = reconstruct_request(fixture, images)
        assert request.fingerprint() == fixture["fingerprint"], path.name
        response = provider.generate(request)
        assert response.data == fixture["response"]["data"], path.name
        assert response.model_id == fixture["response"]["model_id"]
        replayed.add(fixture["tag"])
    assert replayed == REPLAY_GRADE_TAGS


def test_replay_grade_image_parts_resolve_to_committed_pages():
    images = committed_images_by_sha()
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture["tag"] not in REPLAY_GRADE_TAGS:
            continue
        for part in fixture["request"]["parts"]:
            if "sha256" in part:
                assert part["sha256"] in images, f"{path.name} references an uncommitted page"


def test_evidence_grade_fixtures_are_marked_by_uncommitted_pages():
    # The boundary probes reference workdir renders that are deliberately not
    # committed; this pins the replay/evidence split so a future re-recording
    # that accidentally flips a tag's grade fails loudly.
    images = committed_images_by_sha()
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        tag = fixture["tag"]
        if tag in REPLAY_GRADE_TAGS:
            continue
        image_shas = [part["sha256"] for part in fixture["request"]["parts"] if "sha256" in part]
        unresolved = [sha for sha in image_shas if sha not in images]
        assert unresolved, f"{path.name} is fully reconstructable — promote it to REPLAY_GRADE_TAGS"
