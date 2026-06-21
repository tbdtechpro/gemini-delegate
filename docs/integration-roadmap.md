# Integration roadmap

Capabilities planned for `gemini-delegate` but **not yet implemented**. Each has
a task on the project board (GD-014…GD-019). This file holds the same
comparison treatment the README gives the live integrations, so the analysis is
ready when each one ships — it intentionally stays out of the README until the
feature is live.

## Planned integrations

| Capability | What it would do | vs Claude Code | vs best free Python library | vs best free local model (RTX 3090) |
|---|---|---|---|---|
| **Text → speech** (TTS)<br/>`gemini-3.1-flash-tts` | Turn text into natural spoken audio (many voices, 100+ languages) | ❌ | Some improvement · [Coqui&nbsp;TTS / XTTS‑v2](https://github.com/coqui-ai/TTS) | Some improvement · [Kokoro‑82M](https://hf.co/hexgrad/Kokoro-82M) |
| **Speech generation** (expressive)<br/>Interactions | Director-controlled, multi-speaker, tagged expressive speech | ❌ | Significant improvement · [Coqui&nbsp;TTS](https://github.com/coqui-ai/TTS) | Significant improvement · [Kokoro‑82M](https://hf.co/hexgrad/Kokoro-82M) |
| **Audio → text** (understanding)<br/>Interactions | Transcribe **and** answer questions about audio | ❌ | Some improvement · [faster‑whisper](https://github.com/SYSTRAN/faster-whisper) | Some improvement · [Qwen2‑Audio‑7B](https://huggingface.co/Qwen/Qwen2-Audio-7B-Instruct) |
| **Live** (realtime)<br/>`gemini-3.1-flash-live` | Low-latency bidirectional audio/video/text streaming | ❌ | ❌ | Some improvement · [Qwen2.5‑Omni‑7B](https://huggingface.co/Qwen/Qwen2.5-Omni-7B) |
| **Text → music**<br/>`lyria-3-pro` | Generate full-length music from a prompt | ❌ | Significant improvement · [audiocraft (MusicGen)](https://github.com/facebookresearch/audiocraft) | Significant improvement · [musicgen‑large](https://huggingface.co/facebook/musicgen-large) |
| **Text embeddings** | Turn text into vectors for search / similarity / RAG | ❌ | Some improvement · [sentence‑transformers](https://github.com/UKPLab/sentence-transformers) | Equivalent · [Qwen3‑Embedding‑8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B) |

Ratings describe how the **Gemini** capability compares to each alternative
(*Worse / Equivalent / Some improvement / Significant improvement*; **❌** = no
comparable option there). The local-model column targets a single **NVIDIA RTX
3090 (24 GB VRAM)**. This is a rough, hand-made guide — **not** based on
benchmarks — and there are very likely capable Python libraries or local models
our search didn't surface.

## Notes per capability

- **TTS** (`gemini-3.1-flash-tts-preview`, [docs](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview)) — GD-014. The local field is strong here (Kokoro, XTTS-v2, Piper, F5-TTS), so Gemini's edge is mostly breadth of languages and naturalness, not a category leap.
- **Speech generation** ([docs](https://ai.google.dev/gemini-api/docs/interactions/speech-generation)) — GD-018. Overlaps heavily with TTS; the differentiator is *director's-notes* control, inline audio tags, and multi-speaker dialogue, which local stacks don't really match — hence the larger gap.
- **Audio understanding** ([docs](https://ai.google.dev/gemini-api/docs/interactions/audio)) — GD-015. `faster-whisper` is transcription-only; Gemini adds Q&A / summarization / very long inputs. For pure transcription the gap shrinks (Whisper large-v3 is excellent).
- **Live** (`gemini-3.1-flash-live-preview`, [docs](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview)) — GD-017. No drop-in Python library equivalent; Qwen2.5-Omni-7B is an emerging local option but trails on latency, robustness, and video parity, and is setup-heavy. This is a streaming surface that may not fit the one-shot envelope cleanly — design TBD.
- **Music** (`lyria-3-pro-preview`, [docs](https://ai.google.dev/gemini-api/docs/models/lyria-3-pro-preview)) — GD-016. MusicGen produces short instrumental clips; Lyria targets longer, structured songs — a real capability gap today.
- **Embeddings** ([docs](https://ai.google.dev/gemini-api/docs/embeddings)) — GD-019. The local field is genuinely competitive (top MTEB open models fit a 3090), so this is the closest-to-parity of the set; Gemini's edge is multimodal inputs and managed scale.
