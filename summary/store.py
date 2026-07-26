import json
from pathlib import Path

from application.summary.models import SummaryGeneration


class SummaryStore:
    """Persist application-layer summaries without changing B's database."""

    def __init__(self, root: str | Path = "data/summaries") -> None:
        self.root = Path(root)

    def save(self, generation: SummaryGeneration) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{generation.target_date.isoformat()}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                generation.as_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def load(self, target_date) -> SummaryGeneration | None:
        path = self.root / f"{target_date.isoformat()}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return SummaryGeneration.from_dict(value)

    def clear(self) -> None:
        """Delete all generated daily summaries."""

        if not self.root.exists():
            return
        for pattern in ("*.json", "*.json.tmp"):
            for path in self.root.glob(pattern):
                if path.is_file():
                    path.unlink()
