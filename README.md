# FCP Speaker Cutter

Automatically cut a specific speaker out of a video using AI-based speaker diarization, and export the result as a Final Cut Pro-ready FCPXML file.

The script identifies who is speaking at every moment, shows you a labeled summary with language detection and sample transcripts, and lets you choose which speaker to keep. It outputs an `.fcpxml` that references either your proxy export or the original high-resolution source clips.

---

## How it works

1. **Audio extraction** — FFmpeg extracts a mono 16 kHz WAV from your video.
2. **Speaker diarization** — [pyannote.audio](https://github.com/pyannote/pyannote-audio) segments the audio by speaker and labels each segment `SPEAKER_00`, `SPEAKER_01`, etc. Results are cached to a JSON file so re-runs are instant.
3. **Language detection + transcription** — [OpenAI Whisper](https://github.com/openai/whisper) transcribes a short sample from each speaker and detects the language.
4. **Speaker selection** — A summary is displayed (sorted by speaking time) with language and a sample quote. You enter the number of the speaker to keep.
5. **FCPXML generation** — The script builds a new timeline containing only the kept speaker's segments and writes an `.fcpxml` ready to import into Final Cut Pro.

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

Accept the model conditions on Hugging Face for both pyannote models, then set your token:

```bash
export HF_TOKEN=hf_your_token_here
```

The script will exit with an error if `HF_TOKEN` is not set.

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
python3 diarize.py "MyVideo.mov"
```

The output `.fcpxml` will be written next to the video file.

### With speaker count hint

If you know how many speakers are in the video, pass it to improve diarization accuracy:

```bash
python3 diarize.py "MyVideo.mov" --speakers 2
```

### With original production file — recommended

For the best result, run diarization on a small proxy export while the output FCPXML asset reference points to your original full-resolution file:

```bash
python3 diarize.py "MyProxy.mov" --original "/path/to/MyOriginal.mov"
```

The generated `.fcpxml` will embed a reference to the original file, so when you import it into Final Cut Pro the timeline plays back at full quality.

**Import into FCP:**
`File → Import → XML…` and select the generated `.fcpxml`. A new event called **Speaker Cut** will appear in your library.

---

## Output files

| File | Description |
|---|---|
| `*_audio_mono.wav` | Extracted audio used for diarization (cached) |
| `*_segments.json` | Diarization results + per-speaker language info (cached) |
| `* — SPEAKER_XX+… removed.fcpxml` | The Final Cut Pro project ready to import |

The JSON cache means re-running the script (e.g. to keep a different speaker) skips the slow diarization step and finishes in under a minute.

---

## Example session

```
=== Speaker Diarization → FCPXML ===
Video: Interview.mov
Duration: ~96m 19s

Audio already extracted (Interview_audio_mono.wav), skipping.
Found cached diarization (Interview_segments.json). Use it? [Y/n]:

Transcribing speaker samples for identification...
  Transcribing SPEAKER_00... [English]
  Transcribing SPEAKER_01... [Romanian]

=== Speaker Summary ===
  [0] SPEAKER_00: 4821s  (83%)  [English]
       "Welcome to today's episode. We're going to be talking about..."

  [1] SPEAKER_01: 847s  (15%)  [Romanian]
       "Bună ziua, sunt foarte bucuros să fiu aici..."

Which speaker(s) do you want to KEEP?
  Enter one number, or multiple comma-separated (e.g. 0,2)
  [0] SPEAKER_00: 4821s  [English]
  [1] SPEAKER_01: 847s  [Romanian]
Enter number(s): 1

Keeping SPEAKER_01, removing SPEAKER_00...

=== Done ===
  Kept 312 segments  (14.1 min)
  Removed: 80.4 min of SPEAKER_00

  Output: Interview — SPEAKER_00 removed.fcpxml

Open in Final Cut Pro now? [y/N]:
```

---

## Known limitations

- **Primary storyline only** — Connected clips (B-roll), titles, generators, compound clips, and multicam clips are not included in the output. Best used on a rough-cut with only the primary storyline before adding B-roll.
- **Full-sequence proxy** — The proxy video must be an export of the entire sequence from start to finish. Exporting a range will cause the timestamp mapping to be off.
- **First sequence only** — If the FCPXML contains multiple projects, only the first sequence is processed.
- **Gaps become silence** — `<gap>` elements (black frames between clips) are processed as silence by diarization and are not included in the output. The result is a tighter edit, which is usually desirable.

---

## Recommended workflow

1. Edit your interview rough-cut in FCP (primary storyline only, no B-roll yet).
2. Share/export a small proxy `.mov` for fast diarization.
3. Export the FCP project as XML (`File → Export XML…`).
4. Run the script, choose the speaker to keep.
5. Import the output `.fcpxml` into FCP.
6. Layer B-roll, titles, colour grade, and effects on top of the cleaned edit.
