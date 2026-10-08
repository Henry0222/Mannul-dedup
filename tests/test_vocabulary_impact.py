import unittest

from wos_filter.models import WosRecord
from wos_filter.project_workspace import record_key
from wos_filter.vocabulary_impact import vocabulary_impact


def record(*, author="Li, A", journal="Journal", keyword="water-waste"):
    return WosRecord(
        {"TI": ["A study"], "PY": ["2024"], "AB": ["A full abstract"], "AU": [author],
         "SO": [journal], "DE": [keyword]}, "", "sample.ris", 1,
        source_kind="scopus")


class VocabularyImpactTests(unittest.TestCase):
    def test_keyword_display_format_keeps_dedupe_and_ai(self):
        old, new = record(keyword="water-waste"), record(keyword="Water waste")
        runs = [{"preset": "strict"}]
        self.assertEqual(vocabulary_impact([old], [new], runs, {record_key(old)}),
                         (False, False))

    def test_keyword_synonym_stales_ai_but_keeps_dedupe(self):
        old, new = record(keyword="wastewater"), record(keyword="water pollution")
        self.assertEqual(vocabulary_impact([old], [new], [{"preset": "strict"}],
                                           {record_key(old)}), (False, True))

    def test_author_change_requires_new_dedupe_review(self):
        old, new = record(author="Li, A"), record(author="Lee, A")
        self.assertEqual(vocabulary_impact([old], [new], [{"preset": "strict"}], set()),
                         (True, False))

    def test_journal_change_does_not_affect_quick_mode(self):
        old, new = record(journal="Journal A"), record(journal="Journal B")
        self.assertEqual(vocabulary_impact([old], [new], [{"preset": "quick"}], set()),
                         (False, False))


if __name__ == "__main__":
    unittest.main()
