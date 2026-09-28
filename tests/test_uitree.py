"""uiautomator 控件树独立核验的单元测试（mock adb，无需设备）。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.adb import uitree  # noqa: E402

# 仿微信聊天页：底部"发送"可点击按钮 + 中间一条普通消息 + 空文本节点
SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" content-desc="" clickable="true" bounds="[0,0][1080,2400]"/>
  <node index="1" text="AI交流群" content-desc="" clickable="false" bounds="[0,80][1080,180]"/>
  <node index="2" text="今晚八点开会" content-desc="" clickable="false" bounds="[40,200][700,320]"/>
  <node index="3" text="" content-desc="消息内容区域" clickable="false" bounds="[0,180][1080,2100]"/>
  <node index="4" text="发送" content-desc="" clickable="true" bounds="[900,2200][1080,2320]"/>
  <node index="5" text="" content-desc="" clickable="false" bounds="[902,2202][1078,2318]"/>
</hierarchy>
"""


# "icon + 兄弟文字"式按钮：点击落在 icon 上，标签"发送"是同级 text 节点
SIBLING_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" content-desc="" clickable="false" bounds="[0,0][1080,2400]"/>
  <node index="1" text="" content-desc="" clickable="true" bounds="[900,2200][1080,2320]">
    <node index="2" text="" content-desc="发送图标" clickable="false" bounds="[905,2205][950,2315]"/>
    <node index="3" text="发送" content-desc="" clickable="false" bounds="[955,2205][1075,2315]"/>
  </node>
</hierarchy>
"""

# 整页可点击的大容器：子树里远处的"发送"字样不应算进其他位置的点击目标
BIG_CONTAINER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" content-desc="" clickable="true" bounds="[0,0][1080,2400]">
    <node index="1" text="帮我发送文件" content-desc="" clickable="false" bounds="[40,100][800,220]"/>
    <node index="2" text="普通卡片" content-desc="" clickable="false" bounds="[40,1500][800,1620]"/>
  </node>
</hierarchy>
"""


def completed(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["adb"], rc, stdout=stdout, stderr="")


class TextsAtTest(unittest.TestCase):
    def test_send_button_text_found(self):
        # 点在"发送"按钮中心：内层空节点 + 可点击祖先 text="发送"
        self.assertEqual(uitree.texts_at(SAMPLE_XML, 990, 2260), ["发送"])

    def test_innermost_text_and_clickable_ancestors(self):
        # 点在普通消息上：内层节点文本 + 页面根（clickable，无文本）
        texts = uitree.texts_at(SAMPLE_XML, 300, 250)
        self.assertIn("今晚八点开会", texts)

    def test_point_outside_any_node(self):
        self.assertEqual(uitree.texts_at(SAMPLE_XML, 5000, 5000), [])

    def test_html_entities_unescaped(self):
        xml = '<node text="a&amp;b" content-desc="" clickable="false" bounds="[0,0][10,10]"/>'
        self.assertIn("a&b", uitree.texts_at(xml, 5, 5))

    def test_leading_garbage_tolerated(self):
        # 部分设备 dump 输出前有 INFO 行
        self.assertIn("发送", uitree.texts_at("INFO: dumped\n" + SAMPLE_XML, 990, 2260))

    def test_invalid_bounds_skipped(self):
        xml = '<node text="x" clickable="true" bounds="[10,10][10,10]"/>' \
              '<node text="y" clickable="true" bounds="[0,0][100,100]"/>'
        texts = uitree.texts_at(xml, 50, 50)
        self.assertEqual(texts, ["y"])

    def test_sibling_text_inside_button_found(self):
        # 点击落在 icon 上（无文字），按钮标签"发送"是兄弟节点：
        # 按钮级可点击祖先的子树文本必须被收集（树解析的核心价值）
        texts = uitree.texts_at(SIBLING_XML, 927, 2260)
        self.assertIn("发送", texts)
        self.assertIn("发送图标", texts)  # content-desc 也算

    def test_page_sized_container_does_not_pull_all_texts(self):
        # 整页可点击的大容器：远处的"发送"字样不算进"普通卡片"处的点击目标
        texts = uitree.texts_at(BIG_CONTAINER_XML, 400, 1560)
        self.assertEqual(texts, ["普通卡片"])

    def test_malformed_xml_falls_back_to_regex(self):
        # 截断的 XML 建不了树 → 回退正则几何包含，仍能给出结果
        truncated = SAMPLE_XML[:SAMPLE_XML.rfind("</hierarchy>")]
        self.assertIn("发送", uitree.texts_at(truncated, 990, 2260))


class DumpTest(unittest.TestCase):
    def test_dump_success_from_stdout(self):
        with mock.patch.object(uitree, "run_adb", return_value=completed(SAMPLE_XML)):
            self.assertEqual(uitree.ui_texts_at_point(990, 2260), ["发送"])

    def test_dump_failure_returns_none(self):
        for fake in (completed("ERROR: could not get idle state", rc=1), completed("")):
            with mock.patch.object(uitree, "run_adb", return_value=fake):
                self.assertIsNone(uitree.ui_texts_at_point(1, 1))

    def test_dump_timeout_returns_none(self):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="adb", timeout=10)

        with mock.patch.object(uitree, "run_adb", side_effect=raise_timeout):
            self.assertIsNone(uitree.ui_texts_at_point(1, 1))

    def test_dump_on_stderr_fallback(self):
        fake = subprocess.CompletedProcess(["adb"], 0, stdout="", stderr=SAMPLE_XML)
        with mock.patch.object(uitree, "run_adb", return_value=fake):
            self.assertIn("发送", uitree.ui_texts_at_point(990, 2260))


if __name__ == "__main__":
    unittest.main()
