"""Public files for the previous web version. Run as a module to export the static site."""

from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT / "versions" / "previous"
FEATURES = (
    "home", "dashboard", "auth", "account", "evidence-collection",
    "evidence-management", "verification", "reports", "shared",
)
ASSET_FOLDERS = {"auth": "assets/auth", "reports": "assets/report"}


def public_files() -> dict[str, Path]:
    """Index feature files by the URLs used by existing links and bookmarks."""
    files = {}
    seen = set()

    def register(url: str, source: Path) -> None:
        if source.name.startswith(".") or not source.is_file():
            return
        if source.resolve() != source:
            raise ValueError(f"Public files must be inside their feature directory: {source}")
        key = url.casefold()
        if key in seen:
            raise ValueError(f"Duplicate public URL: {url} ({source})")
        seen.add(key)
        files[url] = source

    for feature in FEATURES:
        folder = PREVIOUS / feature
        for source in folder.iterdir():
            if source.suffix.lower() in {".html", ".css", ".js"}:
                register(source.name, source)
        if feature in ASSET_FOLDERS:
            for source in (folder / "assets").iterdir():
                url = f"{ASSET_FOLDERS[feature]}/{source.name}"
                if source.suffix.lower() in {".svg", ".ttf", ".txt"} or url == "assets/report/pdfmake.min.js":
                    register(url, source)
    return files


def export_static() -> Path:
    files = public_files()
    build = ROOT / ".build"
    destination = build / "previous"
    if destination.resolve() != destination:
        raise ValueError(f"The static output must be inside {build}")
    build.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=build) as temp:
        staged = Path(temp) / "site"
        staged.mkdir()
        for url, source in files.items():
            target = staged / url
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(source, target)
        backup = Path(temp) / "previous"
        if destination.exists():
            destination.rename(backup)
        try:
            staged.rename(destination)
        except OSError:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
    return destination


if __name__ == "__main__":
    print(export_static())
