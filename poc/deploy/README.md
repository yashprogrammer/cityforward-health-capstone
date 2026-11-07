# Temporary Azure course demo

This deployment places two separate teaching stages on one VM:

```text
Browser --HTTPS--> Caddy --/-----> legacy MediTrack --> PostgreSQL
                        --/poc/-> Streamlit POC -----FHIR-----> MediTrack
                                      |
                                      +--managed identity--> Azure Foundry
```

The original legacy app and the fictional payer run in their own containers.
The POC reads synthetic charts from the legacy FHIR API using a read-only SMART
backend client. Its private signing key is generated on the VM, outside Git;
`register_reader.py` registers only its public key with the demo EHR. Foundry
access uses the VM's managed identity. The local file-based POC remains an option.

`compose.yaml` extends the legacy Compose file; `Dockerfile` packages only the
POC. Caddy terminates HTTPS and routes the two browser paths. PostgreSQL and the
mock payer listen only on the VM's loopback interface. The Azure network security
group restricts HTTPS and SSH to the owner's public IP; port 80 supports HTTPS
certificate issuance and redirects to HTTPS. An Azure auto-shutdown schedule
deallocates the VM independently of the guest.

This is a **synthetic-data course demonstration**, not the integrated system or
a production EHR: neither stage supplies production staff authentication, and
the POC does not redact identifiers before sending evidence to a model. Do not
use real patient data. Network restriction is not a substitute for login. The
POC can fall back to a labelled template when its evidence checks fail. Missing
FHIR coverage is displayed as unavailable, not assumed to be self-pay.

VM deallocation stops VM compute charges but does not delete the disk, public IP,
or other resources, and model use is billed separately. The scheduled time is
not a hard spending cap. The owner must review charges and delete resources when
the demo is no longer needed; restarting the VM starts compute charges again.
