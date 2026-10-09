import unittest

from wos_filter.models import WosRecord
from wos_filter.vos_record_filter import (ALL_TITLES, CHINESE_TITLES,
                                          ENGLISH_TITLES, filter_vos_records)


def record(source, title, keyword):
    return WosRecord({"TI": [title], "DE": [keyword], "PY": ["2024"]},
                     "", f"{source}.txt", 1, source_kind=source)


class VOSRecordFilterTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            record("wos", "English paper", "health"),
            record("scopus", "Another paper", "care"),
            record("cnki", "中文文献", "machine learning"),
            record("cnki", "English title from CNKI", "口腔医学"),
        ]

    def test_database_selection_is_independent_of_title_and_keyword_language(self):
        selected = filter_vos_records(self.records, {"cnki"})
        self.assertEqual(selected, self.records[2:])
        self.assertEqual(selected[0].text("DE"), "machine learning")
        self.assertEqual(filter_vos_records(self.records, {"wos", "cnki"}, ALL_TITLES),
                         [self.records[0], *self.records[2:]])

    def test_optional_title_filter(self):
        self.assertEqual(filter_vos_records(self.records, {"cnki"}, CHINESE_TITLES),
                         [self.records[2]])
        self.assertEqual(filter_vos_records(self.records, {"cnki"}, ENGLISH_TITLES),
                         [self.records[3]])

    def test_empty_selection_reports_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "勾选一个数据库"):
            filter_vos_records(self.records, set())
        with self.assertRaisesRegex(ValueError, "没有题录"):
            filter_vos_records(self.records, {"pubmed"})


if __name__ == "__main__":
    unittest.main()
