"""Check sampling boundaries and paired statistics for the installer benchmark."""
import ctypes
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tooling.benchmark_installer import UsageInfo, bootstrap_change, cpu_seconds, footprint_sample, summarize, tree_sample


class InstallerBenchmarkTests(unittest.TestCase):
    def test_tree_aggregation_excludes_unrelated_processes(self):
        rows = [(9, 1, 2048, 1.1), (10, 9, 4096, 2.2), (11, 10, 8192, 3.3),
                (99, 1, 999999, 100)]
        rss, usage = tree_sample(list(reversed(rows)), 9)
        self.assertEqual(rss, 14)
        self.assertEqual(usage, {9: 1.1, 10: 2.2, 11: 3.3})
        self.assertEqual(tree_sample(rows, 123), (0, {}))
        self.assertEqual(cpu_seconds('123:04.50'), 7384.5)
        self.assertEqual(cpu_seconds('1:02:03'), 3723)

    def test_footprint_uses_ledger_and_ignores_an_exited_process(self):
        class FakeLibproc:
            @staticmethod
            def proc_pid_rusage(pid, flavor, buffer):
                self.assertEqual(flavor, 2)
                if pid != 9:
                    return -1
                usage = ctypes.cast(buffer, ctypes.POINTER(UsageInfo)).contents
                usage.phys_footprint = 24 * 1048576
                return 0
        self.assertEqual(ctypes.sizeof(UsageInfo), 160)
        self.assertEqual(footprint_sample([9, 99], FakeLibproc()), (24, 1))

    def test_paired_statistics_and_warmup_exclusion(self):
        records = []
        for variant, values in [('before', [4, 6, 8]), ('after', [2, 3, 4])]:
            for i, value in enumerate([1000, *values]):
                records.append(dict(case='example', variant=variant, warmup=i==0,
                                    metrics={'wall_s':value}, parity={'cells':96}))
        summary = summarize(records)['example']
        metric = summary['metrics']['wall_s']
        self.assertEqual(metric['before']['median'], 6)
        self.assertEqual(metric['after']['median'], 3)
        self.assertEqual(metric['change_percent'], -50)
        self.assertEqual(metric['paired_bootstrap_95_percent'], [-50, -50])
        self.assertEqual(summary['runs_per_variant'], 3)
        records[-1]['parity'] = {'cells':95}
        with self.assertRaisesRegex(ValueError, 'Output parity'):
            summarize(records)

    def test_bootstrap_detects_no_consistent_direction_with_noisy_pairs(self):
        interval = bootstrap_change([1, 3, 2, 5, 4], [3, 1, 2, 4, 5])
        self.assertLess(interval[0], 0)
        self.assertGreater(interval[1], 0)
