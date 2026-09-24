# 07 — Analytics methods

The maths behind every number the platform reports, and — equally important — what each number
is **not**.

A guiding principle runs through all of it:

> **Measure what can actually be measured from a moving bus, and be precise about what the
> measurement means.** A defensible number with a stated caveat beats an impressive number that
> falls over under one question.

---

<a id="congestion"></a>
## 1. Congestion

### The counterintuitive part: the primary signal uses no computer vision

Estimating traffic density by counting vehicles through a windscreen is fragile. Apparent
vehicle size depends on range, the field of view sees perhaps three lanes of a six-lane road,
and a bus in a jam mostly sees the back of one truck.

But the bus is **itself a probe vehicle**. Its own speed against that road's normal speed is a
direct measurement of congestion, it needs nothing but GNSS, and it is the method TomTom, HERE
and Google Traffic actually use. Computer vision then adds the layer none of them have: *why*.

### Free-flow baseline

Per segment, per direction, learned rather than assumed:

```
  v_ff(s, d) = P85 { mean_speed_kmh : passes of (s,d) in trailing 30 days,
                     excluding 07:00–11:00 and 16:00–21:00 }

  requires n ≥ 50 passes; otherwise fall back to  0.8 × OSM maxspeed
```

The 85th percentile of off-peak observations, not the maximum — the maximum is one empty
Sunday 05:00 run and is not a realistic free-flow condition. Learning the baseline per segment
also captures what `maxspeed` cannot: a 60 km/h road that never exceeds 35 because of its
geometry has a free-flow of 35, and grading it against 60 would report permanent congestion.

### Congestion index

```
  CI(s, d, t) = clamp( 1 − v_obs / v_ff , 0, 1 )
```

| CI | Meaning |
|---|---|
| 0.0 | Free flow |
| 0.3 | Noticeably slow |
| 0.5 | Half speed |
| ≥ 0.75 | Severe |

Alongside it we report the figure planners actually use, because CI is unitless and a ratio:

```
  delay(s,d,t)  =  ( 1/v_obs − 1/v_ff ) × 3600     seconds lost per kilometre
```

### Two corrections without which the map is wrong

**Bus stop dwell.** A bus stopping at a stop is not congestion. Time spent below 3 km/h within
25 m of a GTFS stop position is excluded (`dwell_excluded_seconds`). Without this correction
**every bus stop in the city renders as a permanent traffic jam** — roughly 6,000 false
hotspots, precisely at the places buses are supposed to stop. This is the single most important
line in this document.

**Signal queues.** Time stopped at a mapped signalised junction is retained but tagged, and
reported separately as *intersection delay* rather than *link delay*. They have different
remedies: signal retiming versus capacity works.

### What computer vision adds: attribution

For segments where `CI > 0.4`, join the concurrent CV signals:

| Occupancy | Other evidence | Attributed cause |
|---|---|---|
| High | — | **Demand** — genuinely dense traffic |
| Low | active `waterlogging` asset | **Drainage** |
| Low | incident within 200 m | **Incident** |
| Low | `encroachment` asset | **Capacity loss** — parking or construction |
| Medium | high `two_wheeler` fraction | **Mixed-traffic friction** |

This is the part that distinguishes the platform from a GPS-probe product. Anyone can tell a
planner *where* traffic is slow. Telling them **the slowness on this stretch is drainage, three
Tuesdays a month, and here is the photograph** is an actionable insight, and it is only possible
because the probe vehicle also has eyes.

### Bottleneck ranking

Ranking by CI alone promotes empty, slow lanes. Rank by delay actually suffered:

```
  passenger-hours lost(s,d,t)  =  delay_per_km × length_km × passes × mean_bus_occupancy
```

A corridor costing 400 passenger-hours a day outranks a worse-CI lane costing 12. This is the
metric a transport authority allocates budget against, and the fleet measures every term of it
directly — including occupancy, from the cabin camera once one is fitted (roadmap; until then,
from ticketing data or a fixed per-route assumption, stated as such).

### Aggregation and serving

15-minute buckets per segment per direction, pre-computed as a TimescaleDB continuous
aggregate. Bins with fewer than 3 passes are served with a low-confidence flag and rendered
desaturated — **an estimate from one bus is shown as an estimate from one bus.**

---

## 2. Vehicle density, classification and counting

Feeds the attribution layer above, and answers the brief's clause A9 directly.

**Counting is per unique track per segment pass, never per frame.** Summing per-frame
detections would scale with how long the bus spent on the segment, so a bus stuck in traffic
would report more vehicles than one sailing through — inverting the quantity being measured.

Three complementary measures, because no single one is sufficient:

| Measure | Definition | Robust to | Weak to |
|---|---|---|---|
| **Unique tracks** | Distinct track IDs per class per pass | Bus speed | Occlusion (undercounts in dense traffic — exactly when it matters) |
| **Occupancy ratio** | Mean fraction of drivable road area covered by vehicle masks in the forward ROI | Range, occlusion, speed | Needs good road segmentation |
| **PCU** | Σ counts × IRC:106 equivalency factors | Vehicle-mix differences | Inherits track undercounting |

**Occupancy ratio is the one that works in a jam**, which is why it exists: when traffic is
dense enough that individual vehicle tracks fragment, the *area covered* is still measurable.
It is also scale-invariant — the denominator (drivable area visible) shrinks with the numerator
as range changes, so the ratio holds.

**PCU matters specifically in India.** IRC:106 gives an auto-rickshaw ≈ 0.8 PCU and a
two-wheeler ≈ 0.5 against a car's 1.0. A corridor with 200 two-wheelers and one with 200 cars
have the same raw count and roughly half the traffic load. Reporting raw counts would make
Bengaluru's actual traffic mix unreadable to a planner.

---

<a id="pedestrian-density"></a>
## 3. Crowd density — the "population heat map"

### What it is, stated precisely

It is a **rate estimator, not a census.** It does not count how many people are in a place. It
estimates the *typical density of pedestrians visible from the carriageway*, per segment, per
time bin, from repeated sampling.

Getting this framing right is what makes the layer defensible. The wrong framing —
"ARGUS counts the city's population" — collapses under the first question about the people
indoors, on side streets, or behind the bus.

### The estimator

Each segment pass contributes a numerator and a denominator:

```
                  Σ_i  unique_pedestrian_tracks_i
  D̂(s, t)  =  ───────────────────────────────────────  × 100      persons per 100 m²
                  Σ_i  observed_area_m2_i

              summed over all passes i of segment s within time bin t
```

**The denominator is the whole trick.** `observed_area_m2` is the ground area of footpath and
verge the pipeline could actually see during that pass, derived from the segmentation mask and
IPM. Without it, a clear pass down an empty road and an occluded pass behind a truck contribute
equally to a count, and the resulting map measures visibility rather than crowding.

This is also why the continuous aggregate in [`docs/04`](04-backend.md) stores the numerator and
denominator as separate columns and never a pre-divided rate — **rates cannot be re-aggregated.**
Averaging per-segment densities across a ward gives the wrong answer unless each is weighted by
its observed area. Keeping both columns means every later rollup is still correct.

### Why repeated passes help rather than double-count

Each pass is an **independent sample of an unknown density field**, not a tally to be summed.
Fifteen passes give fifteen samples; the estimate converges. Standard error under a Poisson
assumption:

```
  SE( D̂ )  ≈  √( Σ n_i ) / ( Σ A_i ) × 100
```

which is reported alongside every cell. A hexagon estimated from two passes is shown with wide
error; one from ninety is shown as solid.

### Rendering and binning

Segment-level estimates are the primary product. For the city view they are binned to **H3
resolution 10** (~65 m edge), weighted by segment length within each hexagon; zooming past
~z15 switches to resolution 11.

Time bins are hourly, with named presets that match how the question is actually asked: *school
arrival*, *morning peak*, *midday*, *evening peak*, *night*.

### What it is genuinely for

| Use | Why the fleet is the right instrument |
|---|---|
| Crossing demand | High pedestrian density + high `crossing_events` + no `highway=crossing` in OSM = an unmet crossing need, evidenced |
| Bus stop siting and sizing | Footfall at existing stops, measured continuously rather than from a one-day manual count |
| School-hour warden deployment | Recurrence is the signal, and only a daily-passing fleet establishes recurrence |
| Footpath prioritisation | Density on segments with no mapped footpath |

### Caveats we state on the layer itself

- Only pedestrians **visible from the carriageway**. Interior spaces, side lanes and the far
  side of a divided road are not observed.
- Strongly **biased to bus corridors**. The coverage layer is on by default for this reason.
- Sampled at pass times, so a crowd that assembles and disperses between two buses is missed.
- **Never identity, never demographics.** Counts and trajectories only; faces blurred on-device
  before storage. See [`docs/08`](08-privacy-and-compliance.md).

---

<a id="road-condition"></a>
## 4. Road condition map

Per segment, from confirmed unresolved surface assets:

```
  condition(s) = 100 − clamp( Σ_a  w_class(a) × severity_a × min(1, area_a / A_ref) × 100 / L_s ,
                              0, 100 )
```

normalised by segment length `L_s`, so a 1.5 km segment with four potholes scores better than a
200 m segment with the same four. Presented on a 0–100 scale deliberately analogous to a
Pavement Condition Index, so it slots into an engineer's existing mental model.

Reported with **`last_surveyed_at` and `pass_count`** always attached. A segment nobody has
driven in five weeks does not score 100; it scores `unsurveyed`.

---

<a id="ward-scorecard"></a>
## 5. Infrastructure Deficiency Index — the ward scorecard

### The normalisation that makes it valid

```
                Σ_a  w_class(a) × severity_a × confidence_a
  IDI(w)  =  ──────────────────────────────────────────────────
                        km_surveyed(w)
```

over confirmed, unresolved assets in ward `w`.

**Without the denominator this index is just a map of where bus routes are.** A ward with eight
routes accumulates more detections than a ward with one, regardless of road condition, and the
"worst ward" would reliably be the best-connected one. Dividing by kilometres actually surveyed
turns a count into a rate, and a rate into a comparison that survives scrutiny.

This is the single most likely methodological question from a judge with a statistics
background, and having the answer built in rather than improvised is worth the five lines of
SQL.

### Four sub-scores, deliberately not collapsed into one number

| Sub-score | Formula | What it measures |
|---|---|---|
| **Deficiency** | IDI above | How bad the infrastructure is |
| **Responsiveness** | Median age of open work orders | How fast the ward acts |
| **Closure** | Fleet-verified resolutions ÷ issued orders | How much gets genuinely fixed |
| **Durability** | Assets re-opening within 90 days at the same location | **Repair quality** |

**Durability is the one nobody currently measures**, and the fleet gets it almost free. A
pothole patched in March and re-detected in May was not repaired — it was covered. Today that
distinction is invisible to a municipal corporation because nothing re-inspects at scale. A bus
fleet re-inspects every road it drives, several times a day, forever.

They stay as four numbers rather than one composite because a single index invites gaming and
hides the actionable part. A ward with high deficiency and excellent closure needs budget; a
ward with low deficiency and terrible durability needs a contractor audit. One number cannot say
both.

---

<a id="od"></a>
## 6. Origin–destination and corridor flow — Phase 3

### What we will not claim

True vehicle-level O–D requires re-identifying individual vehicles across the city. We are not
building that. It is infeasible on this hardware, and a system that tracks identified private
vehicles city-wide is a surveillance capability that should not be built casually into a
municipal platform. Saying so is a stronger position than gesturing at it.

### What we will deliver

**Directional corridor flow.** PCU per hour per segment per direction, aggregated fleet-wide
and rendered as flow ribbons weighted by volume. Tidal patterns — inbound-heavy mornings,
outbound-heavy evenings — emerge directly and are exactly what corridor planning uses.

**Junction turn ratios.** For tracks entering a junction from approach A, the fraction exiting
via each of B, C, D. Tractable because a bus waiting at a signal has a stable view of the
junction for tens of seconds. Turn ratios are a standard traffic-engineering input, currently
obtained by paying people to stand at junctions with clickers for one day a year. ARGUS
produces them continuously, which is a genuinely better product than the thing it replaces.

**Passenger O–D**, if and only if BMTC ticketing or APC data becomes available. Fare-collection
data is the correct source for passenger O–D; inferring it from cameras would be worse and
creepier. Documented as an integration, not a CV claim.

---

<a id="route-delay"></a>
## 7. Route delay — Phase 3

Needs no computer vision whatsoever, which is worth stating plainly rather than obscuring.

```
  delay(trip, stop)  =  actual_arrival − scheduled_arrival        from GTFS stop_times
  headway_dev(route, stop)  =  observed_headway − scheduled_headway
```

Bunching — two buses arriving together after a long gap — is what passengers experience as
unreliability, more sharply than raw lateness, and headway deviation captures it where average
delay does not.

### Delay attribution is the interesting part

Decompose a trip's total delay across the segments that produced it, by comparing each
segment's traversal time against its free-flow time from §1:

```
  delay_contribution(s)  =  t_actual(s) − ( length(s) / v_ff(s) )
```

Which yields statements of this shape:

> **"41% of Route 500D's evening delay accrues in the 1.2 km approaching the Silk Board
> junction. Median loss: 7 minutes 20 seconds per trip. Attributed cause: demand, 78% of
> observations."**

That sentence is the entire product thesis in one line. It combines schedule data, the
congestion index, and CV-based attribution, and no single one of those three could produce it.

---

## What none of these numbers are

Worth keeping in front of the team, and worth saying out loud in the video — it is the kind of
candour that reads as competence rather than weakness.

| Claim we do **not** make | Why |
|---|---|
| A census of people or vehicles | Rate estimators from a sampled, biased path |
| Complete road coverage | Buses drive bus routes. Hence the coverage layer. |
| Millimetre defect geometry | Monocular depth is a proxy; area is measured, depth is inferred |
| Legally conclusive plate identification | High-confidence ANPR with per-character uncertainty, for human review |
| Real-time in the sub-second sense | Detection to dashboard is seconds; incidents are prioritised, routine findings are queued |
| Universal hazard detection | Trained classes are reliable; the long tail is human-triaged |

Each row in that table is a question a judge may ask. Having answered them in the design is
better than answering them on stage.
