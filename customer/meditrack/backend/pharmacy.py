"""Pharmacy: discharge medication reconciliation (what the pharmacist does when discharge is advised)."""


def reconcile_discharge_meds(conn, ip_no: str) -> int:
    """Copy the active home + in-patient medications into the DISCHARGE list, once. Returns the number of rows added."""
    if conn.execute("SELECT 1 FROM PHR_ISS_DTL WHERE IP_NO=%s AND ISS_TYP='DISCHARGE' LIMIT 1", (ip_no,)).fetchone():
        return 0
    active = conn.execute("""SELECT DRUG_NM, RXN_CD, DOSE, FREQ, ROUTE, DUR, ISS_TYP FROM PHR_ISS_DTL
                             WHERE IP_NO=%s AND MED_STS='ACTIVE' AND ISS_TYP IN ('HOME', 'INPATIENT')
                             ORDER BY CASE ISS_TYP WHEN 'INPATIENT' THEN 0 ELSE 1 END, ID""", (ip_no,)).fetchall()
    seen, added = set(), 0
    for m in active:
        key = (m["RXN_CD"] or m["DRUG_NM"]).lower()
        if key in seen:
            continue
        seen.add(key)
        conn.execute("INSERT INTO PHR_ISS_DTL (IP_NO, DRUG_NM, RXN_CD, DOSE, FREQ, ROUTE, DUR, ISS_TYP, MED_STS) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,'DISCHARGE','ACTIVE')",
                     (ip_no, m["DRUG_NM"], m["RXN_CD"], m["DOSE"], m["FREQ"], m["ROUTE"], m["DUR"]))
        added += 1
    return added
