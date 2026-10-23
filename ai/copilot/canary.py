"""Redaction canary: every few minutes, push a synthetic patient's identifiers through the LIVE gateway's de-identification (without calling any model) and check that none survive.

Why: the leakage unit test proves the code is right at build time. The canary proves the *running system is wired right*: it catches a deployment that bypasses the redactor,
a configuration change, or a refactor that skipped the test. The metric `copilot_redaction_canary_ok` drops to 0 and an alert fires (see deploy/platform/monitoring and the incident drill).
"""
import threading
import time
import os

from . import telemetry
from .chart import Identity

CANARY = Identity(names=["Zzyzx Quillfeather", "Zzyzx", "Quillfeather"], mrn="CF999999", member_id="CANARY-0001", dob="1900-01-01", street="1 Canary Way", city="Canaryville", zip="00000",
                  phones=["000-555-0100"], accounts=["XX0000-0000"], anchor="2026-01-01")


class _Ctx:
    identity = CANARY


def run(gateway) -> dict:
    """{ok, sent_to_model, leaked}. ok is False if any canary identifier would reach the model."""
    probe = (f"Patient {CANARY.names[0]} ({CANARY.names[1]}), MRN {CANARY.mrn}, member {CANARY.member_id}, born {CANARY.dob}, lives at {CANARY.street}, {CANARY.city} {CANARY.zip}, "
             f"phone {CANARY.phones[0]}, account {CANARY.accounts[0]}. Admitted 2026-01-03. Mr. {CANARY.names[2]} reports chest pain.")
    sent = gateway.dry_run("system prompt", probe, patient_id=None, context=_Ctx())
    leaked = [x for x in (*CANARY.names, CANARY.mrn, CANARY.member_id, CANARY.dob, CANARY.street, CANARY.city, *CANARY.phones, *CANARY.accounts, "2026-01-03") if x and x in sent]
    result = {"ok": not leaked, "sent_to_model": gateway.online, "leaked": leaked}
    telemetry.CANARY_OK.set(1 if result["ok"] else 0)
    return result


def start(get_gateway, interval: float | None = None) -> None:
    interval = float(os.getenv("CANARY_SECONDS", "300")) if interval is None else interval
    if interval <= 0:
        return

    def loop():
        while True:
            try:
                run(get_gateway())
            except Exception as e:   # noqa: BLE001 - the canary must never die
                print(f"[canary] {e}")
                telemetry.CANARY_OK.set(0)
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True, name="redaction-canary").start()
