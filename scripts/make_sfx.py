"""Create original, dialogue-friendly chip audio for an authored battle timeline.

All tones/noise are synthesized here. No game recording or game melody is used.
Event impact_at is a time offset relative to event.start, matching the renderer.
Requires NumPy. Use --project project.json, or --timeline timeline.json --out
sfx.wav [--player-id ID]. Project paths resolve relative to the project JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path

import numpy as np

SR = 48000
RNG = np.random.default_rng(486117)


def envelope(n: int, attack: float = .004, release: float = .025) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    a = min(n // 3, round(attack * SR))
    r = min(n // 2, round(release * SR))
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r)
    return env


def chip(freq: float, seconds: float, volume: float = .03, end: float | None = None,
         shape: str = "pulse", duty: float = .25) -> np.ndarray:
    n = max(1, round(seconds * SR))
    # Frequency changes are stepped at 120 Hz to retain an intentional chip feel.
    steps = max(2, math.ceil(seconds * 120))
    frequencies = np.geomspace(max(freq, 15), max(end or freq, 15), steps)
    hz = frequencies[np.minimum(np.arange(n) * steps // n, steps - 1)]
    phase = np.cumsum(hz) / SR
    signal = np.zeros(n, dtype=np.float64)
    for harmonic in range(1, 14):
        if max(freq, end or freq) * harmonic >= SR / 2:
            break
        if shape == "triangle":
            if harmonic % 2:
                signal += ((-1) ** ((harmonic - 1) // 2) / harmonic**2) * np.sin(2 * np.pi * harmonic * phase)
        else:
            signal += (2 * np.sin(np.pi * harmonic * duty) / (np.pi * harmonic)) * np.cos(2 * np.pi * harmonic * phase)
    signal /= max(1, float(np.max(np.abs(signal))))
    return (signal * envelope(n) * volume).astype(np.float32)


def noise(seconds: float, volume: float = .025, rate: float = 4500,
          decay: float = 4.0) -> np.ndarray:
    n = max(1, round(seconds * SR))
    hold = max(1, round(SR / rate))
    signal = np.repeat(RNG.uniform(-1, 1, math.ceil(n / hold)), hold)[:n]
    signal *= np.exp(-np.arange(n) / max(1, n) * decay)
    return (signal * envelope(n) * volume).astype(np.float32)


def effect_parts(effect: str, action: str) -> list[tuple[float, np.ndarray, str]]:
    """Offsets are relative to the exact visual impact; negative = preparation."""
    parts: list[tuple[float, np.ndarray, str]] = []
    add = lambda offset, data, label: parts.append((offset, data, label))
    if action == "drink":
        # Two little glugs align to a cup-at-mouth pose; a quiet recovery follows.
        add(-.23, chip(440, .12, .023, 210, "triangle"), "cup_lift")
        for i in range(2):
            add(i * .14, chip(290, .105, .032, 520, "triangle"), "gulp")
        for i, hz in enumerate([523.25, 698.46, 880.0]):
            add(.32 + i * .085, chip(hz, .11, .026, shape="triangle"), "recovery")
    elif effect == "heal":
        for i, hz in enumerate([440, 587.33, 739.99, 1174.66]):
            add(i * .08, chip(hz, .13, .028, shape="triangle"), "heal_sparkle")
    elif effect == "water":
        add(-.28, noise(.28, .022, 2800, .5), "water_cast")
        add(0, noise(.19, .046, 5800, 3), "water_hit")
        for i, hz in enumerate([740, 560, 860]):
            add(.055 + i * .09, chip(hz, .105, .027, hz * .56, "triangle"), "droplet")
    elif effect == "electric":
        add(-.18, chip(200, .17, .025, 1150, duty=.125), "electric_charge")
        for i in range(6):
            offset = i * .19
            add(offset, noise(.066, .041, 9000, 1.8), "electric_arc")
            add(offset, chip(1080 - 100 * i, .065, .024, 220, duty=.125), "dark_pulse")
    elif effect == "moon":
        add(-.34, chip(220, .34, .023, 660, "triangle"), "moon_rise")
        for i, hz in enumerate([659.25, 987.77, 830.61]):
            add(i * .14, chip(hz, .22, .029, shape="triangle"), "moon_glint")
        add(.30, chip(174.61, .3, .022, 116.54), "moon_status")
    elif effect == "confusion":
        for i, hz in enumerate([330, 349, 262, 277, 330, 262]):
            add(i * .085, chip(hz, .10, .026, hz * 1.07, "triangle"), "confusion_orbit")
    elif effect in ("leaf", "leaves"):
        for i in range(3):
            add(-.30 + i * .10, noise(.11, .022, 3300 + i * 1300, 2), "leaf_flight")
            add(i * .10, chip(960 - i * 150, .09, .029, 240, duty=.125), "leaf_hit")
            add(i * .10, noise(.065, .025, 7200, 5), "leaf_cut")
    elif effect == "ball":
        add(-.30, chip(220, .29, .021, 630, "triangle"), "ball_throw")
        for offset, hz, gain in [(0, 185, .046), (.17, 260, .031), (.32, 350, .020)]:
            add(offset, chip(hz, .10, gain, hz * .5, "triangle"), "ball_bounce")
            add(offset, noise(.038, gain * .65, 6200, 5), "ball_contact")
    elif effect == "fire":
        add(-.23, chip(100, .22, .023, 420), "fire_charge")
        for i in range(4):
            add(i * .12, noise(.18, .038 - i * .003, 7000 - 500 * i, 2.0), "fire_burst")
        add(0, chip(150, .30, .032, 50, "triangle"), "fire_impact")
    elif effect == "counter":
        add(-.13, chip(320, .13, .022, 820, duty=.125), "counter_ready")
        add(0, chip(1174.66, .10, .036, duty=.125), "counter_ping")
        add(.105, chip(1567.98, .12, .029, duty=.125), "counter_reply")
        add(0, noise(.06, .025, 7000, 5), "counter_hit")
    elif action == "hit":
        add(0, noise(.10, .040, 5000, 3), "hit")
        add(0, chip(180, .14, .030, 60, "triangle"), "hit_low")
    elif action in ("react", "defeat"):
        notes = [349.23, 293.66, 220] if action == "react" else [293.66, 220, 164.81, 110]
        for i, hz in enumerate(notes):
            add(i * .10, chip(hz, .12, .023, shape="triangle"), action)
    return parts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Shared project.json; relative JSON paths resolve beside it")
    parser.add_argument("--timeline", help="Explicit timeline JSON, relative to cwd")
    parser.add_argument("--out", help="Explicit WAV destination, relative to cwd")
    parser.add_argument("--player-id", help="Optional player ID for stereo position when no project is supplied")
    parser.add_argument("--overwrite", action="store_true", help="Intentionally rebuild an existing WAV")
    parser.add_argument("--no-music", action="store_true")
    parser.add_argument("--attack-gain-db", type=float, default=7.0,
                        help="Boost attack/impact effects without raising music or drinking cues.")
    args = parser.parse_args()
    if not args.project and not args.timeline:
        parser.error("Provide --project or --timeline")
    project_path = Path(args.project).resolve() if args.project else None
    config = json.loads(project_path.read_text(encoding="utf-8-sig")) if project_path else {}
    base = project_path.parent if project_path else Path(args.timeline).resolve().parent
    def resolve(value):
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (base / path).resolve()
    timeline_path = Path(args.timeline).resolve() if args.timeline else resolve(config["timeline_path"])
    out = Path(args.out).resolve() if args.out else resolve(config.get("sfx_path", "sfx.wav"))
    if out.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists; use --overwrite for an intentional rebuild: {out}")
    player_id = args.player_id or config.get("player_id")
    data = json.loads(timeline_path.read_text(encoding="utf-8-sig"))
    events = data["events"]
    if not events:
        raise ValueError("Timeline requires authored events")
    duration = float(data.get("total_duration", data.get("duration", max(e["end"] for e in events))))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Timeline duration must be finite and positive")
    audio = np.zeros((round(duration * SR), 2), dtype=np.float32)
    speech = np.zeros(len(audio), dtype=np.float32)
    for caption in data.get("captions", []):
        start = max(0, round((float(caption["start"]) - .06) * SR))
        end = min(len(audio), round((float(caption["end"]) + .06) * SR))
        speech[start:end] = 1
    log: list[dict] = []

    def add(at: float, signal: np.ndarray, pan: float = 0, bed: bool = False) -> None:
        offset = round(at * SR)
        if offset < 0:
            signal = signal[-offset:]
            offset = 0
        count = min(len(signal), len(audio) - offset)
        if count <= 0:
            return
        # Keep effects at the renderer's exact impact, duck gain rather than timing.
        duck = 1 - speech[offset:offset + count] * (.68 if bed else .27)
        angle = (np.clip(pan, -.8, .8) + 1) * np.pi / 4
        sample = signal[:count] * duck
        audio[offset:offset + count, 0] += sample * np.cos(angle)
        audio[offset:offset + count, 1] += sample * np.sin(angle)

    # Sparse original cue pattern; deliberately much quieter than dialogue/effects.
    if not args.no_music:
        chapters = list(dict.fromkeys(str(event.get("chapter", "")) for event in events))
        root_choices = [146.8324, 164.8138, 130.8128, 174.6141, 196.0, 123.4708]
        roots = {name: root_choices[i % len(root_choices)] for i, name in enumerate(chapters)}
        for event_index, event in enumerate(events):
            start, end = float(event["start"]), float(event["end"])
            chapter = str(event.get("chapter", ""))
            root = roots.get(chapter, 146.8324)
            # Each phrase has its own skipped beats; no repeated recognizable tune.
            intervals = [1, 1.5, 1.25, 2, 1.125, 1.5, 1.0, 1.875]
            for beat, at in enumerate(np.arange(start + .18, end - .22, .375)):
                if (beat + event_index) % 4 == 2:
                    continue
                hz = root * intervals[(beat * 3 + event_index) % len(intervals)]
                add(float(at), chip(hz, .09, .0045, shape="triangle"), -.15 if beat % 2 else .15, True)
                if beat % 4 == 0:
                    add(float(at), chip(root / 2, .12, .005, shape="triangle"), 0, True)

    for index, event in enumerate(events):
        start, end = float(event["start"]), float(event["end"])
        if not 0 <= start < end <= duration + 1e-6:
            raise ValueError(f"event {index}: interval outside timeline duration")
        authored_action = str(event.get("action", "idle")).lower()
        action = {"throw": "attack", "cast": "attack", "counter": "attack", "scratch": "react",
                  "confusion": "react", "exasperated": "react", "heal": "idle"}.get(authored_action, authored_action)
        effect = str(event.get("effect", "none")).lower()
        if effect == "none":
            effect = {"heal": "heal", "counter": "counter", "scratch": "confusion", "confusion": "confusion"}.get(authored_action, effect)
        if effect not in ("none", "heal", "water", "electric", "moon", "confusion", "leaf", "leaves", "ball", "fire", "counter"):
            raise ValueError(f"event {index}: unsupported authored effect {effect!r}")
        impact_offset = float(event.get("impact_at", min(1.25, (end - start) * .45)))
        impact = start + impact_offset
        parts = effect_parts(effect, action)
        if parts and not (start <= impact < end):
            raise ValueError(f"event {index}: relative impact_at falls outside event")
        target = event.get("target", event.get("opponent"))
        pan = 0 if player_id is None or target is None else (-.22 if target == player_id else .22)
        actor = event.get("actor", event.get("speaker"))
        add(start + .025, chip(780 if player_id is not None and actor == player_id else 920, .045, .018, duty=.125), 0)
        for relative, signal, label in parts:
            at = impact + relative
            attack_gain = 10 ** (args.attack_gain_db / 20) if action in ("attack", "hit") else 1.0
            add(at, signal * attack_gain, pan)
            log.append({"event_index": index, "chapter": event.get("chapter"), "effect": effect,
                        "action": action, "authored_action": authored_action, "label": label, "at": round(at, 6),
                        "duration": len(signal) / SR, "visual_impact": impact,
                        "attack_gain_db": args.attack_gain_db if action in ("attack", "hit") else 0})
        log.append({"event_index": index, "chapter": event.get("chapter"), "effect": effect,
                    "action": action, "authored_action": authored_action, "label": "event_summary", "start": start, "end": end,
                    "impact_offset": impact_offset, "visual_impact": impact, "parts": len(parts)})

    fade = min(round(.5 * SR), len(audio))
    audio[-fade:] *= np.linspace(1, 0, fade)[:, None]
    audio[:min(round(.03 * SR), len(audio))] *= np.linspace(0, 1, min(round(.03 * SR), len(audio)))[:, None]
    peak = float(np.max(np.abs(audio)))
    # Conservative ceiling for narration-first mixing; normally no gain scaling needed.
    gain = min(1.0, .115 / max(peak, 1e-9))
    audio *= gain
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes(pcm.tobytes())
    metadata = {
        "path": str(out.resolve()), "timeline_path": str(timeline_path), "player_id": player_id,
        "duration": duration, "sample_rate": SR, "channels": 2,
        "sample_count": len(audio), "peak": float(np.max(np.abs(audio))),
        "peak_dbfs": float(20 * np.log10(max(np.max(np.abs(audio)), 1e-9))),
        "rms_dbfs": float(20 * np.log10(max(np.sqrt(np.mean(audio**2)), 1e-9))),
        "event_count": len(events), "sound_parts": sum(e["label"] != "event_summary" for e in log),
        "music": not args.no_music,
        "attack_gain_db": args.attack_gain_db,
        "provenance": "Original procedural pulse/triangle/noise synthesis; no game audio or copied melody.",
        "timing": "impact_at is relative to event.start; audio ducked beneath caption intervals.",
        "suggested_mix": "Use at unity gain beside a normalized dialogue track. Do not apply loudness normalization to this SFX track alone.",
        "events": log,
    }
    (out.parent / "sfx_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if project_path:
        try:
            config["sfx_path"] = out.relative_to(base).as_posix()
        except ValueError:
            config["sfx_path"] = str(out)
        project_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in metadata.items() if k != "events"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
