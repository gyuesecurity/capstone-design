"""Public URL maps and portable static exports for both web versions."""

from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
FEATURES = (
    "home", "dashboard", "auth", "account", "evidence-collection",
    "evidence-management", "verification", "reports", "shared",
)
ASSET_FOLDERS = {"auth": "assets/auth", "reports": "assets/report"}


def public_files(version: str = "previous") -> dict[str, Path]:
    """Index feature files by the URLs used by existing links and bookmarks."""
    if version not in ("previous", "page4"):
        raise ValueError(f"Unknown web version: {version}")
    source_root = ROOT / "versions" / version
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
        folder = source_root / feature
        for source in folder.iterdir():
            if source.suffix.lower() in {".html", ".css", ".js"}:
                register(source.name, source)
        if feature in ASSET_FOLDERS:
            for source in (folder / "assets").iterdir():
                url = f"{ASSET_FOLDERS[feature]}/{source.name}"
                if source.suffix.lower() in {".svg", ".ttf", ".txt"} or url == "assets/report/pdfmake.min.js":
                    register(url, source)
    if version == "page4":
        register("assets/auth/NotoSansKR-UI.woff", source_root / "auth/assets/NotoSansKR-UI.woff")
        for folder, prefix in (("shared/assets/brand", "brand"), ("evidence-collection/assets", "evidence")):
            for source in (source_root / folder).glob("*.svg"):
                register(f"assets/{prefix}/{source.name}", source)
    return files


def export_static(version: str = "previous") -> Path:
    files = public_files(version)
    build = ROOT / ".build"
    destination = build / version
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
        backup = Path(temp) / "backup"
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
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", choices=("previous", "page4"), nargs="?", default="previous")
    print(export_static(parser.parse_args().version))
