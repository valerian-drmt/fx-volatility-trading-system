"""Analytics engine — the 6th microservice.

Runs the vol-engine's heavy hourly batch analytics (PCA surface snapshot,
GMM regime refit, PC3 skew/convex history) on its own decoupled loop so the
compute can be cpu-capped independently. No IB connection — reads from
Redis / Postgres and publishes results back.
"""
