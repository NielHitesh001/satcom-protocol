# Phase 2 — Routing status

## Implemented behavior

- AODV-style route-request flooding and destination-originated route replies
- Route expiry and local invalidation when a directly used link goes down
- Buffered AODV data is reported as **residual** when the simulation ends
- OLSR HELLO/TC exchange, MPR selection, and shortest-path recomputation
- Practical predictive routing based on current topology and estimated link lifetime

## Deliberate simplifications

This is not an RFC-complete AODV or OLSR implementation.

- AODV does **not** yet implement scheduled RREQ retry/timeout.
- AODV does **not** send or process RERR packets.
- AODV does **not** implement intermediate-node RREP.
- OLSR timing and flooding are simplified for the discrete-event model.

Metrics obey: `sent = delivered + dropped + residual`.  A residual packet is either
still scheduled for arrival or buffered pending AODV discovery at the cutoff.
