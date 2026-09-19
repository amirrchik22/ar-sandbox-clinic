"""Формат .hmz: запись и чтение без потерь."""
import numpy as np

from sandbox.recorder.heightmap_writer import HeightmapWriter, read_heightmaps


def test_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 200, size=(24, 32)).astype(np.float32) for _ in range(5)]
    w = HeightmapWriter(tmp_path / "s.hmz", 32, 24, fps=10, session_id="S-test")
    for i, f in enumerate(frames):
        w.write(i / 10, f)
    w.close()
    out = list(read_heightmaps(tmp_path / "s.hmz"))
    assert len(out) == 5
    for (t, got), want in zip(out, frames):
        assert np.array_equal(got, np.rint(want).astype(np.int16))
