"""Made-up releases of this checkout, for tests/windows_update.py:  python tests/fake_releases.py OUT
(good 9.9.9, good2 9.9.12, cant 9.9.10: needs a package that doesn't exist, dies 9.9.11: installs, but its server doesn't
start; own: this version as it is). Needs the `build` package."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1])


def make(name, version=None, change=None):
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "src"
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "dist", "node_modules", "tests"))
        init = src / "app" / "__init__.py"
        s = init.read_text(encoding="utf-8")
        version = version or re.search(r'__version__ = "([^"]+)"', s).group(1)
        init.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', s), encoding="utf-8")
        if change:
            change(src)
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(d), str(src)], check=True, stdout=subprocess.DEVNULL)
        wheel = next(d.glob("*.whl")).name
        shutil.copy(ROOT / "constraints.txt", d)
        shutil.copy(ROOT / "install.ps1", d)
        (d / "latest.json").write_text(json.dumps({"version": version, "wheel": wheel, "notes": "- made up"}), encoding="utf-8")
        print(d, wheel)


def cant(src):
    p = src / "pyproject.toml"
    s = p.read_text(encoding="utf-8")
    p.write_text(re.sub(r"(?m)^dependencies = \[", 'dependencies = [\n  "no-such-package-rb-xyz>=9",', s, count=1), encoding="utf-8")
    assert "no-such-package-rb-xyz" in p.read_text(encoding="utf-8")


def dies(src):
    p = src / "app" / "server.py"
    p.write_text("raise RuntimeError('this made-up release does not start')\n" + p.read_text(encoding="utf-8"), encoding="utf-8")


make("own")
make("good", "9.9.9")
make("cant", "9.9.10", cant)
make("dies", "9.9.11", dies)
make("good2", "9.9.12")
