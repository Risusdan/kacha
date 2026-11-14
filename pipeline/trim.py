"""Rough-cut candidates and sentence cut points (work/trim.json).

Three kinds of cut come out of a transcript, all placed at the quietest spot near the time the
transcript gives (pipeline.audio.snap_cut) and on the 30 fps grid:

- run trims: fillers, repeats and earlier takes the speaker added, cut by default; a reworded take
  and extra speech are only listed, and each one-character hesitation in them that the caption
  dictionary makes a word of its own (not the 恩 of 感恩) is a filler trim of its own inside the listed
  one;
- pauses: a gap of more than PAUSE_MIN_S between two recognised units is cut down to about
  PAUSE_KEEP_S, which keeps the room sound of the recording;
- sentence cut points: where the reel may start and end around each spoken sentence, for the head and
  tail of the reel and for the stretches the user drops.

A cut point that is not in silence marks its trim as needing listening. When the times of the recognised
units do not overlap, trims that suggest a cut never overlap one another, nor do listed ones; a trim that
suggests a cut may lie inside a listed one (a hesitation or a pause in it) or across its edge (a pause
next to it). Where those times do overlap, this is not assured: a hesitation is placed around its own
unit, not kept clear of the trims before it as a run trim is. No cut point lies inside a
recognised unit that was matched to the script (a misrecognised word counts as matched, so it is
never cut), except for the rounding of the frame grid.
"""

import json
import math
import re
from collections import Counter
from collections.abc import Callable

from pipeline.align import HESITATIONS, LEFT_OUT_OF_ASR_TEXT
from pipeline.audio import Envelope, envelope, few_pauses, is_silent, noise_floor_db, silence_threshold_db, snap_cut
from pipeline.errors import PipelineError
from pipeline.project import FORMAT_VERSIONS, Project, refuse_older_document, run_step, write_text_atomically
from pipeline.words import word_ends

Snap = Callable[[float, float, float], float]  # (target time, lowest, highest) -> a cut time

TRIM_VERSION = FORMAT_VERSIONS["trim"]
FPS = 30
PAUSE_MIN_S = 0.7  # a gap longer than this between two recognised units is a pause to shorten
PAUSE_KEEP_S = 0.35  # what is left of a shortened pause
PAUSE_MIN_PIECE_S = 0.1  # a pause the cut candidates leave shorter than this is not a candidate
LEAD_S = 0.3  # a sentence's cut point is at most this far from its first or last word
_EPSILON = 1e-6  # transcript times are rounded to microseconds
FEW_PAUSES_WARNING = "few pauses: quiet-point detection unreliable"

# Run kind -> (trim kind, suggest).
_RUN_TRIMS = {
    "filler": ("filler", "cut"),
    "repeat": ("repeat", "cut"),
    "retake": ("retake", "cut"),
    "reworded": ("retake", "list"),
    "extra": ("extra", "list"),
}
_HESITATION_RUNS = ("extra", "reworded")  # run kinds whose hesitations are filler trims of their own
_PAUSE_REASON = re.compile(r"pause (\d+\.\d\d)s -> (\d+\.\d\d)s")  # the shape pause_reason writes


def build_trims(transcript: dict, env: Envelope) -> dict:
    """The work/trim.json document for a transcript and the loudness envelope of its audio."""
    tokens = transcript["tokens"]
    audio_s = transcript["audio_s"]
    floor = noise_floor_db(env)
    threshold = silence_threshold_db(env)  # computed once: what counts as silence in every check below
    last_point = math.floor(audio_s * FPS + 1e-9) / FPS  # no cut is later than the audio

    def snap(t: float, lo: float, hi: float) -> float:
        point = _between(snap_cut(t, lo, hi, env, fps=FPS), lo, hi)
        return min(max(point, 0.0), last_point)  # last, so that nothing moves a cut out of the audio

    runs = _run_trims(transcript, snap)
    pauses = _pauses(tokens, transcript["runs"], runs, snap)
    trims = sorted(runs + pauses, key=lambda t: (t["start"], t["end"]))
    for number, candidate in enumerate(trims, 1):
        candidate["id"] = f"t{number:02d}"
    return {
        "version": TRIM_VERSION,
        "noise_floor_db": round(floor, 1),
        "warnings": [FEW_PAUSES_WARNING] if few_pauses(env) else [],
        "trims": [_finish(candidate, env, threshold) for candidate in trims],
        "bounds": _bounds(transcript, snap, env, threshold),
    }


def _between(point: float, lo: float, hi: float) -> float:
    """`point` kept between two neighbouring recognised units, `lo` being the end of the first and `hi`
    the start of the second, so that a cut does not take a piece of either word. snap_cut may leave
    the range when the units touch or when rounding to the grid carries it over; the grid point
    nearest to the range is used when no grid point lies inside it."""
    low, high = math.ceil(lo * FPS - 1e-9) / FPS, math.floor(hi * FPS + 1e-9) / FPS
    if low <= high:
        return min(max(point, low), high)
    return round((lo + hi) / 2 * FPS) / FPS


def _hesitations(texts: list[str]) -> list[bool]:
    """Which units of a run, given as their texts in order, are hesitations: a unit whose text is one of
    HESITATIONS and that the caption dictionary makes a word of its own in the run's text (the texts
    joined), that is, a word starts where it starts and ends where it ends (words.word_ends without
    guessing new words; the start and the end of the text count). So 恩 said alone, each 呃 of 呃呃 and the
    額 of 對額 are hesitations, and the 恩 of 感恩 and the 額 of 額外 or 金額 are not. Guessing is left out
    because it glues 額 or 恩 to a neighbouring character (對額, 恩恩) that no dictionary word holds. Only a
    run of more than one unit that holds one of HESITATIONS reads the dictionary (word_ends raises
    PipelineError without it)."""
    flags = [text in HESITATIONS for text in texts]
    if len(texts) < 2 or not any(flags):
        return flags
    joined = "".join(texts)
    ends = word_ends(joined, guess_new_words=False) | {0, len(joined)}
    at = 0
    for k, text in enumerate(texts):
        flags[k] = flags[k] and at in ends and at + len(text) in ends
        at += len(text)
    return flags


def _run_trims(transcript: dict, snap: Snap) -> list[dict]:
    """One candidate per run, from the middle of the gap before the run to the middle of the gap
    after it, but for extra speech that lies wholly before the first unit matched to the script or
    after the last one, and for a run whose words the caption of a sentence shown as recognised text
    holds (extra speech or a reworded take of that sentence: align leaves only the kinds of
    LEFT_OUT_OF_ASR_TEXT out of such a caption), which cannot be cut without leaving caption words
    unheard. A candidate that came out empty, or lies inside an earlier one, is dropped; one that
    starts inside an earlier one starts where that one ends. The length of a candidate counts the
    units of its run that are not hesitations (see _hesitations).

    Each hesitation of extra speech or of a reworded take is a filler candidate of its own, placed the
    same way around that one unit, unless the run belongs to a sentence shown as recognised text. It
    lies inside the run's candidate and is only dropped when it came out empty."""
    tokens, audio_s = transcript["tokens"], transcript["audio_s"]
    matched = [i for i, token in enumerate(tokens) if token["s"] is not None]
    as_recognised = {s["id"] for s in transcript["sentences"] if s["display_source"] == "asr"}

    def placed(h0: int, h1: int) -> dict:
        before = tokens[h0 - 1]["end"] if h0 > 0 else 0.0
        after = tokens[h1]["start"] if h1 < len(tokens) else audio_s
        first, last = tokens[h0]["start"], max(t["end"] for t in tokens[h0:h1])
        return {"start": snap((before + first) / 2, before, first), "end": snap((last + after) / 2, last, after)}

    found, hesitations = [], []
    for run in transcript["runs"]:
        h0, h1 = run["h0"], run["h1"]
        kind, suggest = _RUN_TRIMS[run["kind"]]
        hesitant = _hesitations([t["text"] for t in tokens[h0:h1]])
        in_caption = run["sentence"] in as_recognised
        on_caption = in_caption and run["kind"] not in LEFT_OUT_OF_ASR_TEXT  # its words are on the caption
        outside = run["kind"] == "extra" and (not matched or h1 <= matched[0] or h0 > matched[-1])
        if not (on_caption or outside):
            found.append({
                "kind": kind, **placed(h0, h1), "sentence": run["sentence"], "text": run["text"],
                "reason": _run_reason(run, kind, suggest), "suggest": suggest, "length": hesitant.count(False),
            })
        if run["kind"] in _HESITATION_RUNS and not in_caption:
            for h in range(h0, h1):
                text = tokens[h]["text"]
                if hesitant[h - h0]:
                    hesitations.append({
                        "kind": "filler", **placed(h, h + 1), "sentence": run["sentence"], "text": text,
                        "reason": f"filler {text}", "suggest": "cut", "length": 0,
                    })
    kept: list[dict] = []
    for candidate in sorted(found, key=lambda t: (t["start"], t["end"])):
        if kept:
            candidate["start"] = max(candidate["start"], kept[-1]["end"])
        if candidate["start"] < candidate["end"]:
            kept.append(candidate)
    return kept + [candidate for candidate in hesitations if candidate["start"] < candidate["end"]]


def _run_reason(run: dict, kind: str, suggest: str) -> str:
    if kind == "extra":
        return f"extra speech {run['text']}"
    if kind == "retake" and suggest == "list":
        return f"reworded take of {run['sentence']}"
    if kind == "retake":
        command = run["command"]
        ending = "" if command is None else f" ended with {command}"
        return f"earlier take of {run['sentence']}{ending}"
    return f"{'filler' if kind == 'filler' else 'repeated'} {run['text']}"


def _pauses(tokens: list[dict], runs: list[dict], run_trims: list[dict], snap: Snap) -> list[dict]:
    """A pause candidate for every gap of more than PAUSE_MIN_S between two neighbouring recognised
    units that lie between the first and the last unit matched to the script, cut from
    PAUSE_KEEP_S / 2 after the first unit to PAUSE_KEEP_S / 2 before the second. What lies inside a
    run trim that suggests a cut, or inside a pause already placed, is taken off it (a pause that is
    left in two pieces keeps the longer one); a listed run trim takes nothing off. A pause left
    shorter than PAUSE_MIN_PIECE_S is not a candidate."""
    matched = [i for i, token in enumerate(tokens) if token["s"] is not None]
    if not matched:
        return []
    owner = _owners(tokens, runs)
    margin = PAUSE_KEEP_S / 2
    taken = [(candidate["start"], candidate["end"]) for candidate in run_trims if candidate["suggest"] == "cut"]
    out = []
    for i in range(matched[0], matched[-1]):
        a, b = tokens[i]["end"], tokens[i + 1]["start"]
        gap = round(b - a, 6)
        if gap <= PAUSE_MIN_S:
            continue
        span = (snap(a + margin, a, b), snap(b - margin, a, b))
        pieces = [(lo, hi) for lo, hi in _subtract(span, taken) if lo < hi]
        if not pieces:
            continue
        start, end = max(pieces, key=lambda piece: piece[1] - piece[0])
        if end - start < PAUSE_MIN_PIECE_S - _EPSILON:
            continue
        taken.append((start, end))
        out.append({
            "kind": "pause", "start": start, "end": end, "sentence": owner[i + 1], "text": "",
            "reason": pause_reason(gap, gap - (end - start)), "suggest": "cut", "length": 0,
        })
    return out


def pause_reason(gap: float, left: float) -> str:
    """The reason text of a pause candidate: the gap between the two units and what the cut leaves of it."""
    return f"pause {gap:.2f}s -> {left:.2f}s"


def pause_lengths(reason: str) -> tuple[float, float] | None:
    """(gap, left) read back from a pause_reason text, rounded as the text has them; None for any other reason."""
    found = _PAUSE_REASON.fullmatch(reason)
    return None if found is None else (float(found[1]), float(found[2]))


def _owners(tokens: list[dict], runs: list[dict]) -> list[str]:
    """The sentence each recognised unit belongs to: its own, or the one the run it is in belongs to."""
    owner = [token["s"] for token in tokens]
    for run in runs:
        owner[run["h0"]:run["h1"]] = [run["sentence"]] * (run["h1"] - run["h0"])
    return owner


def _subtract(span: tuple[float, float], taken: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of `span` that no interval of `taken` covers."""
    pieces = [span]
    for lo, hi in taken:
        left = []
        for a, b in pieces:
            if hi <= a or lo >= b:
                left.append((a, b))
                continue
            if a < lo:
                left.append((a, lo))
            if hi < b:
                left.append((hi, b))
        pieces = left
    return pieces


def _bounds(transcript: dict, snap: Snap, env: Envelope, threshold: float) -> dict:
    """Where the reel may start (cut_in) and end (cut_out) around each spoken sentence: the middle of
    the gap to the neighbouring recognised unit, but not further than LEAD_S from the sentence (the
    cut is searched inside that limit too). Before the first unit the gap starts at second 0, after
    the last it ends with the audio."""
    tokens, audio_s = transcript["tokens"], transcript["audio_s"]
    out = {}
    for sentence in transcript["sentences"]:
        if not sentence["spoken"]:
            continue
        start, end = sentence["start"], sentence["end"]
        before = max((t["end"] for t in tokens if t["start"] < start - _EPSILON), default=0.0)
        after = min((t["start"] for t in tokens if t["end"] > end + _EPSILON), default=audio_s)
        cut_in = snap(max((before + start) / 2, start - LEAD_S), max(before, start - LEAD_S), start)
        cut_out = snap(min((end + after) / 2, end + LEAD_S), end, min(after, end + LEAD_S))
        out[sentence["id"]] = {
            "cut_in": cut_in,
            "cut_out": cut_out,
            "needs_listen": not (is_silent(cut_in, env, threshold) and is_silent(cut_out, env, threshold)),
        }
    return out


def _finish(candidate: dict, env: Envelope, threshold: float) -> dict:
    silent = is_silent(candidate["start"], env, threshold) and is_silent(candidate["end"], env, threshold)
    order = ("id", "kind", "start", "end", "sentence", "text", "reason", "suggest", "length")
    return {**{key: candidate[key] for key in order}, "needs_listen": not silent}


# ---- trim step --------------------------------------------------------------------------------


def _summary(doc: dict) -> dict:
    trims = doc["trims"]
    return {
        "trims": len(trims),
        "by_kind": dict(Counter(t["kind"] for t in trims)),
        "suggest_cut": sum(t["suggest"] == "cut" for t in trims),
        "needs_listen": sum(t["needs_listen"] for t in trims),
        "warnings": doc.get("warnings", []),
    }


def trim(p: Project, force: bool = False) -> dict:
    """Run the trim step: read work/transcript.json and work/audio48k.wav and write work/trim.json.
    Returns trims, by_kind, suggest_cut, needs_listen and warnings (and skipped, from run_step)."""
    transcript_path = p.work / "transcript.json"
    inputs = {"transcript": transcript_path, "audio": p.work / "audio48k.wav"}
    out = p.work / "trim.json"

    def run() -> dict:
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PipelineError(f"cannot read {transcript_path}: {error}") from error
        refuse_older_document(transcript_path, transcript, "transcribe", FORMAT_VERSIONS["transcribe"])
        doc = build_trims(transcript, envelope(inputs["audio"]))
        write_text_atomically(out, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        return _summary(doc)

    done = run_step(p, "trim", inputs, [out], run, force=force)
    if done["skipped"]:
        done = {**_summary(json.loads(out.read_text(encoding="utf-8"))), "skipped": True}
    return done
