"""
Satellite Ad Hoc Network Simulator
==================================

A discrete-event simulator for ad hoc routing over LEO satellite constellations
using real TLE orbital data (Starlink as the primary model).

Modules
-------
config   : Scenario and parameter loading
orbit    : TLE loading and SGP4 propagation
links    : Time-varying visibility / capacity graph
routing  : Pluggable AODV / OLSR / predictive protocols
traffic  : Flow generation
engine   : Discrete-event simulation core
metrics  : Collection and export of performance metrics
analysis : Plotting and summary tables
"""

__version__ = "0.1.0"
