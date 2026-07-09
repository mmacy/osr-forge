from pathlib import Path

import pytest

from osrforge.contracts.run import TokenUsage
from osrforge.errors import FixtureMissError, SchemaValidationError
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, ModelResponse, TextPart
from osrforge.providers.fixtures import FixtureProvider, RecordingProvider

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}

# Golden fingerprint: locks the canonicalization algorithm (sorted keys, compact
# separators, UTF-8, image parts as sha256+size). If this changes, every
# recorded fixture in the repo is stranded — that's a schema-version event.
GOLDEN_FINGERPRINT = "8289717b09561838034b07bdd0706cae3262bcb412218d6a08328643c81db76d"


def make_request(**kwargs) -> ModelRequest:
    defaults = {
        "tag": "survey",
        "system": "You are a surveyor.",
        "parts": (TextPart(text="page one"), ImagePart(png=b"not-really-a-png")),
        "schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    }
    defaults.update(kwargs)
    return ModelRequest(**defaults)


class StubProvider:
    def __init__(self, response: ModelResponse):
        self.response = response
        self.calls: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return self.response


def test_fingerprint_golden():
    assert make_request().fingerprint() == GOLDEN_FINGERPRINT


def test_fingerprint_ignores_dict_key_order():
    reordered = make_request(
        schema={"required": ["title"], "properties": {"title": {"type": "string"}}, "type": "object"}
    )
    assert reordered.fingerprint() == GOLDEN_FINGERPRINT


def test_fingerprint_changes_with_image_bytes():
    same_length = make_request(parts=(TextPart(text="page one"), ImagePart(png=b"not-really-a-pnh")))
    assert same_length.fingerprint() != GOLDEN_FINGERPRINT
    identical = make_request(parts=(TextPart(text="page one"), ImagePart(png=b"not-really-a-png")))
    assert identical.fingerprint() == GOLDEN_FINGERPRINT


def test_fingerprint_changes_with_tag():
    assert make_request(tag="survey2").fingerprint() != GOLDEN_FINGERPRINT


def test_fingerprint_changes_with_part_order():
    swapped = make_request(parts=(ImagePart(png=b"not-really-a-png"), TextPart(text="page one")))
    assert swapped.fingerprint() != GOLDEN_FINGERPRINT


def test_tag_must_be_a_stable_identifier():
    for tag in ("", "free prose tag", "tag/with/slashes"):
        with pytest.raises(ValueError):
            make_request(tag=tag)
    make_request(tag="probe.image-limits_01")  # stage names and probe ids pass


def test_record_then_replay_round_trip(tmp_path: Path):
    request = make_request()
    response = ModelResponse(
        data={"title": "The Example Barrow"},
        usage=TokenUsage(input_tokens=100, output_tokens=10),
        model_id="gpt-5.4",
    )
    inner = StubProvider(response)
    recorded = RecordingProvider(inner, tmp_path).generate(request)
    assert recorded == response

    fixture_file = tmp_path / f"survey.{GOLDEN_FINGERPRINT[:12]}.json"
    assert fixture_file.is_file()

    replayed = FixtureProvider(tmp_path).generate(request)
    assert replayed == response


def test_recording_is_idempotent_by_fingerprint(tmp_path: Path):
    request = make_request()
    response = ModelResponse(data={"title": "x"}, usage=TokenUsage(), model_id="gpt-5.4")
    provider = RecordingProvider(StubProvider(response), tmp_path)
    provider.generate(request)
    first_bytes = (tmp_path / f"survey.{GOLDEN_FINGERPRINT[:12]}.json").read_bytes()
    provider.generate(request)
    assert (tmp_path / f"survey.{GOLDEN_FINGERPRINT[:12]}.json").read_bytes() == first_bytes
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_fixture_digest_is_human_reviewable(tmp_path: Path):
    import json

    request = make_request()
    response = ModelResponse(data={"title": "x"}, usage=TokenUsage(), model_id="gpt-5.4")
    RecordingProvider(StubProvider(response), tmp_path).generate(request)
    fixture = json.loads((tmp_path / f"survey.{GOLDEN_FINGERPRINT[:12]}.json").read_text(encoding="utf-8"))
    assert fixture["fingerprint"] == GOLDEN_FINGERPRINT
    assert fixture["tag"] == "survey"
    assert fixture["request"]["system"] == "You are a surveyor."
    assert fixture["request"]["parts"][0] == {"text": "page one"}
    image_digest = fixture["request"]["parts"][1]
    assert set(image_digest) == {"sha256", "bytes"}
    assert image_digest["bytes"] == len(b"not-really-a-png")


def test_fixture_miss_names_the_pinned_diagnostics(tmp_path: Path):
    response = ModelResponse(data={"title": "x"}, usage=TokenUsage(), model_id="gpt-5.4")
    RecordingProvider(StubProvider(response), tmp_path).generate(make_request(tag="probe.trivial"))

    request = make_request()
    with pytest.raises(FixtureMissError) as excinfo:
        FixtureProvider(tmp_path).generate(request)
    message = str(excinfo.value)
    assert "survey" in message
    assert request.fingerprint() in message
    assert str(tmp_path) in message
    assert "probe.trivial" in message


def test_replay_revalidates_against_incoming_schema(tmp_path: Path):
    # Record a response that satisfies the recorded schema, then replay with a
    # changed schema: the stale fixture must fail loudly, not answer wrongly.
    response = ModelResponse(data={"title": "x"}, usage=TokenUsage(), model_id="gpt-5.4")
    RecordingProvider(StubProvider(response), tmp_path).generate(make_request())

    changed = make_request(schema={"type": "object", "properties": {"rooms": {"type": "array"}}, "required": ["rooms"]})
    provider = FixtureProvider(tmp_path)
    # The schema participates in the fingerprint, so a changed schema first
    # surfaces as a miss; replaying a hand-relocated stale fixture must fail
    # schema validation instead.
    with pytest.raises(FixtureMissError):
        provider.generate(changed)

    stale = tmp_path / f"survey.{make_request().fingerprint()[:12]}.json"
    relocated = tmp_path / f"survey.{changed.fingerprint()[:12]}.json"
    import json

    fixture = json.loads(stale.read_text(encoding="utf-8"))
    fixture["fingerprint"] = changed.fingerprint()
    relocated.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        provider.generate(changed)


def test_both_providers_satisfy_the_protocol(tmp_path: Path):
    response = ModelResponse(data={}, usage=TokenUsage(), model_id="m")
    assert isinstance(FixtureProvider(tmp_path), ModelProvider)
    assert isinstance(RecordingProvider(StubProvider(response), tmp_path), ModelProvider)
    assert isinstance(StubProvider(response), ModelProvider)
