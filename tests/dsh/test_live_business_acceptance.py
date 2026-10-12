"""离线核验办公包比较不会误判时间戳，也不会忽略实际表格变化。"""
import io
import unittest
import zipfile

from live_business_acceptance import package_changes, records


def package(entries):
    """构造有限内存包，不触发数据库或模型。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def presentation(value, timestamp, title="周期"):
    """模拟含图表工作簿的 PPT，时间与业务值可分别改变。"""
    core = f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:created>{timestamp}</dcterms:created><dcterms:modified>{timestamp}</dcterms:modified></cp:coreProperties>'
    workbook = package({"docProps/core.xml": core, "xl/worksheets/sheet1.xml": f"<v>{value}</v>"})
    return package({"ppt/embeddings/data.xlsx": workbook, "ppt/slides/slide3.xml": title})


class PackageComparisonTests(unittest.TestCase):
    def test_timestamp_change_is_reported_separately(self):
        changes, times = package_changes(presentation(10, "a"), presentation(10, "b", "新版周期"))
        self.assertEqual(changes, ["ppt/slides/slide3.xml"])
        self.assertEqual(times, ["ppt/embeddings/data.xlsx!/docProps/core.xml"])

    def test_chart_value_change_is_not_hidden(self):
        changes, _ = package_changes(presentation(10, "a"), presentation(11, "b", "新版周期"))
        self.assertIn("ppt/embeddings/data.xlsx!/xl/worksheets/sheet1.xml", changes)

    def test_empty_string_is_distinct_from_null(self):
        self.assertNotEqual(records([{"value": ""}], ["value"]), records([{"value": None}], ["value"]))


if __name__ == "__main__":
    unittest.main()
