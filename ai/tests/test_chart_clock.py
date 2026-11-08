"""The chart's own clock must come from clinical data, never from what this service filed back into the chart."""
from copilot.chart import Chart, Document, Encounter


def test_documents_filed_by_the_service_do_not_move_the_charts_clock():
    chart = Chart(patient_id="p1", name="Test Patient", birth_date="1950-01-01", sex="male")
    chart.encounters.append(Encounter(id="e1", cls="IMP", type="inpatient", start="2026-10-19T08:00:00"))
    chart.documents.append(Document(ref="DocumentReference/1", kind="progress note", date="2026-10-21T09:00:00"))
    chart.documents.append(Document(ref="DocumentReference/2", kind="prior authorization", date="2026-10-24T10:15:00", generated=True))
    assert chart.as_of == "2026-10-21"
