"""Записать N кадров датчика в .npz для проигрывания через FakeSensor(replay=...).

python tools/record_fake.py --sensor kinect2 --seconds 20 --out data/replays/session.npz
"""
import argparse
import time

import numpy as np

from sandbox.sensors import make_sensor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensor", default="fake")
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    s = make_sensor(a.sensor)
    s.start()
    frames, t0 = [], time.monotonic()
    while time.monotonic() - t0 < a.seconds:
        frames.append(s.depth_frame().depth_mm)
        time.sleep(1 / 30)
    s.stop()
    np.savez_compressed(a.out, frames=np.stack(frames))
    print(f"{len(frames)} кадров → {a.out}")


if __name__ == "__main__":
    main()
