"""Bounded local rendering. Stdin is an admitted private composition envelope.

No account, network, arbitrary command, source-directory discovery or publication.
The content-ops caller provides immutable source snapshots and an exclusive attempt
directory. This renderer implements the protocol; it does not admit that effect.
"""
from __future__ import annotations

import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import wave
import array
import math

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps
from fontTools.ttLib import TTFont

PROTOCOL = "content_ops_media_renderer_v0"
INK, MUTED, BLUE, LINE = "#182031", "#657087", "#2768ea", "#dce3f0"
Image.MAX_IMAGE_PIXELS = 16_000_000


class RenderCancelled(Exception):
    pass


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, check=True, timeout=timeout)


def font_file(content: str = "LoopX") -> str | None:
    required = {ord(char) for char in content if not char.isspace()}
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "C:/Windows/Fonts/arial.ttf"):
        if Path(name).is_file():
            with TTFont(name, fontNumber=0, lazy=True) as font:
                if required <= set(font.getBestCmap() or {}): return name
    return None


def stamp(seconds: float, *, srt: bool = False) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{',' if srt else '.'}{milliseconds:03}"


def captions(plan: dict, out: Path) -> None:
    vtt, srt, transcript = ["WEBVTT\n"], [], [plan["title"]]
    for i, cue in enumerate(plan["captions"], 1):
        if re.search(r"\n[\t ]*\n", cue["text"]):
            raise ValueError("Caption blank lines cannot be serialized as one cue")
        if re.search(r"[<>]|&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);", cue["text"]):
            raise ValueError("Caption markup has no unambiguous plain SubRip representation")
        payload = escape(cue["text"], quote=False)
        vtt.append(f"{i}\n{stamp(cue['start'])} --> {stamp(cue['end'])}\n{payload}\n")
        srt.append(f"{i}\n{stamp(cue['start'], srt=True)} --> {stamp(cue['end'], srt=True)}\n{cue['text']}\n")
        transcript.append(f"{cue['start']:g}–{cue['end']:g}s · {cue['text']}")
    for filename, lines in (("captions.vtt", vtt), ("captions.srt", srt), ("transcript.txt", transcript)):
        (out / filename).write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def soundtrack(plan: dict, sources: dict[str, Path], out: Path, ff: str, check) -> dict:
    mode = plan["narration"]["mode"]
    duration = sum(shot["seconds"] for shot in plan["shots"])
    destination = out / "narration.wav"
    if mode == "silent":
        return {"mode": mode, "subjective_listening": "not_tested"}
    if mode == "supplied":
        run([ff, "-v", "error", "-n", "-i", str(sources[plan["narration"]["asset_id"]]),
             "-vn", "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(destination)])
        with wave.open(str(destination)) as audio:
            seconds = audio.getnframes() / audio.getframerate()
        if abs(seconds - duration) > 0.1:
            raise ValueError("Audio duration differs from the composition; revise timing explicitly")
        return {"mode": mode, "seconds": seconds, "subjective_listening": "not_tested"}
    if sys.platform != "darwin" or not Path("/usr/bin/say").is_file():
        raise ValueError("System speech unavailable; supply a timed audio asset or choose explicit silence")
    timeline = bytearray(round(duration * 44100) * 2)
    rows = []
    for i, cue in enumerate(plan["captions"]):
        check()
        text, aiff, wav = (out / f"voice-{i}.{suffix}" for suffix in ("txt", "aiff", "wav"))
        text.write_text(cue["text"], encoding="utf-8")
        run(["/usr/bin/say", "-v", plan["narration"]["voice"], "-r", str(plan["narration"]["words_per_minute"]), "-f", str(text), "-o", str(aiff)])
        run([ff, "-v", "error", "-n", "-i", str(aiff), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
        with wave.open(str(wav)) as audio:
            samples, rate = audio.readframes(audio.getnframes()), audio.getframerate()
        actual = len(samples) / 2 / rate
        if actual > cue["end"] - cue["start"] - 0.15:
            raise ValueError("Narration exceeds its cue; extend the brief instead of silently speeding speech")
        start = round((cue["start"] + 0.075) * 44100) * 2
        timeline[start:start + len(samples)] = samples
        rows.append({"cue": i, "speech_seconds": actual})
        for path in (text, aiff, wav):
            path.unlink()
    with wave.open(str(destination), "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(44100); audio.writeframes(timeline)
    return {"mode": mode, "cues": rows, "subjective_listening": "not_tested"}


def render(request: dict) -> dict:
    if request.get("protocol") != PROTOCOL:
        raise ValueError("Unsupported renderer protocol")
    plan, out = request["plan"], Path(request["attempt_directory"])
    if any(shot["kind"] in {"excerpt", "blender"} and shot.get("body") for shot in plan["shots"]):
        raise ValueError("The spatial layout does not support body text; revise the brief")
    if any(shot.get("framing", "editorial") not in {"editorial", "full_frame"}
           or ("framing" in shot and shot["kind"] not in {"excerpt", "blender"}) for shot in plan["shots"]):
        raise ValueError("Unsupported shot framing")
    out.mkdir(mode=0o700, exist_ok=False)
    started = time.monotonic()
    cancel = Path(request["cancel_file"])

    def check():
        if cancel.exists():
            raise RenderCancelled()
        if time.monotonic() - started > 105:
            raise TimeoutError("Composition exceeded the bounded render duration")

    sources = {}
    for asset in plan["assets"]:
        path = Path(request["sources"][asset["id"]])
        if path.is_symlink() or not path.is_file() or digest(path) != asset["sha256"]:
            raise ValueError("Source bytes changed; prepare a new revision")
        sources[asset["id"]] = path
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    captions(plan, out)
    audio = soundtrack(plan, sources, out, ff, check)
    spatial_frames, spatial_qa = {}, []
    if any(shot["kind"] == "blender" for shot in plan["shots"]):
        from .blender import project_archive, render_shot
        for shot in plan["shots"]:
            if shot["kind"] == "blender":
                frames, qa = render_shot(shot, plan, out, check)
                spatial_frames[shot["id"]] = frames
                spatial_qa.append(qa)
        project_archive(plan, out)
    check()
    width, height, fps = plan["width"], plan["height"], plan["fps"]
    scale = width / 1280
    style = request["layout_style"]
    ink, muted, accent = style["text"], style["muted"], style["accent"]
    visible_text = "LOOPX / CONTENT STUDIO " + " ".join(
        shot["title"] + " " + shot["body"] + " " + shot["boundary"] + " " + " ".join(shot.get("nodes", []))
        for shot in plan["shots"])
    font_path = font_file(visible_text)
    if not font_path: raise ValueError("No installed font covers this brief; install a suitable font before retrying")
    fonts = {}

    def font(size):
        pixels = max(12, round(size * scale))
        if pixels not in fonts:
            fonts[pixels] = ImageFont.truetype(font_path, pixels) if font_path else ImageFont.load_default(size=pixels)
        return fonts[pixels]

    def text_lines(draw, text, size, limit, max_lines):
        selected = font(size)
        lines, current = [], ""
        # Preserve English words and allow CJK line breaks without inventing
        # spaces or silently drawing missing-glyph boxes.
        previous_cjk = False
        for word in re.findall(r"[\u2e80-\u9fff\uf900-\ufaff]|[^\s\u2e80-\u9fff\uf900-\ufaff]+", text):
            cjk = bool(re.fullmatch(r"[\u2e80-\u9fff\uf900-\ufaff]", word))
            candidate = current + (" " if current and not cjk and not previous_cjk else "") + word
            if draw.textlength(candidate, font=selected) > limit and current:
                lines.append(current); current = word
            else:
                current = candidate
            previous_cjk = cjk
        if current: lines.append(current)
        if len(lines) > max_lines or any(draw.textlength(line, font=selected) > limit for line in lines):
            raise ValueError("Text exceeds the layout; revise or split the shot")
        return lines

    def draw_text(draw, position, text, size, color=INK, max_width=None, max_lines=4):
        selected = font(size)
        lines = text_lines(draw, text, size, max_width or width - position[0] * 2, max_lines)
        x, y = position
        for line in lines:
            draw.text((x, y), line, fill=color, font=selected)
            y += round(size * scale * 1.35)
        return y

    video = out / "video.mp4"
    encoder = subprocess.Popen([ff, "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "silent.mp4")], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frames = 0
    try:
        for index, shot in enumerate(plan["shots"]):
            count = round(shot["seconds"] * fps)
            full_frame = shot.get("framing") == "full_frame"
            media_height = height
            if full_frame:
                # Keep every source edge visible, including a source's own text.
                # Attribution has a measured strip, never an occluding overlay.
                inset = max(8, round(20 * scale))
                boundary_font = font(22)
                boundary_lines = text_lines(ImageDraw.Draw(Image.new("RGB", (1, 1))),
                                            shot["boundary"], 22, width - 2 * inset, 3)
                ascent, descent = boundary_font.getmetrics()
                line_height = ascent + descent + max(2, round(4 * scale))
                strip_height = len(boundary_lines) * line_height + 2 * inset
                media_height = height - strip_height
                if media_height < height / 2:
                    raise ValueError("Source boundary leaves insufficient picture space; shorten or split the shot")
            decoder = None
            source_image = None
            if shot["kind"] == "excerpt":
                asset = next(a for a in plan["assets"] if a["id"] == shot["asset_id"])
                if asset["kind"] == "image":
                    source_image = ImageOps.exif_transpose(Image.open(sources[asset["id"]])).convert("RGB")
                else:
                    # Set the frame clock before scaling; a trailing fps filter
                    # after swscale can lose the final source frame at EOF.
                    resize = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
                    if full_frame:
                        # Raw RGB carries no sample-aspect metadata. Bake the
                        # display aspect into pixel dimensions before dropping it.
                        resize = f"scale=w='min({width},{media_height}*dar)':h='min({media_height},{width}/dar)',setsar=1"
                    decoder = subprocess.Popen([ff, "-v", "error", "-ss", str(shot["start_seconds"]), "-i", str(sources[asset["id"]]), "-t", str(shot["seconds"]), "-vf", f"fps={fps},{resize},pad={width}:{media_height}:(ow-iw)/2:(oh-ih)/2:color={style['background']}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                for n in range(count):
                    check()
                    im = Image.new("RGB", (width, height), style["background"])
                    draw = ImageDraw.Draw(im)
                    margin = round(56 * scale)
                    if not full_frame:
                        draw_text(draw, (margin, round(27 * scale)), "LOOPX / CONTENT STUDIO", 16, accent)
                        draw.line((margin, round(65 * scale), width - margin, round(65 * scale)), fill=LINE)
                    if shot["kind"] in {"excerpt", "blender"}:
                        frame = source_image
                        if shot["kind"] == "blender":
                            with Image.open(spatial_frames[shot["id"]] / f"frame-{n+1:04}.png") as image:
                                frame = image.convert("RGBA")
                        if decoder:
                            raw = decoder.stdout.read(width * media_height * 3)
                            if len(raw) != width * media_height * 3:
                                raise ValueError("Excerpt is shorter than the declared shot; no frozen-frame substitution")
                            frame = Image.frombytes("RGB", (width, media_height), raw)
                        if full_frame:
                            fitted = ImageOps.contain(frame, (width, media_height))
                            im.paste(fitted, ((width - fitted.width) // 2, (media_height - fitted.height) // 2), fitted.getchannel("A") if fitted.mode == "RGBA" else None)
                        else:
                            fitted = frame.copy()
                            fitted.thumbnail((width - 2 * margin, round(height * 0.62)))
                            im.paste(fitted, ((width - fitted.width) // 2, round(height * 0.27)), fitted.getchannel("A") if fitted.mode == "RGBA" else None)
                            draw_text(draw, (margin, round(88 * scale)), shot["title"], 32, ink, max_lines=2)
                    else:
                        enter = 1 - (1 - min(1, n / max(1, fps * 0.6))) ** 3
                        y = round((125 + 16 * (1 - enter)) * scale)
                        bottom = draw_text(draw, (margin, y), shot["title"], 48, ink, max_lines=3)
                        if shot["body"]:
                            bottom = draw_text(draw, (margin, bottom + round(24 * scale)), shot["body"], 25, muted, max_lines=3)
                        if bottom > height * (.57 if shot["kind"] == "diagram" else .85):
                            raise ValueError("Text overlaps the scene; revise or split the shot")
                        if shot["kind"] == "diagram":
                            nodes = shot["nodes"]
                            gap = round(22 * scale)
                            box_w = (width - 2 * margin - gap * (len(nodes) - 1)) // len(nodes)
                            y, h = round(height * .60), round(height * .19)
                            for j, label in enumerate(nodes):
                                x = margin + j * (box_w + gap)
                                progress = max(0, min(1, (n / fps - j * .3) / .65))
                                active = progress > 0
                                draw.rounded_rectangle((x, y, x + box_w, y + h), radius=round(10 * scale), fill=style["background"], outline=accent if active else LINE, width=2)
                                node_end = draw_text(draw, (x + round(13 * scale), y + round(20 * scale)), label, 22, ink if active else muted, max_width=box_w - round(26 * scale), max_lines=3)
                                if node_end > y + h - 8: raise ValueError("Node label overflows; shorten or split the diagram")
                                if j:
                                    a, b = x - gap + 4, x - 4
                                    draw.line((a, y + h / 2, a + (b - a) * progress, y + h / 2), fill=accent, width=2)
                                    if progress == 1:
                                        draw.polygon(((b, y+h/2), (b-6, y+h/2-4), (b-6, y+h/2+4)), fill=accent)
                    if full_frame:
                        for row, line in enumerate(boundary_lines):
                            draw.text((inset, media_height + inset + row * line_height), line, fill=ink, font=boundary_font)
                    else:
                        draw_text(draw, (margin, height - round(56 * scale)), shot["boundary"], 16, muted, max_lines=2)
                        bar = margin + (width - 2 * margin) * (frames / request["frame_count"])
                        draw.line((margin, height-8, bar, height-8), fill=accent, width=3)
                    if frames == 0: im.save(out / "poster.png")
                    encoder.stdin.write(im.tobytes()); frames += 1
            finally:
                if decoder:
                    decoder.stdout.close()
                    decoder.terminate()
                    try: decoder.wait(timeout=3)
                    except subprocess.TimeoutExpired: decoder.kill(); decoder.wait()
        encoder.stdin.close()
        if encoder.wait(timeout=15) != 0: raise ValueError("Video encoder failed")
    finally:
        if encoder.poll() is None:
            encoder.kill(); encoder.wait()
    check()
    if audio["mode"] == "silent":
        (out / "silent.mp4").rename(video)
    else:
        run([ff, "-v", "error", "-n", "-i", str(out / "silent.mp4"), "-i", str(out / "narration.wav"), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(video)])
        (out / "silent.mp4").unlink()
    decoded = run([ff, "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(video), "-f", "null", "-"])
    counts = re.findall(rb"^frame=(\d+)$", decoded.stdout, re.M)
    if not counts or int(counts[-1]) != request["frame_count"] or frames != request["frame_count"]:
        raise ValueError("Full decode frame count differs from the plan")
    if audio["mode"] != "silent":
        with wave.open(str(out / "narration.wav")) as wav:
            samples = array.array("h", wav.readframes(wav.getnframes()))
        if sys.byteorder != "little": samples.byteswap()
        peak = max((abs(sample) for sample in samples), default=0)
        audio.update(peak_dbfs=round(20 * math.log10(peak / 32768), 2) if peak else None,
                     clipped_samples=sum(abs(sample) >= 32767 for sample in samples))
    files = {path.name: {"sha256": digest(path), "bytes": path.stat().st_size} for path in out.iterdir() if path.is_file()}
    result = {"ok": True, "protocol": PROTOCOL, "plan_digest": request["plan_digest"], "files": files,
              "qa": {"full_decode": "passed", "frames": frames, "width": width, "height": height, "fps": fps,
                     "source_hashes_verified": True, "narration": audio, **({"blender_scenes": spatial_qa} if spatial_qa else {}),
                     **({"full_frame_shots": [shot["id"] for shot in plan["shots"] if shot.get("framing") == "full_frame"]} if any(shot.get("framing") == "full_frame" for shot in plan["shots"]) else {}),
                     "audience_understanding": "not_tested", "creative_review": "not_tested"},
              "cost": {"render_wall_seconds": round(time.monotonic() - started, 3)}, "publish_authorized": False}
    (out / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    os.umask(0o077)
    try:
        if sys.argv[1:] == ["--doctor"]:
            from .blender import available
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            run([ff, "-version"], timeout=5)
            if not font_file(): raise ValueError("Install a supported local font")
            print(json.dumps({"ok": True, "protocol": PROTOCOL, "system_speech": sys.platform == "darwin", "font": "system", "blender_extra_present": available()}))
            return 0
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024: raise ValueError("Render request exceeds the protocol limit")
        result = render(json.loads(raw))
    except RenderCancelled:
        result = {"ok": False, "error_code": "cancelled"}
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.SubprocessError):
        # Private text, source paths and subprocess stderr must not escape.
        result = {"ok": False, "error_code": "render_failed"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
