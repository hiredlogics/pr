"""PoFA Schedule 4 invitation content scan — global, not operator-specific."""
from __future__ import annotations

import unittest

from pcn_appeal.legal.pofa import scan_ntk_invitations


UKPPO_STYLE_FACE = """
Parking Charge Notice
DO NOT IGNORE THIS NOTICE
UK Parking Patrol Office
If you were not the driver, please supply the full name and current serviceable
postal address of the driver so that liability of the Parking Charge may be
transferred to the driver.
After 28 days, under Schedule 4 of the Protection of Freedoms Act 2012, we have
the right to recover the unpaid amount from the keeper of the vehicle.
"""


class NtkInvitationScan(unittest.TestCase):
    def test_name_invite_without_pass_on_is_defect(self):
        scan = scan_ntk_invitations(UKPPO_STYLE_FACE)
        self.assertTrue(scan.has_name_driver_invitation)
        self.assertFalse(scan.has_pass_to_driver_invitation)
        self.assertTrue(scan.defect_statutory_invitation)

    def test_pass_on_invitation_clears_defect(self):
        text = UKPPO_STYLE_FACE + (
            " Please pass this notice on to the driver if you were not driving."
        )
        scan = scan_ntk_invitations(text)
        self.assertTrue(scan.has_pass_to_driver_invitation)
        self.assertFalse(scan.defect_statutory_invitation)

    def test_insufficient_text_does_not_invent_defect(self):
        scan = scan_ntk_invitations("PCN 1")
        self.assertIsNone(scan.has_pass_to_driver_invitation)
        self.assertFalse(scan.defect_statutory_invitation)


if __name__ == "__main__":
    unittest.main()
