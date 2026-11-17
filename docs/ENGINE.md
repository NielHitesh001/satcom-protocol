# Simulation Engine Architecture

## Event model

The simulator uses a chronological priority queue (`heapq`) of `Event` objects.

**Fields:** `time`, `order` (kind priority), `seq` (monotonic tie-break), `kind`, payload.

### Same-time priority (lower runs first)

| Kind | Order | Role |
|------|------:|------|
| `topology` | 0 | Apply link up/down for a topology snapshot |
| `tx_done` | 0 | Release one DATA queue slot when serialization finishes |
| `tick` | 1 | OLSR HELLO/TC generation |
| `inject` | 2 | Application packet injection |
| `arrival` | 3 | Packet arrives at a node after TX + propagation |
| `end` | 9 | Simulation cutoff |

Within the same `(time, order)`, events are ordered by insertion sequence `seq`. Topology snapshots are scheduled at start, so they run before a `tx_done` at the same timestamp. `tx_done` still runs before `tick`, `inject`, and `arrival`.

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

Broadcast expands to one unicast-like transmission per neighbour.

## Queue, TTL, and loss

- `queue_limit`: max DATA packets accepted on a directed link whose serialization has not finished. A further DATA packet is **dropped** when the link is still busy (`start > now`) and that count is already at the limit. The slot is taken in `_send` and released by a `tx_done` event at `start + serialization`. Control packets are not counted against the limit, but they do occupy link busy time.
- DATA TTL starts at 32 (see `Packet`). Each hop that forwards DATA decrements TTL on the forwarded copy. TTL 0 is dropped and is not transmitted. The destination still accepts a packet that arrives with TTL 0; the check happens on the next send, not on delivery.
- `loss_prob`: Bernoulli drop applied at send time (per hop).
- Forwarded packets are copies. Fan-out of one control PDU (for example RERR to several precursors) does not share a path list.

## Packet fates

Every injected DATA packet ends in exactly one of:

| Fate | Meaning |
|------|---------|
| **delivered** | Arrived at destination |
| **dropped** | Loss, queue overflow, TTL exhausted, no route, or link missing at send |
| **residual** | Still in flight, or still in an AODV buffer, when simulation ends |

Invariant: `sent = delivered + dropped + residual`.

Residual is **not** counted as a network drop.

## Topology

Topology snapshots are precomputed on a `time_step_s` grid. Packet events use the latest snapshot with `time_s <= event.time`.

## Metrics conventions

- Latency percentiles: linear interpolation, index `k = (n-1) * p/100`.
- Undefined latency (no deliveries) → JSON `null`, never `0.0`.
- `loss_ratio = dropped / sent` (residual excluded).
- `delivery_ratio = delivered / sent`.

## Known limitations

- No continuous orbital updates between topology steps.
- No MAC contention beyond per-link FIFO busy-time.
- OLSR tick density is tied to `min(time_step_s, hello_interval_s)`.
- Maximum 5e6 events per run (safety against infinite loops).
