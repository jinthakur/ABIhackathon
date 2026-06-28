"""
Golden dataset tests — ensures Claude extraction output schema hasn't drifted.
Tests that regex extraction produces consistent, correct output on known notes.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from src.llm_agent import extract_wound_data

GOLDEN_DATASET = [
    {
        "description": "Envive format — Stage 3 pressure ulcer, hip, heavy drainage",
        "note_text": "*Envive Care Conference Review - V 4.0\nWound Status: Pressure Ulcer to Right hip / Measures 2.9 cm x 2.8 cm / Stage: Stage 3\nDrainage present - serosanguineous, heavy.",
        "expected": {
            "wound_type": "pressure ulcer",
            "wound_stage": "Stage 3",
            "length_cm": 2.9,
            "width_cm": 2.8,
            "drainage_amount": "heavy",
        }
    },
    {
        "description": "Prose format — abbreviated measurements",
        "note_text": "DFU left heel, Meas 4.2x3.1x1.5cm, moderate drainage, Stage: not applicable",
        "expected": {
            "wound_type": "diabetic foot ulcer",
            "length_cm": 4.2,
            "width_cm": 3.1,
            "depth_cm": 1.5,
            "drainage_amount": "moderate",
        }
    },
    {
        "description": "SOAP format — explicit L/W/D fields",
        "note_text": "O: Wound: Pressure ulcer sacrum Stage 2\nLength: 2.1 cm Width: 1.8 cm Depth: 0.5 cm\nDrainage: light",
        "expected": {
            "wound_type": "pressure ulcer",
            "wound_stage": "Stage 2",
            "length_cm": 2.1,
            "width_cm": 1.8,
            "depth_cm": 0.5,
            "drainage_amount": "light",
        }
    },
    {
        "description": "Burn wound — arm",
        "note_text": "Burn wound to arm, measures 2.8 cm x 1.8 cm, moderate drainage, no stage applicable",
        "expected": {
            "wound_type": "burn",
            "length_cm": 2.8,
            "width_cm": 1.8,
            "drainage_amount": "moderate",
        }
    },
]


class TestGoldenDataset(unittest.TestCase):
    pass


def _make_test(case):
    def test_case(self):
        note = {"note_text": case["note_text"]}
        wound = extract_wound_data([note], [])
        self.assertIsNotNone(wound, f"Expected wound extraction but got None for: {case['description']}")
        for key, expected in case["expected"].items():
            actual = wound.get(key)
            if isinstance(expected, float):
                self.assertAlmostEqual(actual, expected, places=1,
                    msg=f"{key}: expected {expected}, got {actual} | {case['description']}")
            else:
                self.assertEqual(actual, expected,
                    msg=f"{key}: expected {expected!r}, got {actual!r} | {case['description']}")
    return test_case


for i, case in enumerate(GOLDEN_DATASET):
    test_name = f"test_golden_{i+1:02d}_{case['description'][:30].replace(' ','_').replace('—','')}"
    setattr(TestGoldenDataset, test_name, _make_test(case))


if __name__ == "__main__":
    unittest.main(verbosity=2)
