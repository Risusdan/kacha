"""Score every SPIKE_OUT/asr/*.json against ref.txt -> SPIKE_OUT/score.json (throwaway spike code).

Metrics per engine:
  cer                  units (CJK char / Latin word / digit) vs the script, exact characters
  cer_cjk_only         CJK characters only (digits and Latin removed from both sides)
  cer_pinyin           homophone-insensitive: two units match when their toneless pinyin readings
                       overlap (的/地, 回/迴 match; 山/三 do not). Subtitle text comes from the script
                       (spec 8), so what the engine must get right is the sound, for alignment.
  cer_pinyin_consensus HEURISTIC: same as cer_pinyin, but against the script adjusted by every edit that
                       engines of >= 2 different families make identically (likely ad-libs / unspoken lines)
  native_traditional_ratio, rtf, peak_mb, start_dev_ms_* (vs REFERENCE_ENGINE)
"""
import difflib
import json
import re
import statistics

import jiwer
import opencc
from pypinyin import Style, pinyin

from common import SPIKE_OUT, is_cjk, normalize_text, tokenize

REFERENCE_ENGINE = "qwen3-1.7b"
FAMILY = {"qwen3-0.6b": "qwen", "qwen3-1.7b": "qwen", "breeze-25-int8": "whisper", "firered2-ctc-int8": "firered"}
_S2TWP = opencc.OpenCC("s2twp")


def cer_of(hyp: str, ref: str) -> float:
    """Error rate over units of two normalized strings (a Latin word counts as one unit)."""
    ref_units, hyp_units = tokenize(ref), tokenize(hyp)
    if not ref_units:
        raise ValueError("empty reference")
    if not hyp_units:
        return 1.0
    return jiwer.wer(" ".join(ref_units), " ".join(hyp_units))


def cjk_only(s: str) -> str:
    return "".join(ch for ch in s if is_cjk(ch))


def native_traditional_ratio(raw: str) -> float | None:
    chars = [ch for ch in raw if is_cjk(ch)]
    if not chars:
        return None
    return round(sum(_S2TWP.convert(ch) == ch for ch in chars) / len(chars), 4)


def align_map(ref_tokens: list[dict], hyp_tokens: list[dict]) -> dict[int, int]:
    """Index map ref -> hyp for tokens whose text matches inside difflib matching blocks."""
    sm = difflib.SequenceMatcher(a=[t["t"] for t in ref_tokens], b=[t["t"] for t in hyp_tokens], autojunk=False)
    return {blk.a + k: blk.b + k for blk in sm.get_matching_blocks() for k in range(blk.size)}


def start_deviation_ms(ref_tokens: list[dict], hyp_tokens: list[dict]) -> tuple[float | None, float | None, int]:
    m = align_map(ref_tokens, hyp_tokens)
    devs = sorted(abs(hyp_tokens[j]["start"] - ref_tokens[i]["start"]) * 1000 for i, j in m.items())
    if not devs:
        return None, None, 0
    p90 = devs[min(len(devs) - 1, int(round(0.9 * (len(devs) - 1))))]
    return round(statistics.median(devs), 1), round(p90, 1), len(devs)


_DIGITS = "零一二三四五六七八九"
_PLACES = ((10 ** 8, "億"), (10 ** 4, "萬"), (1000, "千"), (100, "百"), (10, "十"))


def int_to_zh(n: int, top: bool = True) -> str:
    """Read an integer the way it is usually spoken: 13 -> 十三, 150 -> 一百五十, 105 -> 一百零五."""
    if n < 10:
        return _DIGITS[n]
    for value, name in _PLACES:
        if n >= value:
            head, rest = divmod(n, value)
            s = ("" if value == 10 and head == 1 and top else int_to_zh(head, False)) + name
            if rest:
                s += ("零" if rest < value // 10 else "") + int_to_zh(rest, False)
            return s
    raise AssertionError("unreachable")


def spoken_form(normalized: str) -> str:
    """Digits -> spoken Chinese numerals, so '20' and '二十' compare equal at the sound level."""
    return re.sub(r"\d+", lambda m: int_to_zh(int(m.group())) if len(m.group()) <= 9 else m.group(), normalized)


def sound_keys(normalized: str) -> list[frozenset]:
    """Per unit of the spoken form: the toneless pinyin readings of a CJK char, else the unit itself."""
    keys = []
    for u in tokenize(spoken_form(normalized)):
        if len(u) == 1 and is_cjk(u):
            keys.append(frozenset(pinyin(u, style=Style.NORMAL, heteronym=True)[0]))
        else:
            keys.append(frozenset([u]))
    return keys


def align_ops(a: list[frozenset], b: list[frozenset]) -> tuple[int, list[tuple]]:
    """Levenshtein where units match if their key sets intersect; returns distance and merged edit spans
    (tag, i1, i2, j1, j2) with tag 'edit' for every maximal run of non-matching operations."""
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        ai, row, prev = a[i - 1], D[i], D[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (0 if ai & b[j - 1] else 1))
    steps, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (0 if a[i - 1] & b[j - 1] else 1):
            steps.append((bool(a[i - 1] & b[j - 1]), i - 1, i, j - 1, j))
            i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1:
            steps.append((False, i - 1, i, j, j))
            i -= 1
        else:
            steps.append((False, i, i, j - 1, j))
            j -= 1
    steps.reverse()
    spans: list[tuple] = []
    for ok, i1, i2, j1, j2 in steps:
        if ok:
            continue
        if spans and spans[-1][2] == i1 and spans[-1][4] == j1:
            spans[-1] = ("edit", spans[-1][1], i2, spans[-1][3], j2)
        else:
            spans.append(("edit", i1, i2, j1, j2))
    return D[n][m], spans


def _key_repr(k: frozenset) -> str:
    return "|".join(sorted(k))


def consensus_adjust(ref: str, hyps: dict[str, str]) -> tuple[list[frozenset], list[dict]]:
    """HEURISTIC: apply sound-level edits made identically by engines of >= 2 different families."""
    ref_keys, ref_units = sound_keys(ref), tokenize(spoken_form(ref))
    seen: dict[tuple, dict] = {}
    for name, hyp in hyps.items():
        hyp_keys, hyp_units = sound_keys(hyp), tokenize(spoken_form(hyp))
        for _, i1, i2, j1, j2 in align_ops(ref_keys, hyp_keys)[1]:
            key = (i1, i2, tuple(_key_repr(k) for k in hyp_keys[j1:j2]))
            entry = seen.setdefault(key, {"families": set(), "keys": hyp_keys[j1:j2], "text": "".join(hyp_units[j1:j2])})
            entry["families"].add(FAMILY.get(name, name))
    out, pos, applied = [], 0, []
    for (i1, i2, _), e in sorted(seen.items()):
        if len(e["families"]) < 2 or i1 < pos:
            continue
        out += ref_keys[pos:i1] + e["keys"]
        pos = i2
        applied.append({"i1": i1, "i2": i2, "script": "".join(ref_units[i1:i2]), "spoken": e["text"],
                        "families": sorted(e["families"])})
    return out + ref_keys[pos:], applied


def main() -> None:
    ref = (SPIKE_OUT / "ref.txt").read_text(encoding="utf-8")
    results = {p.stem: json.loads(p.read_text()) for p in sorted((SPIKE_OUT / "asr").glob("*.json"))}
    hyps = {k: normalize_text(r["text"]) for k, r in results.items()}
    ref_keys = sound_keys(ref)
    adjusted, applied = consensus_adjust(ref, hyps)
    (SPIKE_OUT / "consensus_edits.json").write_text(json.dumps(applied, ensure_ascii=False, indent=1))
    ref_tokens = results.get(REFERENCE_ENGINE, {}).get("tokens")
    score = {}
    for name, r in results.items():
        hyp = hyps[name]
        hyp_keys = sound_keys(hyp)
        med = p90 = n = None
        if ref_tokens is not None and name != REFERENCE_ENGINE and r.get("timestamps", True):
            med, p90, n = start_deviation_ms(ref_tokens, r["tokens"])
        score[name] = {
            "runtime": r.get("runtime"),
            "cer": round(cer_of(hyp, ref), 4),
            "cer_cjk_only": round(jiwer.cer(cjk_only(ref), cjk_only(hyp)), 4),
            "cer_pinyin": round(align_ops(ref_keys, hyp_keys)[0] / len(ref_keys), 4),
            "cer_pinyin_consensus": round(align_ops(adjusted, hyp_keys)[0] / len(adjusted), 4),
            "consensus_edits": len(applied),
            "native_traditional_ratio": native_traditional_ratio(r["text"]),
            "rtf": round(r["wall_s"] / r["audio_s"], 3) if r.get("wall_s") else None,
            "wall_s": r.get("wall_s"),
            "peak_mb": r.get("peak_mb"),
            "start_dev_ms_median": med,
            "start_dev_ms_p90": p90,
            "start_dev_pairs": n,
            "tokens": len(r["tokens"]),
            "timestamps": r.get("timestamps", True),
        }
    (SPIKE_OUT / "score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
    print(json.dumps(score, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
