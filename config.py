import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("API_BASE_URL", "https://hackathon.prod.pulsefoundry.ai")
FACILITIES = [101, 102, 103]
MAX_RETRIES = 20
RETRY_DELAY = 10  # seconds between retries on 429
MAX_WORKERS = 15  # concurrent patient processors
TEST_MODE = False  # set True to process only first 5 patients per facility

WOUND_ICD10_PREFIXES = [
    "L89",   # pressure ulcer
    "L97",   # non-pressure chronic ulcer
    "L98.4", # chronic ulcer NOS
    "E10.621", "E10.622", "E11.621", "E11.622",  # diabetic foot ulcer
    "I83",   # varicose veins with ulcer
    "I87.2", # venous insufficiency
    "T31", "T32",  # burns by extent
    "L02",   # abscess
    "T81",   # surgical site complication
    "M86",   # osteomyelitis (often accompanies wound)
]
