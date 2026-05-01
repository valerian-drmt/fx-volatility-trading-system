"""Events pipeline — multi-source aggregator for the regime gating event_dampener.

See docs/vol_trading_pca/events_pipeline_spec.md for the full design.

Public surface :
    from api.orchestration.events.scheduler import EventsScheduler
    from api.orchestration.events.sources.fred import FREDSource
    from api.orchestration.events.deduplicator import EventDeduplicator
    from api.orchestration.events.repository import EventsRepository
"""
