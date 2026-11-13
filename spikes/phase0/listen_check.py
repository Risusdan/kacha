"""Build SPIKE_OUT/listen/index.html for a person to check by ear (throwaway spike code).

Part 1: 20 reference characters, judged by ear only: a context clip, and a clip that starts at the
        reference engine's predicted start; the person answers right / early / late.
Part 2: disputed spans - consensus edits (asr_score.py) that not every engine family made; the person
        picks which version they actually hear (decides whether an engine drops fillers / repeats).
The page ends with a text box summarising the answers, to paste back into the conversation.
"""
import difflib
import html
import json
import random
import subprocess

from asr_score import FAMILY, REFERENCE_ENGINE, align_map, align_ops, sound_keys, spoken_form
from common import FFMPEG, SPIKE_OUT, is_cjk, normalize_text, tokenize

N, HALF = 20, 0.6
SHOWN = ["qwen3-1.7b", "breeze-25-int8", "firered2-ctc-int8"]  # one engine per family for part 2


def pick(ref_tokens: list[dict]) -> list[int]:
    idx = [i for i, t in enumerate(ref_tokens) if len(t["t"]) == 1 and is_cjk(t["t"]) and t["start"] >= HALF]
    rng = random.Random(42)
    bins = [idx[k * len(idx) // N:(k + 1) * len(idx) // N] for k in range(N)]
    return [rng.choice(b) for b in bins if b]


def cut(audio, wav, t0: float, dur: float) -> None:
    """Write the audio span [t0, t0+dur] to wav."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{dur:.3f}",
                    "-i", str(audio), "-c:a", "pcm_s16le", str(wav)], check=True)


def timing_rows(out, audio, engines) -> list[str]:
    """Judged by ear: clip 1 = the phrase around the character; clip 2 starts exactly at the reference
    engine's predicted start. Right = clip 2 opens on the character itself; early = the previous
    character's tail is heard first; late = the character's first sound is cut off."""
    ref = engines[REFERENCE_ENGINE]["tokens"]
    rows = []
    for n, i in enumerate(pick(ref)):
        start = ref[i]["start"]
        cut(audio, out / f"c{n}.wav", start - 1.2, 2.2)
        cut(audio, out / f"s{n}.wav", start, 0.7)
        before = "".join(t["t"] for t in ref[max(0, i - 3):i])
        after = "".join(t["t"] for t in ref[i + 1:i + 4])
        ch = html.escape(ref[i]["t"])
        opts = "".join(f'<label class="opt"><input type="radio" name="t{n}" value="{v}"> {label}</label>'
                       for v, label in (("ok", f"剛好：一開始就是「{ch}」"),
                                        ("early", "太早：先聽到前一個字的尾巴"),
                                        ("late", f"太晚：「{ch}」的開頭被切掉了")))
        rows.append(f'<section><div class="head"><span class="n">T{n + 1}</span>'
                    f'<span class="ctx">{html.escape(before)}<b>{ch}</b>{html.escape(after)}</span></div>'
                    f'<div class="plays"><span>① 前後文</span><audio controls preload="none" src="c{n}.wav"></audio>'
                    f'<span>② 從「{ch}」開始</span><audio controls preload="none" src="s{n}.wav"></audio></div>'
                    f'<div class="opts">{opts}</div></section>')
    return rows


def ref_time(ref_units: list[str], ref_tokens: list[dict]):
    """ref unit index -> (start of nearest matched unit at/before, start of nearest matched unit at/after)."""
    sm = difflib.SequenceMatcher(a=ref_units, b=[t["t"] for t in ref_tokens], autojunk=False)
    pairs = sorted((b.a + k, b.b + k) for b in sm.get_matching_blocks() for k in range(b.size))
    def before(i):
        c = [ref_tokens[j]["start"] for a, j in pairs if a < i]
        return c[-1] if c else 0.0
    def after(i):
        c = [ref_tokens[j]["start"] for a, j in pairs if a >= i]
        return c[0] if c else ref_tokens[-1]["start"] + 1.0
    return before, after


def dispute_rows(out, audio, engines) -> list[str]:
    ref_text = (SPIKE_OUT / "ref.txt").read_text(encoding="utf-8")
    ref_units, ref_keys = tokenize(spoken_form(ref_text)), sound_keys(ref_text)
    families = {FAMILY.get(n, n) for n in engines}
    disputed = [e for e in json.loads((SPIKE_OUT / "consensus_edits.json").read_text()) if len(e["families"]) < len(families)]
    spans = {}
    for name in SHOWN:
        hyp = normalize_text(engines[name]["text"])
        units = tokenize(spoken_form(hyp))
        spans[name] = [(i1, i2, "".join(units[j1:j2])) for _, i1, i2, j1, j2 in align_ops(ref_keys, sound_keys(hyp))[1]]
    before, after = ref_time(ref_units, engines[REFERENCE_ENGINE]["tokens"])
    rows = []
    for n, e in enumerate(disputed):
        i1, i2 = e["i1"], e["i2"]
        versions: dict[str, list[str]] = {}
        script_v = "".join(ref_units[i1:i2])
        versions.setdefault(script_v, []).append("腳本")
        for name in SHOWN:
            # engine edit spans that overlap [i1, i2); for an insertion point (i1 == i2), spans touching it
            hits = [s for s in spans[name]
                    if (s[0] < i2 and s[1] > i1) or (s[0] == s[1] and i1 <= s[0] <= i2) or (i1 == i2 and s[0] <= i1 <= s[1])]
            if hits:
                lo, hi = min(s[0] for s in hits), max(s[1] for s in hits)
                v = "".join(s[2] for s in hits)
                v = v if (lo, hi) == (i1, i2) else f"{v}（對應腳本：{''.join(ref_units[lo:hi])}）"
            else:
                v = script_v
            versions.setdefault(v, []).append(name)
        t0, t1 = before(i1) - 0.5, after(i2) + 0.5
        dur = max(1.5, min(8.0, t1 - t0))
        cut(audio, out / f"d{n}.wav", t0, dur)
        ctx = (html.escape("".join(ref_units[max(0, i1 - 8):i1])) + f'<mark>{html.escape(script_v) or "＿"}</mark>'
               + html.escape("".join(ref_units[i2:i2 + 8])))
        opts = "".join(
            f'<label class="opt"><input type="radio" name="d{n}" value="{html.escape("+".join(src))}"> '
            f'<b>{html.escape(v) if v else "（沒有這幾個字）"}</b> <small>{html.escape("、".join(src))}</small></label>'
            for v, src in versions.items())
        opts += f'<label class="opt"><input type="radio" name="d{n}" value="none"> 都不像</label>'
        rows.append(f'<section><div class="head"><span class="n">D{n + 1}</span><span class="ctx2">{ctx}</span>'
                    f'<span class="t">{max(0.0, t0):.1f}s</span></div><audio controls preload="none" src="d{n}.wav"></audio>'
                    f'<div class="opts">{opts}</div></section>')
    return rows


def main() -> None:
    out = SPIKE_OUT / "listen"
    out.mkdir(exist_ok=True)
    audio = SPIKE_OUT / "audio16k.wav"
    engines = {p.stem: json.loads(p.read_text()) for p in sorted((SPIKE_OUT / "asr").glob("*.json"))}
    t_rows = timing_rows(out, audio, engines)
    d_rows = dispute_rows(out, audio, engines)
    page = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>聽寫確認</title>
<style>
body{{background:#fff;color:#222;font:16px/1.6 -apple-system,"Noto Sans TC",sans-serif;margin:24px;max-width:1240px}}
h2{{margin-top:40px}}
section{{border-top:1px solid #ddd;padding:12px 0}} .head{{display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
.ctx{{font-size:22px}} .ctx b{{color:#D62728;font-size:28px}} .t{{color:#888}} .n{{font-weight:700;color:#555}}
.ctx2{{font-size:20px}} mark{{background:#FFE08A;padding:0 2px}}
.plays{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}}
.opts{{display:flex;flex-direction:column;gap:4px;margin-top:8px}} .opt small{{color:#888;margin-left:6px}}
#summary{{width:100%;height:120px;font:14px monospace}}
</style>
<h1>聽寫確認</h1>
<p>兩個部分，約 10 分鐘。做完後把最下面方框裡的文字整段複製，貼回對話給 Claude。</p>
<h2>第一部分：字的起點（{len(t_rows)} 字）</h2>
<p>只要用耳朵聽，不用看圖。每一列有兩個播放鍵：</p>
<ol><li>「① 前後文」：這個字前後約兩秒，先聽它在句子裡怎麼念。</li>
<li>「② 從某字開始」：從引擎認為這個字開始的那一刻播起，長度不到一秒。</li></ol>
<p>聽 ② 的第一個聲音：一開口就是這個字，選「剛好」；先聽到前一個字的尾音，選「太早」；這個字的開頭被切掉、聽起來不完整，選「太晚」。差一點點聽不出來的，算「剛好」。</p>
{"".join(t_rows)}
<h2>第二部分：你實際說了哪一個版本（{len(d_rows)} 段）</h2>
<p>黃色是腳本上的字（數字已改成念法，例如 20 寫成「二十」）。播放這一段，選出跟你實際說的最接近的版本。「腳本」代表照稿念；其他選項後面的小字是哪個引擎聽成這樣。同音字不用管，只看有沒有多出或少掉的字，例如「嗯」、重複的詞。</p>
{"".join(d_rows)}
<h2>結果</h2>
<textarea id="summary" readonly></textarea>
<script>
const s=document.getElementById('summary');
const val=n=>{{const c=document.querySelector(`input[name="${{n}}"]:checked`);return c?c.value:'?'}};
// radio names are t0..t19 / d0..d10; the page labels rows from 1 (T1, D1)
const label=n=>n[0].toUpperCase()+(+n.slice(1)+1);
const upd=()=>{{
  const names=[...new Set([...document.querySelectorAll('input[type=radio]')].map(r=>r.name))];
  const t=names.filter(n=>n[0]==='t'), d=names.filter(n=>n[0]==='d');
  const cnt=k=>t.filter(n=>val(n)===k).length;
  s.value=`timing ok=${{cnt('ok')}} early=${{cnt('early')}} late=${{cnt('late')}} unanswered=${{cnt('?')}}\\n`
    +t.map(n=>`${{label(n)}}=${{val(n)}}`).join(' ')+'\\n'+d.map(n=>`${{label(n)}}=${{val(n)}}`).join('\\n');
}};
document.querySelectorAll('input').forEach(i=>i.addEventListener('change',upd));upd();
</script></html>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print(out / "index.html", len(t_rows), "timing rows,", len(d_rows), "disputed spans")


if __name__ == "__main__":
    main()
