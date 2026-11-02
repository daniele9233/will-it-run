"""Synthetic regression fixtures, never a GPU, model server or owner data."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from diagnose import analyze, command, compare, foreground


class DiagnosticsTests(unittest.TestCase):
    def test_endpoint_mismatch_and_neutral_background(self):
        a = np.full((128, 128, 3), 255, dtype=np.uint8)
        b = a.copy()
        b[40:88, 40:88] = 0
        self.assertEqual(compare(a, a)["mse"], 0)
        self.assertTrue(compare(a, a)["exact_pixels"])
        self.assertLess(compare(a, b)["ssim"], 0.95)
        self.assertIsNone(foreground(a))
        self.assertFalse(foreground(b)["clipped"])

    def test_full_hd_motion_and_exact_export_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "motion.mp4"
            command(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                     "testsrc2=size=1920x1080:rate=30:duration=8", "-c:v", "libx264",
                     "-preset", "ultrafast", "-crf", "18", str(video)])
            capture = cv2.VideoCapture(str(video))
            ok, first = capture.read()
            self.assertTrue(ok)
            capture.release()
            cv2.imwrite(str(root / "first.png"), first)
            report = analyze(video, root / "out", scenario="scroll", first=root / "first.png", export_count=12)
            self.assertEqual(report["decoded_frames"], 240)
            self.assertEqual(report["endpoints"]["first"]["ssim"], 1)
            self.assertEqual(report["browser_frames"]["count"], 12)
            self.assertFalse(report["failures"])
            video.write_bytes(b"broken mp4")
            with self.assertRaises(subprocess.CalledProcessError):
                analyze(video, root / "broken", scenario="scroll")

    def test_black_freeze_and_insufficient_geometry_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "black.mp4"
            command(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                     "color=black:size=256x144:rate=30:duration=8", "-c:v", "libx264", str(video)])
            report = analyze(video, root / "out", scenario="geometry", export_count=12)
            self.assertEqual(len(report["black_frames"]), 240)
            self.assertEqual(len(report["duplicate_frames_at_640x360"]), 239)
            self.assertIn("geometry_track_survival", report["failures"])
            self.assertEqual(report["status"], "failed")


if __name__ == "__main__":
    unittest.main()
