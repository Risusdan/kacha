"""The two lists at the top of the gate 1 storyboard: what the user decides, and the other places worth
hearing.

The decisions are about what is in the reel:
- asr_text: a kept sentence whose captions show the recognised text (display_source "asr");
- reworded: a reworded take, a retake that is only listed (suggest "list");
- extra: extra speech of EXTRA_ASK_MIN units or more.

The other places to listen to:
- cut_point: a cut point of the reel (_cut_edges) whose owner's bounds say it is not in silence, one item
  for each point, so the head and the tail are two;
- trim_cut_point: a candidate edit.json accepts, in the reel, whose cut point is not in silence, unless
  it is a decision already;
- low_match: a kept sentence that matches the script poorly and shows the script's text.

A sentence that edit.json gives a caption text of its own (captions.text) is in neither of these: the user has
fixed its captions, which no longer show the recognised text or the script's.

The cut points (_cut_edges) are the points of trim.json bounds that the cut reel really cuts at: the start
and the end of the reel, and for a cuts.extra range with a kept sentence on both sides the from sentence's
cut_in and the to sentence's cut_out (the item is then on that removed sentence). The start and the end
are read from the first and the last segment build_segments makes: the start is the first kept sentence's
cut_in, but when a range before it ends later (the bounds of neighbouring sentences can overlap) it is
that range's cut_out; the end is the last kept sentence's cut_out, or the cut_in of a range after it that
starts earlier. The item of the start (the end) is on the first (last) kept sentence, decided by the
bounds[sid].needs_listen of the sentence that owns the point, and when that is a removed sentence the
label says so ("片頭切點在刪除的 s01 之後"). When an accepted candidate starts or ends the reel, no sentence
owns the point, so there is no cut_point item; the candidate's own needs_listen decides, as for any
accepted candidate in the reel (trim_cut_point). A range at the head or the tail has no cut of its own
otherwise. bounds[sid].needs_listen is one flag for both cut points of a sentence. A range edge inside an
accepted candidate is still counted as used.

A candidate is in the reel as trims_in_reel says. A kept sentence is one edit.kept_sentences keeps. Each
item has a clip to play: a sentence with LISTEN_PAD_S around it, a candidate with TRIM_PAD_S, a cut point
with EDGE_PAD_S. A clip starts at 0 at the earliest; its end is left as it is, and the storyboard keeps
it inside the audio. Each list runs in source time order, on a tie in the order of the kinds above; the
numbers run from 1 through the decisions and go on through the other list.
"""

from dataclasses import dataclass, replace

from pipeline.edit import Segment, build_segments, caption_texts, in_reel, kept_sentences, to_frame

EXTRA_ASK_MIN = 3  # extra speech of this many units or more is a decision
LISTEN_PAD_S = 0.3  # a sentence clip runs this far before and after the sentence
EDGE_PAD_S = 1.0  # a clip of a cut point runs this far before and after the point
TRIM_PAD_S = 1.0  # a candidate clip runs this far before and after the candidate

DECISION_KINDS = ("asr_text", "reworded", "extra")
LISTEN_KINDS = ("cut_point", "trim_cut_point", "low_match")

_HEAD, _TAIL = "片頭切點", "片尾切點"  # the labels of the first and the last cut point of the reel
_BEFORE_RANGE, _AFTER_RANGE = "刪除段前切點", "刪除段後切點"


@dataclass
class _Edge:
    """A cut point of the reel (see _cut_edges). It becomes a cut_point item about `row` when the bounds of
    `owner` say it is not in silence, and the row of that sentence in the table then points at the item."""

    row: str  # the sentence whose row carries the mark
    owner: str  # the sentence whose bounds hold the point (the row's own, or a removed neighbour)
    label: str
    time: float  # source seconds


@dataclass
class Item:
    """One entry of a review list."""

    number: int  # shared by the two lists, from 1
    kind: str  # one of DECISION_KINDS or LISTEN_KINDS
    time: float  # source seconds, what the list is sorted by
    sentence: str | None  # the sentence it is about
    trim: str | None  # the candidate it is about
    text: str  # what was said: the candidate's text, or the sentence's
    label: str  # a cut_point's _Edge.label; empty for the other kinds
    cut: bool | None  # for a candidate, whether edit.json accepts it; None for the other kinds
    clip: tuple[float, float]  # what to play, in source seconds


def _cut_edges(edit: dict, transcript: dict, trims: dict, kept: list[str], segments: list[Segment]) -> list[_Edge]:
    """The cut points of the bounds that the cut reel cuts at (see the module text), head first, then the
    tail, then the edges of the ranges between kept sentences."""
    bounds = trims["bounds"]
    order = {sentence["id"]: i for i, sentence in enumerate(transcript["sentences"])}
    ranges = [(item["from"], item["to"]) for item in edit["cuts"].get("extra", [])]
    head, tail = kept[0], kept[-1]
    edges: list[_Edge] = []
    # In the order of build_segments: the first kept sentence's cut_in, then a range before it that ends later.
    starts = [(head, "cut_in", _HEAD)] + [
        (last, "cut_out", f"{_HEAD}在刪除的 {last} 之後") for _, last in ranges if order[last] < order[head]
    ]
    ends = [(tail, "cut_out", _TAIL)] + [
        (first, "cut_in", f"{_TAIL}在刪除的 {first} 之前") for first, _ in ranges if order[first] > order[tail]
    ]
    for row, candidates, at in ((head, starts, segments[0].start), (tail, ends, segments[-1].end)):
        for owner, key, label in candidates:
            if to_frame(bounds[owner][key]) == to_frame(at):
                edges.append(_Edge(row, owner, label, bounds[owner][key]))
                break
    for first, last in ranges:
        if order[head] < order[first] and order[last] < order[tail]:
            edges.append(_Edge(first, first, _BEFORE_RANGE, bounds[first]["cut_in"]))
            edges.append(_Edge(last, last, _AFTER_RANGE, bounds[last]["cut_out"]))
    return edges


def trims_in_reel(edit: dict, transcript: dict, trims: dict, segments: list[Segment]) -> set[str]:
    """The ids of the candidates that are in the reel: those that share a frame (edit.in_reel) with the
    reel as it would be without their own cut. A rejected candidate is checked against `segments`, the
    reel build_segments makes of the edit; an accepted one against the reel build_segments makes when
    the edit leaves that one id out of cuts.accept, every other accepted candidate and cuts.extra range
    staying cut. So an accepted cut inside the reel is in it, while a candidate that lies before the
    reel's start or after its end, only touches it, or lies inside a cuts.extra range or inside another
    accepted candidate is not. Needs a valid edit, as build_segments does."""
    accepted = edit["cuts"]["accept"]
    inside = set()
    for trim in trims["trims"]:
        reel = segments
        if trim["id"] in accepted:
            others = [trim_id for trim_id in accepted if trim_id != trim["id"]]
            reel = build_segments({**edit, "cuts": {**edit["cuts"], "accept": others}}, transcript, trims)
        if in_reel(trim["start"], trim["end"], reel):
            inside.add(trim["id"])
    return inside


def _around(start: float, end: float, pad: float) -> tuple[float, float]:
    """The clip from `pad` before `start` to `pad` after `end`, starting at 0 at the earliest."""
    return max(0.0, start - pad), end + pad


def _decision_kind(trim: dict) -> str | None:
    """The kind of decision a candidate in the reel is, or None when it is not one."""
    if trim["kind"] == "retake" and trim["suggest"] == "list":
        return "reworded"
    if trim["kind"] == "extra" and trim["length"] >= EXTRA_ASK_MIN:
        return "extra"
    return None


def _numbered(items: list[Item], kinds: tuple[str, ...], first: int) -> list[Item]:
    """`items` in time order (on a tie, in the order of `kinds`), numbered from `first`."""
    ordered = sorted(items, key=lambda item: (item.time, kinds.index(item.kind)))
    return [replace(item, number=number) for number, item in enumerate(ordered, first)]


def review_items(edit: dict, transcript: dict, trims: dict, segments: list[Segment]) -> tuple[list[Item], list[Item]]:
    """(the decisions, the other places to listen to) for the reel `segments` that build_segments makes
    of the edit: see the module text. Needs a valid edit."""
    sentences = {sentence["id"]: sentence for sentence in transcript["sentences"]}
    kept = kept_sentences(edit, transcript)
    accepted = set(edit["cuts"]["accept"])
    inside = trims_in_reel(edit, transcript, trims, segments)
    fixed = caption_texts(edit)
    decisions: list[Item] = []
    listen: list[Item] = []
    for sid in kept:
        if sid in fixed:
            continue
        sentence = sentences[sid]
        clip = _around(sentence["start"], sentence["end"], LISTEN_PAD_S)
        if sentence["display_source"] == "asr":
            decisions.append(Item(0, "asr_text", sentence["start"], sid, None, sentence["text"], "", None, clip))
        elif sentence["display_source"] == "script" and "low_match" in sentence["reasons"]:
            listen.append(Item(0, "low_match", sentence["start"], sid, None, sentence["text"], "", None, clip))
    for trim in trims["trims"]:
        if trim["id"] not in inside:
            continue
        kind, cut = _decision_kind(trim), trim["id"] in accepted
        if kind is None and not (cut and trim["needs_listen"]):
            continue
        item = Item(0, kind or "trim_cut_point", trim["start"], trim["sentence"], trim["id"], trim["text"], "", cut,
                    _around(trim["start"], trim["end"], TRIM_PAD_S))
        (decisions if kind else listen).append(item)
    for edge in _cut_edges(edit, transcript, trims, kept, segments):
        if trims["bounds"][edge.owner]["needs_listen"]:
            listen.append(Item(0, "cut_point", edge.time, edge.row, None, sentences[edge.row]["text"], edge.label,
                               None, _around(edge.time, edge.time, EDGE_PAD_S)))
    decisions = _numbered(decisions, DECISION_KINDS, 1)
    return decisions, _numbered(listen, LISTEN_KINDS, len(decisions) + 1)
