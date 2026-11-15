"""Licence check of the installed dependencies, and the table of indirect dependencies for THIRD_PARTY.md.

    uv run python scripts/third_party.py --check
    uv run python scripts/third_party.py --table

Both start from `uv export --no-dev --no-hashes --format requirements-txt`, run from uv.lock alone
(--frozen --offline), so they need neither the network nor the user's uv cache. They then take the npm
packages of the Remotion project, every package of scenes/package-lock.json but the project itself and dev
packages, with the licence the lock records for each. The licence of every
package is read from the metadata of the copy installed in the interpreter that runs this script:
License-Expression, else the License field (when it names a licence and is not the whole text of one),
else the License classifiers.

--check passes a package when its licence is on the allow list (MIT, BSD, Apache-2.0, ISC, PSF,
MPL-2.0, Unlicense, 0BSD, Zlib, HPND, LGPL). Any other package must have a row in the indirect
dependency section of THIRD_PARTY.md for the installed version, whose 能否商業使用 column says 可, put there
after somebody read the licence of that version on the official page. A row for another version of the
package does not count: an upgrade can change the licence. --check lists every package that does not pass
and exits with 1. An npm package passes the same way; one whose lock entry has no licence passes only
with such a row, and an optional one that is not installed under scenes/node_modules (a binary for another
platform) is skipped with a note.

--table prints the indirect dependencies as the Markdown table of that section. A package whose
licence is off the allow list gets 待查 in the 能否商業使用 column, to be changed to 可 once it was checked.
"""

import argparse
import datetime
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "THIRD_PARTY.md"
NPM_LOCK = ROOT / "scenes" / "package-lock.json"
NPM_USE = "Remotion 專案（scenes/）的 npm 套件"  # the 用途 cell of an npm package in --table

INDIRECT_HEADING = "間接相依"  # the section of the register that --check reads and --table fills
HEADER = "| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |"
USABLE, NOT_CHECKED, NO_LICENCE = "可", "待查", "未標示"
VERSION_COLUMN = 1  # position of 版本或來源 in a row
USABLE_COLUMN = 3  # position of 能否商業使用 in a row
MAX_LICENCE_FIELD = 100  # a License field longer than this, or on several lines, is a licence text, not a name

# The allow list, matched against a licence name with everything but letters and digits left out and
# in lower case, so that "BSD 3-Clause License", "BSD-3-Clause" and "bsd_3_clause" are one name.
_ALLOWED = [re.compile(pattern) for pattern in (
    r"(the)?mit(license)?",
    r"(the)?(new|simplified|modified)?bsd([23]clause)?(license)?|0bsd|bsdzeroclause(license)?",
    r"apache(software)?(license)?(version)?(20?)?(license)?",
    r"isc(license)?(iscl)?",
    r"(psf|python|pythonsoftwarefoundation)(license)?(20?1?)?",
    r"mpl(20?)?|mozillapubliclicense(20?)?(mpl20?)?",
    r"(the)?unlicense(unlicense)?",
    r"zlib(libpng)?(license)?",
    r"hpnd|historicalpermissionnoticeanddisclaimer(hpnd)?",
    r"(gnu)?(lesser|library)generalpubliclicense(v?[23]\d?)?(only|orlater)?(lgpl(v?[23])?)?"
    r"|lgpl(v?[23]\d?)?(only|orlater)?",
)]
_OPERATOR = re.compile(r"\b(AND|OR|WITH)\b|[()]", re.IGNORECASE)
_TOKEN = re.compile(r"\(|\)|[^\s()]+")


class CheckError(Exception):
    """Something this script needs is missing (uv, the lock file); --check reports it and exits with 1."""


class Requirement(NamedTuple):
    name: str
    version: str
    marker: str  # the part after ";" in the export, "" when there is none
    via: tuple[str, ...]  # the packages that ask for it; "kacha" is this project


class NpmPackage(NamedTuple):
    name: str
    version: str
    licence: str  # "" when the lock records none
    optional: bool
    path: str  # the key of the package in the lock, e.g. node_modules/remotion


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---- licences ---------------------------------------------------------------------------------


def _name_is_allowed(name: str) -> bool:
    letters = re.sub(r"[^a-z0-9]+", "", name.lower())
    return any(pattern.fullmatch(letters) for pattern in _ALLOWED)


def _expression_is_allowed(text: str) -> bool:
    """An SPDX licence expression: names joined by AND and OR (AND binds tighter), parentheses, and
    WITH an exception, which leaves the verdict of the licence before it as it is."""
    tokens = _TOKEN.findall(text)
    position = 0

    def peek() -> str:
        return tokens[position].upper() if position < len(tokens) else ""

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise ValueError("expression ends too early")
        position += 1
        return tokens[position - 1]

    def term() -> bool:
        token = take()
        if token == "(":
            value = either()
            if take() != ")":
                raise ValueError("missing )")
            return value
        if token.upper() in ("AND", "OR", "WITH") or token == ")":
            raise ValueError(f"unexpected {token}")
        value = _name_is_allowed(token.removesuffix("+"))
        if peek() == "WITH":
            take()
            take()
        return value

    def both() -> bool:
        value = term()
        while peek() == "AND":
            take()
            value = term() and value
        return value

    def either() -> bool:
        value = both()
        while peek() == "OR":
            take()
            value = both() or value
        return value

    try:
        value = either()
    except ValueError:
        return False
    return value and position == len(tokens)


def is_allowed(text: str) -> bool:
    """Whether a licence, as a name or an SPDX expression, is on the allow list."""
    text = text.strip()
    if not text or text.count("(") != text.count(")"):
        return False
    return _name_is_allowed(text) or (bool(_OPERATOR.search(text)) and _expression_is_allowed(text))


def licence_claims(meta) -> list[str]:
    """What the metadata of a package says its licence is: the License-Expression alone, or else the
    License field (not when it is a whole licence text) and every License classifier."""
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return [expression]
    claims: list[str] = []
    field = (meta.get("License") or "").strip()
    if field and "\n" not in field and len(field) <= MAX_LICENCE_FIELD:
        claims.append(field)
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            name = classifier.split(" :: ")[-1].strip()
            if name != "OSI Approved":  # the classifier of the group, it names no licence
                claims.append(name)
    return list(dict.fromkeys(claims))


# ---- requirements and register ----------------------------------------------------------------

_REQUIREMENT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;]+)\s*(?:;\s*(.*))?")


def parse_requirements(text: str) -> list[Requirement]:
    """The packages of `uv export` output with their version, marker and the packages they come via."""
    found: list[list] = []  # name, version, marker, via (as a list)
    current: list | None = None  # the requirement whose annotation is being read
    reading_via = False
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):  # an annotation of the requirement above
            body = line.strip().lstrip("#").strip()
            if current is not None and body.startswith("via"):
                rest = body[3:].strip()
                reading_via = not rest
                if rest:
                    current[3].append(rest)
            elif current is not None and reading_via and body:
                current[3].append(body)
            continue
        reading_via = False
        match = _REQUIREMENT.fullmatch(line.strip())
        current = [match.group(1), match.group(2), (match.group(3) or "").strip(), []] if match else None
        if current is not None:
            found.append(current)
    return [Requirement(name, version, marker, tuple(via)) for name, version, marker, via in found]


def direct_names(pyproject: Path) -> set[str]:
    """The names of the dependencies pyproject.toml asks for, in canonical form."""
    dependencies = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    return {canonical(re.split(r"[=<>!~\[;\s]", item.strip(), maxsplit=1)[0]) for item in dependencies}


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def register_rows(markdown: str) -> dict[str, list[tuple[str, str]]]:
    """name (canonical) -> (version, the 能否商業使用 cell) of every table row of that name, in the section of the
    register whose heading names the indirect dependencies. The version is the text of the 版本或來源 cell
    before its first whitespace or full-width parenthesis. The section runs to the next heading of its own
    level or a higher one. Fenced code, header rows and separator rows are not rows."""
    rows: dict[str, list[tuple[str, str]]] = {}
    level = None  # the heading level of the indirect section while inside it
    fenced = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        if heading:
            depth = len(heading.group(1))
            if level is not None and depth <= level:
                level = None
            if level is None and INDIRECT_HEADING in heading.group(2):
                level = depth
            continue
        if level is None or not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) <= USABLE_COLUMN or all(re.fullmatch(r":?-+:?", cell) for cell in cells if cell):
            continue
        if cells[0] == "名稱":
            continue
        name = re.split(r"[\s（(]", cells[0].strip("` "), maxsplit=1)[0]
        version = re.split(r"[\s（]", cells[VERSION_COLUMN], maxsplit=1)[0]
        rows.setdefault(canonical(name), []).append((version, cells[USABLE_COLUMN]))
    return rows


def is_usable(cell: str) -> bool:
    """Whether a 能否商業使用 cell says 可 (and not 不可)."""
    return cell.startswith(USABLE)


# ---- npm packages -----------------------------------------------------------------------------


def npm_packages(lock: dict) -> list[NpmPackage]:
    """The packages of a package-lock.json (lockfileVersion 2 or 3) in its order, but the project itself and the
    packages only dev dependencies need."""
    found = []
    for path, info in lock.get("packages", {}).items():
        if not path or info.get("dev"):
            continue
        name = info.get("name") or path.rsplit("node_modules/", 1)[-1]
        found.append(NpmPackage(name, info.get("version", ""), (info.get("license") or "").strip(),
                                bool(info.get("optional")), path))
    return found


def npm_installed(path: str) -> bool:
    """Whether the package at `path` of the lock is installed under scenes/."""
    return (NPM_LOCK.parent / path / "package.json").is_file()


def _register_problem(label: str, name: str, version: str, rows: dict[str, list[tuple[str, str]]],
                      missing: str) -> str | None:
    """None when the register has a usable row for `name` at `version`; else what is wrong, `missing` when it has
    no row of that name at all."""
    listed = rows.get(canonical(name), [])
    for_version = [cell for listed_version, cell in listed if listed_version == version]
    if any(is_usable(cell) for cell in for_version):
        return None
    if listed and not for_version:
        versions = ", ".join(dict.fromkeys(listed_version for listed_version, _ in listed))
        return (f"{name} {version}: THIRD_PARTY.md lists it for version {versions}; "
                f"read the licence of {version} on the official page and update the row")
    return f"{label}: {missing}"


def check_npm(packages: list[NpmPackage], installed: Callable[[str], bool],
              rows: dict[str, list[tuple[str, str]]]) -> tuple[list[str], list[str]]:
    """(problems, notes) for the npm packages, as check gives them for the Python ones."""
    problems: list[str] = []
    notes: list[str] = []
    for package in packages:
        label = f"{package.name} {package.version}"
        if not installed(package.path):
            if package.optional:
                notes.append(f"{label}: skipped, an optional package not installed here (for another platform)")
            else:
                problems.append(f"{label}: not installed here (run npm ci in scenes/)")
            continue
        if package.licence and is_allowed(package.licence):
            continue
        missing = (
            f"licence {package.licence!r} is not on the allow list and the indirect dependency table of "
            f"THIRD_PARTY.md has no row for it that says {USABLE}; read the official page first"
            if package.licence else
            "no licence in scenes/package-lock.json; read the official page and list it in THIRD_PARTY.md"
        )
        problem = _register_problem(label, package.name, package.version, rows, missing)
        if problem:
            problems.append(problem)
    return problems, notes


def npm_table_rows(packages: list[NpmPackage], today: datetime.date) -> list[str]:
    """The rows of the indirect dependency table for the npm packages."""
    lines = []
    for package in packages:
        passed = bool(package.licence) and is_allowed(package.licence)
        cells = [package.name, package.version, package.licence or NO_LICENCE, USABLE if passed else NOT_CHECKED,
                 NPM_USE, today.isoformat()]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return lines


def read_npm_lock() -> list[NpmPackage]:
    try:
        return npm_packages(json.loads(NPM_LOCK.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise CheckError(f"cannot read {NPM_LOCK}: {getattr(error, 'strerror', None) or error}") from error


# ---- the command's parts ----------------------------------------------------------------------


def export_requirements(root: Path = ROOT) -> str:
    """The output of `uv export` for the locked packages without the dev group, read from uv.lock alone."""
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise CheckError("uv not found: run this script with `uv run python scripts/third_party.py`")
    command = [uv, "export", "--no-dev", "--no-hashes", "--format", "requirements-txt", "--frozen", "--offline"]
    try:
        done = subprocess.run(command, cwd=root, capture_output=True, text=True)
    except OSError as error:  # UV names a file that is missing or cannot be run
        raise CheckError(f"cannot run {uv}: {error.strerror or error}") from error
    if done.returncode != 0:
        detail = done.stderr.strip().splitlines()
        raise CheckError(f"uv export failed with exit code {done.returncode}" + (f": {detail[-1]}" if detail else ""))
    return done.stdout


def installed_metadata(name: str):
    """The metadata of the installed package `name`, or None when it is not installed."""
    try:
        return importlib.metadata.distribution(name).metadata
    except importlib.metadata.PackageNotFoundError:
        return None


def marker_applies(marker: str) -> bool:
    """Whether an environment marker of the export holds on this machine. Without the `packaging`
    library every marker is taken to apply, so that a package is never skipped by mistake."""
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ImportError:
        return True
    return Marker(marker).evaluate()


def check(
    requirements: list[Requirement],
    metadata_of: Callable,
    rows: dict[str, list[tuple[str, str]]],
    applies: Callable[[str], bool],
) -> tuple[list[str], list[str]]:
    """(problems, notes). A problem is a package that fails: not installed, no licence in its metadata, or a
    licence off the allow list without a usable row in the register for the installed version. A note is a
    package left out because its marker does not apply here and it is not installed."""
    problems: list[str] = []
    notes: list[str] = []
    for requirement in requirements:
        label = f"{requirement.name} {requirement.version}"
        meta = metadata_of(requirement.name)
        if meta is None:
            if applies(requirement.marker):
                problems.append(f"{label}: not installed here (run uv sync)")
            else:
                notes.append(f"{label}: skipped, not installed and its marker ({requirement.marker}) does not apply here")
            continue
        claims = licence_claims(meta)
        if not claims:
            problems.append(f"{label}: no licence in its metadata; read the official page and list it in THIRD_PARTY.md")
        elif not all(is_allowed(claim) for claim in claims):
            missing = (
                f"licence {'; '.join(claims)!r} is not on the allow list and the indirect dependency table of "
                f"THIRD_PARTY.md has no row for it that says {USABLE}; read the official page first"
            )
            problem = _register_problem(label, requirement.name, meta["Version"] or requirement.version, rows, missing)
            if problem:
                problems.append(problem)
    return problems, notes


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def indirect_table(requirements: list[Requirement], metadata_of: Callable, direct: set[str], today: datetime.date) -> str:
    """The Markdown table of the installed packages that pyproject.toml does not ask for itself."""
    lines = [HEADER, "|---|---|---|---|---|---|"]
    for requirement in requirements:
        meta = metadata_of(requirement.name)
        if meta is None or canonical(requirement.name) in direct:
            continue
        claims = licence_claims(meta)
        passed = bool(claims) and all(is_allowed(claim) for claim in claims)
        used = f"由 {'、'.join(requirement.via)} 帶入" if requirement.via else "間接相依"
        cells = [requirement.name, meta["Version"], claims[0] if claims else NO_LICENCE,
                 USABLE if passed else NOT_CHECKED, used, today.isoformat()]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check the licence of every dependency")
    mode.add_argument("--table", action="store_true", help="print the table of indirect dependencies")
    args = parser.parse_args(argv)

    try:
        requirements = parse_requirements(export_requirements())
        packages = read_npm_lock()
    except CheckError as error:
        print(f"error: {error}")
        return 1

    if args.table:
        today = datetime.date.today()
        print(indirect_table(requirements, installed_metadata, direct_names(ROOT / "pyproject.toml"), today))
        for line in npm_table_rows(packages, today):
            print(line)
        return 0

    try:
        rows = register_rows(REGISTER.read_text(encoding="utf-8"))
    except OSError as error:
        print(f"error: cannot read {REGISTER}: {error.strerror or error}")
        return 1
    problems, notes = check(requirements, installed_metadata, rows, marker_applies)
    npm_problems, npm_notes = check_npm(packages, npm_installed, rows)
    problems, notes = problems + npm_problems, notes + npm_notes
    for line in notes:
        print(f"note: {line}")
    if problems:
        for line in problems:
            print(f"FAIL {line}")
        print(f"{len(problems)} of {len(requirements) + len(packages)} packages failed the licence check")
        return 1
    print(f"OK: {len(requirements) + len(packages) - len(notes)} packages checked, all licences are usable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
