"""Recorded-fixture providers: replay for tests, recording for the spike and evals.

Fixture files are named `<tag>.<fingerprint[:12]>.json` and carry the artifact
schema version, the full fingerprint, the tag, a human-reviewable request
digest (system text and text parts verbatim, images as sha256 + size — so
fixture diffs are readable in PRs), and the full response.
"""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from osrforge.contracts.run import TokenUsage
from osrforge.errors import FixtureMissError, ProviderError
from osrforge.providers.base import ModelProvider, ModelRequest, ModelResponse, TextPart, ensure_schema
from osrforge.versioning import SCHEMA_VERSION
from osrforge.workdir import write_json_artifact

__all__ = ["FixtureProvider", "RecordingProvider"]


class _FixtureResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data: object
    usage: TokenUsage
    model_id: str


class _Fixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    fingerprint: str
    tag: str
    request: dict[str, object]
    response: _FixtureResponse


def _fixture_filename(tag: str, fingerprint: str) -> str:
    return f"{tag}.{fingerprint[:12]}.json"


def _request_digest(request: ModelRequest) -> dict[str, object]:
    parts: list[dict[str, object]] = []
    for part in request.parts:
        if isinstance(part, TextPart):
            parts.append({"text": part.text})
        else:
            parts.append({"sha256": hashlib.sha256(part.png).hexdigest(), "bytes": len(part.png)})
    return {"system": request.system, "parts": parts}


class FixtureProvider:
    """Replays recorded request/response fixtures — zero network, zero cost."""

    def __init__(self, fixture_dir: Path) -> None:
        """Bind to a directory of recorded fixtures.

        Args:
            fixture_dir: The directory containing `<tag>.<fp12>.json` files.
        """
        self.fixture_dir = fixture_dir

    def _recorded_tags(self) -> list[str]:
        # Filenames are <tag>.<fp12>.json and tags may themselves contain dots.
        return sorted({path.name.rsplit(".", 2)[0] for path in self.fixture_dir.glob("*.json")})

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Replay the fixture matching the request's fingerprint.

        Replayed data is re-validated against the incoming request's schema, so
        a prompt or schema change against stale fixtures fails as a clear
        `SchemaValidationError`, not a silent wrong answer.

        Args:
            request: The request to replay.

        Returns:
            The recorded response.

        Raises:
            FixtureMissError: If no fixture matches the fingerprint.
            SchemaValidationError: If the recorded data fails the incoming
                request's schema.
            ProviderError: If the fixture file's schema version is not this
                package's.
        """
        fingerprint = request.fingerprint()
        path = self.fixture_dir / _fixture_filename(request.tag, fingerprint)
        if not path.is_file():
            raise FixtureMissError(
                f"no fixture for tag {request.tag!r} with fingerprint {fingerprint} in {self.fixture_dir} "
                f"(recorded tags: {self._recorded_tags() or 'none'})"
            )
        fixture = _Fixture.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if fixture.schema_version != SCHEMA_VERSION:
            raise ProviderError(
                f"fixture {path} has schema version {fixture.schema_version}, expected {SCHEMA_VERSION}"
            )
        if fixture.fingerprint != fingerprint:
            raise FixtureMissError(
                f"fixture {path} records fingerprint {fixture.fingerprint}, but the request's is {fingerprint} "
                f"(recorded tags: {self._recorded_tags()})"
            )
        ensure_schema(fixture.response.data, request.schema, str(path))
        return ModelResponse(
            data=fixture.response.data,
            usage=fixture.response.usage,
            model_id=fixture.response.model_id,
        )


class RecordingProvider:
    """A pass-through that writes each exchange as a replayable fixture file.

    This is how the spike records real fixtures, and later how evals re-record.
    Writes are idempotent by fingerprint — re-recording an identical request
    overwrites its fixture in place.
    """

    def __init__(self, inner: ModelProvider, fixture_dir: Path) -> None:
        """Wrap a real provider and record its exchanges.

        Args:
            inner: The provider that actually answers.
            fixture_dir: Where fixture files land; created if missing.
        """
        self.inner = inner
        self.fixture_dir = fixture_dir

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate through the inner provider and persist the exchange.

        Args:
            request: The request to run and record.

        Returns:
            The inner provider's response, unchanged.
        """
        response = self.inner.generate(request)
        fingerprint = request.fingerprint()
        fixture = _Fixture(
            schema_version=SCHEMA_VERSION,
            fingerprint=fingerprint,
            tag=request.tag,
            request=_request_digest(request),
            response=_FixtureResponse(data=response.data, usage=response.usage, model_id=response.model_id),
        )
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(self.fixture_dir / _fixture_filename(request.tag, fingerprint), fixture)
        return response
