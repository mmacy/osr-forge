"""The per-module working directory: layout paths, `run.json` I/O, and the artifact writer.

No other module builds workdir paths by hand, and every JSON artifact goes
through [`write_json_artifact`][osrforge.workdir.write_json_artifact] — pinning
the byte format once means the byte-stability tests that arrive with assembly
(phase 2) never chase formatting noise.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from osrforge.contracts.run import RunMeta

__all__ = ["Workdir", "write_json_artifact"]


def write_json_artifact(path: Path, artifact: BaseModel | Mapping[str, object]) -> None:
    """Write a JSON artifact in the pinned byte format.

    The format: `model_dump(mode="json")` for models, UTF-8, 2-space indent,
    keys in model-declaration (or mapping-insertion) order — no sorting;
    pydantic order is deterministic — and a trailing newline.

    Args:
        path: The destination file.
        artifact: A pydantic model, or an already-serialized mapping (osrlib's
            stamped `adventure.json` document is a plain dict).
    """
    data = artifact.model_dump(mode="json") if isinstance(artifact, BaseModel) else artifact
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")


class Workdir:
    """One conversion's working directory, owning the spec's layout.

    Attributes:
        root: The workdir root, e.g. `my-module.forge/`.
    """

    def __init__(self, root: Path) -> None:
        """Bind to a workdir root without touching the filesystem.

        Args:
            root: The workdir root directory.
        """
        self.root = root

    @property
    def source_pdf(self) -> Path:
        """The copied source module."""
        return self.root / "source.pdf"

    @property
    def run_json(self) -> Path:
        """The run metadata file."""
        return self.root / "run.json"

    @property
    def pages_dir(self) -> Path:
        """Per-page renders and text layers."""
        return self.root / "pages"

    @property
    def stages_dir(self) -> Path:
        """Cached raw model-stage outputs."""
        return self.root / "stages"

    @property
    def overrides_yaml(self) -> Path:
        """The human correction file."""
        return self.root / "overrides.yaml"

    @property
    def previews_dir(self) -> Path:
        """Rendered SVG level maps."""
        return self.root / "previews"

    @property
    def report_json(self) -> Path:
        """The extraction report."""
        return self.root / "report.json"

    @property
    def adventure_json(self) -> Path:
        """The stamped osrlib adventure document."""
        return self.root / "adventure.json"

    def page_png(self, page_number: int) -> Path:
        """Return the render path for a page.

        Args:
            page_number: The 1-based page number.

        Returns:
            `pages/NNNN.png`, zero-padded to 4 digits.
        """
        return self.pages_dir / f"{page_number:04d}.png"

    def page_txt(self, page_number: int) -> Path:
        """Return the text-layer path for a page.

        Args:
            page_number: The 1-based page number.

        Returns:
            `pages/NNNN.txt`, zero-padded to 4 digits.
        """
        return self.pages_dir / f"{page_number:04d}.txt"

    def read_run(self) -> RunMeta:
        """Load and validate `run.json`.

        Returns:
            The run metadata.
        """
        return RunMeta.model_validate_json(self.run_json.read_text(encoding="utf-8"))

    def write_run(self, run: RunMeta) -> None:
        """Write `run.json` in the pinned artifact format.

        Args:
            run: The run metadata to persist.
        """
        write_json_artifact(self.run_json, run)
