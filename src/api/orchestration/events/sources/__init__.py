"""One module per economic-events data source.

Each module exports a single class implementing
:class:`api.orchestration.events.sources.base.EventSource`.

Tier 1 (primary, full coverage) :
    fred.py        — FRED /releases/dates       (US, all official releases)
    ecb.py         — ECB Governing Council      (EU, rate decisions)        [TODO]
    boe.py         — Bank of England MPC        (GB, rate decisions)        [TODO]
    fomc.py        — Fed FOMC calendar HTML     (US, meetings + minutes)    [TODO]
    eurostat.py    — Eurostat release calendar  (EU, HICP/GDP)              [TODO]

Tier 2 (backup) :
    ons.py         — ONS UK release calendar    (GB, CPI/GDP)               [TODO]
    bls.py         — BLS schedule               (US, fallback if FRED down) [TODO]
"""
