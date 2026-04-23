"""Execution layer — vol structures, greek aggregation, delta hedging.

Phase P5 scaffolding. Module stays decoupled from ``ib_insync`` so the
trade preview math is testable without a live IB connection ; a
separate thin adapter (not yet implemented) will translate
``OrderSpec`` into IB orders when the operator clicks Submit on the
Trade Preview panel.
"""
