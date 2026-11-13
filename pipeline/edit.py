"""work/edit.json: validation, the kept sentences, the segments the render keeps, and time mapping.

edit.json is written by Claude from the user's Gate 1 decisions; nothing here writes it. validate_edit
is the gate between that file and the render: it lists every problem it finds, each as
"<JSON path>: <problem>" (cuts.accept[2], sentences[1].sem, captions.mode), and never raises on a
value of the wrong type. An error is one line: a value or key it quotes is shown as its repr, cut
after SHOWN_CHARS characters, and a key that is not a plain word goes into the path as edit['a b']. A
key an object writes twice is an error too (the parser alone would let the last one win). Phase 1
knows the keys version, style, cuts, sentences and captions, and the optional key notes (a suggestion
for a trim or a spoken sentence); the keys of later phases are refused with the phase that brings them.
captions takes the mode and the optional key text: for a spoken sentence, the one line of text, with a
character that shows, its captions show in place of the sentence's own (see captions.caption_source).

A sentence is kept when it was spoken and no cuts.extra range covers it. The reel runs from the first
kept sentence's cut_in to the last kept sentence's cut_out (trim.json bounds); the accepted trims and
the extra ranges (from the start sentence's cut_in to the end sentence's cut_out) are taken off that
span as one union, so a cut point that lies inside an accepted trim does no harm. What is left are the
segments, on the 30 fps grid, and map_time turns a source time into a time in the cut reel. in_reel
says whether a source span shares a frame with the segments.
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pipeline.errors import EditInvalid, PipelineError
from pipeline.project import FORMAT_VERSIONS, Project, edit_file_hashes, record_edit_checked, refuse_older_document
from pipeline.style import UnknownStyle, load_style
from pipeline.trim import FPS

EDIT_VERSION = 1
UNCHANGED_EDIT_WARNING = (
    "edit.json is unchanged since it was checked against an older transcript.json or trim.json: "
    "trim and sentence ids may now point at different candidates"
)
MIN_SEGMENT_FRAMES = 2  # a piece shorter than this is dropped after the cuts are taken off

SEM_TAGS = ("hook", "claim", "number", "list", "compare", "steps", "example", "quote", "define", "warning",
            "cta", "aside")
WEIGHTS = ("main", "sub")
CAPTION_MODES = ("line", "word")
LINE_BREAK_CHARS = "\n\r\x0b\x0c\x85\u2028\u2029"  # a caption text holds none of these (captions makes them spaces)
TOP_KEYS = ("version", "style", "cuts", "sentences", "captions")
OPTIONAL_KEYS = ("notes",)  # top-level keys an edit may leave out
NOTE_PREFIX = "建議"  # a note starts with this, then says what the director suggests
NOTE_REASON = "。因為"  # and a note contains this, which opens the reason
FUTURE_KEYS = {"scenes": 2, "broll": 2, "punch_in": 2, "sfx": 3, "music": 3}  # key -> phase that adds it

SHOWN_CHARS = 60  # a value or a key goes into an error as its repr, cut after this many characters

_JSON_TYPES = ((bool, "a boolean"), (int, "a number"), (float, "a number"), (str, "a string"),
               (list, "an array"), (dict, "an object"), (type(None), "null"))


@dataclass(frozen=True)
class Segment:
    """A stretch of the source that stays in the reel, in source seconds on the frame grid."""

    start: float
    end: float


# ---- loading ----------------------------------------------------------------------------------


def _describe(value: object) -> str:
    """The JSON type of `value`, as 'a string', 'an array' ..."""
    for kind, name in _JSON_TYPES:
        if isinstance(value, kind):
            return name
    return type(value).__name__


def _show(value: object) -> str:
    """`value` as an error says it: its repr, which is one line whatever the value holds, and when that
    is longer than SHOWN_CHARS characters its first SHOWN_CHARS followed by "…"."""
    text = repr(value)
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS] + "…"


def _join(path: str, key: str) -> str:
    """The path of `key` in the object at `path`: cuts.extra, or edit['a b'] when the key is not a plain
    word (empty, a space or a control character in it, longer than SHOWN_CHARS), which would make the path
    ambiguous, invisible or several lines long."""
    if len(key) <= SHOWN_CHARS and re.fullmatch(r"\w+", key):
        return f"{path}.{key}" if path else key
    return f"{path or 'edit'}[{_show(key)}]"


class _Pairs(list):
    """A JSON object as the parser hands it over: its (key, value) pairs in file order, so that a key
    written twice is not lost before _unpair looks at it."""


def _unpair(root: object) -> tuple[object, list[str]]:
    """`root`, parsed with object_pairs_hook=_Pairs, as plain dicts and lists (the last of a repeated key
    wins, as json.loads would have it), and one error for each key an object repeats, in the order the
    objects come in the file: "<path>: duplicate key '<key>'". Works on a stack, not by recursion, so a
    document as deep as the parser takes it does not hit the recursion limit here."""
    errors: list[str] = []
    result: list[object] = [None]
    pending: list[tuple[object, list | dict, object, str]] = [(root, result, 0, "")]
    while pending:
        node, parent, slot, path = pending.pop()
        if isinstance(node, _Pairs):
            obj: dict = {}
            parent[slot] = obj
            repeated: set[str] = set()
            for key, _ in node:
                if key in obj and key not in repeated:
                    repeated.add(key)
                    errors.append(f"{_join(path, key)}: duplicate key {_show(key)}")
                obj[key] = None  # keeps the place of the key's first appearance
            # reversed, so the last of a repeated key is the last to be stored
            pending.extend((value, obj, key, _join(path, key)) for key, value in reversed(node))
        elif isinstance(node, list):
            items: list[object] = [None] * len(node)
            parent[slot] = items
            pending.extend((value, items, i, f"{path}[{i}]") for i, value in reversed(list(enumerate(node))))
        else:
            parent[slot] = node
    return result[0], errors


def _read_edit(path: Path) -> tuple[dict, list[str]]:
    """The parsed edit.json and the errors for its repeated keys (see _unpair). Text that is not valid
    JSON (or not UTF-8), or JSON that is not an object, raises EditInvalid; a file that cannot be read
    raises PipelineError."""
    name = Path(path).name
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise EditInvalid([f"{name}: not valid UTF-8 ({error.reason} at byte {error.start})"]) from error
    except OSError as error:
        raise PipelineError(f"cannot read {path}: {error.strerror or error}") from error
    try:
        root = json.loads(text, object_pairs_hook=_Pairs)
    except json.JSONDecodeError as error:
        raise EditInvalid(
            [f"{name}: not valid JSON (line {error.lineno}, column {error.colno}: {error.msg})"]
        ) from error
    except RecursionError:
        raise EditInvalid([f"{name}: not valid JSON (nested too deeply)"]) from None
    except ValueError as error:  # an integer of more digits than sys.get_int_max_str_digits() allows
        raise EditInvalid(
            [f"{name}: not valid JSON (a number has more than {sys.get_int_max_str_digits()} digits)"]
        ) from error
    data, duplicates = _unpair(root)
    if not isinstance(data, dict):
        raise EditInvalid([f"{name}: must be an object, got {_describe(data)}"])
    return data, duplicates


def load_edit(path: Path) -> dict:
    """The parsed edit.json. Text that is not valid JSON (or not UTF-8), JSON that is not an object, or an
    object that repeats a key raises EditInvalid; a file that cannot be read raises PipelineError."""
    data, duplicates = _read_edit(path)
    if duplicates:
        raise EditInvalid(duplicates)
    return data


def load_document(path: Path, step: str, keys: tuple[str, ...], version: int | None = None) -> dict:
    """A JSON document that pipeline step `step` wrote (transcript.json, trim.json), with the top-level
    `keys` in it. With a `version`, a document that has a "version" of another value was made by an older
    pipeline and is refused; one without a "version" is read as it is."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PipelineError(f"{path} not found: run {step} first") from None
    except (OSError, ValueError) as error:
        raise PipelineError(f"cannot read {path}: {error}") from error
    if version is not None:
        refuse_older_document(path, data, step, version)
    missing = [key for key in keys if not isinstance(data, dict) or key not in data]
    if missing:
        raise PipelineError(f"cannot read {path}: no {', '.join(missing)} (run {step} again)")
    return data


def require_valid(p: Project) -> tuple[dict, dict, dict]:
    """Read work/edit.json, work/transcript.json and work/trim.json, validate the edit and return
    (edit, transcript, trims). A problem in the edit raises EditInvalid with every error; a document
    that is missing or unreadable raises PipelineError. A key an object of edit.json repeats is an error
    too, listed before the others."""
    edit, duplicates = _read_edit(p.edit_path)
    transcript = load_document(
        p.work / "transcript.json", "transcribe", ("sentences",), version=FORMAT_VERSIONS["transcribe"]
    )
    trims = load_document(p.work / "trim.json", "trim", ("trims", "bounds"), version=FORMAT_VERSIONS["trim"])
    errors = duplicates + validate_edit(edit, transcript, trims)
    if errors:
        raise EditInvalid(errors)
    return edit, transcript, trims


def validate_project(p: Project) -> dict:
    """What the validate command reports: the number of segments, the length of the cut reel in
    seconds and the number of kept sentences. Raises like require_valid.

    A pass is recorded in state.json as edit_checked: the sha256 of edit.json, transcript.json and
    trim.json, which next_step and the storyboard compare with the files they find. A failure records
    nothing. When the pass before this one was on this same edit.json but on another transcript.json or
    trim.json, the result also has a `warnings` list that says so: the ids in it were not re-checked."""
    hashes = edit_file_hashes(p)  # before the files are read: one that changes meanwhile fails the next comparison
    edit, transcript, trims = require_valid(p)
    segments = build_segments(edit, transcript, trims)
    result = {
        "segments": len(segments),
        "duration_s": round(total_duration(segments), 3),
        "kept_sentences": len(kept_sentences(edit, transcript)),
    }
    before = record_edit_checked(p, hashes)
    if (
        before is not None
        and before["edit"] == hashes["edit"]
        and (before["transcript"], before["trim"]) != (hashes["transcript"], hashes["trim"])
    ):
        result["warnings"] = [UNCHANGED_EDIT_WARNING]
    return result


# ---- validation -------------------------------------------------------------------------------

Table = dict[str, tuple[int, bool]]  # sentence id -> (position in the script, spoken)


def _is_object(value: object, path: str, errors: list[str]) -> bool:
    if isinstance(value, dict):
        return True
    errors.append(f"{path}: must be an object, got {_describe(value)}")
    return False


def _is_array(value: object, path: str, what: str, errors: list[str]) -> bool:
    if isinstance(value, list):
        return True
    errors.append(f"{path}: must be an array of {what}, got {_describe(value)}")
    return False


def _check_keys(obj: dict, path: str, required: tuple[str, ...], errors: list[str],
                optional: tuple[str, ...] = ()) -> None:
    """Every key of `required` must be there and no key may be outside `required` and `optional`."""
    for key in required:
        if key not in obj:
            errors.append(f"{_join(path, key)}: missing")
    for key in obj:
        if key not in required and key not in optional:
            errors.append(f"{_join(path, str(key))}: unknown key")


def _check_choice(value: object, path: str, choices: tuple[str, ...], errors: list[str]) -> None:
    if not isinstance(value, str) or value not in choices:
        errors.append(f"{path}: {_show(value)} is not one of {', '.join(choices)}")


def _table(transcript: dict) -> Table:
    return {s["id"]: (i, bool(s["spoken"])) for i, s in enumerate(transcript["sentences"])}


def validate_edit(edit: dict, transcript: dict, trims: dict) -> list[str]:
    """Every problem of `edit` against the transcript and the trims, as "<JSON path>: <problem>". An
    empty list means the edit may go on to the render. Checks the phase-1 keys, rule 1 (every trim
    decided once, extra ranges, the sentence list, the value sets) and rule 7 (the sem tags), and the
    caption texts and the notes when there are any."""
    if not isinstance(edit, dict):
        return [f"edit: must be an object, got {_describe(edit)}"]
    errors: list[str] = []
    table = _table(transcript)

    for key in edit:
        if key in FUTURE_KEYS:
            errors.append(f"{key}: not supported until phase {FUTURE_KEYS[key]}")
        elif key not in TOP_KEYS and key not in OPTIONAL_KEYS:
            errors.append(f"{_join('', str(key))}: unknown key")
    for key in TOP_KEYS:
        if key not in edit:
            errors.append(f"{key}: missing")

    _check_version(edit, errors)
    _check_style(edit, errors)
    ranges: list[tuple[str, str]] = []
    if "cuts" in edit and _is_object(edit["cuts"], "cuts", errors):
        ranges = _check_cuts(edit["cuts"], table, trims, errors)
    kept = _kept(transcript, table, ranges)
    if "sentences" in edit:
        _check_sentences(edit["sentences"], table, ranges, kept, errors)
    if "captions" in edit and _is_object(edit["captions"], "captions", errors):
        _check_keys(edit["captions"], "captions", ("mode",), errors, optional=("text",))
        if "mode" in edit["captions"]:
            _check_choice(edit["captions"]["mode"], "captions.mode", CAPTION_MODES, errors)
        if "text" in edit["captions"] and _is_object(edit["captions"]["text"], "captions.text", errors):
            _check_caption_text(edit["captions"]["text"], transcript, table, errors)
    if "notes" in edit and _is_object(edit["notes"], "notes", errors):
        _check_notes(edit["notes"], table, trims, errors)
    bounds_ok = _check_cut_points(transcript, trims, errors)
    _check_something_is_left(edit, transcript, trims, kept, bounds_ok, errors)
    return errors


def _check_version(edit: dict, errors: list[str]) -> None:
    if "version" in edit and (type(edit["version"]) is not int or edit["version"] != EDIT_VERSION):
        errors.append(f"version: must be {EDIT_VERSION}, got {_show(edit['version'])}")


def _check_notes(notes: dict, table: Table, trims: dict, errors: list[str]) -> None:
    """Each note belongs to a trim or to a spoken sentence, is a string, and reads as a suggestion with
    a reason: it starts with NOTE_PREFIX and contains NOTE_REASON."""
    known = {t["id"] for t in trims["trims"]}
    for key, text in notes.items():
        path = _join("notes", str(key))
        if key not in known and not (key in table and table[key][1]):
            errors.append(f"{path}: not a trim or a spoken sentence")
        elif not isinstance(text, str):
            errors.append(f"{path}: must be a string, got {_describe(text)}")
        elif not text.startswith(NOTE_PREFIX) or NOTE_REASON not in text:
            errors.append(f"{path}: must start with {NOTE_PREFIX} and contain {NOTE_REASON}")


def _check_caption_text(texts: dict, transcript: dict, table: Table, errors: list[str]) -> None:
    """Each caption text belongs to a spoken sentence (a removed one may keep its text) and is a string that
    holds something besides whitespace, no line break, and a character that shows: one of its characters
    ends up in a unit of the sentence (captions.remap_units leaves at least one unit)."""
    from pipeline.captions import remap_units  # captions imports this module

    sentences = {sentence["id"]: sentence for sentence in transcript["sentences"]}
    for key, text in texts.items():
        path = _join("captions.text", str(key))
        if not (key in table and table[key][1]):
            errors.append(f"{path}: not a spoken sentence")
        elif not isinstance(text, str):
            errors.append(f"{path}: must be a string, got {_describe(text)}")
        elif not text.strip():
            errors.append(f"{path}: must not be empty")
        elif any(char in LINE_BREAK_CHARS for char in text):
            errors.append(f"{path}: must be one line")
        elif not remap_units(sentences[key]["text"], sentences[key]["units"], text):
            errors.append(f"{path}: must have a character to show")


def _check_style(edit: dict, errors: list[str]) -> None:
    if "style" not in edit:
        return
    name = edit["style"]
    if not isinstance(name, str):
        errors.append(f"style: must be a style name (a string), got {_describe(name)}")
        return
    try:
        load_style(name)
    except UnknownStyle:  # there is no such style folder
        errors.append(f"style: unknown style {_show(name)}")
    except PipelineError as error:  # the folder is there and load_style refuses what is in it: say what
        errors.append(error.message)
    except (OSError, ValueError) as error:
        reason = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
        errors.append(f"style: cannot read style {_show(name)}: {reason}")


def _check_cuts(cuts: dict, table: Table, trims: dict, errors: list[str]) -> list[tuple[str, str]]:
    """Rule 1 for the cuts object. Returns the extra ranges that are well formed, as (from, to)."""
    _check_keys(cuts, "cuts", ("accept", "reject"), errors, optional=("extra",))
    known = [t["id"] for t in trims["trims"]]
    decided: dict[str, str] = {}  # trim id -> where it was first decided
    lists_ok = True
    for name in ("accept", "reject"):
        if name not in cuts:
            lists_ok = False
            continue
        path = f"cuts.{name}"
        if not _is_array(cuts[name], path, "trim ids", errors):
            lists_ok = False
            continue
        for i, item in enumerate(cuts[name]):
            where = f"{path}[{i}]"
            if not isinstance(item, str):
                errors.append(f"{where}: must be a trim id (a string), got {_describe(item)}")
            elif item not in known:
                errors.append(f"{where}: unknown trim {_show(item)}")
            elif item not in decided:
                decided[item] = where
            elif decided[item].startswith(path):
                errors.append(f"{where}: {item} is listed twice (also {decided[item]})")
            else:
                errors.append(f"{where}: {item} is also in {decided[item].split('[')[0]}")
    if lists_ok:
        for trim_id in known:
            if trim_id not in decided:
                errors.append(f"cuts: trim {trim_id} is neither accepted nor rejected")
    return _check_extra(cuts, table, errors)


def _check_extra(cuts: dict, table: Table, errors: list[str]) -> list[tuple[str, str]]:
    if "extra" not in cuts or not _is_array(cuts["extra"], "cuts.extra", "ranges", errors):
        return []
    found: list[tuple[int, str, str]] = []  # (index in cuts.extra, from, to)
    for i, item in enumerate(cuts["extra"]):
        ends = _extra_range(item, f"cuts.extra[{i}]", table, errors)
        if ends is not None:
            found.append((i, *ends))
    for n, (i, a, b) in enumerate(found):
        for i0, a0, b0 in found[:n]:
            if table[a][0] <= table[b0][0] and table[a0][0] <= table[b][0]:
                errors.append(f"cuts.extra[{i}]: overlaps cuts.extra[{i0}] ({a0} to {b0})")
                break  # once per range, against the first earlier one it meets
    return [(a, b) for _, a, b in found]


def _extra_range(item: object, path: str, table: Table, errors: list[str]) -> tuple[str, str] | None:
    """(from, to) of an extra item whose two sentences are spoken and in order, else None. A bad reason
    or an unknown key is reported but leaves the range usable."""
    if not _is_object(item, path, errors):
        return None
    _check_keys(item, path, ("from", "to", "reason"), errors)
    if "reason" in item and not isinstance(item["reason"], str):
        errors.append(f"{path}.reason: must be a string, got {_describe(item['reason'])}")
    ends = []
    for key in ("from", "to"):
        if key not in item:
            continue
        value = item[key]
        if not isinstance(value, str):
            errors.append(f"{path}.{key}: must be a sentence id (a string), got {_describe(value)}")
        elif value not in table:
            errors.append(f"{path}.{key}: unknown sentence {_show(value)}")
        elif not table[value][1]:
            errors.append(f"{path}.{key}: {value} was not spoken")
        else:
            ends.append(value)
    if len(ends) != 2:
        return None
    a, b = ends
    if table[a][0] > table[b][0]:
        errors.append(f"{path}: from {a} comes after to {b}")
        return None
    return a, b


def _removed(table: Table, ranges: list[tuple[str, str]]) -> set[str]:
    """Every sentence (spoken or not) that lies inside one of the extra ranges."""
    return {sid for a, b in ranges for sid, (index, _) in table.items() if table[a][0] <= index <= table[b][0]}


def _kept(transcript: dict, table: Table, ranges: list[tuple[str, str]]) -> list[str]:
    removed = _removed(table, ranges)
    return [s["id"] for s in transcript["sentences"] if s["spoken"] and s["id"] not in removed]


def _check_sentences(items: object, table: Table, ranges: list[tuple[str, str]], kept: list[str],
                     errors: list[str]) -> None:
    if not _is_array(items, "sentences", "sentence entries", errors):
        return
    removed = _removed(table, ranges)
    listed: dict[str, int] = {}  # sentence id -> index of its first entry
    for i, item in enumerate(items):
        path = f"sentences[{i}]"
        if not _is_object(item, path, errors):
            continue
        _check_keys(item, path, ("id", "sem", "weight"), errors)
        if "id" in item:
            _check_listed_id(item["id"], f"{path}.id", i, table, removed, listed, errors)
        if "sem" in item:
            _check_choice(item["sem"], f"{path}.sem", SEM_TAGS, errors)
        if "weight" in item:
            _check_choice(item["weight"], f"{path}.weight", WEIGHTS, errors)
    for sid in kept:
        if sid not in listed:
            errors.append(f"sentences: {sid} is kept but not listed")


def _check_listed_id(sid: object, path: str, index: int, table: Table, removed: set[str],
                     listed: dict[str, int], errors: list[str]) -> None:
    if not isinstance(sid, str):
        errors.append(f"{path}: must be a sentence id (a string), got {_describe(sid)}")
    elif sid not in table:
        errors.append(f"{path}: unknown sentence {_show(sid)}")
    elif sid in listed:
        errors.append(f"{path}: {sid} is listed twice (also sentences[{listed[sid]}])")
    elif not table[sid][1]:
        errors.append(f"{path}: {sid} was not spoken")
    elif sid in removed:
        errors.append(f"{path}: {sid} is removed by cuts.extra")
    else:
        listed[sid] = index


def _check_cut_points(transcript: dict, trims: dict, errors: list[str]) -> bool:
    """Every spoken sentence needs its cut_in and cut_out: a trim.json from before the transcript changed
    may lack them. Returns whether they are all there."""
    ok = True
    for sentence in transcript["sentences"]:
        if sentence["spoken"] and sentence["id"] not in trims["bounds"]:
            errors.append(f"sentences: {sentence['id']} has no cut points in trim.json (run trim again)")
            ok = False
    return ok


def _check_something_is_left(edit: dict, transcript: dict, trims: dict, kept: list[str], bounds_ok: bool,
                             errors: list[str]) -> None:
    if not any(s["spoken"] for s in transcript["sentences"]):
        errors.append("sentences: nothing is left, no sentence was spoken")
    elif not kept:
        errors.append("cuts.extra: nothing is left, every spoken sentence is removed")
    elif bounds_ok and not build_segments(edit, transcript, trims):
        errors.append("cuts: nothing is left, no piece of 2 frames or more remains after the cuts")


# ---- kept sentences and segments --------------------------------------------------------------


def _extra_ranges(edit: dict, table: Table) -> list[tuple[str, str]]:
    """The well-formed cuts.extra ranges as (from, to). Whatever is malformed is left out: validation
    reports it, and these helpers must not raise on it."""
    cuts = edit.get("cuts") if isinstance(edit, dict) else None
    items = cuts.get("extra") if isinstance(cuts, dict) else None
    if not isinstance(items, list):
        return []
    found = (_extra_range(item, "", table, []) for item in items)
    return [ends for ends in found if ends is not None]


def kept_sentences(edit: dict, transcript: dict) -> list[str]:
    """The ids of the sentences that stay in the reel, in script order: spoken, and in no extra range."""
    table = _table(transcript)
    return _kept(transcript, table, _extra_ranges(edit, table))


def caption_texts(edit: dict) -> dict[str, str]:
    """captions.text of `edit`: sentence id -> the text its captions show in place of the sentence's own.
    Empty when the edit has none. Anything malformed is left out: validation reports it, and this must not
    raise on it."""
    captions = edit.get("captions") if isinstance(edit, dict) else None
    texts = captions.get("text") if isinstance(captions, dict) else None
    if not isinstance(texts, dict):
        return {}
    return {sid: text for sid, text in texts.items() if isinstance(text, str)}


def _accepted(edit: dict) -> set[str]:
    cuts = edit.get("cuts") if isinstance(edit, dict) else None
    items = cuts.get("accept") if isinstance(cuts, dict) else None
    return {item for item in items if isinstance(item, str)} if isinstance(items, list) else set()


def to_frame(t: float) -> int:
    """The frame of the 30 fps grid that source time `t` (seconds) falls on, rounded to the nearest."""
    return round(t * FPS)


def in_reel(start: float, end: float, segments: list[Segment]) -> bool:
    """Whether the source span from `start` to `end` shares a frame with one of `segments`: its frames
    [to_frame(start), to_frame(end)) overlap the frames of a segment. A span that only touches the edge
    of a segment is not in the reel."""
    first, last = to_frame(start), to_frame(end)
    return any(first < to_frame(segment.end) and to_frame(segment.start) < last for segment in segments)


def build_segments(edit: dict, transcript: dict, trims: dict) -> list[Segment]:
    """The stretches of the source the render keeps, in order, on the 30 fps grid. The reel spans the
    first kept sentence's cut_in to the last kept sentence's cut_out; the accepted trims and the extra
    ranges are taken off as one union, and a piece shorter than MIN_SEGMENT_FRAMES is dropped. Needs a
    valid edit: every spoken sentence has bounds."""
    table = _table(transcript)
    ranges = _extra_ranges(edit, table)
    kept = _kept(transcript, table, ranges)
    if not kept:
        return []
    bounds = trims["bounds"]
    first, last = to_frame(bounds[kept[0]]["cut_in"]), to_frame(bounds[kept[-1]]["cut_out"])
    accepted = _accepted(edit)
    removed = [(to_frame(t["start"]), to_frame(t["end"])) for t in trims["trims"] if t["id"] in accepted]
    removed += [(to_frame(bounds[a]["cut_in"]), to_frame(bounds[b]["cut_out"])) for a, b in ranges]

    pieces: list[tuple[int, int]] = []
    cursor = first
    for lo, hi in sorted(removed):
        if lo > cursor:
            pieces.append((cursor, min(lo, last)))
        cursor = max(cursor, hi)
        if cursor >= last:
            break
    if cursor < last:
        pieces.append((cursor, last))
    return [Segment(a / FPS, b / FPS) for a, b in pieces if b - a >= MIN_SEGMENT_FRAMES]


def total_duration(segments: list[Segment]) -> float:
    """The length of the cut reel: what map_time gives at and after the end of the last segment."""
    total = 0.0
    for segment in segments:
        total += segment.end - segment.start
    return total


def map_time(t: float, segments: list[Segment]) -> float:
    """The time in the cut reel of source time `t`. A time inside a segment is shifted by the length of
    the segments before it; a time inside a cut gives the start of the next segment; a time before the
    first segment gives 0 and one after the last gives the total length."""
    offset = 0.0
    for segment in segments:
        if t < segment.start:
            return offset
        if t < segment.end:
            return offset + (t - segment.start)
        offset += segment.end - segment.start
    return offset
