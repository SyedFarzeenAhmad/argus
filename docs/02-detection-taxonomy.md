# 02 — Detection taxonomy

What ARGUS detects, how each one is actually detected, why the obvious approach fails, and
what a municipality is supposed to *do* about it.

The last column matters more than it looks. A detection with no attached action is a
statistic; a detection with an action is a work order. The problem statement asks for
*"actionable insights"*, and this is where that word gets cashed.

---

## The classes at a glance

| Class | Category | Method | Severity from | Sev. | Phase |
|---|---|---|---|---|---|
| `pothole` | Road defect | Instance segmentation | Ground area (m²) × depth proxy | med–high | 1 |
| `damaged_road` | Road defect | Semantic segmentation | % distressed area per 10 m | med | 2 |
| `waterlogging` | Hazard | Segmentation + persistence | Extent × carriageway fraction | high | 2 |
| `road_debris` | Hazard | Object detection | Size × lane position | med–high | 3 |
| `open_manhole` | Hazard | Object detection | Binary — always critical | critical | 3 |
| `damaged_divider` | Infrastructure | Detection + condition head | Gap length | med | 2 |
| `missing_divider` | Infrastructure | **Ledger inference** | Gap length | med | 2 |
| `faded_zebra` | Infrastructure | Segmentation + contrast score | Stripe contrast ratio | med | 2 |
| `missing_zebra` | Infrastructure | **Ledger inference** | School-zone multiplier | high | 2 |
| `damaged_sign` | Infrastructure | Detection + condition head | Sign class × damage | low–med | 2 |
| `missing_sign` | Infrastructure | **Ledger inference** | Sign class criticality | med | 3 |
| `encroachment` | Infrastructure | Segmentation | Carriageway width lost | med | 3 |
| `pedestrian_risk` | Safety | Tracking + zone + time gate | Cluster size × exposure | high | 2 |
| **Incidents** | Safety | Temporal kinematics | Per type | crit | 3 |

`missing_*` classes are **never emitted by the edge.** They are conclusions the backend draws
from repeated non-observation. See [Missing classes](#missing-classes).

---

## Road defects

### `pothole`

**Method.** Instance segmentation (not bounding-box detection). The mask is projected to the
ground plane by inverse perspective mapping, giving a real area in m².

**Why not a bounding box.** A box tells you a pothole exists; it does not tell you whether
it's a 20 cm scab or a 2 m crater, because apparent box size depends entirely on how far away
the bus was. Two observations of the same pothole from 8 m and 22 m produce boxes differing
by ~7×. A ground-projected mask area is **view-invariant**, which means — critically — that
it is *fusable across passes*. Severity you can average is severity you can trust.

**Depth is the hard part, and we are honest about it.** Monocular depth of a road cavity is
unreliable. We use a proxy: shadow-fill ratio inside the mask plus the presence of a dark
interior gradient, which correlates with depth but is not a measurement. Severity is
therefore reported as a *band* with the area in m² alongside — an engineer reads the area,
not our depth guess. If a stereo pair or a cheap ToF sensor is available later, this is the
first place to spend it.

**Data.** RDD2022 (India split) + IDD + a Roboflow pothole corpus + our own Bengaluru
capture. Target: mAP@50 ≥ 0.70 on a held-out Bengaluru set, which is a realistic figure —
published pothole detectors sit in the 0.6–0.8 range and anyone claiming 0.95 is testing on
their training distribution.

**Action.** *Raise patching work order to ward engineer.* SLA 7 days for high, 30 for medium.

**Failure modes.** Tar patches (a repaired pothole) read as potholes — mitigated by the
`rejected` operator feedback path and a dedicated `patch` negative class. Shadows under trees
read as potholes — mitigated by the illumination-gated `inspected_for` list and by fusion
requiring distinct-device corroboration, since a shadow is at a *fixed* place at a *fixed*
time of day and will not corroborate across the morning and evening passes.

<a id="damaged_road"></a>
### `damaged_road`

**Method.** Semantic segmentation of surface distress (cracking, ravelling, rutting,
edge-break), aggregated to **percentage of distressed surface area per 10 m of carriageway.**

**Why not counting cracks.** A crack count is meaningless — one crack 4 m long and forty
hairlines are not comparable, and neither is stable across viewing geometry. Percentage
distressed area is the input municipal engineers already use (it's how a Pavement Condition
Index is built), so the output lands in a format the customer's existing process accepts.
That's a product decision as much as a technical one.

**Action.** *Schedule resurfacing survey for this segment.* Rolls into the road-condition map
rather than into individual work orders — resurfacing is a segment-level intervention.

### `waterlogging`

**Method.** Segmentation, plus two cues that a single frame cannot provide:

1. **Specular reflectance** — standing water mirrors the sky and surrounding vehicles;
   merely wet tarmac scatters. Measured as local intensity variance within the mask against
   the surrounding road.
2. **Temporal persistence** — the region must remain across ≥ 8 consecutive sampled frames
   as the bus approaches, with geometry consistent with a static ground-plane patch.

**Why the obvious approach fails.** A single-frame "is this wet?" classifier confuses a rain-
slicked road with 200 mm of standing water, and the difference between those two is the entire
point: one is weather, the other is a blocked storm drain and a hazard to two-wheelers. The
persistence + reflectance combination is what separates them.

**Severity.** Extent × fraction of carriageway width affected. Water across a full lane during
a monsoon evening is high; a puddle on the verge is not.

**Action.** *Dispatch de-silting crew; flag drain blockage.* Waterlogging is also cross-
referenced with the congestion index — recurring waterlogging at a recurring bottleneck is a
drainage business case, which is precisely the kind of insight the brief is asking for.

### `road_debris`, `open_manhole` — the hazard tail

`open_manhole` is treated as **always critical** and pre-empts the uplink queue like an
incident. It is rare, it is lethal, and the cost of a false positive (someone drives out to
look) is trivially smaller than the cost of a false negative.

<a id="long-tail"></a>
**The genuine long tail** — fallen branches, abandoned vehicles, construction spill, cattle —
cannot be exhaustively trained. An open-vocabulary model (YOLO-World / GroundingDINO) runs at
low frequency as a **triage** pass: anything it flags goes to a human review queue, never to
an automatic work order. Claiming to detect "all road hazards" would be dishonest; claiming to
surface unknown hazards for human confirmation is both true and useful.

---

<a id="missing-classes"></a>
## Missing infrastructure — the map-difference problem

**This is the most technically interesting part of the design, and the part most teams will
get wrong.**

You cannot detect an absence with an object detector. There is no bounding box around a zebra
crossing that isn't there. Training a "missing zebra crossing" class means training a model to
fire on *arbitrary empty road*, which is exactly as broken as it sounds — every metre of every
road in the city is a positive.

### How it actually works

```
  ┌── EXPECTATION ──────────────┐    ┌── OBSERVATION ────────────────┐
  │ OSM says highway=crossing   │    │ SegmentPass #1: inspected_for  │
  │ at node 1029384756          │    │   includes faded_zebra, found  │
  │                             │    │   nothing.  day, occl 0.1      │
  │ OR                          │    │ SegmentPass #2: same.          │
  │                             │    │ SegmentPass #3: same.          │
  │ ARGUS itself observed a     │    │   ... × 12, across 9 devices   │
  │ crossing here until Aug 4   │    │                                │
  └──────────────┬──────────────┘    └───────────────┬────────────────┘
                 │                                   │
                 └──────────────┬────────────────────┘
                                ▼
                  ┌──────────────────────────────┐
                  │ ROAD ASSET LEDGER            │
                  │ expected ∧ ¬observed × N     │
                  │ across ≥ K distinct devices  │
                  │ under qualifying conditions  │
                  │        ⇒ missing_zebra       │
                  └──────────────────────────────┘
```

A pass only counts as evidence if it *qualified*: the segment was traversed, the class was in
`inspected_for` (so conditions permitted assessment — no zebra judgements at night or in heavy
rain), and `assessable_fraction` was high enough that the bus wasn't stuck behind a truck.

### Why this is stronger than detection

- **One bus missing it is occlusion. Twelve buses missing it is a fact.** The fleet is the
  instrument; a single dashcam fundamentally cannot do this.
- It produces a **date**. "Last positively observed 2026-08-04" is a far better municipal
  artifact than "currently absent", because it tells a ward engineer *when* it was lost.
- It is **auditable**. The `ledger` block on the Asset records what was expected, from where,
  and how many consecutive absences supported the conclusion. A human can check the reasoning
  rather than being asked to trust a score.

### The graded middle: `faded_zebra`

Real crossings do not vanish overnight — they fade. So the detector has a *present* class with
a condition score (measured as stripe-to-asphalt contrast ratio), and the lifecycle is:

```
  painted ──fades──▶ faded_zebra ──fades──▶ missing_zebra
   (no ticket)        (repaint order)        (urgent, esp. school zone)
```

Catching a crossing at `faded` is *proactive maintenance* — the brief's phrase — in a way that
catching it at `missing` is not. Same mechanism for `damaged_divider → missing_divider` and
`damaged_sign → missing_sign`.

<a id="damaged_sign"></a>
### `damaged_sign`

Detection of the sign, then a **condition head**: intact / bent / occluded-by-vegetation /
faded / defaced. Occluded-by-vegetation is worth calling out separately because the remedy is
a pruning crew rather than a signage crew — a different department, a different budget line,
and the system routing it correctly is the kind of detail that makes a platform usable.

Sign *class* also gates severity: a damaged STOP or school-zone sign is materially more urgent
than a damaged parking-restriction sign.

---

<a id="pedestrian_risk"></a>
## Vulnerable pedestrians

The brief asks for *"vulnerable pedestrian situations such as school children crossing roads."*

**We do not attempt to visually classify children.** Age estimation from a moving bus at 15 m
is unreliable, and building a system that classifies minors by appearance is a privacy and
ethics liability that a government deployment should not carry. It would also be the weakest
link in the whole pipeline if a judge pushed on it.

**What we do instead** — three signals that are each individually reliable:

| Signal | Source | Reliability |
|---|---|---|
| Pedestrian cluster of size ≥ N near the carriageway | Detection + tracking | High |
| Segment intersects an OSM `amenity=school` buffer | **The map** | Exact |
| Time falls in a school arrival/dismissal window | **The clock** | Exact |

Two of the three signals are ground truth, not inference. A cluster of 14 pedestrians at a
school gate at 15:40 with crossing trajectories into the carriageway is a *vulnerable
pedestrian situation* by any reasonable definition, and we reached it without guessing anyone's
age.

**Action.** *Assess for warden posting during school hours* — plus a standing recommendation
for a raised crossing or signal where the pattern recurs. The recurrence is the value: any one
bus sees a crowd once, but a fleet passing daily establishes that **this junction, at this
time, every weekday** has unprotected crossing demand.

---

<a id="incidents"></a>
## Incidents — a structurally different kind of detection

Everything above is a static thing observed in a frame. An incident is **a conclusion drawn
from a trajectory over a window of seconds.** The pipeline treats them differently end to end:
different trigger path, different confidence semantics, different transmission priority, and
video evidence instead of a crop.

| Type | Primary trigger | Corroborating signals |
|---|---|---|
| `rash_driving` | Lateral accel of tracked vehicle > threshold, sustained | Lane-change rate, TTC to other tracks |
| `dangerous_overtake` | Track crosses into oncoming lane with closing TTC | Ego braking response |
| `wrong_way` | Track heading opposed to segment `oneway` direction | Sustained over ≥ 3 s |
| `red_light_violation` | Track crosses stop line while signal state red | Requires signal-state detection — Phase 4, flagged as hard |
| `collision` | **Ego IMU impact spike** | Track discontinuity, debris |
| `hit_and_run` | Collision + subject departure above speed threshold | Post-event flight trajectory |
| `near_miss_vru` | TTC to pedestrian/cyclist track < 0.8 s | Evasive ego manoeuvre |

**The IMU leads on collisions, not the camera.** A chassis-mounted accelerometer registers an
impact the camera may not have framed — the collision could be behind the bus, or out of view
in rain. Vision then supplies *who*. This is a sensor-fusion argument that also happens to be
much more robust than a vision-only trigger, and it is cheap.

**Confidence for incidents means something different.** For a pothole, confidence is "how sure
is the detector that this is a pothole." For an incident it is a *composite* over the
kinematic triggers, and — crucially — the `triggers` block ships with it, so a reviewing
operator sees **why**: `ttc_s: 0.6, lateral_accel: 4.2 m/s², ego_impact: 2.1 g`. A number
alone is not evidence. The numbers behind it are.

### ANPR sits here

Registration extraction runs **only** on an incident's subject track, never continuously. This
is deliberate and it is a privacy position as much as a compute one: ARGUS does not read the
plate of every vehicle it passes. It reads the plate of a vehicle involved in a detected
incident, and it logs that it did so. Method is in [`03`](03-cv-pipeline.md#anpr).

---

## Vehicles and pedestrians — the counting classes

These are not "detections" in the work-order sense; they feed the analytics in
[`07`](07-analytics-methods.md). They are listed here because the class list has an
India-specific requirement that is easy to miss.

| Class | Why it must be its own class |
|---|---|
| `car` | — |
| `two_wheeler` | ~35–45% of Bengaluru's vehicle mix. A model that lumps these with cars is useless for density. |
| `auto_rickshaw` | **Not in COCO.** Must be trained. Also has a distinct PCU factor and distinct behaviour near stops. |
| `bus` | Needed to exclude our own fleet from counts and to detect bunching. |
| `truck` / `lcv` | Different PCU weights; goods-vehicle movement is its own planning question. |
| `bicycle` | Vulnerable road user, feeds `near_miss_vru`. |
| `pedestrian` | Crowd density, crossing events, school-zone signal. |

**COCO-pretrained weights get you roughly half of this list.** The auto-rickshaw gap alone
forces custom training, which is why the Indian Driving Dataset is in the data plan rather
than as a nice-to-have. PCU equivalency factors follow IRC:106 so that density numbers are
comparable to what a traffic engineer already computes.

---

## Confidence is calibrated, not raw

Every `confidence` in `contracts/` is documented as *"a calibrated probability, not a raw
softmax score"*, and that is a commitment with work behind it.

A detector's raw output is systematically overconfident. Left uncalibrated, a 0.90 does not
mean "right 90% of the time", which makes every downstream threshold arbitrary and makes
log-odds fusion mathematically wrong. We apply **temperature scaling** fitted on a held-out
validation set, and report a reliability diagram in the model card.

This matters more here than in a typical detection project because fusion *accumulates*
confidence across observations. Accumulating miscalibrated evidence compounds the error
rather than averaging it out.

---

## Adding a class

1. Add it to `DetectionClass` in `contracts/schemas/common.schema.json`.
2. Bump the schema **minor** version; regenerate Python and TS types.
3. Add its row to this document — method, why-not-the-obvious, severity, action.
4. Add its recommended action and SLA to `backend/argus_api/fusion/actions.py`.
5. Add its colour and glyph to the frontend token file.
6. Decide whether it is edge-emitted or ledger-inferred, and whether it belongs in
   `inspected_for` (i.e. whether its *absence* is meaningful).

Step 6 is the one people forget, and it's the one that determines whether the class can ever
produce a false "missing" or a premature auto-resolution.
