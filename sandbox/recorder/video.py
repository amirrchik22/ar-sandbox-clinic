"""Команды ffmpeg для записи видео сегментами по 60 секунд."""
from __future__ import annotations

from pathlib import Path


def ffmpeg_segments_cmd(width: int, height: int, fps: int, out_dir: str | Path,
                        prefix: str, hw: str = "none", bitrate: str = "4M") -> list[str]:
    """ffmpeg читает сырые кадры BGR из stdin и пишет сегменты prefix_000.mp4, prefix_001.mp4...

    hw: none | vaapi (Intel/AMD на Linux) | nvenc (NVIDIA). Сегменты по 60 с:
    при отключении питания теряется не больше минуты. Склейка после сеанса —
    concat demuxer без перекодирования.
    """
    out = str(Path(out_dir) / f"{prefix}_%03d.mp4")
    base = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-"]
    if hw == "vaapi":
        enc = ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload",
               "-c:v", "h264_vaapi", "-b:v", bitrate]
    elif hw == "nvenc":
        enc = ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", bitrate]
    else:
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    seg = ["-f", "segment", "-segment_time", "60", "-reset_timestamps", "1", "-movflags", "+faststart"]
    return base + enc + seg + [out]


def concat_cmd(list_file: str | Path, out_path: str | Path) -> list[str]:
    """Склейка сегментов без перекодирования; list_file — строки вида file 'seg_000.mp4'."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(out_path)]
