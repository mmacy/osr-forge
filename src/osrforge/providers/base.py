"""The extraction stages' provider protocol and its request/response types.

Exactly one seam wide: a single `generate` method taking a structured-output
request. Schema enforcement is the provider's contract —
`generate` either returns `data` that validates against `request.schema` or
raises [`SchemaValidationError`][osrforge.errors.SchemaValidationError] after
its retry budget, so callers trust `response.data`.

The tracing stage uses two other protocols, each in its own module:
[`ReadingProvider`][osrforge.providers.reading.ReadingProvider] for Claude's
whole-map readings and judgments, and
[`DecisionProvider`][osrforge.providers.decisions.DecisionProvider] for
Clef's answers about one cell, edge, or key. The content part types here are
shared by all three.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import jsonschema

from osrforge.contracts.run import TokenUsage
from osrforge.errors import SchemaValidationError

__all__ = [
    "ImagePart",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "TextPart",
    "ensure_schema",
    "pixel_digest",
]

_TAG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class TextPart:
    """One ordered text content part of a request.

    A page's extracted text layer, or prompt text like a batch header.
    """

    text: str


@dataclass(frozen=True)
class ImagePart:
    """One ordered image content part of a request: a page render, a map image, or a crop.

    The bytes are always PNG. Preprocessing and the tracing stage emit PNG,
    and each adapter does its own packaging: the Foundry adapter sends a
    base64 data URL, the Claude adapter a base64 image block, and the Clef
    adapter re-encodes a large crop as JPEG (see
    [`osrforge.providers.clef`][]).
    """

    png: bytes


def pixel_digest(png: bytes) -> str:
    """Return the sha256 hex digest of an image's decoded pixels.

    The digest covers the image's size and its pixels converted to RGB, so two
    PNG files that encode the same picture differently, or a PNG and the JPEG
    the Clef adapter made from it, have the same digest. The tracing
    providers' recording keys use it (see
    [`ReadingRequest.key`][osrforge.providers.reading.ReadingRequest.key] and
    [`DecisionRequest.key`][osrforge.providers.decisions.DecisionRequest.key]),
    where [`ModelRequest.fingerprint`][osrforge.providers.base.ModelRequest.fingerprint]
    hashes the PNG bytes.

    Args:
        png: The PNG bytes.

    Returns:
        A 64-character sha256 hex digest of `f"{width}x{height}:"` followed by
        the RGB pixel bytes, row by row.

    Raises:
        ValueError: If the bytes aren't an image Pillow can decode.
    """
    raise NotImplementedError("chunk: claude-provider")


@dataclass(frozen=True)
class ModelRequest:
    """One structured-output completion request.

    Attributes:
        tag: A short stable label like `survey` or `probe.image-limits` — a
            pipeline stage name or capability-probe id, never free prose. It names
            fixture files, attributes usage, and participates in the
            fingerprint (it is part of request identity).
        system: The system text.
        parts: The ordered text and image content parts.
        schema: The JSON Schema the response `data` must satisfy.
    """

    tag: str
    system: str
    parts: tuple[TextPart | ImagePart, ...]
    schema: dict[str, object]

    def __post_init__(self) -> None:
        if not _TAG_PATTERN.match(self.tag):
            raise ValueError(f"request tag must be a stable identifier ([A-Za-z0-9._-]+): {self.tag!r}")

    def fingerprint(self) -> str:
        """Return the request's identity hash, which names the extraction stages' fixtures.

        The fingerprint is the sha256 hex of the canonical JSON (sorted keys,
        compact separators, UTF-8) of the request with each image part replaced
        by `{"sha256": ..., "bytes": ...}`, so it hashes each image's PNG
        bytes. A re-encoded page render changes it. The tracing providers'
        recording keys hash decoded pixels instead
        ([`pixel_digest`][osrforge.providers.base.pixel_digest]), because the
        tracing stage draws its crops at run time and a different PNG encoder
        must still find the recording.

        Returns:
            A 64-character sha256 hex digest.
        """
        parts: list[dict[str, object]] = []
        for part in self.parts:
            if isinstance(part, TextPart):
                parts.append({"text": part.text})
            else:
                parts.append({"sha256": hashlib.sha256(part.png).hexdigest(), "bytes": len(part.png)})
        canonical = json.dumps(
            {"tag": self.tag, "system": self.system, "parts": parts, "schema": self.schema},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelResponse:
    """One completion's parsed, schema-valid result.

    Attributes:
        data: The parsed JSON, already validated against the request's schema.
        usage: Token consumption as the provider reported it.
        model_id: The model identifier the service returned.
    """

    data: object
    usage: TokenUsage
    model_id: str


def ensure_schema(data: object, schema: dict[str, object], context: str) -> None:
    """Validate response data against a request's JSON Schema.

    Args:
        data: The parsed response JSON.
        schema: The JSON Schema from the request.
        context: Where the data came from, for the error message (e.g. a tag or
            a fixture path).

    Raises:
        SchemaValidationError: If the data doesn't satisfy the schema.
    """
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as error:
        raise SchemaValidationError(f"response data for {context} failed schema validation: {error.message}") from error


@runtime_checkable
class ModelProvider(Protocol):
    """The one seam between the pipeline and any model vendor.

    Any object with a conforming `generate` method is a provider — no
    subclassing, no registration. The provider owns schema enforcement:
    validate with [`ensure_schema`][osrforge.providers.base.ensure_schema]
    (and retry as needed) before returning, so callers can trust
    `response.data`.

    Examples:
        ```python
        from osrforge.contracts.run import TokenUsage
        from osrforge.providers.base import ModelRequest, ModelResponse, ensure_schema

        class MyVendorProvider:
            def generate(self, request: ModelRequest) -> ModelResponse:
                data = my_vendor_call(request.system, request.parts, request.schema)
                ensure_schema(data, request.schema, request.tag)
                return ModelResponse(data=data, usage=TokenUsage(), model_id="my-model")
        ```
    """

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Run one structured-output completion.

        Args:
            request: System text, ordered content parts, and the JSON Schema
                the response must satisfy.

        Returns:
            The parsed, schema-validated response with token usage.

        Raises:
            ProviderError: On transport, auth, or rate-limit exhaustion.
            SchemaValidationError: If no schema-valid response was obtained
                within the provider's retry budget.
        """
        ...
