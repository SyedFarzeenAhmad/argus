# 08 — Privacy and compliance

A platform that puts cameras on 6,400 public vehicles and drives them past every road in a city
is a surveillance system unless it is deliberately designed not to be. This document is the
deliberate design.

It is also, pragmatically, a scoring differentiator. A government problem statement is evaluated
in part on deployability, and a system with an unanswered privacy story is not deployable at any
price. Most of what follows costs little to implement and is much harder to retrofit.

---

## The legal frame

| Instrument | Relevance |
|---|---|
| **Digital Personal Data Protection Act, 2023** | Faces and registration marks linkable to an individual are personal data. Governs purpose limitation, minimisation, retention, security, breach notification. |
| **Information Technology Act, 2000** (s.43A, SPDI Rules) | Reasonable security practices for sensitive data. |
| **Motor Vehicles Act, 1988** | Registration marks are public identifiers, but enforcement use has its own evidentiary process. ARGUS produces evidence for that process; it does not adjudicate. |
| BBMP / BMTC operational policy | Actual retention and access rules will be set by the deploying authority. Our defaults are conservative starting points, and configurable. |

The DPDP Act provides broad legitimate-use grounds for State functions, so a municipal
deployment likely has lawful basis without individual consent. **That is a reason to design
carefully, not a reason to skip it.** "We were legally permitted to" is a weak answer to "why
did you retain that?"

---

## Seven design decisions

<a id="face-blurring-deferred"></a>
### 1. Face blurring — deferred, to be done later

> **Decision 2026-09-25.** Face blurring is **not part of the MVP**. We will design and build it
> later, when it is time — most likely in the backend, before any frame is labelled, shared or
> shown. Until then the phones store frames exactly as captured, and every mention of blurring
> elsewhere in these docs is on hold until this section is updated.

What stays true meanwhile: frames never leave the bus over cellular, camera phones store
nothing, and the processing client's frames stay on that phone until someone pulls them
deliberately. When blurring is designed, it needs to cover everything already captured:
`processed/pothole/` crops and frames, and the `dataset/` training frames.

### 2. We do not build the capabilities we do not need

Explicitly out of scope, permanently, not "phase 4":

- Face recognition or matching of any kind
- Age, gender or demographic inference — **including for the school-children clause**, which we
  answer with map and clock data instead ([`docs/02`](02-detection-taxonomy.md#pedestrian_risk))
- Vehicle re-identification across buses or across the city
- Passenger behaviour or emotion analysis from the cabin camera
- Any linkage from a detected person to an identity

Several of these would be *technically easier* than what we are doing instead. Choosing the
harder, narrower path is the position.

### 3. ANPR is narrow and logged

Plate reading runs **only** on the subject track of a detected incident. Not on passing traffic,
not continuously, not for parking or tolling.

Every ANPR invocation writes an audit record: which incident, which device, when, which
operator later viewed it. A platform that *could* read every plate it passes and is configured
not to should be able to prove it didn't.

### 4. The cabin camera does one thing

Not fitted in the MVP (front + rear only). When it is: occupancy count. That is all. At 0.2 Hz, face blurring as in [section 1](#face-blurring-deferred) once it exists, no crop of an
individual passenger ever retained. Occupancy feeds the passenger-hours-lost metric in
[`docs/07`](07-analytics-methods.md#congestion), which is a genuinely useful planning input and
requires nothing but a number.

### 5. Retention is short by default and tiered by purpose

| Data | Default retention | Why |
|---|---|---|
| Evidence crops (defects) | 90 days | Long enough for a work-order cycle and an appeal |
| Incident clips | 1 year, or until case closure | Evidentiary; encrypted at rest |
| Raw `observation` rows | 180 days | Retraining and audit; no imagery after crop expiry |
| `segment_pass`, `telemetry` | 30 days raw | Aggregates persist; raw rows do not |
| Continuous aggregates | Indefinite | **No personal data** — speeds, counts, densities |
| `asset` records | Indefinite | Infrastructure state, not personal data |

The tiering does real work: the analytics that make the platform valuable long-term contain no
personal data at all, so indefinite retention of *those* is unobjectionable. Everything that
could identify a person expires.

### 6. Access is role-based and audited

Three roles — `viewer`, `engineer`, `admin`. **Every access to incident evidence is logged
against a named operator identity**, and the log is queryable by the deploying authority. Bulk
export of evidence requires `admin` and is logged separately.

### 7. Chain of custody is cryptographic

SHA-256 computed on-device at capture, verified at ingest, stored with the record, re-verifiable
at any point. If an incident clip supports an enforcement action, its integrity is demonstrable
rather than assumed.

---

## Minimisation is the architecture, not a policy

The strongest privacy property of this system is a consequence of the bandwidth design rather
than a separate feature:

> **Video does not leave the bus.** Roughly 43 GB of footage per bus per day from two cameras
> is processed and discarded on board. About 12.5 MB of findings leaves.

There is no central archive of everything every bus saw, because there is no link that could
carry it and no storage that could hold it. The thing that makes the system affordable is the
same thing that makes it minimal. That alignment is worth stating in the video — privacy and
cost point the same direction here, which is unusual and persuasive.

---

## Residual risks, stated

Naming these is part of the design being credible.

| Risk | Mitigation | Residual |
|---|---|---|
| Faces in stored frames | Face blurring is deferred ([section 1](#face-blurring-deferred)); frames stay on the processing phone until pulled deliberately | **Open** until blurring is built |
| Plate visible incidentally in a defect crop | Crops are tight to the defect and ground-facing; plates are rarely in frame | Low |
| Re-identification by inference from patterns | We store no track identity beyond a segment pass | Low |
| Mission creep by a future operator | Capabilities absent from the codebase, not merely disabled by config | Requires new development, which is the point |
| Device physical compromise in a depot | Per-device certificates in the edge phone's Android Keystore, revocable individually; no shared secrets | Bounded to one bus |
| A phone is lifted off the bus | Camera phones hold no footage and no credentials; the edge phone's spool is encrypted and its certificate revocable | Bounded to that phone's unsent findings |

That last row is why device identity is a certificate rather than an API key. A bus is a box
parked overnight in a place many people can reach.

---

## What to say if a judge asks

> *"Every camera stays on the bus. What leaves is a finding — a pothole at these coordinates,
> this confident, with one photograph of the road surface. We do not run face recognition. We do not read the plate of
> every vehicle we pass; we read one plate, of one vehicle, in one detected incident, and we log
> that we did. Of 43 gigabytes a bus sees in a day, about 12 megabytes leaves it — and that's
> not a privacy feature we added, it's the same decision that makes the system affordable."*
