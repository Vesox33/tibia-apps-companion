"""Generuje ikonę (icon.ico, icon.png) i dźwięki (clip.wav, clip_dup.wav) do huntuploader/data."""
import math
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "huntuploader" / "data"


def icon(size=1024):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # tło: zaokrąglony kwadrat z pionowym gradientem
    grad = Image.new("RGBA", (s, s))
    top, bot = (38, 140, 255), (10, 78, 190)
    for y in range(s):
        t = y / (s - 1)
        grad.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bot)) + (255,), (0, y, s, y + 1))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((int(s * .04),) * 2 + (int(s * .96),) * 2, radius=int(s * .22), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx, cy = s / 2, s / 2
    r = s * .27
    w = int(s * .05)
    # celownik: okrąg + cztery kreski na zewnątrz
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=white, width=w)
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        d.line((cx + dx * (r - w * .5), cy + dy * (r - w * .5), cx + dx * (r + s * .13), cy + dy * (r + s * .13)),
               fill=white, width=w)
    # strzałka w górę w środku celownika
    d.line((cx, cy + s * .13, cx, cy - s * .04), fill=white, width=int(w * 1.1))
    head = s * .11
    tip = cy - s * .16
    d.polygon([(cx, tip), (cx - head, tip + head * 1.05), (cx + head, tip + head * 1.05)], fill=white)
    return img


def tone(path, freqs, dur=0.07, vol=0.35, rate=44100):
    frames = []
    for f in freqs:
        n = int(rate * dur)
        for i in range(n):
            t = i / rate
            env = min(1, i / (rate * .004)) * math.exp(-t * 38)   # szybki atak, krótki zanik
            frames.append(int(32767 * vol * env * math.sin(2 * math.pi * f * t)))
        frames += [0] * int(rate * .012)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(frames)}h", *frames))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    big = icon()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    tone(OUT / "clip.wav", [880, 1320])      # krótkie „blip-blip” w górę
    tone(OUT / "clip_dup.wav", [660, 440])   # w dół – tekst był już wysłany
    print("OK", OUT)
