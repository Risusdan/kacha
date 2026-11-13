# Practitioner workflows: Claude Code as orchestrator for short-form video (2025-2026)

Research date: 2026-11-11. Terms used below:
- **ASR** (automatic speech recognition): speech-to-text transcription.
- **EDL** (edit decision list): a file listing which source clips/time ranges go into the cut, in order.
- **Skill**: a Claude Code `SKILL.md` package of instructions plus optional scripts; invoked by description match or as a slash command.
- **Subagent**: a Claude Code helper agent with its own context window, tools and optional cheaper model.
- "Snippet-level" = seen only in a search-result summary, page not fetched; treat as UNVERIFIED.
- Star counts are as shown on the page on the fetch date (2026-11-11) unless noted; aggregator counts disagree with each other.

## 1. Which public case studies, repos, templates and courses exist?

### Takeaway
There is a real, fast-growing ecosystem (most of it from 2026) built on three rendering engines: transcript-driven ffmpeg editing (browser-use `video-use`), HTML-to-video (HeyGen HyperFrames) and React-to-video (Remotion official skills plus community skills). Documented end-to-end case studies with numbers are few: Anthropic's own "Fable edits its launch video" (June 2026), a Taiwanese creator's 12-skill pipeline (July 2026), two Hong Kong Substack tests (Aug and Nov 2026) and one vendor blog (May 2026). Chinese-language coverage is substantial (Taiwan, Hong Kong, mainland).

### Cited Findings

**Open-source repos and frameworks (verified by fetching the page)**
- **browser-use/video-use**: "Drop raw footage in a folder, chat with Claude Code, get `final.mp4` back." MIT, 30.9k stars, 23 commits on main at fetch time. Installed by symlinking the repo into `~/.claude/skills/video-use`; also targets Codex, Hermes, OpenClaw "or any agent with shell access." Requires ffmpeg and an `ELEVENLABS_API_KEY` (transcription via ElevenLabs Scribe). Pipeline: Transcribe → Pack → LLM reasons → EDL → Render → Self-eval (up to 3 fix-and-re-render loops). Animations via HyperFrames, Remotion, Manim or PIL, "in parallel sub-agents, one per animation." — [GitHub browser-use/video-use](https://github.com/browser-use/video-use). Launch timing: a mainland Tencent News article dated 2026-04-20 titled "用Claude Code剪视频，自动去口癖、加字幕、调色，完全免费开源" ([Tencent News](https://view.inews.qq.com/a/20260420A08K7R00), title only, page redirected and not read); an aggregator snapshot from April 2026 reportedly showed 3,151 stars (snippet-level, [DEV review](https://dev.to/andrew-ooo/video-use-review-browser-use-teams-ai-video-editor-58nl)); another aggregator dates the launch 2026-07-04 ([aitoolly](https://aitoolly.com/en/ai-news/article/2026-07-04-browser-use-launches-video-use-a-new-paradigm-for-editing-videos-via-programming-agents)). Launch date is therefore inconsistent across sources; April 2026 is better supported.
- **heygen-com/hyperframes** (HeyGen): "Write HTML. Render video. Built for agents." Apache 2.0, 82.1k stars. Nine skills: `/hyperframes` (entry point, render contract), `/hyperframes-cli`, `/hyperframes-registry` (~450 blocks/components), `/media-use` (music, voiceover, images, logos, color grades), `/reverse-any-video` and `/style-sampling` (both "HyperFrames Pro"), and on-demand `/figma`, `/slideshow`, `/remotion-to-hyperframes`. Install as a Claude Code plugin (`claude plugin marketplace add heygen-com/hyperframes` then `claude plugin install hyperframes@hyperframes`) or as skills (`npx skills add heygen-com/hyperframes`). CLI loop: `npx hyperframes init` → `preview` (browser, live reload) → `render` (MP4); needs Node 22+ and FFmpeg. — [GitHub heygen-com/hyperframes](https://github.com/heygen-com/hyperframes). Listed in Anthropic's plugin directory at [claude.com/plugins/hyperframes](https://claude.com/plugins/hyperframes) (snippet-level). HeyGen also offers a hosted MCP server for its own accounts (snippet-level, [themenonlab](https://themenonlab.blog/blog/hyperframes-claude-code-writes-renders-videos)).
- **remotion-dev/skills** (official Remotion): `npx skills add remotion-dev/skills` or `gh skill install remotion-dev/skills`. 13 skills including `/remotion-best-practices`, `/remotion-create`, `/remotion-markup` (compositions, animation, typography, fonts, timing), `/remotion-studio` (preview), `/remotion-render`, `/remotion-captions`, `/remotion-interactivity`, `/remotion-docs`, `/remotion-upgrade`, `/remotion-multimedia`. No dedicated transcription skill and no quality guidance on that page. — [Remotion docs: Agent Skills](https://www.remotion.dev/docs/ai/skills). One search summary put the repo at about 6.8k stars (snippet-level, unverified).
- **AgriciDaniel/claude-shorts**: long-form to short-form; MIT, 368 stars. `/shorts` slash command, `SKILL.md` with a 10-step pipeline, plus `CLAUDE.md`. Deterministic scripts: `transcribe.py` (faster-whisper), `detect_content.py` (MediaPipe classifier: talking-head / screen recording / podcast), `compute_reframe.py` (face/cursor tracking), `snap_boundaries.py`, `preflight.sh`, `export.sh`. Remotion components for three caption styles (Bold/Montserrat, Bounce/Bangers, Clean/Inter), hook overlay for first 3.5 s, progress bar. Claude scores 8-12 candidate segments on a weighted rubric (hook 0.30, standalone coherence 0.25, emotion 0.20, value density 0.15, payoff 0.10; threshold 60/100). Windows needs WSL 2; NVIDIA GPU recommended. — [GitHub AgriciDaniel/claude-shorts](https://github.com/AgriciDaniel/claude-shorts)
- **social-media-skills/remotion-shorts-skill** (Frank Heijdenrijk, DEV post "Nov 3", year not shown, so 2025 or 2026): the agent writes only one JSON file of scenes; a pre-built Remotion project does layout, timing, render and review sheets. Six scene types, five themes, 1080x1920 @ 30 fps, MIT. — [DEV Community](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)
- **kurbaitaev/ghost-editor**: open-source Claude Code skill; talking-head in, 1080x1920 reel out; local transcription, silence cutting, captions that avoid the face, effects, preview before final. Requires Apple-silicon Mac and a paid Claude plan; Windows not officially supported; author tested v0.3.0. — [Isaac's Substack, 2026-11-08](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)

**Repos seen only at snippet level (UNVERIFIED, not fetched)**
- JhontyBarreto/video-editing-skill: script → ElevenLabs voiceover with word timestamps → speech-to-text check of takes → synced captions → render → QA; "Claude stops for your approval on the script before spending any credits." — [GitHub](https://github.com/JhontyBarreto/video-editing-skill)
- mromidev/claude-animated-shorts: 55-70 s vertical story shorts with Remotion + three.js; an aggregator claims 2.2K stars. — [explainx.ai](https://explainx.ai/blog/claude-animated-shorts-remotion-threejs-agent-skill-github-trending-guide-2026)
- awa-video-reel v1.1 (Chinese-language X post by ElioEx): a Claude Code skill; input up to one minute; "先给剪辑计划，得到同意再渲染" (gives an edit plan, renders only after consent); keyword captions, face-tracking effects, music ducking under voice; can scroll/click a website to record B-roll; outputs two MP4s (with and without music). — [X @elioex0](https://x.com/elioex0/status/2116519361707946118)
- Others: veedstudio/open-edit ([gittrend](https://gittrend.io/repo/veedstudio/open-edit)), mcp-video ([pastorsimon1798.github.io](https://pastorsimon1798.github.io/mcp-video)), ClipGenius ffmpeg skill ([agent-skills.cc](https://agent-skills.cc/skills/ClipGenius-ClaudeCode-ffmpeg)), HeyGen `video-edit` skill ([claudemarketplaces](https://claudemarketplaces.com/skills/heygen-com/skills/video-edit)), FFmpeg Micro MCP tutorial ([DEV](https://dev.to/javidjamae/build-an-ai-video-editing-agent-with-claude-and-ffmpeg-micro-mcp-4915)). An aggregator claims to have graded 464 Claude video/Remotion skills ([agentskillshub](https://agentskillshub.top/best/claude-video-skills/)); treat as directory noise, not evidence of quality.
- Cardboard (YC W2026), an agentic video editor said to have had the top-upvoted Hacker News launch in its batch; not shown to be Claude Code-based. — [startuphub.ai](https://www.startuphub.ai/news/claudes-corner-cardboard-yc-w2026) (secondary; I found no HN thread directly).

**Case studies with reported process and numbers**
- **Anthropic / Claude Code team, "How Fable edited its own launch video" (June 2026)**: ~3-min 4K talking-head launch video posted by @ClaudeDevs 2026-06-09; directed by Thariq (Claude Code team) "who never opened a video editor." 17 takes, 4 scenes, ~25 GB Sony S-Log3. Steps: Whisper word-level transcripts → Claude writes a JSON EDL (`final-edit.json`) with written rationale per pick → ffmpeg cuts/concat → Claude re-transcribes output to check filler words → Claude writes 7 candidate color LUTs, human picks one → 11 designer PNGs rebuilt as parameterized Remotion components timed to spoken words → Figma MCP round trips for a design pass → render, screenshot stills, self-review, re-render. First 2:50 cut "within minutes"; ~10 re-renders in one night; 4 days total (June 6-9). Token/compute cost not reported. A colorist noted the S-Log3 footage was not properly color-managed. — [jc-aiwiki summary (secondary)](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video); primary deck exists but its slides are mostly images: [thariqs.github.io deck, "Claude Code · June 2026"](https://thariqs.github.io/cc-video-editing-deck/) (confirms title, 17 takes / 4 scenes, "Sony masters · S-Log3 · 4K · ≈25 GB"); launch post [X @ClaudeDevs](https://x.com/ClaudeDevs/status/2064399512664526853). Note: this is horizontal long-form, not a Reel, but it is the most detailed first-party architecture.
- **追日Gucci (Taiwan), 2026-07-05**: "12 skills" covering "20+ editing mechanisms" with "610+ automated tests" for output quality; per-step on/off switches or "Full Auto"; Whisper and ElevenLabs Scribe for ASR, Remotion or HyperFrames for animated B-roll, Suno for music, paid image API for thumbnails. A 28-min recording cut to ~14 min. Reported time: traditional post 6-7 h vs ~30 min of approvals. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)
- **Isaac's Substack (Hong Kong), claude-shorts test, 2026-08-03**: ~50-min Cantonese YouTube video → 7 shorts in ~40 min (excluding first install). Good framing/person tracking and reasonable hooks; Cantonese subtitle recognition errors, occasional subtitle timing offsets, no B-roll/animation. Verdict: "一個聰明的長片切片工具" (a smart long-video clipping tool), not a complete AI editor. — [Isaac's Substack](https://isaacwong2024.substack.com/p/claude-20-40-460)
- **Isaac's Substack, Ghost Editor test, 2026-11-08**: ~30 min across 7 versions to an acceptable Reel; that session used ~44% of the Claude usage cap (plan tier not stated in the visible text). — [Isaac's Substack](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel) (rest of article paywalled).
- **Clixie AI blog (vendor), last updated 2026-05-28**: Claude Code + HyperFrames on a 37-s raw talking-head clip; 4 render passes; context reset needed at 263K tokens; ~10% of a $200/month Max plan's 5-hour session limit. The page contains unfilled placeholders ("[PUBLICATION BLOCKER...]", "[ADD MY EXPERIENCE...]") and some mismatched citations, so treat as a draft from a vendor. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- **Abby Chi (Taiwan), 2026-10-23**: guide "用 Claude 剪片、做動畫一直卡關？三個常見原因與正確做法"; recommends Claude Code (not chat or cloud Cowork) for local files/FFmpeg/MP4, one workflow per request, storyboard first, 2-3 s test renders. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)

**Chinese-language tutorials (snippet-level unless noted)**
- Taiwan: YouTube "AI 真的會剪片了！Claude Code 零基礎教學：丟影片、講人話，就能自動剪輯＋上字幕" ([YouTube](https://www.youtube.com/watch?v=lP0rUMMIhKU)); maplefeather 2026 article describing a 口播 chain "Whisper 轉 SRT → Auto-Editor 去停頓 → ImageMagick 字卡 → ffmpeg 燒字幕 → 輸出 9:16" ([maplefeather](https://maplefeather.com/article/ai-auto-video-editing-claude-code-2026)); HyperFrames Traditional Chinese guide warning subscriptions, model usage and assets may cost extra ([aiposthub](https://www.aiposthub.com/claude-code-hyperframes-video-editing-guide/)).
- Mainland: Tencent News 2026-04-20 (title above); ElioEx X post (above). I did not reach Bilibili, 小紅書, Threads, 方格子, iThome or 少數派 pages directly; no findings from those platforms.

### Inferences
- The most-adopted building blocks are engines-as-skills (video-use, HyperFrames, Remotion), not complete Reels products; complete Reels-oriented workflows are mostly individual creators' private pipelines (Gucci) or small skills (Ghost Editor, awa-video-reel, claude-shorts).
- No public case study combines all four of our target steps (talking-head rough cut, subtitles, effects, simple 2D animation) with measured per-video cost. Fable (rough cut + graphics + grade) and Gucci (rough cut + subtitles + B-roll animation) come closest.

### Gaps
- No Reddit or Hacker News thread with first-hand Claude Code video workflow reports was found within the search budget; HN/Reddit coverage remains unverified.
- No Bilibili/小紅書/Threads/方格子/iThome/少數派 primary page was read.
- The Fable deck's slides are images; I could not read its architecture slides directly and relied on a secondary wiki for the tool list.
- No course was found that specifically teaches Claude Code Reels production (other than paid content such as Isaac's paywalled prompts and Abby Chi's paid skill offer).

## 2. Architecture patterns (subagents, skills, commands, hooks, folders, state files, gates)

### Takeaway
The dominant pattern is "transcript as interface + deterministic scripts + one structured edit file + human gate before render + frame-sample self-check." Skill-per-engine (and scripts-per-step inside one skill) is far more common in public repos than role-based subagents. Subagents appear in practice for parallel, isolated jobs (one per animation in video-use). Hooks are not documented in any case study found.

### Cited Findings
**Transcript-driven editing (LLM reads text, not video)**
- video-use: ElevenLabs Scribe per source gives word timestamps, speaker diarization and audio events; all takes packed into a ~12 KB `takes_packed.md`; cuts "come from speech boundaries and silence gaps"; removes "umm/uh," false starts and dead space; 30 ms audio fades at every cut; subtitles burned in as 2-word UPPERCASE chunks by default. — [GitHub video-use](https://github.com/browser-use/video-use)
- Fable: Whisper word-level JSON → Claude chooses takes and in/out points → re-transcribe output to verify fillers removed. — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)
- claude-shorts: faster-whisper word timestamps → Claude scores segments → `snap_boundaries.py` aligns cuts to word/sentence ends and silences. — [GitHub claude-shorts](https://github.com/AgriciDaniel/claude-shorts)

**Structured edit / state files**
- Fable: JSON EDL `final-edit.json` with rationale per pick. — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)
- video-use: EDL step between reasoning and render (format not described on README); session memory in `project.md` so later sessions resume. — [GitHub video-use](https://github.com/browser-use/video-use)
- remotion-shorts-skill: the agent writes only one scene JSON; author's rationale: when an agent writes new Remotion code per video, "each video can introduce new bugs," there is no check that text clears app UI, and scene duration "is otherwise a guess." — [DEV](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)
- HyperFrames: `frame.md` translates web design specs into a "DESIGN.md superset" for composing video. — [GitHub hyperframes](https://github.com/heygen-com/hyperframes)

**Skills, slash commands, CLAUDE.md, subagents, plugins**
- Skill-per-engine: `/hyperframes`, `/hyperframes-cli`, `/media-use` ([HyperFrames](https://github.com/heygen-com/hyperframes)); `/remotion-studio`, `/remotion-render`, `/remotion-captions` ([Remotion](https://www.remotion.dev/docs/ai/skills)).
- Scripts-per-step inside one skill: claude-shorts (`/shorts` + `SKILL.md` + `CLAUDE.md` + Python/shell scripts; references `scoring-rubric.md`, `caption-styles.md`, `platform-specs.md`, `remotion-patterns.md`). — [GitHub claude-shorts](https://github.com/AgriciDaniel/claude-shorts)
- video-use `SKILL.md` holds "production rules and editing craft" including "12 hard rules"; `helpers/` holds editing scripts; bundles a `skills/manim-video/` sub-skill. — [GitHub video-use](https://github.com/browser-use/video-use)
- Many-skills pipeline: Gucci uses 12 skills with per-step toggles and 610+ automated tests. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)
- Subagents in practice: video-use runs animation overlays "in parallel sub-agents, one per animation." — [GitHub video-use](https://github.com/browser-use/video-use). No other fetched repo documents subagents (claude-shorts: "Subagents: not mentioned").
- Plugin packaging: HyperFrames ships as a Claude Code plugin marketplace entry. — [GitHub hyperframes](https://github.com/heygen-com/hyperframes)
- Claude Code subagent facts (official docs, fetched 2026-11-11): Markdown + YAML frontmatter in `.claude/agents/` (project) or `~/.claude/agents/` (user) or a plugin's `agents/`; fields include `tools`, `model` (`sonnet`, `opus`, `haiku`, `fable`, full ID, or `inherit`), `skills` (preloaded), `hooks` (scoped to the subagent), `memory` (`user`/`project`/`local`; `project` stores at `.claude/agent-memory/<agent>/`, first 200 lines or 25 KB of `MEMORY.md` loaded), `background`, `isolation`, `permissionMode`. Own context window; returns only a summary. Nesting up to three layers by default. — [Claude Code docs: subagents](https://code.claude.com/docs/en/sub-agents)

**Project folder conventions**
- video-use: all outputs to `<videos_dir>/edit/`, final at `edit/final.mp4`, keeping the skill directory clean. — [GitHub video-use](https://github.com/browser-use/video-use)
- claude-shorts: output `shorts/short_01_yt.mp4` etc.; venv at `~/.shorts-skill/`. — [GitHub claude-shorts](https://github.com/AgriciDaniel/claude-shorts)
- Gucci: one reference-photo folder (thumbnails) and one output folder collecting titles, descriptions, social posts. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)
- Abby Chi: back up originals, work only on copies. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)

**Human checkpoint gates**
- video-use: proposes a strategy and "waits for approval before touching any cut"; principle "Ask → confirm → execute → self-eval → persist"; previews shown only after self-eval passes. — [GitHub video-use](https://github.com/browser-use/video-use)
- claude-shorts step 6 "Approve": user picks segments, adjusts timecodes, caption style, platform. — [GitHub claude-shorts](https://github.com/AgriciDaniel/claude-shorts)
- Gucci's seven gates: confirm retake/cut decisions (high-confidence trusted, medium needs a listen); ~7-s before/after comparison per retake; choose opener from 3-5 scored candidates; title-card quotes; Shorts segment; thumbnail; copy direction; then a final watch. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)
- Clixie: HTML storyboard plan is "the main human gate"; "storyboard-only pass" prompt ("Do not render anything yet."); timestamp-based revision feedback. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- Abby Chi: confirm storyboard; review three key frames (opening, middle, end); review the proposed cut list with timestamps and reasons before deleting pauses; keep natural breaths. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)
- Fable: human picks one of 7 color grades and approves edit picks. — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)

**Preview-before-full-render and parallelism**
- remotion-shorts-skill `--stills` produces `sheet.jpg`, `cuts.jpg`, `cover.jpg`, `report.json` "in seconds"; warnings stop render with exit code 2; `--force` cannot push text off screen. — [DEV](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)
- Abby Chi: test with a 2-3 s clip before rendering the full version. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)
- Clixie: concurrent renders caused CPU/RAM pressure; mitigation was sequential rendering, one video at a time. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- claude-shorts `render.mjs`: Remotion "bundle-once-render-many." — [GitHub claude-shorts](https://github.com/AgriciDaniel/claude-shorts)

### Inferences
- For our workflow, the converging design is: one skill per stage (transcribe, cut, caption, animate, render) backed by scripts; one JSON edit file as the single source of truth between stages; a human gate after the cut plan and after the design/storyboard; stills or short test renders before the full render.
- Role-based subagents are not proven in public repos for this domain; subagents are used where work parallelises (one animation each) or where verbose output should stay out of the main context. A "designer" role maps more naturally to a style config plus a template set than to a free-form agent.
- No public source shows Claude Code hooks used for video (e.g., a PostToolUse hook validating the EDL). That is an open design space, not an established pattern.

### Gaps
- video-use's EDL schema and its 12 hard rules were not readable from the README extraction.
- No public source documents parallel multi-project operation (several Reels in flight); Clixie mentions "60 video variations" in one session without detail.

## 3. Keeping Claude usage low; reported token counts and plan limits

### Takeaway
Cost control in practice comes from never feeding raw video to the model (text transcripts plus a few PNG filmstrips/stills), pushing deterministic work into scripts or fixed templates, resetting context around 200-260K tokens, and gating renders behind approvals. Reported usage is sparse and anecdotal: ~10% of a Max 5-hour window for a 37-s HyperFrames clip with 4 renders (vendor), ~44% of a usage cap for a 7-version Ghost Editor Reel (Hong Kong creator, plan tier not visible). No source gives per-video token counts beyond the 263K context figure.

### Cited Findings
- video-use's own estimate: naive frame dumping ≈ 30,000 frames × 1,500 tokens ≈ 45M tokens, versus ~12 KB of text plus a few PNGs; `timeline_view` (filmstrip + waveform + word labels for a time range) is called only at decision points and at cut boundaries during self-eval. Self-reported estimate, not independently verified. — [GitHub video-use](https://github.com/browser-use/video-use)
- Clixie (vendor, draft-quality page, 2026-05-28): 37-s clip, 4 renders, ≈10% of a $200/month Max 5-hour session limit; context reset needed at 263K tokens; advises handoff summary and new session at 200-260K tokens; warns revision passes on a 260K+ session degrade planning quality and accelerate cost; "multi-video days compound this quickly." — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- Ghost Editor test: ~30 min, 7 versions, ~44% of the Claude usage cap; author attributes many rounds to a vague first prompt ("第一句太含糊，後面就會變成多輪修正"). — [Isaac's Substack 2026-11-08](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)
- Fable launch video: ~10 re-renders in one night, 4 days total; cost not reported. — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)
- remotion-shorts-skill: agent writes only JSON; review sheets in seconds; a 20-s short renders in under a minute on a recent laptop; 8 GB RAM machines render 2 frames at a time (`--concurrency 1` for memory kills); no paid API. — [DEV](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)
- External paid services shift cost outside Claude: video-use needs ElevenLabs ([GitHub](https://github.com/browser-use/video-use)); Gucci uses paid image/Shorts APIs and Suno ([guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)); claude-shorts and Ghost Editor transcribe locally ([claude-shorts](https://github.com/AgriciDaniel/claude-shorts), [Isaac](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)).
- Official mechanisms: subagent `model: haiku`/`sonnet` routes work to cheaper models; `CLAUDE_CODE_SUBAGENT_MODEL` sets a default; subagent tokens count toward the same usage limits; combined subagent descriptions over ~15,000 tokens trigger a warning; "routing routine work to Haiku is a documented way to control costs." — [Claude Code docs: subagents](https://code.claude.com/docs/en/sub-agents)
- Pro and Max share one rate-limit pool across Claude and Claude Code (snippet-level summary of Anthropic support). — [Anthropic support](https://support.anthropic.com/?q=terminen)
- Time per video reported: Gucci ~30 min of approvals vs 6-7 h manual for a 28→14-min long-form edit ([guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)); claude-shorts 7 shorts from ~50 min in ~40 min ([Isaac](https://isaacwong2024.substack.com/p/claude-20-40-460)); Ghost Editor ~30 min for one Reel ([Isaac](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)); Clixie explicitly reports no measured Claude times ([Clixie](https://www.clixie.ai/blog/claude-code-video-editing)).

### Inferences
- Revision rounds, not the first pass, dominate usage (7 versions → 44% cap; 4 renders + 263K context). Front-loading constraints (face-safe zones, cut aggressiveness, style) and using a fixed template reduce rounds more than model choice does.
- No source reports using Haiku/Sonnet subagents specifically for video steps; that is an available lever (docs) rather than an observed practice.

### Gaps
- No per-video token counts, no Pro-plan experience reports, and no dollar cost per Reel from any first-hand source.
- The Ghost Editor 44% figure lacks the plan tier (Pro vs Max) in the visible text.

## 4. Reported quality problems and mitigations

### Takeaway
The recurring failures are ASR errors (especially Cantonese/Mandarin proper nouns), word-boundary sync offsets, missing CJK fonts, effects colliding with the face, text overflowing app-UI safe zones, and output that is usable as a rough cut but not publishable without human pacing/B-roll work. Mitigations that practitioners report as working are mostly deterministic: glossary/proper-noun lists and transcript proofreading before captioning, word/silence boundary snapping, fixed templates with measured text layout and safe zones, stills/contact sheets before render, and frame sampling at cut boundaries.

### Cited Findings
**ASR and subtitle problems**
- Cantonese subtitle recognition errors and occasional timing offsets with claude-shorts. — [Isaac 2026-08-03](https://isaacwong2024.substack.com/p/claude-20-40-460)
- Ghost Editor: Whisper misheard Cantonese words; fix by proofreading against a subtitle script. — [Isaac 2026-11-08](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)
- Chinese ASR produces awkward line breaks; Gucci's fixes: sentence/noun-boundary line-break logic, stutter and filler removal, number normalization (including Chinese numerals), and a glossary that "learns corrections" so errors do not repeat. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)
- Transcription APIs can sit ~120 ms off word boundaries ("roughly four frames at 30fps"), compounding across cuts. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- Abby Chi: give a proper-noun/brand list first; proofread the transcript manually before adding subtitles. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)
- Mitigation for cut boundaries: `snap_boundaries.py` (claude-shorts) and cuts from speech boundaries/silence gaps with 30 ms fades (video-use). — [claude-shorts](https://github.com/AgriciDaniel/claude-shorts); [video-use](https://github.com/browser-use/video-use)

**Fonts and layout**
- Ghost Editor's built-in subtitle fonts cover Latin and Cyrillic, no Chinese; Traditional Chinese must be checked in preview. — [Isaac 2026-11-08](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)
- Specify the Chinese font (example: Noto Sans TC) and confirm it is installed before rendering, or characters render as boxes. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)
- remotion-shorts-skill mitigations: real-font text measurement in `calculateMetadata`; cost function penalizing one-word lines, >3 lines, uneven lines; safe zones `{ top: 288, bottom: 672, left: 96, right: 192 }` (strictest across TikTok/Reels/Shorts); reading-speed timing 17 chars/s and 0.3 s/word; frame 0 fully set (used as thumbnail); words rise from a mask instead of fading; scenes start in motion to avoid empty frames; 59-s cap. Gotcha: the review-sheet composition must match `durationInFrames` or every tile shows frame 0. — [DEV](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)

**Effects, design consistency, "AI look"**
- Ghost Editor: missing effects at some points, an effect covering the speaker, effects too small to notice, too little footage cut; fix by stating constraints up front (effects off the face, don't over-cut, show preview first). — [Isaac 2026-11-08](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel)
- Clixie: short-form pacing "needs significant human steering" ("I wouldn't post this, but it's getting better."); a logo present in a draft was missing from the final export; localhost preview sometimes showed 0:00/0:00; brand fonts/colors/logos applied correctly when a brand doc was ingested and a 3-s brand test card rendered. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- claude-shorts and Isaac: no automatic B-roll or animation; rhythm not redesigned. — [Isaac 2026-08-03](https://isaacwong2024.substack.com/p/claude-20-40-460)
- Fable: S-Log3 footage not properly color-managed per a professional colorist ("crew-approved, not colorist-approved"). — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)
- Design-system mitigations offered by engines: HyperFrames ships ~10 design templates and `frame.md`; claude-shorts centralizes styles in `styles/theme.ts` and `styles/fonts.ts`; remotion-shorts-skill limits the agent to six scene types and five themes. — [HyperFrames](https://github.com/heygen-com/hyperframes); [claude-shorts](https://github.com/AgriciDaniel/claude-shorts); [DEV](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)
- Abby Chi: supply real copy, data, logo and product images before discussing style; take colors from the logo; don't let Claude invent claims or numbers; keep editing, animation and short-video as separate requests. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)

**Visual QA via frame extraction**
- video-use: `timeline_view` PNG at each cut boundary after render; fix and re-render up to 3 times. — [GitHub video-use](https://github.com/browser-use/video-use)
- Fable: Claude renders, screenshots stills to review its own work, re-renders. — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)
- Clixie "Render QA checklist" prompt with frame sampling and a written QA summary. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- Gucci: 610+ automated tests on output quality. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)

**Render time and hardware**
- remotion-shorts-skill: 20-s short in under a minute on a recent laptop; 8 GB RAM limits concurrency. — [DEV](https://dev.to/fheijdenrijk/remotion-skill-for-claude-code-finished-shorts-from-one-json-file-checked-before-render-m23)
- Clixie: concurrent renders caused CPU/RAM pressure and one face-cam glitch. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- Platform constraints: claude-shorts and Ghost Editor are not native on Windows (WSL 2 / unsupported); video-use documented for macOS and Linux (snippet-level for the latter claim). — [claude-shorts](https://github.com/AgriciDaniel/claude-shorts); [Isaac](https://isaacwong2024.substack.com/p/ghost-editor-claude-code-reel); [themenonlab](https://themenonlab.blog/blog/video-use-ai-video-editing-claude-code-agents)

### Inferences
- For a Traditional-Chinese talking-head workflow, the highest-value human gate is transcript proofreading with a persistent glossary before captioning; every Cantonese/Chinese report hit ASR errors.
- Fixed templates with measured layout (remotion-shorts-skill approach) are the most concrete published answer to inconsistent design across scenes; free-form agent-written animation code is where inconsistency and "AI look" enter.
- A CJK font must be an explicit, checked dependency of the style config.

### Gaps
- No source quantifies sync drift over a full Reel or compares ASR engines on Mandarin/Cantonese accuracy.
- "Generic AI look" is asserted (Clixie quote) but no source gives a concrete checklist of what makes captions/animations look AI-made, beyond the remotion-shorts-skill layout rules.

## 5. Where Claude Design or Figma MCP enters for style definition

### Takeaway
Claude Design (Anthropic Labs, launched 2026-04-17, research preview) can infer a design system and hand a bundle to Claude Code; HyperFrames documents a Claude Design handoff path and a `/figma` skill, and Anthropic's own Fable video used Figma MCP for a design pass. No practitioner case study found shows Claude Design actually defining a Reels caption/animation style end to end.

### Cited Findings
- Claude Design: Anthropic Labs product at claude.ai/design, launched 2026-04-17; makes designs, prototypes, slides via conversation; "Claude packages everything into a handoff bundle that you can pass to Claude Code with a single instruction"; at setup reads a team's codebase and design files to infer palette, typography scale, components, layout patterns; Pro/Max get it, Enterprise off by default (secondary summaries of the launch). — [Anthropic announcement](https://www.anthropic.com/news/claude-design-anthropic-labs?lang=us); [unite.ai](https://www.unite.ai/anthropic-launches-claude-design-for-visual-prototyping-and-presentations/)
- HyperFrames: guides cover "handoff from Claude Design and Open Design"; `frame.md` = "DESIGN.md superset"; `/figma` skill brings Figma links, assets and tokens into a video. — [GitHub hyperframes](https://github.com/heygen-com/hyperframes). A skill named `claude-design-hyperframes` is listed under heygen-com/hyperframes (snippet-level). — [claudemarketplaces](https://claudemarketplaces.com/skills/heygen-com/hyperframes/claude-design-hyperframes)
- Fable launch video: designer PNGs rebuilt as Remotion components; "Figma MCP for two code→Figma→code round trips"; Thariq handled the Figma design pass. — [jc-aiwiki](https://jc-aiwiki.pages.dev/ai-video-content/fable-edits-own-launch-video)
- Clixie: "Brand system ingestion (from URL or design doc)" prompt plus a 3-s brand test card. — [Clixie](https://www.clixie.ai/blog/claude-code-video-editing)
- Abby Chi's Oct 2026 guide does not mention Claude Design; it does mention "Claude Motion" as a Team/Enterprise beta that exports MP4. Single source, UNVERIFIED; I found no Anthropic page confirming it. — [abbychi.com](https://abbychi.com/claude-video-editing-animation-guide/)
- A third-party wiki says Claude Design "complements" HyperFrames (agent-generated video vs HTML-composed); marked inferred by that wiki, UNVERIFIED. — [jc-aiwiki Claude Design](https://jc-aiwiki.pages.dev/claude-ai/claude-design)

### Inferences
- The realistic entry point is upstream: define brand tokens (palette, type scale, caption styles, motion rules) in Claude Design or Figma once, export to a `DESIGN.md`/tokens file in the project, and have the render templates read it. Per-video design work then becomes token selection, not new design.

### Gaps
- No public example of Claude Design used for a short-form video style guide or caption system.
- "Claude Motion" could not be verified against an Anthropic source.
- HyperFrames' Claude Design handoff guide content was not read (only its existence on the README).
