# Factory Machine Observation Contract

`factory/machine-observation` is the single public machine-readable envelope
used by Chemvas, orca_auto, ollama_bot, and Chemleaf. This repository is the
versioned source of truth for the envelope schema, registered routes, payload
schemas, semantic validator, and conformance fixtures. Each durable operation
generation or delivered package exposes exactly one public metadata file named
`machine.json`.

Product-owned queue databases, lock files, recovery journals, and mutable state
files are internal implementation details. They are not alternate handoff
contracts and consumers such as Hermes must not parse them.

## Envelope v1

The top level contains exactly these nine fields:

1. `contract` - envelope identity (`factory/machine-observation`, version `1`)
2. `producer` - product name and product version
3. `operation` - producer-unique operation id and registered operation kind
4. `lifecycle` - execution phase, outcome, and stable machine codes
5. `handoff` - whether a downstream consumer may act
6. `delivery` - whether required artifacts were published
7. `artifacts` - artifact receipts keyed by stable artifact id
8. `lineage` - immediate consumed upstream envelopes
9. `payload` - one registered domain payload, or `null`

The normative JSON Schema is
[`schemas/machine-observation-v1.schema.json`](schemas/machine-observation-v1.schema.json).
Registered producer/operation/payload routes, payload keys, artifact-reference
closure, and payload readiness predicates are listed in
[`registry.json`](registry.json). Envelope v1 is frozen: registering new routes
and payload contracts is the only kind of growth it accepts, and changing the
meaning of anything already registered belongs to a new envelope version.
[COMPATIBILITY.md](COMPATIBILITY.md) states which changes qualify, how releases
are numbered, and how producers and consumers advance their pins.

`chemistry/elementary-step` has two registered versions. Version 2 renames
`endpoint_pair` to `endpoint_geometry` and never carries an arrangement of
separate molecules: a multicomponent endpoint is handed off as its individual
components, and placing them relative to each other is left to the consumer.
Its readiness predicate requires `endpoint_geometry` to be non-null. Version 1
stays registered so existing generations remain verifiable. Chemvas defines the
interior layout of both versions; the validator does not inspect it, so a ready
v2 envelope does not establish that its geometry is well formed.

## Required Semantics

- `queued` and `running` observations use `pending` for lifecycle outcome,
  handoff, and delivery.
- `finished` observations use a terminal lifecycle outcome and non-pending
  handoff and delivery states.
- `handoff.status == "ready"` requires `finished`, `succeeded`, `complete`, a
  non-null payload, and the payload contract's own readiness predicate.
- Lifecycle outcome and delivery are independent. A successful calculation
  whose required publication failed is `succeeded / blocked / incomplete`.
- Delivery is not independent of the artifact receipts. `complete` requires
  every receipt marked `required` to be `available`, and `incomplete` requires
  at least one that is not, so the failed publication above is recorded as a
  required receipt rather than left out of the table.
- `uncertain` is reserved for an operation or commit result that cannot be
  established. It is not a synonym for incomplete delivery.
- An `available` artifact receipt carries a package-root-relative POSIX path,
  media type, byte count, and SHA-256 of the exact bytes.
- Payloads refer to files by artifact id. They do not repeat paths, sizes, or
  hashes.
- Lineage lists only immediate upstream `machine.json` envelopes actually
  consumed, identified by producer, operation id, and the exact SHA-256 of the
  upstream envelope's bytes. Entries must be distinct; the hash itself is a
  producer obligation, since resolving it would require the upstream package.
- A terminal `machine.json` is published after all artifacts and is immutable.
  A later hash mismatch means the package is corrupt; producers do not rewrite
  the observation to report that corruption.

Human HTML, XYZ, CSV, Markdown, logs, and document binaries may accompany
`machine.json`; they are not additional machine metadata files.

## Validation

Run everything CI enforces — lint, format, and the schema, registry and semantic
fixture checks — with:

```bash
make check
```

It selects a Python that can import `jsonschema.Draft202012Validator` and run
`ruff`, so it does not depend on how the calling shell resolves `python3`. Set
`PYTHON_BIN` to choose one yourself.

Validate a producer generation, including the required basename and every
available artifact's exact bytes, with:

```bash
python3 scripts/validate.py --machine path/to/generation/machine.json
```

### Human-readable output and errors

The default output reports every explicit input in order, followed by a batch
summary. For example, this hand-authored fixture passes the **envelope** checks
while declaring a blocked handoff:

```text
Validation scope: envelope (artifact files NOT checked)

[VALID] fixtures/chemvas-blocked.json
  Producer: chemvas 1.0.0
  Operation: chemistry/elementary-step-export (step-S02-a1fce4363854ff88)
  Payload: chemistry/elementary-step v1
  Lifecycle: finished / succeeded
  Delivery: complete
  Handoff: blocked
  Handoff codes: chemvas/multicomponent_precomplex_geometry_not_provided
  Next: Do not continue downstream; inspect the producer's handoff codes and evidence.

Summary: 1 valid, 0 failed (1 inputs).
Validation is not execution success or authorization for downstream work.
```

`[VALID]` means contract validation passed, not that the operation succeeded or
downstream execution is authorized. Each status axis and its codes remain
separate. Without `--machine`, artifact files are not checked. With `--machine`,
available artifacts are checked, but consumer context and product-specific
acceptance still belong to the caller.

`[FAIL]` shows the error type/message and a `Next:` hint instead of a traceback
for known validation/read errors. Later inputs are still checked. A failure can
come from an input **or the validator's schema/registry resources**; it does not
always mean the input is invalid. Hints never repair files or authorize work.
Non-printable characters in displayed fields are escaped to keep results on
their own terminal lines.

Both successful results and known failures go to stdout. Exit `0` means all
inputs validated, `1` means at least one validation/read failure, and `2` means
an argument error (usage on stderr). Unexpected internal/dependency failures
may prevent a report and must not be treated as successful validation.
`--help` explains the scope and exit codes.

This replaces the old `ok: PATH` / first-error traceback presentation. Human
wording is not a parsing contract; automation should use the `--json`
report below, not scrape these lines.

### JSON reports for consumers

Add `--json` to obtain one report for all explicit input paths, in input order:

```bash
python3 scripts/validate.py --machine --json path/to/generation/machine.json
```

The report has `report_version: 1`, `scope`, aggregate `valid`, and `results`.
Each result contains `path`, `valid`, `observation`, and `error`. On success,
`error` is null and `observation` contains the validated `contract`, `producer`,
`operation`, `lifecycle`, `handoff`, and `delivery` blocks, plus
`payload_contract` (the payload's name/version block, or null). On failure,
`observation` is null and `error` contains `code` and a human-readable `message`.
No unvalidated status is presented as an observation.

- `scope: "package"` selects `--machine` validation. For a valid result, the
  basename and every available artifact's containment, byte count and SHA-256
  passed as well as the envelope checks. A failed result may stop earlier.
- `scope: "envelope"` selects only JSON structure and registered semantic
  checks. Even a valid result does **not** establish that artifact files exist
  or match their receipts. This mode is useful for the hand-authored fixtures.
- Exit `0` means every input is valid; exit `1` means at least one input failed.
  Known validation/read failures do not prevent checking the remaining inputs.
  Require both exit `0` and `valid: true` for an all-valid batch; never use only
  the first result to decide that a batch passed.
- Error codes are `contract_error`, `json_error`, `encoding_error`, and
  `io_error`. They describe the failure type, including failures reading the
  validator's own schema/registry resources; they do not always blame the
  submitted file. Message text is diagnostic, not a field to parse.
- Argument errors use argparse's stderr and exit `2`, without a JSON report.
  Dependency or unexpected internal failures may also prevent a report. A
  missing/malformed report or nonzero exit is never successful validation.

`valid` is contract conformance, not execution success. A blocked Chemvas
export, an uncertain ORCA run, or a failed document run with delivered output
can all be valid observations. Keep `lifecycle`, `handoff`, and `delivery`
separate. Even a package-validated `handoff.status: "ready"` establishes only
the registered readiness rules: callers must still bind the expected
producer/operation/payload, obtain execution authorization, and apply any
product-specific scientific acceptance checks.

The summary comes from the same parsed document used for validation. The
report is a read-only stdout diagnostic, not another producer envelope, an
immutable evidence store, or a guarantee that files stay unchanged after the
check. It does not discover generations, resolve lineage, repair files, or
launch downstream work. Neither output mode changes a v1 validation verdict.
Its compatibility class is `none` because the accepted envelope language is
unchanged; default human text has deliberately changed as described above.
The report version is independent of the envelope version. Pin this validator,
its registry and schemas to the same immutable commit when consuming reports.

The validator needs `jsonschema`, listed in
[`requirements.txt`](requirements.txt). No product depends on this repository at
runtime, and none needs `jsonschema` in order to produce or consume the
envelope. Each producer CI checks out this repository at an immutable commit,
runs an actual emitter example, and passes the resulting `machine.json` to this
validator. Contract updates therefore land here first, then each producer
deliberately advances its pinned commit and conformance test.

The schema alone is weaker than this validator, so reproducing the verdicts —
not just the schema — is what makes a consumer conforming.
[COMPATIBILITY.md](COMPATIBILITY.md) lists which rules live only here.

Hermes remains a third-party consumer. Its user-local validator bundles the
exact `registry.json` from the pinned contract commit and applies the same route,
artifact-reference, and readiness table without requiring `jsonschema` at
runtime; the Hermes core is not modified.

## How this was built

I'm a chemist, not a programmer. AI coding agents write the schemas, validator and
fixtures in this repository. I decide what the envelope must guarantee, and
[COMPATIBILITY.md](COMPATIBILITY.md) fixes what may still change now that v1 is frozen.

I don't review the code line by line, so a change is accepted on evidence, not on an
agent's report that it works:

- `make check` runs lint, formatting and the schema, registry and semantic fixture
  checks. CI runs the same checks.
- The CI of Chemvas, ORCA_auto and Chemleaf runs each product's real emitter and
  validates the resulting `machine.json` against a pinned commit of this repository,
  so a contract change reaches a product only when that product deliberately advances
  its pin.
