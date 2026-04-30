"""Events pipeline — multi-source aggregator for the regime gating event_dampener.

See docs/vol_trading_pca/events_pipeline_spec.md for the full design.

Public surface :
    from api.services.events.scheduler import EventsScheduler
    from api.services.events.sources.fred import FREDSource
    from api.services.events.deduplicator import EventDeduplicator
    from api.services.events.repository import EventsRepository
"""
