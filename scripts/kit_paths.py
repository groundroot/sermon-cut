# Machine-specific paths for the kit, so the same scripts run on any Mac.
# Order: environment variable -> kit_paths.local.json at the kit root (git/zip-ignored, per Mac) -> default.
#   SILENCE_CUTTER   folder that contains the silence_cutter package (default: the kit's vendor/ copy)
#   FONT_LIBRARY     your own font library folder, one family per sub-folder (font_catalog.py index/install)
import json, os
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
_LOCAL = KIT / "kit_paths.local.json"
_local = json.loads(_LOCAL.read_text()) if _LOCAL.exists() else {}


def get(name, default=""):
    return os.environ.get(name) or _local.get(name) or str(default)


SILENCE_CUTTER = get("SILENCE_CUTTER", KIT / "vendor")
FONT_LIBRARY = get("FONT_LIBRARY")

# Final Cut Pro to open builds in, and the FCPXML version written for it (2026-11-19).
#   FCP_APP          the app bundle; default: the Creator Studio (subscription, 13.x) app if installed, else
#                    /Applications/Final Cut Pro.app. FCP 13 reads 1.14 and 1.15; 12.4 reads up to 1.14.
#   FCPXML_VERSION   default: the newest DTD the chosen app ships (1.15 for 13.0, 1.14 for 12.4)
import glob, plistlib


def _default_fcp():
    hits = sorted(glob.glob("/Applications/**/Final Cut Pro Creator Studio.app", recursive=True))
    return hits[0] if hits else "/Applications/Final Cut Pro.app"


FCP_APP = get("FCP_APP", _default_fcp())


def _plist(key, default=""):
    try:
        return plistlib.loads((Path(FCP_APP) / "Contents/Info.plist").read_bytes()).get(key, default)
    except OSError:
        return default


FCP_VERSION = _plist("CFBundleShortVersionString")
FCP_BUNDLE_ID = _plist("CFBundleIdentifier", "com.apple.FinalCut")


def _newest_dtd():
    dtds = glob.glob(str(Path(FCP_APP) / "Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv1_*.dtd"))
    vers = sorted((int(Path(d).stem.split("_")[1]) for d in dtds), reverse=True)
    return f"1.{vers[0]}" if vers else "1.14"


FCPXML_VERSION = get("FCPXML_VERSION", _newest_dtd())


def fcpxml_dtd(version=None):
    """Local copy of the DTD (no spaces in path, for xmllint); copied from the app on first use."""
    v = (version or FCPXML_VERSION).replace(".", "_")
    dst = Path.home() / f".cache/video-editor/FCPXMLv{v}.dtd"
    if not dst.exists():
        import shutil
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(FCP_APP) / f"Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv{v}.dtd", dst)
    return dst


# Sales layout (~/Movies/Claude Video Kit: System/ Projects/ Rules/ Assets/): scripts/project.py marks the current project.
# Without one (the seller's own kit) these return the given default, so nothing changes there.
def project_dir():
    cur = KIT / ".current_project"
    if cur.exists():
        p = KIT.parent / "Projects" / cur.read_text().strip()
        if p.is_dir():
            return p
    return None


def library_dir(default):
    """Where a job's .fcpbundle lives: the project folder in the sales layout."""
    return project_dir() or Path(default)


def library_path(default_dir, name):
    """The job's .fcpbundle. Sales layout: <project>/<project folder name>.fcpbundle, so the bundle (a folder) has the
    project's English name too (user, 2026-11-26: every folder name is English)."""
    p = project_dir()
    return p / f"{p.name}.fcpbundle" if p else Path(default_dir) / f"{name}.fcpbundle"


def output_dir(default):
    """Where finished files go: <project>/결과물 in the sales layout."""
    p = project_dir()
    return p / "Exports" if p else Path(default)
