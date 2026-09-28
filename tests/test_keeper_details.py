"""Keeper details: on the notice, checked by the customer, letterhead only."""
import unittest
from fastapi.testclient import TestClient
from pcn_appeal import prompts
from pcn_appeal.api import CUSTOMER_FIELDS, app


class KeeperDetails(unittest.TestCase):
    def test_extraction_asks_for_the_addressee(self):
        body = prompts.system("extraction")
        self.assertIn("keeper_name", body)
        self.assertIn("keeper_address", body)

    def test_the_prompt_forbids_treating_the_addressee_as_the_driver(self):
        self.assertIn("never treat the addressee as", prompts.system("extraction").lower())

    def test_the_customer_checks_them(self):
        names = [n for n, _ in CUSTOMER_FIELDS]
        self.assertIn("keeper_name", names)
        self.assertIn("keeper_address", names)

    def test_they_are_withheld_from_the_drafter(self):
        """A generated sentence must not be able to contain the customer's name
        or home address; the letterhead is the only place they belong."""
        import inspect
        from pcn_appeal.engines import reasoning
        src = inspect.getsource(reasoning)
        self.assertIn("withheld_from_drafter", src)
        self.assertIn("keeper_name", src.split("withheld_from_drafter")[1][:200])

    def test_pdf_says_so_when_they_are_missing(self):
        from pcn_appeal.pdf import _compiled
        html = _compiled.render(
            brand_name="X", brand_colour="#000", brand_wordmark="X", brand_footer="f",
            brand_logo=None, case_id="C-1", today="1 Jan 2026", subject="Re: x",
            paragraphs=["body"], facts={}, evidence_list=[], grounds=[],
            keeper_name=None, keeper_address=None, keeper_address_lines=[])
        self.assertIn("Add your name and address", html)

    def test_pdf_uses_them_when_present(self):
        from pcn_appeal.pdf import _compiled
        html = _compiled.render(
            brand_name="X", brand_colour="#000", brand_wordmark="X", brand_footer="f",
            brand_logo=None, case_id="C-1", today="1 Jan 2026", subject="Re: x",
            paragraphs=["body"], facts={}, evidence_list=[], grounds=[],
            keeper_name="MR A KEEPER", keeper_address="FLAT 1\nSLOUGH",
            keeper_address_lines=["FLAT 1", "SLOUGH"])
        self.assertIn("MR A KEEPER", html)
        self.assertIn("SLOUGH", html)
        self.assertNotIn("Add your name and address", html)

    def test_logo_is_absent_unless_configured(self):
        from pcn_appeal.pdf import _logo_data_uri
        self.assertIsNone(_logo_data_uri())


if __name__ == "__main__":
    unittest.main()
