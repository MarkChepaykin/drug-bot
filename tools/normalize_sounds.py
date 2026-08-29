"""Приводит sounds/*.mp3 к одной громкости — той же, что у синтеза речи (-20 LUFS).

Саундборд качается с мемных сайтов, где каждый файл смастерен как попало: разброс был
от -70 до +3.8 LUFS, то есть в 70 дБ. Играются звуки тем же путём, что и речь
(ears.play, speechVolume), поэтому громкость должна совпадать с голосом.

Запускать после добавления новых звуков: python tools/normalize_sounds.py
Скрипт идемпотентен — уже нормализованные файлы пропускает.

Нужен ffmpeg в PATH или в FFMPEG (переменная окружения).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TARGET_LUFS = -20.0        # уровень edge-tts (измерено: -19.5 LUFS у ru-RU-DmitryNeural)
TARGET_RMS = -20.5         # для клипов короче окна EBU R128 — по mean_volume
TOLERANCE = 0.7            # ближе этого к цели не трогаем
PEAK_CEILING = -1.0        # запас до клиппинга

if hasattr(sys.stdout, "reconfigure"):  # windows-консоль по умолчанию cp1251
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOUNDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds")
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def _run(args: list[str]) -> str:
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostats", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stderr


def measure(path: str) -> tuple[float, float, bool]:
    """(уровень, пик в dBFS, по_LUFS_ли). Для клипов короче ~0.4с EBU R128 врёт -70 —
    для них считаем по RMS."""
    out = _run(["-i", path, "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    m = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", out)
    lufs = float(m.group(1)) if m else -99.0

    out = _run(["-i", path, "-af", "volumedetect", "-f", "null", "-"])
    mean = float(re.search(r"mean_volume:\s*(-?[\d.]+)", out).group(1))
    peak = float(re.search(r"max_volume:\s*(-?[\d.]+)", out).group(1))

    if lufs > -50.0:
        return lufs, peak, True
    return mean, peak, False


def normalize(path: str) -> str:
    level, peak, by_lufs = measure(path)
    target = TARGET_LUFS if by_lufs else TARGET_RMS
    gain = target - level
    if abs(gain) < TOLERANCE:
        return f"ok    {level:+7.1f} → уже ровно"

    # Тихие, но пиковые мемы (короткий громкий транзиент) при подъёме до цели ушли бы
    # в клиппинг — их ловит alimiter, а не срезанное усиление.
    chain = f"volume={gain:.2f}dB"
    if peak + gain > PEAK_CEILING:
        chain += f",alimiter=limit={10 ** (PEAK_CEILING / 20):.4f}"

    tmp = os.path.join(tempfile.gettempdir(), "norm_" + os.path.basename(path))
    err = _run(["-y", "-i", path, "-af", chain, "-c:a", "libmp3lame", "-q:a", "2", tmp])
    if not os.path.isfile(tmp) or os.path.getsize(tmp) < 512:
        return f"FAIL  {err.strip().splitlines()[-1] if err.strip() else 'ffmpeg молчит'}"
    shutil.move(tmp, path)
    now, _, _ = measure(path)
    return f"gain  {level:+7.1f} → {now:+7.1f} ({gain:+.1f} dB{', лимитер' if 'alimiter' in chain else ''})"


def main() -> int:
    manifest = json.load(open(os.path.join(SOUNDS, "manifest.json"), encoding="utf-8"))
    files = sorted({meta.get("file", f"{tag}.mp3") for tag, meta in manifest["sounds"].items()})
    width = max(len(f) for f in files)
    for name in files:
        path = os.path.join(SOUNDS, name)
        if not os.path.isfile(path):
            print(f"{name:<{width}}  НЕТ ФАЙЛА")
            continue
        print(f"{name:<{width}}  {normalize(path)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
