# Simulation Engine Architecture

## Event model

The simulator uses a chronological priority queue (`heapq`) of `Event` objects.

**Fields:** `time`, `order` (kind priority), `seq` (monotonic tie-break), `kind`, payload.

### Same-time priority (lower runs first)

| Kind | Order | Role |
|------|------:|------|
| `topology` | 0 | Apply link up/down for a topology snapshot |
| `tick` | 1 | OLSR HELLO/TC generation |
| `inject` | 2 | Application packet injection |
| `arrival` | 3 | Packet arrives at a node after TX + propagation |
| `end` | 9 | Simulation cutoff |

Within the same `(time, order)`, events are ordered by insertion sequence `seq`.

## Delay model

For a unicast transmission on directed link `src → nh`:

```
serialization_s = (packet_size_bytes * 8) / (capacity_mbps * 1e6)
prop_s          = distance_km / c          # c = 299792.458 km/s
start_tx        = max(now, link_free_at[src,nh])
arrival_time    = start_tx + serialization_s + prop_s
link_free_at[src,nh] = start_tx + serialization_s
```

Opposite directions `(u,v)` and `(v,u)` are independent channels.

## Packet fates

| Fate | Meaning |
|------|---------|
| **delivered** | Arrived at destination |
| **dropped** | Loss, queue overflow, no route, or link missing at send |
| **residual** | Still scheduled (in-flight) when simulation ends |

Invariant: `sent = delivered + dropped + residual`.

## Metrics conventions

- Latency percentiles: linear interpolation, index `k = (n-1) * p/100`.
- Undefined latency (no deliveries) → JSON `null`, never `0.0`.
- `loss_ratio = dropped / sent` (residual excluded).

## Known limitations

- No continuous orbital updates between topology steps.
- No MAC contention beyond per-link FIFO busy-time.
- Maximum 5e6 events per run (safety against infinite loops).
