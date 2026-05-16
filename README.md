# FCP Speaker Cutter

Automatically cut a specific speaker out of a video using AI-based speaker diarization, and export the result as a Final Cut Pro-ready FCPXML file.

The script identifies who is speaking at every moment, shows you a labeled summary with language detection and sample transcripts, and lets you choose which speaker to keep. It outputs an `.fcpxml` that imports directly into the correct event in your existing FCP library, referencing your original high-resolution source clips — plus a `.txt` file with the full transcript of the kept speaker.

---

## How it works

1. **Audio extraction** — FFmpeg extracts a mono 16 kHz WAV from your video.
2. **Speaker diarization** — [pyannote.audio](https://github.com/pyannote/pyannote-audio) segments the audio by speaker and labels each segment `SPEAKER_00`, `SPEAKER_01`, etc. Speakers with less than 1 minute of total speech are automatically discarded.
3. **Language detection + transcription** — Whisper transcribes a short sample from each remaining speaker and detects the language. Supports [OpenAI Whisper](https://github.com/openai/whisper) (default) or [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) for faster transcription on Apple Silicon via the ANE.
4. **Speaker selection** — A summary is displayed (sorted by speaking time) with language and a sample quote. You enter the number of the speaker to keep.
5. **FCPXML generation** — The script builds a new timeline containing only the kept speaker's segments. When a source FCP project XML is provided, the output references the original source clips and imports into the correct event in your existing library.
6. **Transcript export** — A full Whisper transcription of the kept speaker's audio is written to a `.txt` file alongside the FCPXML.

---

## Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) (must be on your `PATH`)
- A [Hugging Face](https://huggingface.co/) account with access to:
  - [`pyannote/speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1)
  - [`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0)

### Python dependencies

```bash
pip install pyannote.audio openai-whisper torch
```

For faster transcription on Apple Silicon (optional):

```bash
pip install mlx-whisper
```

On Apple Silicon, MPS (GPU acceleration) is used automatically for diarization. With `--whisper mlx`, transcription runs on the ANE.

---

## Setup

### 1. Hugging Face token

Accept the model conditions on Hugging Face for both pyannote models, then create a `.env` file in the project directory:

```bash
echo 'HF_TOKEN=hf_your_token_here' > .env
```

The script loads `.env` automatically on startup and will exit with an error if `HF_TOKEN` is not set.

### 2. Install dependencies

```bash
pip install pyannote.audio openai-whisper torch
brew install ffmpeg   # macOS
```

---

## Usage

### Basic — proxy video only

```bash
python3 diarize.py "fcp-media/MyVideo.mov"
```

### With original FCP project — recommended

Export your FCP project XML and pass it with `--fcpxml`. The output will reference your original full-resolution source clips and import directly into the correct event in your library:

```bash
python3 diarize.py "fcp-media/MyProxy.mov" --fcpxml "fcp-media/MyProject.fcpxmld"
```

### With mlx-whisper (Apple Silicon)

```bash
python3 diarize.py "fcp-media/MyProxy.mov" --fcpxml "fcp-media/MyProject.fcpxmld" --whisper mlx
```

**Import into FCP:**
`File → Import → XML…` and select the generated `.fcpxml`. The project will appear inside the original event in your library, with all source clips, LUTs, and colour grading intact.

### Options

| Flag | Description |
|---|---|
| `--speakers N` | Tell pyannote the exact number of speakers (improves diarization accuracy) |
| `--fcpxml <path>` | Path to the exported FCP project XML or `.fcpxmld` bundle |
| `--event <name>` | Override the FCP event name in the output (defaults to the source project's event) |
| `--whisper openai\|mlx` | Transcription backend. `openai` (default) uses openai-whisper; `mlx` uses mlx-whisper (faster on Apple Silicon) |

---

## File layout

```
final-cut-speaker-cutter/
├── diarize.py          # main script
├── .env                # HF_TOKEN (not committed)
└── fcp-media/
    ├── MyProxy.mov           # proxy video (input)
    ├── MyProject.fcpxmld/    # exported FCP project XML (input)
    │   └── Info.fcpxml
    └── output/               # all generated files
        ├── MyProxy_audio_mono.wav
        ├── MyProxy — SPEAKER_XX kept.fcpxml
        └── MyProxy — SPEAKER_XX kept.txt
```

Source files (proxy videos, FCP project XMLs) go in `fcp-media/`. Generated files go in `fcp-media/output/` and are excluded from git.

---

## Output files

| File | Description |
|---|---|
| `*_audio_mono.wav` | Extracted audio used for diarization |
| `* — … removed.fcpxml` | The Final Cut Pro project ready to import |
| `* — … removed.txt` | Full transcript of the kept speaker's speech |

When many speakers are removed, the filename switches to `… kept.fcpxml` to avoid hitting OS and FCP filename length limits.

---

## Example session

```
=== Speaker Diarization → FCPXML ===
Video: Mauricio Day 3.mov
Extracting audio (mono 16kHz)...
  → mauricio-day3_audio_mono.wav
Loading pyannote diarization model...
  Using Apple MPS (GPU)
  Running diarization — this takes a few minutes for long videos...
  Found 1538 segments
  Skipping 5 speaker(s) with <1 min of speech.

Original FCPXML: Info.fcpxml
  Parsing edit decisions...
  4 source clip(s) on timeline
  Target event: "Day 3" in library "Mauricio.fcpbundle"
Duration: ~65m 30s

Transcribing speaker samples for identification...
  Transcribing SPEAKER_06... [Spanish]
  Transcribing SPEAKER_03... [Romanian]
  ...

=== Speaker Summary ===
  [0] SPEAKER_06: 1403s  (36%)  [Spanish]
       "El hícaro normalmente está en una lengua..."

  [1] SPEAKER_03: 1306s  (33%)  [Romanian]
       "Unii care o este ceva ce este într-o limba..."
  ...

Which speaker(s) do you want to KEEP?
  Enter one number, or multiple comma-separated (e.g. 0,2)
  [0] SPEAKER_06: 1403s  [Spanish]
  [1] SPEAKER_03: 1306s  [Romanian]
Enter number(s): 0

Keeping SPEAKER_06, removing SPEAKER_03...

Transcribing full audio for kept speaker(s)...
  Transcribing SPEAKER_06 (677 segments, 23.4 min)...
  Done [Spanish]

=== Done ===
  Kept 677 clip(s)  (39.3 min)
  Removed: 21.8 min of SPEAKER_03

  Output: mauricio-day3 — SPEAKER_03 removed.fcpxml
  Transcript: mauricio-day3 — SPEAKER_03 removed.txt

Open in Final Cut Pro now? [y/N]:
```

---

## Recommended workflow

1. Edit your interview rough-cut in FCP (primary storyline only, no B-roll yet).
2. Share/export a small proxy `.mov` for fast diarization.
3. Export the FCP project as XML (`File → Export XML…`) and place it in `fcp-media/`.
4. Run the script with `--fcpxml` and `--whisper mlx`, choose the speaker to keep.
5. Import the output `.fcpxml` into FCP — it lands in your existing event with full-resolution clips.
6. Use the `.txt` transcript for reference, subtitles, or further editing.
7. Layer B-roll, titles, colour grade, and effects on top of the cleaned edit.

---

## Known limitations

- **Primary storyline only** — Connected clips (B-roll), titles, generators, compound clips, and multicam clips are not included in the output. Best used on a rough-cut with only the primary storyline before adding B-roll.
- **Full-sequence proxy** — The proxy video must be an export of the entire sequence from start to finish. Exporting a range will cause the timestamp mapping to be off.
- **First sequence only** — If the FCPXML contains multiple projects, only the first sequence is processed.
- **Gaps become silence** — `<gap>` elements (black frames between clips) are processed as silence by diarization and are not included in the output. The result is a tighter edit, which is usually desirable.
- **External drive required** — If the original source clips reference a disconnected drive, FCP will show offline media on import. Reconnect the drive before importing.
