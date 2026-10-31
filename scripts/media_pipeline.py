#!/usr/bin/env python3
"""Portable source preparation for an agent-authored pixel battle edit.

Requires FFmpeg/FFprobe on PATH. Download also requires yt-dlp; transcription
optionally imports openai-whisper in this Python environment. Selection of clips,
speakers, captions and move names belongs to the agent/user, never this script.
All paths in a project/plan are relative to the project JSON directory (or the
plan directory when no project is supplied). CLI paths are relative to cwd.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"Required executable not on PATH: {name}")
    return found


def run(command: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{Path(command[0]).name} failed ({result.returncode}):\n{detail[-8000:]}")
    return result


def metadata(source: Path) -> dict:
    return json.loads(run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)]).stdout)


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Must be a finite positive number")
    return number


def fraction(value: str | None) -> float | None:
    try:
        return float(Fraction(value or "0/0"))
    except (ValueError, ZeroDivisionError):
        return None


def project_context(args: argparse.Namespace) -> tuple[dict, Path, Path | None]:
    if getattr(args, "project", None):
        path = Path(args.project).resolve()
        return read_json(path), path.parent, path
    return {}, Path.cwd(), None


def command_download(args: argparse.Namespace) -> dict:
    config, base, project_path = project_context(args)
    project_dir = Path(args.project_dir).resolve() if args.project_dir else base
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    url = args.url or config.get("source_url")
    if not url:
        raise ValueError("download requires --url or project.source_url")
    prior_manifest = source_dir / "download_manifest.json"
    existing_video = [p for p in source_dir.glob("video.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if existing_video:
        previous_url = read_json(prior_manifest).get("source_url") if prior_manifest.exists() else None
        if previous_url != url:
            raise FileExistsError("source/video already exists without a matching download manifest URL; use a new project directory")
    height = args.max_height
    fmt = args.format or f"bv*[ext=mp4][height<={height}]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]"
    command = [tool("yt-dlp"), "--no-playlist", "--continue", "--no-overwrites", "--no-progress",
               "--write-info-json", "--write-subs", "--write-auto-subs", "--sub-langs", args.sub_langs,
               "--sub-format", "srt/vtt/best", "--convert-subs", "srt", "--merge-output-format", "mp4",
               "-f", fmt, "-o", str(source_dir / "video.%(ext)s"), "--print", "after_move:%(filepath)s", url]
    result = run(command)
    candidates = [Path(line.strip()) for line in result.stdout.splitlines() if Path(line.strip()).is_file()]
    videos = [p for p in candidates if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not videos:
        videos = [p for p in source_dir.glob("video.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not videos:
        raise RuntimeError("yt-dlp returned without a recognizable downloaded video")
    video = max(videos, key=lambda p: p.stat().st_mtime_ns).resolve()
    info_path = source_dir / "video.info.json"
    info = read_json(info_path) if info_path.exists() else {}
    manual = sorted((info.get("subtitles") or {}).keys())
    automatic = sorted((info.get("automatic_captions") or {}).keys())
    report = {"source_url": url, "source_path": str(video), "title": info.get("title"),
              "id": info.get("id"), "info_json": str(info_path) if info_path.exists() else None,
              "subtitle_files": [str(p) for p in source_dir.glob("video.*") if p.suffix.lower() in (".srt", ".vtt")],
              "available_manual_languages": manual, "available_automatic_languages": automatic,
              "note": "No subtitle files means no selected track was available/downloaded. Burned-in text still requires visual review or OCR."}
    write_json(source_dir / "download_manifest.json", report)
    if project_path:
        config.update(source_url=url, source_path=relative_or_absolute(video, base))
        write_json(project_path, config)
    return report


def command_probe(args: argparse.Namespace) -> dict:
    source = Path(args.source).resolve()
    data = metadata(source)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    report = {"source_path": str(source), "probe": data, "timestamp_verification": "not_requested"}
    if video:
        report.update(width=video["width"], height=video["height"],
                      declared_fps=fraction(video.get("r_frame_rate")),
                      average_fps=fraction(video.get("avg_frame_rate")),
                      video_duration_seconds=float(video.get("duration", data["format"]["duration"])))
        if not args.quick:
            raw = run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_frames",
                       "-show_entries", "frame=best_effort_timestamp_time,duration_time", "-of", "json", str(source)]).stdout
            frames = json.loads(raw)["frames"]
            stamps = [float(f["best_effort_timestamp_time"]) for f in frames if "best_effort_timestamp_time" in f]
            if not stamps:
                raise RuntimeError("No decoded video timestamps were returned")
            first = stamps[0]
            normalized = [t - first for t in stamps]
            buckets = Counter(math.floor(t + 1e-7) for t in normalized)
            deltas = [b - a for a, b in zip(stamps, stamps[1:])]
            report.update(timestamp_verification="all_decoded_video_timestamps", decoded_frame_count=len(stamps),
                          first_pts_seconds=first, last_pts_seconds=stamps[-1],
                          min_frame_delta_seconds=min(deltas) if deltas else None,
                          max_frame_delta_seconds=max(deltas) if deltas else None,
                          non_increasing_timestamp_count=sum(d <= 0 for d in deltas),
                          frames_per_second=[{"second": k, "frame_count": buckets.get(k, 0)}
                                             for k in range(max(buckets) + 1)],
                          note="Counts are encoded/decoded frame cadence, not unique motion frames. The final second may be partial.")
            if args.csv:
                csv_path = Path(args.csv).resolve()
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["frame_index_1based", "source_pts_seconds", "relative_seconds"])
                    writer.writerows((i + 1, f"{t:.6f}", f"{t-first:.6f}") for i, t in enumerate(stamps))
                report["frame_timestamps_csv"] = str(csv_path)
    if args.out:
        write_json(Path(args.out).resolve(), report)
    return report


def command_extract(args: argparse.Namespace) -> dict:
    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    existing = list(out.glob("frame_*.jpg"))
    if existing:
        raise FileExistsError(f"Use an empty extraction directory; it already contains {len(existing)} frame images: {out}")
    if args.start < 0:
        raise ValueError("--start must be non-negative")
    filters = [f"trim=start={args.start}" + (f":duration={args.duration}" if args.duration else "")]
    if not args.all_frames:
        # Keep source PTS, selecting actual frames rather than synthesized fps output.
        gap = 1 / args.fps - 1e-7
        filters.append(f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{gap})")
    if args.max_width:
        filters.append(f"scale='min({args.max_width},iw)':-2")
    filters.append("showinfo")
    result = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-loglevel", "info", "-i", str(source),
                  "-map", "0:v:0", "-an", "-vf", ",".join(filters), "-fps_mode", "vfr", "-q:v", str(args.quality),
                  str(out / "frame_%06d.jpg")])
    timestamps = [float(t) for t in re.findall(r"\bn:\s*\d+.*?\bpts_time:([\d.eE+-]+)", result.stderr)]
    files = sorted(out.glob("frame_*.jpg"))
    if not files or len(files) != len(timestamps):
        raise RuntimeError(f"Extraction produced {len(files)} frames but {len(timestamps)} timestamps; inspect FFmpeg output")
    with (out / "timestamps.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "source_timestamp_seconds"])
        writer.writerows((p.name, f"{t:.6f}") for p, t in zip(files, timestamps))
    report = {"source_path": str(source), "directory": str(out), "count": len(files),
              "mode": "all_source_frames" if args.all_frames else "sampled_actual_source_frames",
              "requested_sampling_fps": None if args.all_frames else args.fps,
              "first_source_timestamp": timestamps[0], "last_source_timestamp": timestamps[-1],
              "jpeg_quality": args.quality, "max_width": args.max_width,
              "note": "Actual source PTS recorded in timestamps.csv; irregular source cadence can make sample intervals longer than requested."}
    write_json(out / "extraction_manifest.json", report)
    return report


def srt_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_srt(path: Path, rows: list[dict]) -> None:
    text = "\n\n".join(f"{i}\n{srt_time(r['start'])} --> {srt_time(r['end'])}\n{r['text']}" for i, r in enumerate(rows, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def read_srt(path: Path) -> list[dict]:
    rows = []
    pattern = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()):
        lines = block.splitlines()
        marker = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if marker is None:
            continue
        times = pattern.findall(lines[marker])
        if len(times) != 2:
            raise ValueError(f"Malformed SRT time in {path}: {lines[marker]}")
        seconds = [int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000 for h, m, s, ms in times]
        rows.append({"start": seconds[0], "end": seconds[1], "text": "\n".join(lines[marker+1:])})
    return rows


def command_transcribe(args: argparse.Namespace) -> dict:
    tool("ffmpeg")
    try:
        import whisper
        import torch
    except ImportError as exc:
        raise RuntimeError("transcribe requires openai-whisper and torch in the Python environment running this script") from exc
    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    device = args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
    kwargs = {"device": device}
    if args.cache_dir:
        kwargs["download_root"] = str(Path(args.cache_dir).expanduser().resolve())
    model = whisper.load_model(args.model, **kwargs)
    result = model.transcribe(str(source), language=args.language, task="transcribe", word_timestamps=True,
                              fp16=device.startswith("cuda"), initial_prompt=args.initial_prompt, verbose=False)
    write_json(out / "asr_raw.json", result)
    raw_rows = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in result["segments"]]
    write_srt(out / "asr_raw.srt", raw_rows)
    rows = [dict(r) for r in raw_rows]
    if args.traditional:
        try:
            from opencc import OpenCC
        except ImportError as exc:
            raise RuntimeError("--traditional requires opencc; raw ASR files were already saved") from exc
        converter = OpenCC("s2twp")
        for row in rows:
            row["text"] = converter.convert(row["text"])
        write_srt(out / "asr_traditional_unreviewed.srt", rows)
    (out / "asr_raw.txt").write_text("\n".join(r["text"] for r in raw_rows) + "\n", encoding="utf-8")
    report = {"source_path": str(source), "model": args.model, "device": device, "language": result.get("language"),
              "segments": len(rows), "output_directory": str(out), "status": "unreviewed_asr",
              "note": "ASR is not an extraction of burned-in subtitles. Verify names, quotes, speakers and timing against source audio/frames."}
    write_json(out / "transcription_manifest.json", report)
    return report


def assemble_edits(plan: dict, default_source: Path | None, base: Path) -> tuple[list[dict], float]:
    edits = []
    cursor_samples = 0
    for index, raw in enumerate(plan.get("audio_edits", [])):
        row = dict(raw)
        kind = row.get("type", "source")
        if kind == "insert_silence":
            kind = "silence"
        if kind not in ("source", "silence"):
            raise ValueError(f"audio_edits[{index}] unknown type {kind!r}")
        if kind == "source":
            source = resolve(base, row["source_path"]) if row.get("source_path") else default_source
            if source is None or not source.is_file():
                raise FileNotFoundError(f"audio_edits[{index}] source file does not exist: {source}")
            start, end = float(row["source_start"]), float(row["source_end"])
            duration = end - start
            if start < 0:
                raise ValueError("source_start must be non-negative")
            row.update(source_path=str(source), source_start=start, source_end=end)
        else:
            duration = float(row.get("duration", float(row.get("output_end", 0)) - float(row.get("output_start", 0))))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"audio_edits[{index}] has non-positive duration")
        samples = round(duration * SAMPLE_RATE)
        start_out, end_out = cursor_samples / SAMPLE_RATE, (cursor_samples + samples) / SAMPLE_RATE
        if "output_start" in row and abs(float(row["output_start"]) - start_out) > 1/SAMPLE_RATE + 1e-7:
            raise ValueError(f"audio_edits[{index}] output_start is not contiguous; add an explicit silence edit instead of an implicit gap/overlap")
        if "output_end" in row and abs(float(row["output_end"]) - end_out) > 1/SAMPLE_RATE + 1e-7:
            raise ValueError(f"audio_edits[{index}] output_end disagrees with unmodified source duration")
        row.update(type=kind, output_start=start_out, output_end=end_out, duration=samples/SAMPLE_RATE,
                   sample_count=samples, id=row.get("id", f"clip_{index+1:03}"))
        edits.append(row)
        cursor_samples += samples
    if not edits:
        raise ValueError("Plan must contain a non-empty audio_edits list authored by the agent/user")
    duration = cursor_samples/SAMPLE_RATE
    declared = plan.get("total_duration", plan.get("duration"))
    if declared is not None and abs(float(declared)-duration) > 1/SAMPLE_RATE + 1e-7:
        raise ValueError(f"Plan duration {declared} disagrees with audio_edits total {duration}")
    return edits, duration


def mapped_captions(rows: list[dict], edits: list[dict], default_source: Path | None,
                    base: Path, already_output: bool, duration: float) -> list[dict]:
    mapped = []
    for index, original in enumerate(rows):
        row = dict(original)
        if already_output and "source_start" not in row:
            if not 0 <= float(row["start"]) < float(row["end"]) <= duration + 1e-5:
                raise ValueError(f"Output caption {index} is outside the assembled duration")
            row.setdefault("provenance", "agent_authored_output_caption")
            mapped.append(row)
            continue
        a, b = float(row.get("source_start", row.get("start"))), float(row.get("source_end", row.get("end")))
        if a < 0 or b <= a:
            raise ValueError(f"Caption {index} has invalid source interval")
        source = resolve(base, row["source_path"]) if row.get("source_path") else default_source
        for edit in edits:
            if edit["type"] != "source" or (row.get("clip_id") and row["clip_id"] != edit["id"]):
                continue
            if source and Path(edit["source_path"]).resolve() != source.resolve():
                continue
            left, right = max(a, edit["source_start"]), min(b, edit["source_end"])
            if right <= left:
                continue
            item = {**row, "start": edit["output_start"] + left-edit["source_start"],
                    "end": edit["output_start"] + right-edit["source_start"],
                    "source_start": left, "source_end": right, "source_path": edit["source_path"],
                    "source_caption_start": a, "source_caption_end": b, "clip_id": edit["id"],
                    "partial_source_caption": left > a or right < b, "provenance": "source_caption_time_mapping"}
            mapped.append(item)
    # Sorting only places captions on the preserved edit order; source clips are never reordered.
    return sorted(mapped, key=lambda r: (float(r["start"]), float(r["end"])))


def command_assemble(args: argparse.Namespace) -> dict:
    config, project_base, project_path = project_context(args)
    plan_path = Path(args.plan).resolve()
    plan = read_json(plan_path)
    base = project_base if project_path else plan_path.parent
    source_value = args.source or plan.get("source_path") or config.get("source_path")
    default_source = (Path(args.source).resolve() if args.source else resolve(base, source_value)) if source_value else None
    edits, duration = assemble_edits(plan, default_source, base)
    if args.out:
        out = Path(args.out).resolve()
        audio_path, timeline_path = out / "dialogue.wav", out / "timeline.json"
    elif project_path:
        audio_path = resolve(base, config.get("audio_path", "audio/dialogue.wav"))
        timeline_path = resolve(base, config.get("timeline_path", "analysis/timeline.json"))
    else:
        out = plan_path.parent / "assembled"
        audio_path, timeline_path = out / "dialogue.wav", out / "timeline.json"
    srt_path = timeline_path.parent / "subtitles.srt"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (audio_path, timeline_path, srt_path):
        if path.exists() and not args.overwrite:
            raise FileExistsError(f"Output exists; use --overwrite for an intentional rebuild: {path}")
    sources = {e["source_path"] for e in edits if e["type"] == "source"}
    source_metadata = {}
    for source in sources:
        info = metadata(Path(source))
        if not any(s["codec_type"] == "audio" for s in info["streams"]):
            raise ValueError(f"Selected source has no audio track: {source}")
        source_metadata[source] = info
    for edit in edits:
        if edit["type"] == "source":
            available = float(source_metadata[edit["source_path"]]["format"]["duration"])
            if edit["source_end"] > available + .001:
                raise ValueError(f"Edit {edit['id']} ends beyond source duration")
    if args.source_srt:
        rows = read_srt(Path(args.source_srt).resolve())
        already_output = False
    elif "source_captions" in plan:
        rows, already_output = plan["source_captions"], False
    else:
        rows, already_output = plan.get("captions", []), True
    captions = mapped_captions(rows, edits, default_source, base, already_output, duration)
    for event in plan.get("events", []):
        if not 0 <= float(event["start"]) < float(event["end"]) <= duration + 1e-5:
            raise ValueError("An event lies outside the assembled timeline")
    with tempfile.TemporaryDirectory(prefix="audio_assemble_", dir=audio_path.parent) as temp_dir:
        temp = Path(temp_dir)
        assembled = temp / "assembled.wav"
        with wave.open(str(assembled), "wb") as output:
            output.setnchannels(CHANNELS)
            output.setsampwidth(SAMPLE_WIDTH)
            output.setframerate(SAMPLE_RATE)
            for index, edit in enumerate(edits):
                samples = edit["sample_count"]
                if edit["type"] == "silence":
                    # Stream large silences without allocating a whole long timeline.
                    remaining = samples
                    zero = bytes(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
                    while remaining:
                        chunk = min(remaining, SAMPLE_RATE)
                        output.writeframesraw(zero[:chunk * CHANNELS * SAMPLE_WIDTH])
                        remaining -= chunk
                    continue
                segment = temp / f"clip_{index:04}.wav"
                run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", str(edit["source_start"]),
                     "-i", edit["source_path"], "-map", "0:a:0", "-vn", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                     "-af", f"aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,apad,atrim=end_sample={samples},asetpts=PTS-STARTPTS",
                     "-c:a", "pcm_s16le", str(segment)])
                with wave.open(str(segment), "rb") as part:
                    if part.getnframes() != samples:
                        raise RuntimeError(f"Segment sample count mismatch for {edit['id']}")
                    while True:
                        block = part.readframes(SAMPLE_RATE)
                        if not block:
                            break
                        output.writeframesraw(block)
        if args.loudness is not None:
            normalized = temp / "normalized.wav"
            run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(assembled),
                 "-af", f"loudnorm=I={args.loudness}:TP=-1.5:LRA=11,aresample={SAMPLE_RATE},apad,atrim=end_sample={round(duration*SAMPLE_RATE)}",
                 "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                 "-c:a", "pcm_s16le", str(normalized)])
            assembled = normalized
        shutil.copy2(assembled, audio_path)
    timeline = {**plan, "total_duration": duration, "audio_path": relative_or_absolute(audio_path, base),
                "source_path": relative_or_absolute(default_source, base) if default_source else None,
                "audio_edits": edits, "captions": captions,
                "assembly": {"plan_path": str(plan_path), "clip_order": "preserved_as_authored", "speed": 1.0,
                             "sample_rate": SAMPLE_RATE, "channels": CHANNELS, "loudness_target_lufs": args.loudness}}
    write_json(timeline_path, timeline)
    write_srt(srt_path, captions)
    report = {"audio_path": str(audio_path), "timeline_path": str(timeline_path), "subtitles_path": str(srt_path),
              "duration_seconds": duration, "sample_count": round(duration * SAMPLE_RATE),
              "clip_count": len(edits), "source_clip_count": sum(e["type"] == "source" for e in edits),
              "caption_count": len(captions), "partial_caption_count": sum(c.get("partial_source_caption", False) for c in captions),
              "note": "Clip order and playback speed preserved. Partial captions retain original text and require agent review; no dialogue or speaker identity is invented."}
    write_json(timeline_path.parent / "assemble_manifest.json", report)
    if project_path:
        config.update(audio_path=relative_or_absolute(audio_path, base), timeline_path=relative_or_absolute(timeline_path, base))
        write_json(project_path, config)
    return report


def command_validate(args: argparse.Namespace) -> dict:
    config, base, _ = project_context(args)
    source = Path(args.source).resolve() if args.source else resolve(base, config["output"])
    data = metadata(source)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    failures = []
    fmt = config.get("format", {})
    expected_duration = args.duration
    if expected_duration is None and config.get("timeline_path"):
        timeline = read_json(resolve(base, config["timeline_path"]))
        expected_duration = timeline.get("total_duration", timeline.get("duration"))
    for name, expected in [("width", args.width or fmt.get("width")), ("height", args.height or fmt.get("height"))]:
        if expected is not None and (not video or video.get(name) != expected):
            failures.append(f"{name}: expected {expected}, got {video.get(name) if video else None}")
    expected_fps = args.fps or fmt.get("fps")
    if expected_fps is not None and (not video or abs((fraction(video.get("avg_frame_rate")) or 0)-float(expected_fps)) > 1e-5):
        failures.append(f"FPS differs from expected {expected_fps}")
    if expected_duration is not None and abs(float(data["format"]["duration"])-float(expected_duration)) > args.tolerance:
        failures.append(f"Duration differs from expected {expected_duration}")
    expected_frames = args.frames
    if expected_frames is None and expected_duration is not None and expected_fps is not None:
        expected_frames = math.ceil(float(expected_duration)*float(expected_fps) - 1e-9)
    if expected_frames is not None and (not video or int(video.get("nb_frames", -1)) != expected_frames):
        failures.append(f"Frame count differs from expected {expected_frames}")
    if args.require_audio and not audio:
        failures.append("No audio stream")
    levels = None
    if audio and args.audio_levels:
        result = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(source), "-map", "0:a:0", "-vn",
                      "-af", "volumedetect", "-f", "null", "-"])
        levels = {}
        for key in ("mean_volume", "max_volume"):
            match = re.search(rf"{key}:\s*([-\w.]+) dB", result.stderr)
            if match:
                levels[key + "_dbfs"] = float(match.group(1))
        if levels.get("max_volume_dbfs", -1) >= 0:
            failures.append("Audio reaches 0 dBFS sample peak; inspect for clipping")
    if args.decode:
        run([tool("ffmpeg"), "-v", "error", "-i", str(source), "-f", "null", "-"])
    report = {"source_path": str(source), "status": "passed" if not failures else "failed",
              "metadata": data, "audio_levels": levels, "full_decode_performed": args.decode,
              "failures": failures, "note": "This is technical validation. Visual pose, caption and timing review remains required."}
    if args.out:
        write_json(Path(args.out).resolve(), report)
    if failures:
        raise ValueError("Validation failed: " + "; ".join(failures))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("download", help="Download one authorized URL and available selected subtitles")
    p.add_argument("--url"); p.add_argument("--project"); p.add_argument("--project-dir")
    p.add_argument("--max-height", type=int, default=1080)
    p.add_argument("--format", help="Optional yt-dlp format selector")
    p.add_argument("--sub-langs", default="zh.*,en.*")
    p.set_defaults(func=command_download)
    p = sub.add_parser("probe", help="Read metadata and, by default, all actual decoded frame timestamps")
    p.add_argument("--source", required=True); p.add_argument("--out"); p.add_argument("--csv")
    p.add_argument("--quick", action="store_true", help="Metadata only; does not verify actual frame cadence")
    p.set_defaults(func=command_probe)
    p = sub.add_parser("extract", help="Extract sparse actual frames by default; no contact-sheet dependency")
    p.add_argument("--source", required=True); p.add_argument("--out", required=True)
    p.add_argument("--start", type=float, default=0); p.add_argument("--duration", type=positive)
    p.add_argument("--fps", type=positive, default=1)
    p.add_argument("--all-frames", action="store_true", help="Explicitly request every frame instead of sparse samples")
    p.add_argument("--max-width", type=int); p.add_argument("--quality", type=int, choices=range(1, 32), default=4)
    p.set_defaults(func=command_extract)
    p = sub.add_parser("transcribe", help="Optional local Whisper ASR, preserving raw unreviewed output")
    p.add_argument("--source", required=True); p.add_argument("--out", required=True)
    p.add_argument("--model", default="turbo"); p.add_argument("--device", default="auto")
    p.add_argument("--cache-dir"); p.add_argument("--language"); p.add_argument("--initial-prompt")
    p.add_argument("--traditional", action="store_true", help="Additional OpenCC s2twp copy; raw ASR preserved")
    p.set_defaults(func=command_transcribe)
    p = sub.add_parser("assemble", help="Assemble original audio and map captions from an agent-authored edit plan")
    p.add_argument("--plan", required=True); p.add_argument("--project"); p.add_argument("--source")
    p.add_argument("--source-srt"); p.add_argument("--out", help="Override output directory")
    p.add_argument("--loudness", type=float, help="Optional single-pass LUFS normalization; omitted preserves source gain")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=command_assemble)
    p = sub.add_parser("validate", help="Validate final metadata/audio; full decode is explicit")
    p.add_argument("--source"); p.add_argument("--project"); p.add_argument("--out")
    p.add_argument("--duration", type=positive); p.add_argument("--fps", type=positive)
    p.add_argument("--width", type=int); p.add_argument("--height", type=int); p.add_argument("--frames", type=int)
    p.add_argument("--tolerance", type=positive, default=.05)
    p.add_argument("--require-audio", action="store_true"); p.add_argument("--audio-levels", action="store_true")
    p.add_argument("--decode", action="store_true", help="Decode the whole output once; skip if renderer already did this")
    p.set_defaults(func=command_validate)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "download" and not (args.project or args.project_dir):
        raise SystemExit("download requires --project or --project-dir")
    if args.command == "validate" and not (args.source or args.project):
        raise SystemExit("validate requires --source or --project")
    try:
        report = args.func(args)
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
