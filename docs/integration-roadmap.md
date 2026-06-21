# Integration roadmap

Capabilities planned for `gemini-delegate` but **not yet implemented**. Each has
a task on the project board (GD-014…GD-019). This file holds the same
comparison treatment the README gives the live integrations, so the analysis is
ready when each one ships — it intentionally stays out of the README until the
feature is live.

## Planned integrations

| Capability | What it would do | vs Claude Code | vs direct Python library | vs best free local model (RTX 3090) |
|---|---|---|---|---|
| **Text → speech** (TTS)<br/>`gemini-3.1-flash-tts` | Turn text into natural spoken audio (many voices, 100+ languages) | ❌ · no comparable option | Significant improvement · [pyttsx3](https://github.com/nateshmbhat/pyttsx3) *(espeak; robotic)* | Some improvement · [Kokoro‑82M](https://hf.co/hexgrad/Kokoro-82M) |
| **Speech generation** (expressive)<br/>Interactions | Director-controlled, multi-speaker, tagged expressive speech | ❌ · no comparable option | Significant improvement · [pyttsx3](https://github.com/nateshmbhat/pyttsx3) | Significant improvement · [Kokoro‑82M](https://hf.co/hexgrad/Kokoro-82M) |
| **Audio → text** (understanding)<br/>Interactions | Transcribe **and** answer questions about audio | ❌ · no comparable option | Significant improvement · [pocketsphinx](https://github.com/cmusphinx/pocketsphinx) *(classical ASR; dated)* | Some improvement · [Qwen2‑Audio‑7B](https://huggingface.co/Qwen/Qwen2-Audio-7B-Instruct) |
| **Live** (realtime)<br/>`gemini-3.1-flash-live` | Low-latency bidirectional audio/video/text streaming | ❌ · no comparable option | ❌ · no comparable option | Some improvement · [Qwen2.5‑Omni‑7B](https://huggingface.co/Qwen/Qwen2.5-Omni-7B) |
| **Text → music**<br/>`lyria-3-pro` | Generate full-length music from a prompt | ❌ · no comparable option | ❌ · no comparable option | Significant improvement · [musicgen‑large](https://huggingface.co/facebook/musicgen-large) |
| **Text embeddings** | Turn text into vectors for search / similarity / RAG | ❌ · no comparable option | Significant improvement · [scikit‑learn TF‑IDF](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html) *(lexical only)* | Equivalent · [Qwen3‑Embedding‑8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B) |

Ratings describe how the **Gemini** capability compares to each alternative
(*Worse / Equivalent / Some improvement / Significant improvement*; **❌** = no
comparable option there). The **Python** column looks for a library that does the
task *directly* (classical/algorithmic), **not** a wrapper around the neural model
in the next column — hence the **❌** for prompt-driven generation tasks. The
local-model column targets a single **NVIDIA RTX 3090 (24 GB VRAM)**. This is a
rough, hand-made guide — **not** based on benchmarks — and there are very likely
capable libraries or models our search didn't surface.

## Notes per capability

- **TTS** (`gemini-3.1-flash-tts-preview`, [docs](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview)) — GD-014. The local field is strong here (Kokoro, XTTS-v2, Piper, F5-TTS), so Gemini's edge is mostly breadth of languages and naturalness, not a category leap.
- **Speech generation** ([docs](https://ai.google.dev/gemini-api/docs/interactions/speech-generation)) — GD-018. Overlaps heavily with TTS; the differentiator is *director's-notes* control, inline audio tags, and multi-speaker dialogue, which local stacks don't really match — hence the larger gap.
- **Audio understanding** ([docs](https://ai.google.dev/gemini-api/docs/interactions/audio)) — GD-015. The only model-free Python ASR (`pocketsphinx`) is classical and dated; the strong *local* option is Whisper large-v3 (a neural model, so it sits in the local column). Beyond transcription, Gemini also does Q&A / summarization / very long inputs.
- **Live** (`gemini-3.1-flash-live-preview`, [docs](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview)) — GD-017. No drop-in Python library equivalent; Qwen2.5-Omni-7B is an emerging local option but trails on latency, robustness, and video parity, and is setup-heavy. This is a streaming surface that may not fit the one-shot envelope cleanly — design TBD.
- **Music** (`lyria-3-pro-preview`, [docs](https://ai.google.dev/gemini-api/docs/models/lyria-3-pro-preview)) — GD-016. No model-free Python library turns a prompt into music (rule-based algorithmic composition is a different task). Locally, MusicGen produces short instrumental clips while Lyria targets longer, structured songs — a real capability gap.
- **Embeddings** ([docs](https://ai.google.dev/gemini-api/docs/embeddings)) — GD-019. A model-free Python option (TF-IDF via scikit-learn) only captures lexical overlap, not meaning. Locally, top MTEB open embedding models fit a 3090, so this is the closest-to-parity of the set; Gemini's edge is multimodal inputs and managed scale.
