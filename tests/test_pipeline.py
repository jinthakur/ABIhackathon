"""Tests for the data ingestion pipeline."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch, MagicMock
from src.llm_agent import extract_wound_data, determine_eligibility, is_wound_icd10


SAMPLE_NOTE_ENVIVE = {
    "note_text": "*Envive Care Conference Review - V 4.0\n"
                 "Wound Status: Pressure Ulcer to Right hip / Measures 2.9 cm x 2.8 cm / Stage: Stage 3\n"
                 "Drainage present - serosanguineous, heavy. Treatment: Foam dressing change daily."
}

SAMPLE_NOTE_SOAP = {
    "note_text": "S: Patient reports increased pain at wound site.\n"
                 "O: Wound assessment - Diabetic foot ulcer, left heel\n"
                 "   Length 4.2 cm Width 3.1 cm Depth 1.5 cm\n"
                 "   Drainage: moderate\n"
                 "A: DFU with adequate granulation tissue.\n"
                 "P: Continue current dressing protocol."
}

SAMPLE_NOTE_PROSE = {
    "note_text": "Wound eval: Pressure ulcer sacrum, Stage 2, Meas 1.2x1.5x0.2cm, light drainage"
}


class TestWoundExtraction(unittest.TestCase):

    def test_envive_format(self):
        wound = extract_wound_data([SAMPLE_NOTE_ENVIVE], [])
        self.assertIsNotNone(wound)
        self.assertEqual(wound["wound_type"], "pressure ulcer")
        self.assertEqual(wound["wound_stage"], "Stage 3")
        self.assertAlmostEqual(wound["length_cm"], 2.9)
        self.assertAlmostEqual(wound["width_cm"], 2.8)
        self.assertEqual(wound["drainage_amount"], "heavy")
        self.assertIn("Hip", wound["location"])

    def test_soap_format(self):
        wound = extract_wound_data([SAMPLE_NOTE_SOAP], [])
        self.assertIsNotNone(wound)
        self.assertAlmostEqual(wound["length_cm"], 4.2)
        self.assertAlmostEqual(wound["width_cm"], 3.1)
        self.assertAlmostEqual(wound["depth_cm"], 1.5)
        self.assertEqual(wound["drainage_amount"], "moderate")

    def test_prose_format(self):
        wound = extract_wound_data([SAMPLE_NOTE_PROSE], [])
        self.assertIsNotNone(wound)
        self.assertEqual(wound["wound_type"], "pressure ulcer")
        self.assertEqual(wound["wound_stage"], "Stage 2")
        self.assertAlmostEqual(wound["length_cm"], 1.2)

    def test_no_wound_data_returns_none(self):
        note = {"note_text": "Patient doing well. No complaints."}
        wound = extract_wound_data([note], [])
        self.assertIsNone(wound)

    def test_confidence_scoring(self):
        wound = extract_wound_data([SAMPLE_NOTE_ENVIVE], [])
        self.assertIn(wound["confidence"], ("high", "medium", "low"))


class TestICD10Classification(unittest.TestCase):

    def test_pressure_ulcer_code(self):
        self.assertTrue(is_wound_icd10("L89.143"))

    def test_diabetic_foot_ulcer_code(self):
        self.assertTrue(is_wound_icd10("E11.621"))

    def test_non_wound_code(self):
        self.assertFalse(is_wound_icd10("J18.9"))  # Pneumonia
        self.assertFalse(is_wound_icd10("I10"))    # Hypertension

    def test_empty_code(self):
        self.assertFalse(is_wound_icd10(""))
        self.assertFalse(is_wound_icd10(None))


class TestEligibilityRouting(unittest.TestCase):

    def _make_clinical(self, payer_code="MCB", with_wound=True):
        wound = {
            "wound_type": "pressure ulcer", "wound_stage": "Stage 3",
            "location": "Sacrum", "length_cm": 3.0, "width_cm": 2.5,
            "depth_cm": 1.0, "drainage_amount": "moderate",
        } if with_wound else None
        return {
            "patient": {"patient_id": "TEST-001", "primary_payer_code": payer_code,
                        "first_name": "Test", "last_name": "Patient"},
            "patient_id": "TEST-001",
            "facility_id": 101,
            "diagnoses": [{"icd10_code": "L89.143"}],
            "coverage": [{"payer_code": payer_code,
                          "payer_name": "Medicare Part B" if payer_code == "MCB" else "HMO Plan",
                          "effective_to": None}],
            "notes": [], "assessments": [],
        }, wound

    def test_auto_accept_with_all_fields(self):
        clinical, wound = self._make_clinical("MCB", True)
        result = determine_eligibility(clinical, wound)
        self.assertEqual(result["routing_decision"], "auto_accept")
        self.assertTrue(result["has_medicare_part_b"])

    def test_reject_no_medicare_b(self):
        clinical, wound = self._make_clinical("HMO", True)
        result = determine_eligibility(clinical, wound)
        self.assertEqual(result["routing_decision"], "reject")
        self.assertFalse(result["has_medicare_part_b"])

    def test_flag_for_review_no_wound(self):
        clinical, _ = self._make_clinical("MCB", False)
        result = determine_eligibility(clinical, None)
        # Has wound diagnosis (L89.143) but no wound notes → flag
        self.assertIn(result["routing_decision"], ("reject", "flag_for_review"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
