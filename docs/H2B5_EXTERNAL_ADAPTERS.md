# H.2B.5 External Integration Adapters

The Generic GIS, LRMS, and DILRMP adapters are **demo DTO adapters only**. They do not call government services, scrape portals, use credentials, transmit project data, certify records, or publish geometry. Each reports `DEMO` and returns a local acknowledgement only.

Project-scoped users with `export:read` can inspect capabilities, preview the reduced outbound representation, or execute the demo action. Payloads retain project/version/provenance context and explicitly mark draft or unverified geometry as preliminary. Authorized departmental endpoints, contracts, credentials, payload review, secure delivery, and acknowledgement verification are deferred.
