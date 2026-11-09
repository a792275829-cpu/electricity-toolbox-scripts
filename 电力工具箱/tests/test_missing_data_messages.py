import unittest

from toolbox.tasks.errors import ProcessExecutionError, classify_error
from toolbox_shared.log_console import explain_error


class MissingDataMessageTests(unittest.TestCase):
    def test_modules_keep_concrete_missing_data_diagnosis(self):
        for message in (
            "2026-11-01 清远 00:00 电量缺失或无效，停止导出。",
            "日前电价 为空。",
            "2026-11-01 负荷缺少 loadType=1",
            "市场行情看板缺少开机台数",
            "出清电量应有完整24个小时数据",
        ):
            with self.subTest(message=message):
                result = classify_error(ProcessExecutionError(1), "失败：" + message)
                self.assertEqual(result.summary, message)
                self.assertIn("补齐", result.advice)

    def test_unrelated_failures_and_success_are_not_missing_data(self):
        self.assertIsNone(explain_error("数据读取成功"))
        self.assertIsNone(explain_error("账号和密码均不能为空"))
        self.assertIsNone(explain_error("模板行缺少单元格 E7"))

    def test_unavailable_future_realtime_is_informational(self):
        line = "[22:52:55] 实时日均价: 2026-11-10=无数据 2026-11-09=无数据 差值=无数据 厘/kWh"
        self.assertIsNone(explain_error(line))
        result = classify_error(ProcessExecutionError(1), line + "\n[22:53:35] 失败：读取容量信息 2026-11-10 失败：T100\nsubprocess.CalledProcessError: returned non-zero exit status 1")
        self.assertEqual(result.summary, "读取容量信息 2026-11-10 失败：T100")
        self.assertIn("容量信息接口", result.advice)

