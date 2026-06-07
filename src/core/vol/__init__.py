"""Pure volatility-modelling helpers : Yang-Zhang RV, GARCH(1,1)
projection, PCHIP smile interpolation, SVI/SSVI calibration, regime
gating, PCA decomposition.

No I/O — submodules are imported explicitly by consumers (no eager
re-exports here, so importing one submodule does not drag heavy
optional deps like ``arch`` or ``sklearn`` into containers that don't
ship them — e.g. the api image, which is pure-stateless and excludes
the ``[quant]`` extra).
"""
