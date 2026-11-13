"""Script-to-speech alignment, insertion-run classes and work/transcript.json.

The script's comparison units are aligned with the ASR units by dynamic programming. The result
says which take of a sentence is kept, which stretches the speaker added (retakes, fillers,
repeats, reworded or off-script speech), and gives every script unit a time on the prep timeline
(second 0 is the first video frame). The transcribe command runs or reads the recognition and
writes the transcript.
"""

import importlib.metadata
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline.asr import MODELS, AsrResult, AsrToken, load_asr, transcribe_audio
from pipeline.errors import PipelineError
from pipeline.project import FORMAT_VERSIONS, Project, load_state, run_step
from pipeline.script import Marker, Sentence, parse_script
from pipeline.textnorm import FILLERS, Key, char_key, to_taiwan, unit_alts

TRANSCRIPT_VERSION = FORMAT_VERSIONS["transcribe"]
RETAKE_SIMILARITY = 0.80  # an insertion run this close to a sentence start is an NG take
REWORDED_SIMILARITY = 0.50  # from here up to RETAKE_SIMILARITY it is a reworded take
RETAKE_MIN_UNITS = 4  # shorter runs are never retakes or reworded takes
_STUTTER_UNITS = (1, 2)  # a stutter repeats this many units from the start of the words after it
LOW_MATCH_RATIO = 0.90  # a sentence matching less than this needs listening
ASR_TEXT_RATIO = 0.50  # a spoken sentence matching less than this is shown as recognised
_EPSILON = 1e-9  # similarity is a float; a run exactly at a threshold must count as reaching it
# The longest phrase whose s2twp conversion changes length has 10 characters (聖文森特和格林納丁斯 ->
# 聖文森及格瑞那丁 in OpenCC's TWPhrases): the last 10 characters before a phrase ends hold all of it.
_PHRASE_CHARS = 10

ASR_MODEL_FILE = "asr-model.txt"
LEFT_OUT_OF_ASR_TEXT = ("filler", "repeat", "retake")  # run kinds a sentence shown as recognised omits
FILLER_SPELLINGS = FILLERS + ("額", "誒", "恩")  # how a filler may be written, in Taiwan text
HESITATIONS = tuple(word for word in FILLER_SPELLINGS if len(word) == 1)  # one-character fillers (嗯, 呃 ...)
# What a speaker says to start a sentence again: a run that ends with one, right before the script, is an NG take.
COMMAND_PHRASES = ("不對", "重來", "再來一次", "再一次", "重講", "我再講一次", "重新講")


@dataclass(frozen=True)
class Op:
    kind: str  # "match", "sub", "del" or "ins"
    s: int | None  # index of the script unit in the whole script; None for "ins"
    h0: int  # the ASR units used are h0..h1 (h0 == h1 for "del")
    h1: int


# ---- alignment -------------------------------------------------------------------------------

_INF = np.int64(1) << 60
# Option ranks, in the tie order of the backtrace: match, then insertion, then sub, then deletion.
_MATCH, _INS, _SUB, _DEL, _NONE = 0, 1, 2, 3, 9


class _Hits:
    """Which ASR units each key meets (shares a reading with), cached per key and per alternative."""

    def __init__(self, asr_keys: Sequence[Key]):
        self.m = len(asr_keys)
        where: dict[str, list[int]] = {}
        for pos, key in enumerate(asr_keys):
            for reading in key:
                where.setdefault(reading, []).append(pos)
        self.where = {reading: np.array(positions) for reading, positions in where.items()}
        self.hits: dict[Key, np.ndarray] = {}
        self.misses: dict[tuple[Key, ...], np.ndarray] = {}

    def hit(self, key: Key) -> np.ndarray:
        if key not in self.hits:
            out = np.zeros(self.m, dtype=bool)
            for reading in key:
                if reading in self.where:
                    out[self.where[reading]] = True
            self.hits[key] = out
        return self.hits[key]

    def miss_counts(self, alt: tuple[Key, ...]) -> np.ndarray:
        """For every j from len(alt) to m: how many keys of `alt` miss the ASR units j-len(alt)..j.
        Entries below len(alt) are -1 (the alternative does not fit)."""
        if alt not in self.misses:
            length = len(alt)
            out = np.full(self.m + 1, -1, dtype=np.int64)
            if length <= self.m:
                count = self.m - length + 1
                total = np.zeros(count, dtype=np.int64)
                for t, key in enumerate(alt):
                    total += ~self.hit(key)[t:t + count]
                out[length:] = total
            self.misses[alt] = out
        return self.misses[alt]


def _enter(a: np.ndarray, b: np.ndarray, rank_a: np.ndarray, rank_b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The cheaper of two predecessor states per column, and which one (0 = a, 1 = b). On equal cost
    the state whose own best option comes first in the tie order wins, and a on a full tie."""
    pick_b = (b < a) | ((b == a) & (rank_b < rank_a))
    return np.minimum(a, b), pick_b.astype(np.int16)


def align(
    script_alts: list[list[tuple[Key, ...]]], asr_keys: list[Key], sentence_of: Sequence[int] | None = None
) -> list[Op]:
    """Align script units (each a list of alternatives, each a tuple of per-character keys) with ASR
    unit keys. Returns the operations in order: every script unit once, as match, sub or del, and
    every ASR unit not used by one of them as an ins.

    Cost: a script unit against one ASR unit costs 0 when their keys share a reading and 1 when not;
    a number unit eats as many ASR units as the reading it is matched by has characters, at the
    cost of the characters that do not match (the digits alternative eats one ASR unit); every
    inserted ASR unit and every deleted script unit costs 1.

    Secondary cost: among alignments of equal cost, one with fewer pairs of neighbouring script
    units in the same sentence where one is deleted and the other matched or substituted is
    preferred. It only chooses between alignments of equal cost and never makes the cost higher.
    Without it, a skipped sentence that shares words with its neighbour (第一句話 / 第二句話) takes
    the neighbour's spoken words, and would be captioned over the neighbour's audio.
    `sentence_of` gives the sentence of each script unit; without it all units are one sentence.

    Among options still equal the backtrace prefers match, then ins, then sub, then del, so a
    sentence spoken several times binds to its last take and the earlier takes become insertions.
    """
    n, m = len(script_alts), len(asr_keys)
    sentence_of = [0] * n if sentence_of is None else list(sentence_of)
    if len(sentence_of) != n:
        raise ValueError(f"sentence_of has {len(sentence_of)} entries for {n} script units")
    weight = n + 1  # one point of cost outweighs any secondary total (at most n - 1)
    hits = _Hits(asr_keys)
    ramp = np.arange(m + 1, dtype=np.int64) * weight

    # Two states per cell: the last script unit is bound (matched or substituted) or deleted. Row 0
    # (no script unit yet) counts as bound and holds only insertions.
    bound, deleted = ramp.copy(), np.full(m + 1, _INF)
    rank_bound = np.full(m + 1, _INS, dtype=np.int8)
    rank_bound[0] = _MATCH
    rank_deleted = np.full(m + 1, _NONE, dtype=np.int8)
    # Pointers. Bound state: 0 = ins, 1 + 2 * alternative + predecessor state = that unit bound.
    # Deleted state: 0 = ins, 1 + predecessor state = that unit deleted.
    ptr_bound = np.zeros((n + 1, m + 1), dtype=np.int16)
    ptr_deleted = np.zeros((n + 1, m + 1), dtype=np.int16)

    for i in range(1, n + 1):
        switch = 1 if i >= 2 and sentence_of[i - 1] == sentence_of[i - 2] else 0
        into_bound, from_bound = _enter(bound, deleted + switch, rank_bound, rank_deleted)
        into_deleted, from_deleted = _enter(bound + switch, deleted, rank_bound, rank_deleted)

        options = []  # (code, candidate cost, miss count) per alternative
        best = np.full(m + 1, _INF)
        for a, alt in enumerate(script_alts[i - 1]):
            length = len(alt)
            misses = hits.miss_counts(alt)
            cost = np.full(m + 1, _INF)
            code = np.zeros(m + 1, dtype=np.int16)
            if length <= m:
                cost[length:] = into_bound[:m - length + 1] + misses[length:] * weight
                code[length:] = 1 + 2 * a + from_bound[:m - length + 1]
            options.append((code, cost, misses))
            best = np.minimum(best, cost)
        # Insertions after the unit: row[j] = min over k <= j of (candidate[k] + (j - k) * weight).
        row_bound = np.minimum(np.minimum.accumulate(best - ramp) + ramp, _INF)
        deleting = into_deleted + weight
        row_deleted = np.minimum(np.minimum.accumulate(deleting - ramp) + ramp, _INF)

        # Pointers, written from the last-preferred option to the first so the preferred one stays.
        code_bound = np.zeros(m + 1, dtype=np.int16)
        new_rank_bound = np.full(m + 1, _NONE, dtype=np.int8)
        for code, cost, misses in reversed(options):
            ok = (cost == row_bound) & (misses > 0)
            code_bound[ok], new_rank_bound[ok] = code[ok], _SUB
        ins = np.zeros(m + 1, dtype=bool)
        ins[1:] = row_bound[:-1] + weight == row_bound[1:]
        code_bound[ins], new_rank_bound[ins] = 0, _INS
        for code, cost, misses in reversed(options):
            ok = (cost == row_bound) & (misses == 0)
            code_bound[ok], new_rank_bound[ok] = code[ok], _MATCH

        code_deleted = np.zeros(m + 1, dtype=np.int16)
        new_rank_deleted = np.full(m + 1, _NONE, dtype=np.int8)
        ok = deleting == row_deleted
        code_deleted[ok], new_rank_deleted[ok] = 1 + from_deleted[ok], _DEL
        ins = np.zeros(m + 1, dtype=bool)
        ins[1:] = row_deleted[:-1] + weight == row_deleted[1:]
        code_deleted[ins], new_rank_deleted[ins] = 0, _INS

        ptr_bound[i], ptr_deleted[i] = code_bound, code_deleted
        bound, deleted, rank_bound, rank_deleted = row_bound, row_deleted, new_rank_bound, new_rank_deleted

    _, last_state = _enter(bound[m:], deleted[m:], rank_bound[m:], rank_deleted[m:])
    return _backtrace(script_alts, asr_keys, ptr_bound, ptr_deleted, int(last_state[0]))


def _backtrace(
    script_alts: list[list[tuple[Key, ...]]],
    asr_keys: list[Key],
    ptr_bound: np.ndarray,
    ptr_deleted: np.ndarray,
    state: int,
) -> list[Op]:
    i, j = ptr_bound.shape[0] - 1, ptr_bound.shape[1] - 1
    ops: list[Op] = []
    while i > 0 or j > 0:
        code = int((ptr_bound if state == 0 else ptr_deleted)[i, j])
        if code == 0:
            ops.append(Op("ins", None, j - 1, j))
            j -= 1
        elif state == 0:
            a, state = divmod(code - 1, 2)
            alt = script_alts[i - 1][a]
            h0 = j - len(alt)
            kind = "match" if all(key & asr_keys[h0 + t] for t, key in enumerate(alt)) else "sub"
            ops.append(Op(kind, i - 1, h0, j))
            i, j = i - 1, h0
        else:
            ops.append(Op("del", i - 1, j, j))
            i, state = i - 1, code - 1
    ops.reverse()
    return ops


# ---- insertion runs -----------------------------------------------------------------------------

_FILLER_KEYS = [tuple(char_key(c) for c in word) for word in FILLERS]


def _same(a: Sequence[Key], b: Sequence[Key]) -> bool:
    return len(a) == len(b) and all(x & y for x, y in zip(a, b))


def _all_fillers(run: Sequence[Key]) -> bool:
    """Whether `run` splits completely into FILLERS words (key by key, sharing a reading)."""
    reachable = [True] + [False] * len(run)
    for p in range(len(run)):
        if reachable[p]:
            for word in _FILLER_KEYS:
                if _same(run[p:p + len(word)], word):
                    reachable[p + len(word)] = True
    return bool(run) and reachable[-1]


def _all_filler_text(text: str) -> bool:
    """Whether `text` splits completely into FILLER_SPELLINGS words, character for character."""
    reachable = [True] + [False] * len(text)
    for p in range(len(text)):
        if reachable[p]:
            for word in FILLER_SPELLINGS:
                if text.startswith(word, p):
                    reachable[p + len(word)] = True
    return bool(text) and reachable[-1]


def command_phrase(text: str) -> str | None:
    """The longest of COMMAND_PHRASES that `text` ends with, once trailing whitespace and
    FILLER_SPELLINGS words are taken off (as many as there are), or None. Compared by text, not by
    reading, so a word that only sounds like a command is none."""
    while True:
        text = text.rstrip()
        word = next((word for word in FILLER_SPELLINGS if text.endswith(word)), None)
        if word is None:
            break
        text = text[:-len(word)]
    return max((phrase for phrase in COMMAND_PHRASES if text.endswith(phrase)), key=len, default=None)


def _edit_distance(a: Sequence[Key], b: Sequence[Key]) -> int:
    previous = list(range(len(b) + 1))
    for x, key in enumerate(a, 1):
        current = [x]
        for y, other in enumerate(b, 1):
            current.append(min(previous[y] + 1, current[y - 1] + 1, previous[y - 1] + (0 if key & other else 1)))
        previous = current
    return previous[-1]


def _start_similarity(run: Sequence[Key], sentence: Sequence[Key]) -> float:
    """1 - edit distance / run length, against as many units from the start of `sentence`."""
    if not run or not sentence:
        return 0.0
    return max(0.0, 1.0 - _edit_distance(run, sentence[:len(run)]) / len(run))


def _stutter(said: Sequence[Key], next_keys: Sequence[Key]) -> bool:
    """Whether `said` is the first unit, or the first two units, of `next_keys` said once or more."""
    return any(
        len(next_keys) >= p and len(said) >= p and len(said) % p == 0
        and all(_same(said[at:at + p], next_keys[:p]) for at in range(0, len(said), p))
        for p in _STUTTER_UNITS
    )


def classify_run(
    run_keys: list[Key],
    prev_keys: list[Key],
    next_keys: list[Key],
    prev_sentence: list[Key],
    next_sentence: list[Key],
    run_text: str | None = None,
    unit_texts: Sequence[str] | None = None,
) -> tuple[str, float]:
    """The kind of an insertion run and its similarity to the start of the sentence after it or
    before it, whichever is higher.

    `prev_keys` and `next_keys` are the bound units right before and after the run; `prev_sentence`
    and `next_sentence` are the keys of the sentences those units belong to. `unit_texts` is the
    Taiwan text of each unit of the run; its units whose text is one of HESITATIONS are hesitations.
    Every rule after the filler rule leaves them out, and so does the similarity returned. The first
    rule that holds decides: filler; retake (a run right before bound units whose text, without
    hesitations, ends with a command, see command_phrase); retake (at least RETAKE_MIN_UNITS long and
    at least RETAKE_SIMILARITY like either sentence start); repeat (the same keys as the bound units
    right after or right before it, or a stutter: the first one or two bound units after it, said
    once or more); reworded (at least RETAKE_MIN_UNITS long and at least REWORDED_SIMILARITY like a
    sentence start); else extra.

    A run is a filler when `run_text` (its Taiwan text; the unit texts joined when only `unit_texts`
    is given), hesitations included, is made of FILLER_SPELLINGS words. Without either, when its keys
    are FILLERS words, which also lets real words that sound alike (謝謝, 啊) count as fillers. The
    command rule needs a text; without `unit_texts` it reads `run_text` as it is."""
    kind, similarity, _ = _classify_run(
        run_keys, prev_keys, next_keys, prev_sentence, next_sentence, run_text, unit_texts
    )
    return kind, similarity


def _classify_run(
    run_keys: list[Key],
    prev_keys: list[Key],
    next_keys: list[Key],
    prev_sentence: list[Key],
    next_sentence: list[Key],
    run_text: str | None,
    unit_texts: Sequence[str] | None,
) -> tuple[str, float, str | None]:
    """classify_run's kind and similarity, and the command phrase when the command rule decided the
    kind (None otherwise)."""
    if unit_texts is not None and len(unit_texts) != len(run_keys):
        raise ValueError(f"unit_texts has {len(unit_texts)} entries for {len(run_keys)} run units")
    if run_text is None and unit_texts is not None:
        run_text = "".join(unit_texts)
    if unit_texts is None:
        said, said_text = list(run_keys), run_text
    else:
        kept = [k for k, text in enumerate(unit_texts) if text not in HESITATIONS]
        said, said_text = [run_keys[k] for k in kept], "".join(unit_texts[k] for k in kept)
    size = len(said)
    similarity = max(_start_similarity(said, next_sentence), _start_similarity(said, prev_sentence))
    long_enough = size >= RETAKE_MIN_UNITS
    command = command_phrase(said_text) if next_keys and said_text is not None else None
    if _all_fillers(run_keys) if run_text is None else _all_filler_text(run_text):
        kind, command = "filler", None
    elif command is not None:
        kind = "retake"
    elif long_enough and similarity + _EPSILON >= RETAKE_SIMILARITY:
        kind = "retake"
    elif _same(said, next_keys[:size]) or (size and _same(said, prev_keys[-size:])) or _stutter(said, next_keys):
        kind = "repeat"
    elif long_enough and similarity + _EPSILON >= REWORDED_SIMILARITY:
        kind = "reworded"
    else:
        kind = "extra"
    return kind, round(similarity, 4), command


# ---- transcript -------------------------------------------------------------------------------


def _seconds(value: float) -> float:
    return round(float(value), 6)


def _changes_length(texts: list[str]) -> bool:
    joined = "".join(texts)
    return len(to_taiwan(joined)) != len(joined)


def _stretch_texts(texts: list[str]) -> list[str]:
    """The Taiwan text of each ASR unit of one displayed stretch (a run, or a sentence shown as
    recognised), from the units' own texts.

    A phrase whose conversion changes length (博客 -> 部落格, 方便面 -> 泡麵) cannot be cut back into its
    units, so its units are converted one at a time. Each piece of the stretch between such phrases is
    converted as one text, so OpenCC sees whole phrases (头发 -> 頭髮, 软件 -> 軟體), and cut back into
    units; a total length alone would not do, since a phrase that grew and one that shrank cancel out.

    The phrases are found in one pass. At every unit, it and the units before it that fit in
    _PHRASE_CHARS characters (none of a phrase found earlier) are converted together; when that changes
    the length, a phrase ends at this unit, and it is the shortest text ending here whose conversion
    changes length. Apart from the pieces, no text longer than _PHRASE_CHARS characters is converted but
    a single unit, so a stretch of n characters passes OpenCC at most about 18 n characters."""
    phrases: list[tuple[int, int]] = []  # (first unit, end) of every phrase that changes length
    barrier = 0
    for end in range(1, len(texts) + 1):
        start, size = end - 1, len(texts[end - 1])
        while start > barrier and size + len(texts[start - 1]) <= _PHRASE_CHARS:
            start -= 1
            size += len(texts[start])
        if _changes_length(texts[start:end]):
            first = next(a for a in range(end - 1, start - 1, -1) if _changes_length(texts[a:end]))
            phrases.append((first, end))
            barrier = end
    out: list[str] = []
    at = 0
    for first, end in [*phrases, (len(texts), len(texts))]:
        out += _piece_texts(texts[at:first])
        out += [to_taiwan(text) for text in texts[first:end]]
        at = end
    return out


def _piece_texts(texts: list[str]) -> list[str]:
    """`texts` converted as one text and cut back into units, or one at a time when the whole changes
    length after all (a phrase that only matches when the piece is converted whole)."""
    if not texts:
        return []
    joined = "".join(texts)
    whole = to_taiwan(joined)
    if len(whole) != len(joined):
        return [to_taiwan(text) for text in texts]
    out, at = [], 0
    for text in texts:
        out.append(whole[at:at + len(text)])
        at += len(text)
    return out


def _convert_stretch(chosen: list[int], tokens: list[AsrToken], texts: list[str]) -> None:
    """Set texts[h], for every ASR unit h in the displayed stretch `chosen`, to its Taiwan text."""
    for h, text in zip(chosen, _stretch_texts([tokens[h].text for h in chosen])):
        texts[h] = text


def _join(pieces: list[str], latin: list[bool]) -> tuple[str, list[list[int]]]:
    """Pieces joined into one text, with a space between two Latin words, and each piece's span."""
    text, spans = "", []
    for k, piece in enumerate(pieces):
        if k and latin[k] and latin[k - 1]:
            text += " "
        spans.append([len(text), len(text) + len(piece)])
        text += piece
    return text, spans


def _non_decreasing(units: list[dict]) -> list[dict]:
    start = None
    for unit in units:
        if start is not None and unit["start"] < start:
            unit["start"] = start
        unit["end"] = max(unit["end"], unit["start"])
        start = unit["start"]
    return units


def _script_units(sentence: Sentence, ops: list[Op], tokens: list[AsrToken], fallback: float) -> list[dict]:
    """The script units of one sentence with their times.

    A bound unit takes its ASR time (a number from the first ASR unit it ate to the last). A deleted
    unit or any Latin word between two timed units of the sentence shares the gap between them
    equally with its neighbours ("interp"). Otherwise a Latin word keeps its own ASR time, kept
    inside its timed neighbour ("interp" when that leaves it no length), and a deleted unit gets
    length 0 at the nearest unit that has a time, or at `fallback` when no unit of the sentence has
    one."""
    count = len(ops)
    own: list[tuple[float, float] | None] = []
    timed: list[bool] = []
    for unit, op in zip(sentence.units, ops):
        bound = op.kind in ("match", "sub")
        own.append((tokens[op.h0].start, tokens[op.h1 - 1].end) if bound else None)
        timed.append(bound and unit.kind != "latin")
    times: list[tuple[float, float] | None] = [own[k] if timed[k] else None for k in range(count)]
    source = ["asr" if timed[k] else "interp" for k in range(count)]

    k = 0
    while k < count:
        if timed[k]:
            k += 1
            continue
        end = k
        while end < count and not timed[end]:
            end += 1
        left = times[k - 1] if k > 0 else None
        right = times[end] if end < count else None
        if left and right:
            lo, hi = min(left[1], right[0]), right[0]
            for x in range(k, end):
                times[x] = (lo + (hi - lo) * (x - k) / (end - k), lo + (hi - lo) * (x - k + 1) / (end - k))
        else:
            for x in range(k, end):
                if own[x] is not None:  # a Latin word with its own time
                    start, stop = own[x]
                    if left:
                        start = max(start, left[1])
                        stop = max(stop, start)
                    if right:
                        stop = min(stop, right[0])
                        start = min(start, stop)
                    squeezed = stop <= start and (start, stop) != own[x]  # the word's own time is gone
                    times[x], source[x] = (start, stop), "interp" if squeezed else "asr"
        k = end

    for x in range(count):
        if times[x] is None:  # length 0 at the nearest unit with a time
            before = next((times[y][1] for y in range(x - 1, -1, -1) if times[y] is not None), None)
            after = next((times[y][0] for y in range(x + 1, count) if times[y] is not None), None)
            at = before if before is not None else after if after is not None else fallback
            times[x] = (at, at)

    units = [
        {
            "text": sentence.text[u.span[0]:u.span[1]],
            "span": list(u.span),
            "start": _seconds(lo),
            "end": _seconds(hi),
            "src": src,
        }
        for u, (lo, hi), src in zip(sentence.units, times, source)
    ]
    return _non_decreasing(units)


def _asr_units(
    chosen: list[int], tokens: list[AsrToken], texts: list[str], latin: list[bool]
) -> tuple[str, list[dict]]:
    """The text made of the ASR units `chosen` (in Taiwan wording, a space between Latin words) and
    those units with their ASR times and their spans in that text."""
    text, spans = _join([texts[h] for h in chosen], [latin[h] for h in chosen])
    units = [
        {
            "text": texts[h],
            "span": span,
            "start": _seconds(tokens[h].start),
            "end": _seconds(tokens[h].end),
            "src": "asr",
        }
        for h, span in zip(chosen, spans)
    ]
    return text, _non_decreasing(units)


def _sentence_keys(sentence: Sentence) -> list[Key]:
    """The keys of a sentence as it would be spoken: a number by its standard reading."""
    return [key for unit in sentence.units for key in unit_alts(unit)[0]]


def build_transcript(
    sentences: list[Sentence], markers: list[Marker], asr: AsrResult, audio_s: float, fallback_used: bool
) -> dict:
    """The work/transcript.json document for a script and its recognition result."""
    if not sentences:
        raise PipelineError("the script has no sentence to align")
    flat = [(k, unit) for k, sentence in enumerate(sentences) for unit in sentence.units]
    tokens = asr.tokens
    ops = align([unit_alts(unit) for _, unit in flat], [t.key for t in tokens], [k for k, _ in flat])
    keys_of = [_sentence_keys(sentence) for sentence in sentences]

    # Taiwan text, one ASR unit at a time: what runs and partial takes are classified by.
    texts = [to_taiwan(t.text) for t in tokens]
    script_ops = [op for op in ops if op.kind != "ins"]  # one per script unit, in script order
    script_ops, partial_takes = _unbind_skipped(sentences, script_ops, tokens, texts, keys_of)
    token_unit: list[int | None] = [None] * len(tokens)
    for op in script_ops:
        for h in range(op.h0, op.h1):
            token_unit[h] = op.s
    token_sentence = [None if u is None else flat[u][0] for u in token_unit]
    latin = [t.kind == "latin" for t in tokens]
    runs = _runs(sentences, tokens, token_sentence, texts, latin, keys_of, partial_takes)

    groups, first = [], 0  # the script operations of each sentence
    for sentence in sentences:
        groups.append(script_ops[first:first + len(sentence.units)])
        first += len(sentence.units)
    # Every displayed stretch is converted as a whole, once: the caption of each sentence shown as
    # recognised, then each run that is not in such a caption. A run in one takes the caption's text.
    shown = _recognised_stretches(sentences, groups, token_sentence, runs)
    for stretch in shown.values():
        _convert_stretch(stretch, tokens, texts)
    in_captions = {h for stretch in shown.values() for h in stretch}
    for run in runs:
        h0, h1 = run["h0"], run["h1"]
        if h0 not in in_captions:
            _convert_stretch(list(range(h0, h1)), tokens, texts)
        run["text"], _ = _join(texts[h0:h1], latin[h0:h1])

    out_sentences = []
    first = 0
    last_end: float | None = None
    for k, sentence in enumerate(sentences):
        sentence_ops = groups[k]
        first += len(sentence.units)
        spoken, ratio = _spoken_and_ratio(sentence_ops)
        owned = [run for run in runs if run["sentence"] == sentence.id]
        reasons = []
        if ratio < LOW_MATCH_RATIO:
            reasons.append("low_match")
        if any(run["kind"] == "extra" for run in owned):
            reasons.append("extra_speech")
        if k in shown:
            reasons.append("asr_text")
            text, units = _asr_units(shown[k], tokens, texts, latin)
            display_source = "asr"
        else:
            fallback = last_end if last_end is not None else _next_spoken_start(script_ops[first:], tokens)
            units = _script_units(sentence, sentence_ops, tokens, fallback)
            text, display_source = sentence.text, "script"
        start, end = units[0]["start"], max(unit["end"] for unit in units)
        last_end = end
        out_sentences.append({
            "id": sentence.id,
            "text": text,
            "script_text": sentence.text,
            "display_source": display_source,
            "spoken": spoken,
            "start": start,
            "end": end,
            "ratio": ratio,
            "needs_listen": bool(reasons),
            "reasons": reasons,
            "units": units,
        })

    out_tokens = [
        {
            "text": texts[h],
            "start": _seconds(t.start),
            "end": _seconds(t.end),
            "op": "ins" if token_unit[h] is None else script_ops[token_unit[h]].kind,
            "s": None if token_sentence[h] is None else sentences[token_sentence[h]].id,
        }
        for h, t in enumerate(tokens)
    ]
    return {
        "version": TRANSCRIPT_VERSION,
        "asr": {"model": asr.model, "fallback_used": fallback_used, "runtime": _runtime()},
        "audio_s": _seconds(audio_s),
        "tokens": out_tokens,
        "runs": runs,
        "sentences": out_sentences,
        "markers": [{"before": marker.before, "text": marker.text} for marker in markers],
    }


def _spoken_and_ratio(sentence_ops: list[Op]) -> tuple[bool, float]:
    """Whether a sentence was spoken (a unit of it is bound), and the share of its units matched."""
    spoken = any(op.kind in ("match", "sub") for op in sentence_ops)
    return spoken, round(sum(op.kind == "match" for op in sentence_ops) / len(sentence_ops), 4)


def _recognised_stretches(
    sentences: list[Sentence], groups: list[list[Op]], token_sentence: list[int | None], runs: list[dict]
) -> dict[int, list[int]]:
    """For each sentence shown as recognised (spoken, with a ratio below ASR_TEXT_RATIO), by its index:
    the ASR units its caption shows, in order. They are its own units and those of its runs, but for
    the run kinds LEFT_OUT_OF_ASR_TEXT."""
    shown: dict[int, list[int]] = {}
    for k, (sentence, sentence_ops) in enumerate(zip(sentences, groups)):
        spoken, ratio = _spoken_and_ratio(sentence_ops)
        if spoken and ratio < ASR_TEXT_RATIO:
            chosen = {h for h, s in enumerate(token_sentence) if s == k}
            for run in runs:
                if run["sentence"] == sentence.id and run["kind"] not in LEFT_OUT_OF_ASR_TEXT:
                    chosen.update(range(run["h0"], run["h1"]))
            shown[k] = sorted(chosen)
    return shown


def _next_spoken_start(later_ops: list[Op], tokens: list[AsrToken]) -> float:
    """The start of the first ASR unit bound to a later script unit, or 0 when there is none."""
    for op in later_ops:
        if op.kind in ("match", "sub"):
            return tokens[op.h0].start
    return 0.0


def _unbind_skipped(
    sentences: list[Sentence],
    script_ops: list[Op],
    tokens: list[AsrToken],
    texts: list[str],
    keys_of: list[list[Key]],
) -> tuple[list[Op], set[int]]:
    """Turn sentences the speaker skipped into deletions, giving their ASR units back to the
    insertions, so what was said in a skipped sentence's place becomes a run (a filler, an NG take).
    Returns the new operations and the ASR units given back by the second rule below (partial takes).

    The aligner binds a skipped sentence to whatever was said in its gap, because binding k units
    there costs at most max(k, sentence length), less than the k + length of leaving them apart. One
    pass in script order: a sentence is skipped when none of its units is a match, or when its ratio
    is below ASR_TEXT_RATIO and the ASR units bound to it, hesitations left out (units whose Taiwan
    text in `texts` is one of HESITATIONS), at least RETAKE_MIN_UNITS of them as for a retake run,
    are at least RETAKE_SIMILARITY like the start of the next sentence that has a match (compared as
    classify_run compares). A partial take given back holds all its units, hesitations included."""
    spans, first = [], 0
    for sentence in sentences:
        spans.append((first, first + len(sentence.units)))
        first += len(sentence.units)
    has_match = [any(op.kind == "match" for op in script_ops[a:b]) for a, b in spans]
    out = list(script_ops)
    partial_takes: set[int] = set()
    for k, (a, b) in enumerate(spans):
        bound = [op for op in script_ops[a:b] if op.kind in ("match", "sub")]
        if not bound:
            continue
        skipped = not has_match[k]
        if not skipped and sum(op.kind == "match" for op in bound) / (b - a) < ASR_TEXT_RATIO:
            later = next((j for j in range(k + 1, len(sentences)) if has_match[j]), None)
            said = [tokens[h].key for op in bound for h in range(op.h0, op.h1) if texts[h] not in HESITATIONS]
            if later is not None and len(said) >= RETAKE_MIN_UNITS:
                skipped = _start_similarity(said, keys_of[later]) + _EPSILON >= RETAKE_SIMILARITY
                if skipped:
                    partial_takes.update(h for op in bound for h in range(op.h0, op.h1))
        if skipped:
            for x in range(a, b):
                op = out[x]
                if op.kind != "del":
                    out[x] = Op("del", op.s, op.h0, op.h0)
    return out, partial_takes


def _runs(
    sentences: list[Sentence],
    tokens: list[AsrToken],
    token_sentence: list[int | None],
    texts: list[str],
    latin: list[bool],
    keys_of: list[list[Key]],
    partial_takes: set[int],
) -> list[dict]:
    """Every maximal stretch of inserted ASR units, with its kind, the sentence it belongs to and its
    `command`: the phrase command_phrase found when the command rule made it a retake, else None. A
    stretch that holds a partial take _unbind_skipped gave back may be cut into several runs (see
    _cut_off_fillers). `texts` holds each unit's Taiwan text converted on its own: it is what a run is
    classified by, and its text until build_transcript converts the run's displayed stretch.

    A run between two bound units of one sentence belongs to that sentence; a run between two
    sentences, or before the first bound unit, belongs to the sentence after it; a run after the
    last bound unit belongs to the sentence of that unit (the last sentence when nothing is bound).
    Every run of one stretch belongs to the same sentence and is compared with the bound units around
    the stretch."""
    bound = [s is not None for s in token_sentence]
    runs = []
    h0 = 0
    while h0 < len(tokens):
        if bound[h0]:
            h0 += 1
            continue
        h1 = h0
        while h1 < len(tokens) and not bound[h1]:
            h1 += 1
        size = h1 - h0
        before = token_sentence[h0 - 1] if h0 > 0 else None
        after = token_sentence[h1] if h1 < len(tokens) else None
        prev_start = h0
        while prev_start > max(0, h0 - size) and bound[prev_start - 1]:
            prev_start -= 1
        next_end = h1
        while next_end < min(len(tokens), h1 + size) and bound[next_end]:
            next_end += 1
        owner = after if after is not None else before if before is not None else len(sentences) - 1
        for a, b in _cut_off_fillers(texts, partial_takes, h0, h1):
            text, _ = _join(texts[a:b], latin[a:b])
            kind, similarity, command = _classify_run(
                [t.key for t in tokens[a:b]],
                [t.key for t in tokens[prev_start:h0]],
                [t.key for t in tokens[h1:next_end]],
                keys_of[before] if before is not None else [],
                keys_of[after] if after is not None else [],
                run_text=text,
                unit_texts=texts[a:b],
            )
            runs.append({
                "h0": a, "h1": b, "kind": kind, "text": text, "similarity": similarity, "command": command,
                "sentence": sentences[owner].id,
            })
        h0 = h1
    return runs


def _cut_off_fillers(texts: list[str], partial_takes: set[int], h0: int, h1: int) -> list[tuple[int, int]]:
    """The stretch of inserted ASR units h0..h1 cut into runs, as (first, end) pairs. What lies
    between the start of the stretch and the first unit of a partial take in it is a run of its own
    when it is all filler words, and so is what lies between the last such unit and the end of the
    stretch. A stretch of filler words only stays whole, and filler words anywhere else, a take's own
    among them, stay in their run."""
    taken = sorted(partial_takes.intersection(range(h0, h1)))
    if not taken or _all_filler_text("".join(texts[h0:h1])):
        return [(h0, h1)]
    first, last = taken[0], taken[-1] + 1
    if not _all_filler_text("".join(texts[h0:first])):  # also when nothing comes before the take
        first = h0
    if not _all_filler_text("".join(texts[last:h1])):
        last = h1
    return [(a, b) for a, b in ((h0, first), (first, last), (last, h1)) if a < b]


def _runtime() -> str:
    try:
        return f"mlx-qwen3-asr {importlib.metadata.version('mlx-qwen3-asr')}"
    except importlib.metadata.PackageNotFoundError:
        return "mlx-qwen3-asr unknown"


# ---- transcribe step --------------------------------------------------------------------------


def _summary(transcript: dict) -> dict:
    sentences = transcript["sentences"]
    return {
        "sentences": len(sentences),
        "spoken": sum(s["spoken"] for s in sentences),
        "needs_listen": sum(s["needs_listen"] for s in sentences),
        "model": transcript["asr"]["model"],
        "fallback_used": transcript["asr"]["fallback_used"],
    }


def _audio_seconds(audio16k: Path) -> float:
    try:
        return sf.info(str(audio16k)).duration
    except (OSError, RuntimeError) as error:
        raise PipelineError(f"cannot read the length of {audio16k}: {error}") from error


def transcribe(p: Project, model_key: str = "1.7b", asr_json: Path | None = None, force: bool = False) -> dict:
    """Run the transcribe step: recognise work/audio16k.wav (or read `asr_json`, a raw result made
    earlier) and write work/transcript.json. The model key goes into work/asr-model.txt, which is an
    input of the step together with the audio, the script and `asr_json`, so changing any of them
    runs the step again. With `asr_json` the model is the one `model_key` names and no fallback is
    recorded. Returns sentences, spoken, needs_listen, model, fallback_used and skipped."""
    audio16k = p.work / "audio16k.wav"
    script = Path(load_state(p)["source"]["script"])
    model_file = p.work / ASR_MODEL_FILE
    model_file.write_text(model_key + "\n", encoding="utf-8")
    out = p.work / "transcript.json"
    inputs = {"audio": audio16k, "script": script, "asr_model": model_file}
    if asr_json is not None:
        asr_json = asr_json.resolve()
        inputs["asr_json"] = asr_json

    def run() -> dict:
        if asr_json is None:
            raw, model_id, fallback_used = transcribe_audio(audio16k, p.work / "asr", model_key)
        else:
            raw, model_id, fallback_used = asr_json, MODELS[model_key], False
        result = load_asr(raw, model_id)
        sentences, markers = parse_script(script.read_text(encoding="utf-8"))
        transcript = build_transcript(sentences, markers, result, _audio_seconds(audio16k), fallback_used)
        temp = out.with_name(out.name + ".tmp")
        temp.write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, out)
        return _summary(transcript)

    done = run_step(p, "transcribe", inputs, [out], run, force=force)
    if done["skipped"]:
        done = {**_summary(json.loads(out.read_text(encoding="utf-8"))), "skipped": True}
    return done
