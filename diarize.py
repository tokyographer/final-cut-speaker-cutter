#!/usr/bin/env python3
"""
Speaker diarization → FCPXML generator
Runs pyannote diarization on a video, transcribes a sample from each speaker
(with language detection via Whisper), lets you pick which speaker to keep,
and outputs a Final Cut Pro-ready .fcpxml.

Usage:
  python3 diarize.py <proxy_video> [--speakers N] [--fcpxml <original_project.fcpxml>]

Options:
  --speakers N            Tell pyannote the exact number of speakers (improves accuracy).
  --fcpxml <path>         Original FCP project FCPXML. When provided, the output FCPXML
                          references the original source clips instead of the proxy video.
                          Export from FCP via File → Export XML before running.
"""

import sys
import subprocess
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

# --- Parse args ---
args = sys.argv[1:]
if not args or args[0].startswith("--"):
    print("Usage: python3 diarize.py <proxy_video> [--speakers N] [--fcpxml <original_project.fcpxml>]")
    sys.exit(1)

VIDEO_PATH = Path(args[0]).resolve()
if not VIDEO_PATH.exists():
    print(f"Error: file not found: {VIDEO_PATH}")
    sys.exit(1)

NUM_SPEAKERS = None
if "--speakers" in args:
    idx = args.index("--speakers")
    try:
        NUM_SPEAKERS = int(args[idx + 1])
    except (IndexError, ValueError):
        print("Error: --speakers requires an integer argument")
        sys.exit(1)

ORIGINAL_PATH = None
if "--original" in args:
    idx = args.index("--original")
    try:
        ORIGINAL_PATH = Path(args[idx + 1]).resolve()
        if not ORIGINAL_PATH.exists():
            print(f"Error: original file not found: {ORIGINAL_PATH}")
            sys.exit(1)
    except IndexError:
        print("Error: --original requires a file path argument")
        sys.exit(1)

FCPXML_PATH = None
if "--fcpxml" in args:
    idx = args.index("--fcpxml")
    try:
        p = Path(args[idx + 1]).resolve()
        if not p.exists():
            print(f"Error: fcpxml file not found: {p}")
            sys.exit(1)
        # .fcpxmld is a bundle — the real XML is inside at Info.fcpxml
        if p.suffix == ".fcpxmld" and p.is_dir():
            p = p / "Info.fcpxml"
            if not p.exists():
                print(f"Error: Info.fcpxml not found inside bundle: {p.parent}")
                sys.exit(1)
        FCPXML_PATH = p
    except IndexError:
        print("Error: --fcpxml requires a file path argument")
        sys.exit(1)

HF_TOKEN = os.environ.get("HF_TOKEN")
if not HF_TOKEN:
    print("Error: HF_TOKEN environment variable is not set.")
    print("  export HF_TOKEN=hf_your_token_here")
    sys.exit(1)
PROJECT_DIR = VIDEO_PATH.parent
AUDIO_PATH = PROJECT_DIR / f"{VIDEO_PATH.stem}_audio_mono.wav"
SEGMENTS_PATH = PROJECT_DIR / f"{VIDEO_PATH.stem}_segments.json"

LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "zh": "Chinese",
    "ko": "Korean", "ru": "Russian", "ar": "Arabic", "hi": "Hindi",
}


def parse_time(s):
    """Parse FCP rational time string like '83250/90000s' or '0s' → float seconds."""
    s = s.strip().rstrip("s")
    if "/" in s:
        num, den = s.split("/")
        return int(num) / int(den)
    return float(s) if s else 0.0


def parse_fcpxml_project(fcpxml_path):
    """
    Parse an FCP project FCPXML exported from Final Cut Pro.

    Returns a tuple:
      assets        — dict  {asset_id: {src, name, uid, has_video, has_audio, elem}}
      fmt_info      — dict  {fps_num, fps_den, width, height, name, frame_duration_str}
      timeline_clips— list  [{timeline_start, timeline_end, src_start, asset_id, tc_format}]
      seq_duration  — float total sequence duration in seconds
      formats       — dict  {format_id: {fps_num, fps_den, ..., elem}}
      seq_fmt_id    — str   the format id referenced by the sequence
    """
    import xml.etree.ElementTree as ET

    tree = ET.parse(str(fcpxml_path))
    root = tree.getroot()

    def strip_ns(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    # --- formats ---
    formats = {}
    for fmt in root.iter():
        if strip_ns(fmt.tag) != "format":
            continue
        fid = fmt.get("id")
        if not fid:
            continue
        fd = fmt.get("frameDuration", "1/30s").rstrip("s")
        if "/" in fd:
            fd_num_str, fd_den_str = fd.split("/")
            fps_num = int(fd_den_str)
            fps_den = int(fd_num_str)
        else:
            fps_num, fps_den = 30, 1
        formats[fid] = {
            "fps_num": fps_num,
            "fps_den": fps_den,
            "width": int(fmt.get("width", 1920)),
            "height": int(fmt.get("height", 1080)),
            "name": fmt.get("name", ""),
            "frame_duration_str": fmt.get("frameDuration", "1/30s"),
            "elem": fmt,
        }

    # --- assets ---
    assets = {}
    for asset in root.iter():
        if strip_ns(asset.tag) != "asset":
            continue
        aid = asset.get("id")
        if not aid:
            continue
        src = ""
        for child in asset:
            if strip_ns(child.tag) == "media-rep" and child.get("kind") == "original-media":
                src = child.get("src", "")
                break
        if not src:
            src = asset.get("src", "")
        if src.startswith("file://"):
            src = unquote(src[7:])
        assets[aid] = {
            "src": src,
            "name": asset.get("name", Path(src).stem if src else aid),
            "uid": asset.get("uid", ""),
            "has_video": asset.get("hasVideo", "0") == "1",
            "has_audio": asset.get("hasAudio", "0") == "1",
            "duration": parse_time(asset.get("duration", "0s")),
            "elem": asset,  # raw element — emitted verbatim in output
        }

    # --- sequence → spine → asset-clips ---
    seq = None
    for elem in root.iter():
        if strip_ns(elem.tag) == "sequence":
            seq = elem
            break
    if seq is None:
        raise ValueError("No <sequence> found in FCPXML")

    seq_duration = parse_time(seq.get("duration", "0s"))
    seq_fmt_id = seq.get("format", "")
    fmt_info = formats.get(seq_fmt_id, {
        "fps_num": 30000, "fps_den": 1001,
        "width": 1920, "height": 1080,
        "name": "FFVideoFormat1080p2997",
        "frame_duration_str": "1001/30000s",
    })

    spine = None
    for elem in seq:
        if strip_ns(elem.tag) == "spine":
            spine = elem
            break
    if spine is None:
        raise ValueError("No <spine> found in sequence")

    timeline_clips = []
    for elem in spine:
        if strip_ns(elem.tag) != "asset-clip":
            continue
        ref = elem.get("ref", "")
        if ref not in assets:
            continue
        tl_offset = parse_time(elem.get("offset", "0s"))
        duration = parse_time(elem.get("duration", "0s"))
        src_start = parse_time(elem.get("start", "0s"))
        if duration <= 0:
            continue
        timeline_clips.append({
            "timeline_start": tl_offset,
            "timeline_end": tl_offset + duration,
            "src_start": src_start,
            "asset_id": ref,
            "tc_format": elem.get("tcFormat", "NDF"),
        })

    timeline_clips.sort(key=lambda c: c["timeline_start"])
    return assets, fmt_info, timeline_clips, seq_duration, formats, seq_fmt_id


def get_video_info():
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", str(VIDEO_PATH)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def extract_audio():
    print("Extracting audio (mono 16kHz)...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(VIDEO_PATH),
        "-ac", "1", "-ar", "16000", "-vn",
        str(AUDIO_PATH)
    ], check=True, capture_output=True)
    print(f"  → {AUDIO_PATH.name}")


def run_diarization():
    print("Loading pyannote diarization model...")
    import warnings
    warnings.filterwarnings("ignore")
    from pyannote.audio import Pipeline
    import torch

    from huggingface_hub import login
    login(token=HF_TOKEN, add_to_git_credential=False)
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
    if torch.backends.mps.is_available():
        pipeline = pipeline.to(torch.device("mps"))
        print("  Using Apple MPS (GPU)")

    hint = f" (told: {NUM_SPEAKERS} speakers)" if NUM_SPEAKERS else ""
    print(f"  Running diarization{hint} — this takes a few minutes for long videos...")

    kwargs = {}
    if NUM_SPEAKERS:
        kwargs["num_speakers"] = NUM_SPEAKERS

    diarization = pipeline(str(AUDIO_PATH), **kwargs)

    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append({"start": round(turn.start, 3), "end": round(turn.end, 3), "speaker": speaker})

    data = {"speakers": {}, "segments": segments}
    with open(SEGMENTS_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved {len(segments)} segments → {SEGMENTS_PATH.name}")
    return segments, {}


def save_segments(segments, speaker_info):
    """Write segments + per-speaker language metadata to the JSON cache."""
    data = {"speakers": speaker_info, "segments": segments}
    with open(SEGMENTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def load_cached_segments():
    if SEGMENTS_PATH.exists():
        answer = input(f"\nFound cached diarization ({SEGMENTS_PATH.name}). Use it? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            with open(SEGMENTS_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                # Old format — plain list of segments, no language info
                return data, {}
            return data.get("segments", []), data.get("speakers", {})
    return None, None


def transcribe_speaker_samples(segments):
    """Transcribe a short sample from each speaker using Whisper."""
    import whisper

    print("\nTranscribing speaker samples for identification...")
    print("  Loading Whisper model (base)...")
    model = whisper.load_model("base")

    # For each speaker, collect segments until we have ~25s of audio
    speaker_segs = {}
    for seg in segments:
        sp = seg["speaker"]
        dur = seg["end"] - seg["start"]
        if dur < 2.0:
            continue
        if sp not in speaker_segs:
            speaker_segs[sp] = []
        total = sum(s["end"] - s["start"] for s in speaker_segs[sp])
        if total < 25.0:
            speaker_segs[sp].append(seg)

    results = {}
    speakers_sorted = sorted(speaker_segs.keys())
    for sp in speakers_sorted:
        segs = speaker_segs[sp]
        if not segs:
            results[sp] = {"language": "?", "text": "(no usable segments)"}
            continue

        print(f"  Transcribing {sp}...", end=" ", flush=True)

        # Build a concat filter to extract and join their segments into one clip
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name

        try:
            # Extract each segment to a temp file then concat
            part_files = []
            for i, seg in enumerate(segs):
                pf = tmp_path + f"_part{i}.wav"
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(AUDIO_PATH),
                    "-ss", str(seg["start"]),
                    "-t", str(seg["end"] - seg["start"]),
                    "-ac", "1", "-ar", "16000", pf
                ], capture_output=True, check=True)
                part_files.append(pf)

            if len(part_files) == 1:
                os.rename(part_files[0], tmp_path)
            else:
                # Concat via ffmpeg concat demuxer
                list_file = tmp_path + "_list.txt"
                with open(list_file, "w") as lf:
                    for pf in part_files:
                        lf.write(f"file '{pf}'\n")
                subprocess.run([
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", list_file, "-c", "copy", tmp_path
                ], capture_output=True, check=True)
                os.unlink(list_file)
                for pf in part_files:
                    if os.path.exists(pf):
                        os.unlink(pf)

            result = model.transcribe(tmp_path, task="transcribe")
            lang_code = result.get("language", "?")
            lang_name = LANGUAGE_NAMES.get(lang_code, lang_code.upper())
            text = result["text"].strip()
            # Trim to ~300 chars at a word boundary
            if len(text) > 300:
                text = text[:300].rsplit(" ", 1)[0] + "…"
            results[sp] = {"language": lang_name, "lang_code": lang_code, "text": text}
            print(f"[{lang_name}]")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return results


def show_speaker_summary(segments, duration, transcripts=None):
    speakers = {}
    for seg in segments:
        sp = seg["speaker"]
        dur = seg["end"] - seg["start"]
        speakers[sp] = speakers.get(sp, 0) + dur

    print("\n=== Speaker Summary ===")
    for i, (sp, dur) in enumerate(sorted(speakers.items(), key=lambda x: -x[1])):
        lang = ""
        sample = ""
        if transcripts and sp in transcripts:
            t = transcripts[sp]
            lang = f"  [{t['language']}]"
            sample = f"\n       \"{t['text']}\""
        print(f"  [{i}] {sp}: {dur:.0f}s  ({dur / duration * 100:.0f}%){lang}{sample}")
        if sample:
            print()
    return speakers


def snap_to_frame(t, fps_num, fps_den):
    """Round t (seconds) to the nearest frame boundary."""
    frame_index = round(t * fps_num / fps_den)
    return frame_index * fps_den / fps_num


def secs(t, fps_num, fps_den, tb=90000):
    """Encode t as a timebase fraction snapped to the frame grid.
    Pass tb=fps_num to match the source file's native timebase exactly."""
    frames = round(t * fps_num / fps_den)
    numerator = frames * fps_den * tb // fps_num
    return f"{numerator}/{tb}s"


def generate_fcpxml(segments, remove_speakers, video_info, output_path, original_path=None):
    vs = next(s for s in video_info["streams"] if s["codec_type"] == "video")
    fps_num, fps_den = (int(x) for x in vs["r_frame_rate"].split("/"))
    width, height = vs["width"], vs["height"]
    duration = float(video_info["format"]["duration"])

    removed = sorted(
        [s for s in segments if s["speaker"] in remove_speakers],
        key=lambda x: x["start"]
    )
    keep = []
    cursor = 0.0
    for seg in removed:
        if seg["start"] - cursor > 0.05:
            keep.append({"start": cursor, "end": seg["start"]})
        cursor = max(cursor, seg["end"])
    if duration - cursor > 0.05:
        keep.append({"start": cursor, "end": duration})

    # Snap all keep segments to frame boundaries
    snapped_keep = []
    for r in keep:
        s = snap_to_frame(r["start"], fps_num, fps_den)
        e = snap_to_frame(r["end"], fps_num, fps_den)
        if e - s > 0:
            snapped_keep.append({"start": s, "end": e})

    total_dur = sum(r["end"] - r["start"] for r in snapped_keep)
    snapped_duration = snap_to_frame(duration, fps_num, fps_den)
    ref_path = original_path if original_path else VIDEO_PATH
    video_name = ref_path.stem
    src_url = "file://" + quote(str(ref_path.resolve()), safe="/:")
    label = "+".join(sorted(remove_speakers))

    clips = ""
    offset = 0.0
    for r in snapped_keep:
        seg_dur = r["end"] - r["start"]
        clips += (
            f'\n            <asset-clip name="{video_name}" ref="r2"'
            f' offset="{secs(offset, fps_num, fps_den)}"'
            f' duration="{secs(seg_dur, fps_num, fps_den)}"'
            f' start="{secs(r["start"], fps_num, fps_den)}"'
            f' tcFormat="NDF" audioRole="dialogue"/>'
        )
        offset = snap_to_frame(offset + seg_dur, fps_num, fps_den)

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
    <resources>
        <format id="r1" name="FFVideoFormat{height}p{fps_num // fps_den if fps_den == 1 else fps_num}"
            frameDuration="{fps_den}/{fps_num}s"
            width="{width}" height="{height}"
            colorSpace="1-1-1 (Rec. 709)"/>
        <asset id="r2" name="{video_name}"
            uid="{video_name.replace(' ', '_')}"
            start="0s"
            duration="{secs(snapped_duration, fps_num, fps_den)}"
            hasVideo="1" hasAudio="1">
            <media-rep kind="original-media" src="{src_url}"/>
        </asset>
    </resources>
    <library>
        <event name="Speaker Cut">
            <project name="{video_name} — {label} removed">
                <sequence format="r1"
                    tcStart="0s" tcFormat="NDF"
                    audioLayout="stereo" audioRate="48k"
                    duration="{secs(total_dur, fps_num, fps_den)}">
                    <spine>{clips}
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml)

    return snapped_keep, total_dur


def generate_fcpxml_from_source(segments, remove_speakers, parsed_fcpxml, output_path):
    """
    Generate FCPXML referencing the original source clips from the FCP project.

    Works by:
      1. Computing keep intervals in proxy-timeline time (same as generate_fcpxml).
      2. Mapping each interval back to source clips using the edit decisions parsed
         from the original FCPXML.
      3. Writing a new FCPXML whose asset-clips reference the original source files,
         with all original attributes (timecodes, LUTs, formats) preserved verbatim.
    """
    import xml.etree.ElementTree as ET

    assets, fmt_info, timeline_clips, seq_duration, formats, seq_fmt_id = parsed_fcpxml
    fps_num = fmt_info["fps_num"]
    fps_den = fmt_info["fps_den"]
    # Use the native timebase (fps_num) so timecodes match the source exactly
    tb = fps_num

    # Build keep intervals in proxy-timeline / export time
    removed = sorted(
        [s for s in segments if s["speaker"] in remove_speakers],
        key=lambda x: x["start"],
    )
    keep_intervals = []
    cursor = 0.0
    for seg in removed:
        if seg["start"] - cursor > 0.05:
            keep_intervals.append({"start": cursor, "end": seg["start"]})
        cursor = max(cursor, seg["end"])
    if seq_duration - cursor > 0.05:
        keep_intervals.append({"start": cursor, "end": seq_duration})

    # Snap to frame grid
    snapped = []
    for r in keep_intervals:
        s = snap_to_frame(r["start"], fps_num, fps_den)
        e = snap_to_frame(r["end"], fps_num, fps_den)
        if e - s > 0:
            snapped.append({"start": s, "end": e})

    # Map each keep interval onto source clips
    source_clips = []  # [{asset_id, src_start, duration, tc_format}]
    for ks in snapped:
        t_start, t_end = ks["start"], ks["end"]
        for clip in timeline_clips:
            ol_start = max(t_start, clip["timeline_start"])
            ol_end = min(t_end, clip["timeline_end"])
            if ol_end - ol_start < 0.005:
                continue
            src_in = snap_to_frame(
                clip["src_start"] + (ol_start - clip["timeline_start"]),
                fps_num, fps_den,
            )
            seg_dur = snap_to_frame(ol_end - ol_start, fps_num, fps_den)
            if seg_dur > 0:
                source_clips.append({
                    "asset_id": clip["asset_id"],
                    "src_start": src_in,
                    "duration": seg_dur,
                    "tc_format": clip["tc_format"],
                })

    total_dur = sum(c["duration"] for c in source_clips)

    # Collect the format IDs we need: sequence format + all formats used by assets
    used_asset_ids = {c["asset_id"] for c in source_clips}
    used_assets = {aid: assets[aid] for aid in used_asset_ids if aid in assets}
    needed_fmt_ids = {seq_fmt_id}
    for asset in used_assets.values():
        fmt_ref = asset["elem"].get("format", "")
        if fmt_ref:
            needed_fmt_ids.add(fmt_ref)

    # Build <resources> — emit original XML elements verbatim so all attributes
    # (LUTs, audio channels, native timecodes, colorSpace, etc.) are preserved
    res_parts = []
    for fid in sorted(needed_fmt_ids):
        if fid in formats:
            res_parts.append("        " + ET.tostring(formats[fid]["elem"], encoding="unicode").strip())
    for aid in sorted(used_assets):
        elem = used_assets[aid]["elem"]
        res_parts.append("        " + ET.tostring(elem, encoding="unicode").strip())
    res = "\n".join(res_parts) + "\n"

    # Build <spine> clips — original asset IDs and tcFormat preserved
    clips = ""
    offset = 0.0
    for sc in source_clips:
        name = used_assets.get(sc["asset_id"], {}).get("name", "")
        clips += (
            f'\n            <asset-clip name="{name}" ref="{sc["asset_id"]}"'
            f' offset="{secs(offset, fps_num, fps_den, tb=tb)}"'
            f' duration="{secs(sc["duration"], fps_num, fps_den, tb=tb)}"'
            f' start="{secs(sc["src_start"], fps_num, fps_den, tb=tb)}"'
            f' tcFormat="{sc["tc_format"]}" audioRole="dialogue"/>'
        )
        offset = snap_to_frame(offset + sc["duration"], fps_num, fps_den)

    label = "+".join(sorted(remove_speakers))
    project_name = f"{VIDEO_PATH.stem} — {label} removed"

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
    <resources>
{res}    </resources>
    <library>
        <event name="Speaker Cut">
            <project name="{project_name}">
                <sequence format="{seq_fmt_id}"
                    tcStart="0s" tcFormat="NDF"
                    audioLayout="stereo" audioRate="48k"
                    duration="{secs(total_dur, fps_num, fps_den, tb=tb)}">
                    <spine>{clips}
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml)

    return source_clips, total_dur


def main():
    print("=== Speaker Diarization → FCPXML ===")
    print(f"Video: {VIDEO_PATH.name}")
    if NUM_SPEAKERS:
        print(f"Speaker hint: {NUM_SPEAKERS}")

    # Parse original FCPXML if provided — its sequence duration is authoritative
    parsed_fcpxml = None
    if FCPXML_PATH:
        print(f"Original FCPXML: {FCPXML_PATH.name}")
        print("  Parsing edit decisions...")
        parsed_fcpxml = parse_fcpxml_project(FCPXML_PATH)
        _, _, tl_clips, seq_duration, _, _ = parsed_fcpxml
        duration = seq_duration
        print(f"  {len(tl_clips)} source clip(s) on timeline")
    else:
        video_info = get_video_info()
        duration = float(video_info["format"]["duration"])

    print(f"Duration: ~{int(duration) // 60}m {int(duration) % 60}s\n")

    if not AUDIO_PATH.exists():
        extract_audio()
    else:
        print(f"Audio already extracted ({AUDIO_PATH.name}), skipping.")

    segments, cached_speakers = load_cached_segments()
    if segments is None:
        segments, cached_speakers = run_diarization()

    transcripts = transcribe_speaker_samples(segments)

    # Persist language info back to the JSON cache
    speaker_info = {
        sp: {"language": t["language"], "lang_code": t.get("lang_code", "?")}
        for sp, t in transcripts.items()
    }
    save_segments(segments, speaker_info)

    speakers = show_speaker_summary(segments, duration, transcripts)
    # Sort by duration descending — same order shown in the summary above
    speaker_list = sorted(speakers.keys(), key=lambda sp: -speakers[sp])

    print("\nWhich speaker(s) do you want to KEEP?")
    print("  Enter one number, or multiple comma-separated (e.g. 0,2)")
    for i, sp in enumerate(speaker_list):
        lang = f"  [{transcripts[sp]['language']}]" if transcripts and sp in transcripts else ""
        print(f"  [{i}] {sp}: {speakers[sp]:.0f}s{lang}")

    while True:
        raw = input("Enter number(s): ").strip()
        try:
            choices = [int(x.strip()) for x in raw.split(",")]
            if all(0 <= c < len(speaker_list) for c in choices):
                break
        except ValueError:
            pass
        print("  Invalid choice, try again.")

    keep_speakers = {speaker_list[c] for c in choices}
    remove_speakers = set(speaker_list) - keep_speakers
    print(f"\nKeeping {', '.join(sorted(keep_speakers))}, removing {', '.join(sorted(remove_speakers))}...")

    label = "+".join(sorted(remove_speakers))
    output_path = PROJECT_DIR / f"{VIDEO_PATH.stem} — {label} removed.fcpxml"

    if parsed_fcpxml:
        keep, total_dur = generate_fcpxml_from_source(
            segments, remove_speakers, parsed_fcpxml, output_path
        )
    else:
        keep, total_dur = generate_fcpxml(
            segments, remove_speakers, video_info, output_path, original_path=ORIGINAL_PATH
        )

    print(f"\n=== Done ===")
    print(f"  Kept {len(keep)} clip(s)  ({total_dur / 60:.1f} min)")
    for sp in sorted(remove_speakers):
        print(f"  Removed: {speakers[sp] / 60:.1f} min of {sp}")
    if parsed_fcpxml:
        used = {c["asset_id"] for c in keep}
        print(f"  Source files referenced: {len(used)}")
    print(f"\n  Output: {output_path.name}")

    answer = input("\nOpen in Final Cut Pro now? [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        subprocess.run(["open", str(output_path)])


if __name__ == "__main__":
    main()
