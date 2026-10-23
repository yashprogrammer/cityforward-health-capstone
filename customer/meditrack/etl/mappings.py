"""Code mappings the legacy loader needs: SNOMED CT -> ICD-10-CM, lab reference ranges, units, staff."""

# SNOMED CT disorder -> ICD-10-CM. MediTrack stores ICD only; Synthea speaks SNOMED. Conditions we cannot map are
# skipped (and counted) rather than invented. Curated for the conditions that appear in the demo cohort.
SNOMED_TO_ICD10CM = {
    "66383009": "K05.10", "271737000": "D64.9", "414545008": "I25.9", "59621000": "I10", "237602007": "E88.810",
    "431855005": "N18.1", "127013003": "E11.22", "431856006": "N18.2", "90781000119102": "E11.29",
    "157141000119108": "E11.29", "37320007": "K08.109", "433144002": "N18.30", "44054006": "E11.9", "302870006": "E78.1",
    "444814009": "J01.90", "55822004": "E78.5", "39898005": "G47.9", "22298006": "I21.9", "109570002": "K02.9",
    "40055000": "J32.9", "401303003": "I21.3", "73430006": "G47.30", "401314000": "I21.4", "80394007": "R73.9",
    "88805009": "I50.9", "239873007": "M17.9", "197927001": "N39.0", "26929004": "G30.9", "431857002": "N18.4",
    "10509002": "J20.9", "125605004": "T14.8XXA", "195662009": "J02.9", "18718003": "K06.9", "840539006": "U07.1",
    "233604007": "J18.9", "389087006": "R09.02", "65710008": "J96.00", "770349000": "A41.89", "126906006": "D49.59",
    "92691004": "D07.5", "254637007": "C34.90", "424132000": "C34.90", "87433001": "J43.9", "368581000119106": "E11.40",
    "263102004": "S62.90XA", "74400008": "K37", "78275009": "G47.33", "64859006": "M81.0", "201834006": "M19.049",
    "70704007": "S63.509A", "97331000119101": "E11.311", "60573004": "I35.0", "275408006": "N99.0", "196416002": "K01.1",
    "44465007": "S93.409A", "68496003": "K63.5", "713197008": "K62.1", "16114001": "S82.899A", "427898007": "K04.7",
    "61804006": "M27.3", "110030002": "S06.0X0A", "62106007": "S06.0X0A", "27942005": "R57.9", "706870000": "I26.99",
    "234466008": "D68.9", "40095003": "S37.009A", "86175003": "S26.90XA", "84114007": "I50.9", "67782005": "J80",
    "49436004": "I48.91", "90460009": "S19.9XXA", "39848009": "S13.4XXA", "370143000": "F32.9",
    "185086009": "J44.9", "13645005": "J44.9", "195967001": "J45.909", "359817006": "S72.009A", "53741008": "I25.10",
    "38341003": "I10", "40930008": "E03.9", "42343007": "I50.9", "449868002": "F17.210",
}

# LOINC -> (low, high, unit text). Used to fill the reference range and the H/L flag the lab system would send.
LAB_RANGES = {
    "718-7": (12.0, 17.5), "4544-3": (36, 52), "6690-2": (4.0, 11.0), "777-3": (150, 400), "789-8": (4.2, 5.9),
    "787-2": (80, 100), "785-6": (27, 33), "786-4": (32, 36), "788-0": (11.5, 14.5), "2345-7": (70, 140),
    "3094-0": (7, 20), "2160-0": (0.6, 1.3), "17861-6": (8.5, 10.5), "2951-2": (135, 145), "2823-3": (3.5, 5.1),
    "2075-0": (98, 107), "2028-9": (22, 29), "33914-3": (60, None), "2885-2": (6.0, 8.3), "1751-7": (3.5, 5.0),
    "1975-2": (0.1, 1.2), "6768-6": (44, 147), "1742-6": (7, 56), "1920-8": (10, 40), "21377-7": (1.7, 2.2),
    "2093-3": (None, 200), "2571-8": (None, 150), "18262-6": (None, 100), "2085-9": (40, None), "4548-4": (None, 5.7),
    "6301-6": (0.8, 1.2), "3173-2": (25, 35), "71425-3": (None, 125), "33762-6": (None, 125), "89579-7": (None, 26),
    "13969-1": (None, 5), "32693-4": (0.5, 2.0), "2744-1": (7.35, 7.45), "2019-8": (35, 45), "2703-7": (80, 100),
    "1960-4": (22, 26), "1988-5": (None, 10), "33959-8": (None, 0.5), "1989-3": (30, 100),
    "8867-4": (60, 100), "8480-6": (90, 140), "8462-4": (60, 90), "9279-1": (12, 20), "8310-5": (36.1, 37.8),
    "59408-5": (95, 100), "2708-6": (95, 100), "39156-5": (18.5, 24.9),
}
VITAL_LOINC = {"8867-4", "8480-6", "8462-4", "85354-9", "9279-1", "8310-5", "59408-5", "2708-6", "29463-7", "8302-2", "39156-5", "72514-3"}

# demo staff: user id, display name, role, unit, doctor id (attendings / resident are also in DR_MST)
ATTENDING_BY_UNIT = {"4W-CARD": "D9999900001", "3E-MED": "D9999900002", "5S-ORTH": "D9999900003", "2N-SURG": "D9999900004"}
DOCTORS = [  # DR_ID, name, speciality, NPI (synthetic: 9999...)
    ("D9999900001", "Dr. Priya Raman", "Cardiology", "9999900001"),
    ("D9999900002", "Dr. Michael Okafor", "Hospital Medicine", "9999900002"),
    ("D9999900003", "Dr. Laura Whitfield", "Orthopaedic Surgery", "9999900003"),
    ("D9999900004", "Dr. Daniel Reyes", "General Surgery", "9999900004"),
    ("D9999900005", "Dr. Emily Tran", "Internal Medicine (resident)", "9999900005"),
]
USERS = [  # USR_ID, name, role, unit, DR_ID
    ("attending.raman", "Dr. Priya Raman", "ATTENDING", "4W-CARD", "D9999900001"),
    ("attending.okafor", "Dr. Michael Okafor", "ATTENDING", "3E-MED", "D9999900002"),
    ("attending.whitfield", "Dr. Laura Whitfield", "ATTENDING", "5S-ORTH", "D9999900003"),
    ("attending.reyes", "Dr. Daniel Reyes", "ATTENDING", "2N-SURG", "D9999900004"),
    ("res.etran", "Dr. Emily Tran", "RESIDENT", "4W-CARD", "D9999900005"),
    ("nurse.lopez", "Maria Lopez, RN", "NURSE", "4W-CARD", None),
    ("nurse.park", "James Park, RN", "NURSE", "3E-MED", None),
    ("cm.kim", "Rachel Kim, RN Case Manager", "NURSE", "5S-ORTH", None),
    ("pa.hughes", "Carol Hughes (prior authorization)", "PA_COORD", "ALL", None),
    ("him.patel", "Anita Patel, CCS (coding)", "HIM_CODER", "ALL", None),
    ("it.admin", "Sam Whitlock (IT)", "IT_ADMIN", "ALL", None),
]
UNIT_NAMES = {"4W-CARD": "4 West Cardiac Telemetry", "3E-MED": "3 East Medical", "5S-ORTH": "5 South Orthopaedics", "2N-SURG": "2 North Surgical"}

PAYERS = ["Summit Health Plan", "Keystone Health Plan", "Pinnacle Care", "Northwind Health", "Evergreen Health Plan", "Lakeshore Mutual"]
AUTH_QUERY_REASONS = [
    "Implant record / device log not attached",
    "Clinical notes supporting level of care not attached",
    "Operative report not attached",
    "Principal diagnosis inconsistent with admitting diagnosis",
    "Attending NPI missing",
    "Pending results not documented in discharge summary",
]
