-- Wound Care Billing Pipeline Schema

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY,
    patient_id VARCHAR(20) UNIQUE NOT NULL,
    facility_id INTEGER NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    date_of_birth DATE,
    raw_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(20) NOT NULL,
    icd10_code VARCHAR(20),
    description TEXT,
    is_wound_related BOOLEAN DEFAULT FALSE,
    raw_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS coverage (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(20) NOT NULL,
    payer_name VARCHAR(200),
    plan_type VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    is_medicare_part_b BOOLEAN DEFAULT FALSE,
    raw_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS clinical_notes (
    id SERIAL PRIMARY KEY,
    patient_int_id INTEGER,
    patient_id VARCHAR(20),
    note_text TEXT,
    note_date DATE,
    note_type VARCHAR(50),
    raw_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assessments (
    id SERIAL PRIMARY KEY,
    patient_int_id INTEGER,
    patient_id VARCHAR(20),
    assessment_date DATE,
    raw_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wound_extractions (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(20) NOT NULL,
    wound_type VARCHAR(100),
    wound_stage VARCHAR(50),
    location VARCHAR(200),
    length_cm DECIMAL(6,2),
    width_cm DECIMAL(6,2),
    depth_cm DECIMAL(6,2),
    drainage_amount VARCHAR(50),
    source_type VARCHAR(20),
    confidence VARCHAR(20),
    raw_text TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eligibility_results (
    id SERIAL PRIMARY KEY,
    patient_id VARCHAR(20) UNIQUE NOT NULL,
    facility_id INTEGER,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    has_medicare_part_b BOOLEAN DEFAULT FALSE,
    has_wound_diagnosis BOOLEAN DEFAULT FALSE,
    wound_type VARCHAR(100),
    wound_stage VARCHAR(50),
    location VARCHAR(200),
    length_cm DECIMAL(6,2),
    width_cm DECIMAL(6,2),
    depth_cm DECIMAL(6,2),
    drainage_amount VARCHAR(50),
    routing_decision VARCHAR(20),
    reason TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eligibility_routing ON eligibility_results(routing_decision);
CREATE INDEX IF NOT EXISTS idx_eligibility_facility ON eligibility_results(facility_id);
CREATE INDEX IF NOT EXISTS idx_diagnoses_patient ON diagnoses(patient_id);
CREATE INDEX IF NOT EXISTS idx_coverage_patient ON coverage(patient_id);
CREATE INDEX IF NOT EXISTS idx_notes_patient ON clinical_notes(patient_id);
CREATE INDEX IF NOT EXISTS idx_assessments_patient ON assessments(patient_id);
