"""The Foundry capability spike probes. Manual, live-network; see tools/spike/README.md.

Each probe sends one or more requests through RecordingProvider(FoundryProvider)
so every observation lands as a fixture file. Probes print their outcome and
token usage; a refusal or failure is itself a finding, so probe failures are
reported and the run continues.
"""

import argparse
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from osrforge.errors import OsrForgeError
from osrforge.preprocess import preprocess
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, TextPart
from osrforge.providers.fixtures import RecordingProvider
from osrforge.providers.foundry import FoundryProvider, FoundrySettings
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

REPLAY_PAGE_LIMIT = 8

TRIVIAL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

# Survey-shaped: nested arrays, enums, per-area objects — the shape phase 1's
# survey pass will actually use.
SURVEY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "string"}},
        "town": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"],
            "additionalProperties": False,
        },
        "dungeons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "levels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "number": {"type": "integer"},
                                "areas": {"type": "array", "items": {"$ref": "#/$defs/area"}},
                            },
                            "required": ["number", "areas"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "levels"],
                "additionalProperties": False,
            },
        },
        "monster_names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "hooks", "town", "dungeons", "monster_names"],
    "additionalProperties": False,
    "$defs": {
        "area": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "source_pages": {"type": "array", "items": {"type": "integer"}},
                "kind": {"type": "string", "enum": ["room", "corridor", "cave", "landmark", "other"]},
            },
            "required": ["key", "name", "source_pages", "kind"],
            "additionalProperties": False,
        }
    },
}

CONTENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "areas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "description": {"type": "string"},
                    "encounters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"monster": {"type": "string"}, "count": {"type": "string"}},
                            "required": ["monster", "count"],
                            "additionalProperties": False,
                        },
                    },
                    "treasure": {"type": "array", "items": {"type": "string"}},
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "to_key": {"type": "string"},
                                "direction": {
                                    "type": "string",
                                    "enum": ["north", "south", "east", "west", "up", "down", "unknown"],
                                },
                            },
                            "required": ["to_key", "direction"],
                            "additionalProperties": False,
                        },
                    },
                    "source_pages": {"type": "array", "items": {"type": "integer"}},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "key",
                    "description",
                    "encounters",
                    "treasure",
                    "connections",
                    "source_pages",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["areas"],
    "additionalProperties": False,
}


def stress_schema(enum_size: int, defs_depth: int) -> dict[str, object]:
    """A progressively larger/stranger schema: big enums and nested $defs."""
    defs: dict[str, object] = {
        "leaf": {
            "type": "object",
            "properties": {"tag": {"type": "string", "enum": [f"option_{i:04d}" for i in range(enum_size)]}},
            "required": ["tag"],
            "additionalProperties": False,
        }
    }
    for depth in range(defs_depth):
        inner = "leaf" if depth == 0 else f"nest_{depth - 1}"
        defs[f"nest_{depth}"] = {
            "type": "object",
            "properties": {
                "child": {"$ref": f"#/$defs/{inner}"},
                "items": {"type": "array", "items": {"$ref": f"#/$defs/{inner}"}},
            },
            "required": ["child", "items"],
            "additionalProperties": False,
        }
    root = "leaf" if defs_depth == 0 else f"nest_{defs_depth - 1}"
    return {
        "type": "object",
        "properties": {"root": {"$ref": f"#/$defs/{root}"}},
        "required": ["root"],
        "additionalProperties": False,
        "$defs": defs,
    }


class SpikeContext:
    def __init__(self, module_dir: Path, workdir: Path):
        self.module_dir = module_dir
        self.workdir = Workdir(workdir)
        self.pages_dir = module_dir / "pages"  # the committed, replay-grade page subset
        self.fixtures_dir = module_dir / "fixtures"

    @property
    def pdf(self) -> Path:
        pdfs = sorted(self.module_dir.glob("*.pdf"))
        if len(pdfs) != 1:
            sys.exit(f"expected exactly one PDF in {self.module_dir}, found {[p.name for p in pdfs]}")
        return pdfs[0]

    def provider(self) -> ModelProvider:
        return RecordingProvider(FoundryProvider(FoundrySettings.from_env()), self.fixtures_dir)

    def committed_page(self, number: int) -> ImagePart:
        path = self.pages_dir / f"{number:04d}.png"
        if not path.is_file():
            sys.exit(f"committed page {path} missing — run the prepare subcommand first")
        return ImagePart(png=path.read_bytes())

    def committed_text(self, number: int) -> str:
        return (self.pages_dir / f"{number:04d}.txt").read_text(encoding="utf-8")

    def workdir_page(self, number: int) -> ImagePart:
        return ImagePart(png=self.workdir.page_png(number).read_bytes())


def run_probe(context: SpikeContext, request: ModelRequest) -> bool:
    print(f"--- {request.tag}")
    try:
        response = context.provider().generate(request)
    except OsrForgeError as error:
        print(f"    FAILED (a finding, record it): {error}")
        return False
    except Exception:
        print("    UNEXPECTED FAILURE:")
        traceback.print_exc()
        return False
    print(f"    model_id: {response.model_id}")
    print(f"    usage: in={response.usage.input_tokens} out={response.usage.output_tokens}")
    print(f"    data: {str(response.data)[:400]}")
    return True


def cmd_prepare(context: SpikeContext, args: argparse.Namespace) -> None:
    run = preprocess(context.pdf, context.workdir.root, ConversionSettings())
    print(f"preprocessed {run.page_count} pages into {context.workdir.root}")
    pages = args.pages or list(range(1, min(run.page_count, REPLAY_PAGE_LIMIT) + 1))
    if len(pages) > REPLAY_PAGE_LIMIT:
        sys.exit(f"replay-grade page subset is capped at {REPLAY_PAGE_LIMIT} pages")
    context.pages_dir.mkdir(parents=True, exist_ok=True)
    for number in pages:
        for suffix in ("png", "txt"):
            shutil.copyfile(
                context.workdir.pages_dir / f"{number:04d}.{suffix}", context.pages_dir / f"{number:04d}.{suffix}"
            )
    print(f"committed replay-grade page subset {pages} to {context.pages_dir}")


def cmd_structured(context: SpikeContext, args: argparse.Namespace) -> None:
    page = context.committed_page(1)
    run_probe(
        context,
        ModelRequest(
            tag="probe.trivial",
            system="You answer questions about tabletop adventure module pages.",
            parts=(TextPart(text="What is this module's title?"), page),
            schema=TRIVIAL_SCHEMA,
        ),
    )
    run_probe(
        context,
        ModelRequest(
            tag="probe.survey-schema",
            system="You are surveying a tabletop adventure module. Fill the schema from the pages given.",
            parts=(page, context.committed_page(2)),
            schema=SURVEY_SCHEMA,
        ),
    )
    for enum_size, depth in [(16, 2), (128, 4), (512, 8), (2000, 16)]:
        ok = run_probe(
            context,
            ModelRequest(
                tag=f"probe.schema-stress-e{enum_size}-d{depth}",
                system="Reply with any document that satisfies the schema.",
                parts=(TextPart(text="Produce a minimal valid document."),),
                schema=stress_schema(enum_size, depth),
            ),
        )
        if not ok:
            print(f"    schema limit near enum={enum_size} depth={depth}")


def cmd_images(context: SpikeContext, args: argparse.Namespace) -> None:
    run = context.workdir.read_run()
    for count in (1, 4, 8, 16, 32):
        if count > run.page_count:
            print(f"--- skipping {count}-page probe: module has {run.page_count} pages")
            continue
        parts: tuple[TextPart | ImagePart, ...] = (
            TextPart(text=f"These are {count} pages of an adventure module. Name the highest page number shown."),
            *(context.workdir_page(n) for n in range(1, count + 1)),
        )
        ok = run_probe(
            context,
            ModelRequest(
                tag=f"probe.image-count-{count:02d}",
                system="You read adventure module page images.",
                parts=parts,
                schema=TRIVIAL_SCHEMA,
            ),
        )
        if not ok:
            print(f"    image-count ceiling near {count} pages")
            break
    # Per-page token cost by DPI (seeds phase 3's estimate heuristics): render
    # page 1 at each DPI into throwaway workdirs.
    for dpi in (100, 150, 200):
        with tempfile.TemporaryDirectory() as tmp:
            preprocess(context.pdf, Path(tmp) / "dpi.forge", ConversionSettings(render_dpi=dpi))
            png = (Path(tmp) / "dpi.forge" / "pages" / "0001.png").read_bytes()
        run_probe(
            context,
            ModelRequest(
                tag=f"probe.dpi-cost-{dpi}",
                system="You read adventure module page images.",
                parts=(TextPart(text="Name this page's most prominent heading."), ImagePart(png=png)),
                schema=TRIVIAL_SCHEMA,
            ),
        )


def cmd_context(context: SpikeContext, args: argparse.Namespace) -> None:
    run = context.workdir.read_run()

    def attempt(count: int) -> bool:
        parts: list[TextPart | ImagePart] = [
            TextPart(text=f"{count} pages of an adventure module: their text, then their images.")
        ]
        for n in range(1, count + 1):
            parts.append(TextPart(text=f"[page {n}]\n" + context.workdir.page_txt(n).read_text(encoding="utf-8")))
        parts.extend(context.workdir_page(n) for n in range(1, count + 1))
        return run_probe(
            context,
            ModelRequest(
                tag=f"probe.context-{count:02d}pages",
                system="You read whole adventure modules.",
                parts=tuple(parts),
                schema=TRIVIAL_SCHEMA,
            ),
        )

    if attempt(run.page_count):
        print(f"    context ceiling: the whole module ({run.page_count} pages) fits in one request")
        return
    # Bisect between the largest known-good count and the smallest known-bad one.
    known_good, known_bad = 0, run.page_count
    while known_bad - known_good > 1:
        mid = (known_good + known_bad) // 2
        if attempt(mid):
            known_good = mid
        else:
            known_bad = mid
    if known_good == 0:
        print("    even a single page failed — investigate before recording findings")
    else:
        print(f"    context ceiling: {known_good} pages fit; {known_bad} pages fail")


def cmd_extract(context: SpikeContext, args: argparse.Namespace) -> None:
    pages = sorted(int(p.stem) for p in context.pages_dir.glob("*.png"))
    parts: list[TextPart | ImagePart] = []
    for n in pages:
        parts.append(TextPart(text=f"[page {n}]\n" + context.committed_text(n)))
        parts.append(context.committed_page(n))
    run_probe(
        context,
        ModelRequest(
            tag="probe.extract-survey",
            system="Survey this adventure module excerpt: title, hooks, town, dungeons, levels, keyed areas "
            "with page locations, and every monster name that appears.",
            parts=tuple(parts),
            schema=SURVEY_SCHEMA,
        ),
    )
    run_probe(
        context,
        ModelRequest(
            tag="probe.extract-content",
            system="Extract the keyed areas on these pages: description, encounters, treasure, connections, "
            "source pages, and a self-assessed confidence in [0, 1] per area.",
            parts=tuple(parts),
            schema=CONTENT_SCHEMA,
        ),
    )


def cmd_auth(context: SpikeContext, args: argparse.Namespace) -> None:
    import os

    request = ModelRequest(
        tag="probe.auth",
        system="Reply with the single word 'ok'.",
        parts=(TextPart(text="Auth check."),),
        schema=TRIVIAL_SCHEMA,
    )
    if os.environ.get("OSRFORGE_FOUNDRY_API_KEY"):
        print("key auth:")
        run_probe(context, request)
        print("Entra auth: unset OSRFORGE_FOUNDRY_API_KEY and re-run this subcommand")
    else:
        print("Entra auth:")
        run_probe(context, request)
        print("key auth: set OSRFORGE_FOUNDRY_API_KEY and re-run this subcommand")


def main() -> None:
    # Shared options live on a parent parser so they parse after the
    # subcommand, exactly as the README documents the invocations.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--module-dir", type=Path, required=True, help="the fenced spike-module asset directory")
    common.add_argument("--workdir", type=Path, default=Path("spike-module.forge"), help="preprocess workdir")
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser(
        "prepare", parents=[common], help="preprocess the module and commit the replay-grade page subset"
    )
    prepare.add_argument("--pages", type=int, nargs="*", help=f"page numbers to commit (max {REPLAY_PAGE_LIMIT})")
    for name in ("structured", "images", "context", "extract", "auth"):
        subcommands.add_parser(name, parents=[common])
    args = parser.parse_args()

    context = SpikeContext(args.module_dir, args.workdir)
    if args.command != "prepare" and not context.workdir.run_json.is_file():
        sys.exit("run the prepare subcommand first")
    {
        "prepare": cmd_prepare,
        "structured": cmd_structured,
        "images": cmd_images,
        "context": cmd_context,
        "extract": cmd_extract,
        "auth": cmd_auth,
    }[args.command](context, args)


if __name__ == "__main__":
    main()
