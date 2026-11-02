"""Timeout and failure lifecycle tests use a fake client and a fake clock only."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from run import Pipeline


class RunnerTests(unittest.TestCase):
    def test_timeout_cancels_only_its_job_and_stops(self):
        pipeline = Pipeline(Mock(), Path("unused"), 3)
        pipeline.api = Mock()
        with patch("run.time.monotonic", side_effect=[0, 4]), self.assertRaises(TimeoutError):
            pipeline.wait("videos", "owned-job")
        pipeline.api.assert_called_once_with("/api/videos/jobs/owned-job/cancel", {})

    def test_failed_job_never_downloads(self):
        pipeline = Pipeline(Mock(), Path("unused"), 3)
        pipeline.api = Mock(return_value={"job": {"status": "failed", "error": "GPU OOM"}})
        with self.assertRaisesRegex(RuntimeError, "GPU OOM"):
            pipeline.wait("images", "owned-job")
        pipeline.client.get.assert_not_called()

    def test_backend_restart_is_explicit_failure(self):
        pipeline = Pipeline(Mock(), Path("unused"), 3)
        pipeline.api = Mock(return_value={"jobs": []})
        with self.assertRaisesRegex(RuntimeError, "disappeared"):
            pipeline.wait("videos", "owned-job")


if __name__ == "__main__":
    unittest.main()
