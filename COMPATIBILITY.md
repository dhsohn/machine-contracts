# Compatibility policy

`factory/machine-observation` version 1 is frozen as of `v1.0.0`. This file
states what that covers, which changes are still accepted, how releases are
numbered, and how producers and consumers move to a new commit.

The contract is small on purpose. Four products emit it and a third-party
consumer reads it, so an unstable envelope is paid for again in every one of
them. This repository is meant to be the least eventful one in the factory.

## What defines a verdict

An envelope conforms when [`scripts/validate.py`](scripts/validate.py) accepts
it while reading [`registry.json`](registry.json) and the schemas from the same
commit. All three together are normative; none of them alone is. The JSON
Schema in particular is strictly weaker than the validator — the section below
lists what only the validator enforces — so a consumer that implements its own
checker is conforming only if it reproduces the validator's verdicts.

One such reimplementation exists: the Hermes user-local skill vendors
`registry.json`, carries no JSON Schema, and rewrites the semantic layer in
dependency-free Python. It reads four registry blocks — `contract`, `producers`,
`routes`, `payload_contracts` — so those are consumer-visible whether or not
this validator touches them. It guards its vendored copy with a recorded
SHA-256, which catches local tampering but never notices that this repository
moved. Keeping the two implementations in agreement is manual: no automated
check runs both over the same input, so a change to what this validator accepts
has to be mirrored there deliberately.

## Where v1 is closed and where it is open

Getting this boundary right decides most classification questions, so it is
worth stating exactly.

Closed, and frozen:

- The envelope itself and each of its fixed-shape blocks — `contract`,
  `producer`, `operation`, `lifecycle`, `handoff`, `delivery`, `lineage`,
  `payload`, and every artifact receipt — are `additionalProperties: false`. A
  tenth top-level field cannot be added to v1.
- The envelope's status vocabularies, in both directions: `lifecycle.phase`,
  `lifecycle.outcome`, `handoff.status`, `delivery.status`,
  `artifacts.*.status`. Consumers switch exhaustively on these, so a new value
  breaks them the first time it is emitted even though no existing envelope
  changes verdict.
- The conditionals binding those vocabularies: the pending triple for `queued`
  and `running`, the terminal sets for `finished`, and `ready` implying
  finished, succeeded, complete delivery, and a non-null payload.
- The identifier and code grammars in `$defs`, and the artifact path rule.
- Each registered payload contract: its name, version, required keys, readiness
  predicate, artifact-reference descriptor, and its own closed vocabularies —
  `result_kind`, `applied`, and `target.kind`.

Structurally constrained rather than closed: `artifacts` is a map whose keys
follow the identifier grammar and whose values must be receipts, and
`payload.data` is delegated to the registered payload schema.

Open, and deliberately so: each payload schema is closed only at its top level.
These properties are declared as bare objects with no inner constraints —
`source`, `reactant`, `product`, `atom_correspondence`, `bond_changes` and
`geometry_scope` in both versions of `chemistry/elementary-step`, `summary`
and `results` in `chemistry/results-bundle`, `metadata` in
`document/paper-pack`, `pages` in both `document/patch-*`, and
`paragraph_notes` in `manuscript/revision-report`.

**Those interiors are where v1 grows.** A producer may put new structure inside
them with no change to this repository and no pin advance, which is how most
domain evolution should happen. The trade is that the validator cannot check
any of it: growth there is invisible to conformance, so its shape is the
producer's responsibility and its meaning has to be agreed with consumers
outside this contract.

Explicitly not frozen: the validator's internal structure, its error message
text, the fixture set, the test suite, and the wording of documentation.

## What a passing validation proves

Machine-checked, and therefore what conformance actually establishes: rejection
of duplicate JSON keys anywhere in the file; field presence and closure; the
vocabularies and their conditionals; delivery consistency against the required
artifact receipts; lineage entry uniqueness; route registration; route
requirements; payload schema conformance; artifact-reference closure; and
readiness predicates. Everything after "delivery consistency" in that list is
enforced only by the validator, never by the schema. Running with `--machine`
adds the checks that need the package on disk: the `machine.json` basename,
artifact containment inside the package root, and the byte count and SHA-256 of
every `available` artifact.

Producer obligations bind a conforming producer just as firmly, but no
validator here can see them: exactly one `machine.json` per generation or
delivered package; `operation.id` unique within a producer; a terminal
`machine.json` written after its artifacts and never rewritten;
`lineage.upstream` listing only envelopes actually consumed, with hashes that
really are those envelopes' bytes; `uncertain` reserved for a result that
cannot be established; and payloads referring to files by artifact id rather
than repeating paths, sizes, or hashes.

## Change classes

Every pull request declares exactly one class, judged against the current
`main` of this repository together with the emitters as they exist in the four
products.

**none** — no accepted envelope reaches a different verdict and no new surface
is registered. Documentation, tests, fixtures, CI, a validator refactor, error
wording.

**additive** — one of the changes on the closed list below, and nothing else.

**semantic** — the meaning or verdict of an existing construct changes, even
though the JSON still parses.

**breaking** — something accepted today is rejected afterwards, or a registered
element is removed or renamed.

**Semantic and breaking changes are not accepted into envelope v1.** The
question that then matters is which version has to move, and the answer is
usually not the envelope:

- A payload that must change gets a **new payload version**, registered beside
  the old one. At the envelope level that is additive, and it is the normal way
  domain contracts evolve here. This covers widening as well as narrowing: a new
  member of a payload vocabulary such as `result_kind`, or a required key made
  optional, goes to a payload version rather than into v1, because a consumer
  switching exhaustively on that key breaks on first emission either way. The
  version bump is the signal it needs.
- Only a change to the envelope itself — a field, a status vocabulary, a
  conditional, an identifier grammar — needs **envelope v2**, which every
  producer and consumer would move to at once.

An argument that a particular semantic change to v1 is small enough to slip in
is the signal to reach for one of those two instead.

### The additive list is closed

A change is additive only if it is one of these:

1. A new payload contract: a `payload_contracts` entry, its schema file, and at
   least one route reaching it.
2. A new version of an existing payload contract, registered alongside the old
   one rather than replacing it.
3. A new route, provided its
   `(producer, operation_kind, payload_contract, payload_version)` tuple is not
   already registered.
4. A new producer or operation kind, which exists only by way of a route. The
   `producers` and `operation_kinds` arrays are derived summaries and move with
   it.
5. A new optional property in an existing payload schema, not added to
   `required`.

Anything else that changes what the validator accepts is semantic or breaking,
including cases that look harmless: adding a value to a closed vocabulary,
adding a `ready_requirements` or route `requirements` entry, adding an
`artifact_references` block to a contract that lacks one, relaxing an existing
predicate, or making an optional key required.

Two constraints on the list itself. A second payload contract registered for a
`(producer, operation_kind)` pair that already has one must be at least as
strict as its siblings — the same or stronger route requirements, an
`artifact_references` block if any sibling has one, and a readiness predicate no
weaker — because an envelope may otherwise take the laxer route and escape an
invariant this document calls machine-checked. And item 5 is rarely the right
tool: a new top-level payload key must be registered here before any producer
may emit it, while the open interiors described above need no registration at
all.

A change of any class must leave CI green. When an additive or none-class
change reddens a test, the change is incomplete — update the test in the same
pull request. Test breakage is not evidence for a different class.

## Registered surface is append-only

A terminal `machine.json` is immutable and must stay verifiable for as long as
its package is retained. Deregistering a payload contract, a payload version, or
a route therefore invalidates history and counts as breaking rather than as
tidying up, which is why superseded registrations are simply kept. A retired
schema file costs a few kilobytes, and a validator that still accepts an old
payload version costs nothing. There is no compatibility window and no shim
here because none is needed.

The exception is a construct that was registered and never emitted. Removing it
cannot change any envelope that exists, so it is classified by its actual
effect like any other registry edit rather than as breaking. The pull request
states how "never emitted" was established.

## Releases

Releases are annotated git tags on this repository. Each part of the version is
tied to something real: **major** is the envelope contract version, so releases
are `1.y.z` while `contract.version` is `1`; **minor** is an additive change
from the closed list; **patch** is a none-class change.

`v1.0.0` tags commit `bc252035d01edddf1314e6641689c6d5cb88af92`, cut on
2026-08-09 and pinned by Chemvas, orca_auto, ollama_bot, and LLMdocx on
2026-08-10. Tagging it after the fact is deliberate: the first release names the
contract that is actually deployed rather than a newer state nobody is running.

## Pins

Each producer's CI checks out this repository at an **immutable commit SHA**,
never a tag or a branch, and runs the canonical validator against the output of
a real emitter. Tags exist so people can talk about versions; the SHA is what
gets verified.

The order is always: land the contract change here, cut a release, then let each
product advance its pinned SHA in its own pull request. The order is not
etiquette. Payload schemas are closed at the top level, so a producer that emits
a newly registered top-level payload key before the commit registering it fails
its own conformance check. Content added inside an open interior gets no such
signal, which is another reason to prefer it.

Three rules follow.

- **Producer pins may lag, and that is safe.** A product still pinned to an
  older commit validates correctly, because v1 only ever grows. That property is
  what the freeze buys, and it is the reason a semantic change cannot be allowed
  to slip in: it would silently invalidate every lagging pin.
- **A consumer's pin must not lag behind the producer it reads.** A consumer
  holding an older registry rejects a payload contract or route registered after
  its pin as unregistered. When a release registers new surface and a producer
  starts emitting it, every consumer of that producer resyncs first. The Hermes
  skill cannot detect that it is stale, so a release registering new surface is
  not finished until its copy is refreshed by hand.
- **Additive on paper is not free in practice.** The one product that reads
  other generations, `orca_auto`, compares key sets exactly and skips an
  envelope it does not recognise instead of failing loudly, so a newly
  registered top-level payload key would quietly drop generations from resume
  and workflow lineage the moment one was emitted. A pull request that registers
  new surface names the readers that must be updated first.

Whether a change reaches consumers at all is decided by the validator's read
path: `schemas/`, `registry.json`, `scripts/validate.py`, and
`requirements.txt`, which producer CI installs from the pinned checkout before
running the validator. A commit touching none of those is invisible to every
consumer and needs no pin advance anywhere; a dependency bump looks like
plumbing but is not, because it changes what four CIs install once their pins
move.

This repository's CI cannot verify any of it. It lints and runs the fixture
suite; whether the products still conform after a contract change is
established by the author, in the pull request, before merging.

## Scope of this repository

Belongs here: the envelope schema, payload schemas, the registry, the semantic
validator, conformance fixtures, and this policy.

Does not belong here: workflow execution, product-specific file discovery,
latest-generation lookup, orchestration, automatic repair, ledger or database
state, and model calls. A rule that only one product needs belongs to that
product.

New registrations follow demand rather than symmetry. A payload contract is
registered when a real producer will emit it *and* a real consumer will read
it. Adding a route so that a repository resembles its siblings is not a reason.

The files in [`fixtures/`](fixtures) are hand-authored contract examples with
placeholder hashes, not captured production output, and they do not cover every
registered route. Conformance against real emitters is checked where the
emitters live: each producer's CI runs an actual generation through the
validator at the pinned commit. Copying real generations in here would add a
refresh chore for no gain, since `producer.version` changes with every product
release.

## Accepted limitations of v1

Recorded so they are not rediscovered and relitigated. Each would need the
validator to accept less than it does today, which v1 does not allow, so each
waits for an envelope v2 that has an independent reason to exist.

- `artifacts.*.role` is a free-form identifier: nothing rejects an unregistered
  role, and `artifact_roles` is the one registry block no implementation reads.
  Roles guide consumers and do not affect handoff, delivery, or artifact bytes.
- `lineage.upstream[].byte_sha256` is checked for shape and for entry
  uniqueness, never against the referenced envelope, and the uniqueness key
  ignores `producer.version`. Verifying a chain needs the upstream package,
  which the validator does not resolve.
- Terminal immutability and publication order are producer obligations. The
  validator sees one generation at a time.
- A null payload skips route requirements, the payload schema, and reference
  closure entirely, even on a route that declares a payload contract.
- Artifact-reference closure runs only for contracts that declare an
  `artifact_references` block, and only one way: referenced ids must exist among
  the receipts, but a receipt referenced by nothing is legal.
- Only `available` receipts are byte-verified. A `missing` receipt may carry a
  wrong hash without objection.
- The requirement mini-language supports `equals` and `not_null` and cannot
  traverse arrays, so a readiness predicate over a list is not expressible.
