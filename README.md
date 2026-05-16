# FCP Speaker Cutter

Automatically cut a specific speaker out of a video using AI-based speaker diarization, and export the result as a Final Cut Pro-ready FCPXML file.

The script identifies who is speaking at every moment, shows you a labeled summary with language detection and sample transcripts, and lets you choose which speaker to keep. It outputs an `.fcpxml` that imports directly into the correct event in your existing FCP library, referencing your original high-resolution source clips.

---

## How it works

1. **Audio extraction** — FFmpeg extracts a mono 16 kHz WAV from your video.
2. **Speaker diarization** — [pyannote.audio](https://github.com/pyannote/pyannote-audio) segments the audio by speaker and labels each segment `SPEAKER_00`, `SPEAKER_01`, etc. Results are cached to a JSON file so re-runs are instant.
3. **Language detection + transcription** — [OpenAI Whisper](https://github.com/openai/whisper) transcribes a short sample from each speaker and detects the language.
4. **Speaker selection** — A summary is displayed (sorted by speaking time) with language and a sample quote. You enter the number of the speaker to keep.
5. **FCPXML generation** — The script builds a new timeline containing only the kept speaker's segments. When a source FCP project XML is provided, the output references the original source clips and imports into the correct event in your existing library.

---

## Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) (must be on your `PATH`)
- A [Hugging Face](https://huggingface.co/) account with access to:
  - [`pyannote/speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1)
  - [`pyannote/segmentation-3.0`](https://huggingface.co/pyannote/segmentation-3.0)

### Python dependencies

```
pip install pyannote.audio openai-whisper torch
```

On Apple Silicon, MPS (GPU acceleration) is used automatically if available.

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

Run diarization on a video and generate an FCPXML referencing that same file:

```bash
python3 diarize.py "fcp-media/MyVideo.mov"
```

The output `.fcpxml` will be written to `fcp-media/output/`.

### With speaker count hint

If you know how many speakers are in the video, pass it to improve diarization accuracy:

```bash
python3 diarize.py "fcp-media/MyVideo.mov" --speakers 2
```

### With original FCP project — recommended

For the best result, export your FCP project XML and pass it with `--fcpxml`. The output will reference your original full-resolution source clips and import directly into the correct event in your library:

```bash
python3 diarize.py "fcp-media/MyProxy.mov" --fcpxml "fcp-media/MyProject.fcpxmld"
```

The `--fcpxml` path is cached after the first run. Re-running to keep a different speaker does not require passing it again.

**Import into FCP:**
`File → Import → XML…` and select the generated `.fcpxml`. The project will appear inside the original event in your library, with all source clips, LUTs, and colour grading intact.

### Options

| Flag | Description |
|---|---|
| `--speakers N` | Tell pyannote the exact number of speakers (improves diarization accuracy) |
| `--fcpxml <path>` | Path to the exported FCP project XML or `.fcpxmld` bundle |
| `--event <name>` | Override the FCP event name in the output (defaults to the source project's event) |

---

## File layout

```
final-cut-speaker-cutter/
├── diarize.py          # main script
├── .env                # HF_TOKEN (not committed)
├── .env.example        # token template
└── fcp-media/
    ├── MyProxy.mov           # proxy video (input)
    ├── MyProject.fcpxmld/    # exported FCP project XML (input)
    │   └── Info.fcpxml
    └── output/               # all generated files
        ├── MyProxy_audio_mono.wav
        ├── MyProxy_segments.json
        └── MyProxy — SPEAKER_XX removed.fcpxml
```

Source files (proxy videos, FCP project XMLs) go in `fcp-media/`. Generated files (audio, cache, output FCPXML) go in `fcp-media/output/` and are excluded from git.

---

## Output files

| File | Description |
|---|---|
| `*_audio_mono.wav` | Extracted audio used for diarization (cached) |
| `*_segments.json` | Diarization results + per-speaker language info + FCPXML path (cached) |
| `* — SPEAKER_XX+… removed.fcpxml` | The Final Cut Pro project ready to import |

The JSON cache means re-running the script (e.g. to keep a different speaker) skips the slow diarization step and finishes in under a minute.

---

## Example session

```
=== Speaker Diarization → FCPXML ===
Video: Mauricio - SD 480p.mov

Audio already extracted (Mauricio - SD 480p_audio_mono.wav), skipping.
Found cached diarization (Mauricio - SD 480p_segments.json). Use it? [Y/n]:

Using cached FCPXML: Info.fcpxml
Original FCPXML: Info.fcpxml
  Parsing edit decisions...
  4 source clip(s) on timeline
  Target event: "Day 2" in library "Romania School.fcpbundle"
Duration: ~120m 28s

Transcribing speaker samples for identification...
  Transcribing SPEAKER_06... [Romanian]
  Transcribing SPEAKER_02... [Spanish]
  ...

=== Speaker Summary ===
  [0] SPEAKER_06: 2608s  (36%)  [Romanian]
       "orată, cașcum de eruca și rota roata..."

  [1] SPEAKER_02: 2298s  (32%)  [Spanish]
       "No, no, no. Horad, es como horar. Me voy por otro lado..."

  ...

Which speaker(s) do you want to KEEP?
  Enter one number, or multiple comma-separated (e.g. 0,2)
  [0] SPEAKER_06: 2608s  [Romanian]
  [1] SPEAKER_02: 2298s  [Spanish]
  ...
Enter number(s): 1

Keeping SPEAKER_02, removing SPEAKER_00, SPEAKER_01, SPEAKER_03...

=== Done ===
  Kept 1432 segments  (67.9 min)
  Removed: 43.5 min of SPEAKER_06
  Source files referenced: 4

  Output: Mauricio - SD 480p — SPEAKER_00+SPEAKER_01+SPEAKER_03+… removed.fcpxml

Open in Final Cut Pro now? [y/N]:
```

---

## Recommended workflow

1. Edit your interview rough-cut in FCP (primary storyline only, no B-roll yet).
2. Share/export a small proxy `.mov` for fast diarization.
3. Export the FCP project as XML (`File → Export XML…`) and place it in `fcp-media/`.
4. Run the script with `--fcpxml`, choose the speaker to keep.
5. Import the output `.fcpxml` into FCP — it lands in your existing event with full-resolution clips.
6. Layer B-roll, titles, colour grade, and effects on top of the cleaned edit.

---

## Known limitations

- **Primary storyline only** — Connected clips (B-roll), titles, generators, compound clips, and multicam clips are not included in the output. Best used on a rough-cut with only the primary storyline before adding B-roll.
- **Full-sequence proxy** — The proxy video must be an export of the entire sequence from start to finish. Exporting a range will cause the timestamp mapping to be off.
- **First sequence only** — If the FCPXML contains multiple projects, only the first sequence is processed.
- **Gaps become silence** — `<gap>` elements (black frames between clips) are processed as silence by diarization and are not included in the output. The result is a tighter edit, which is usually desirable.
- **External drive required** — If the original source clips reference a disconnected drive, FCP will show offline media on import. Reconnect the drive before importing.
