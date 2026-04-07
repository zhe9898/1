# ADR Template

- Status: Draft | Proposed | Accepted | Superseded | Rejected | Archived
- Date: YYYY-MM-DD
- Scope: Short description of the boundary affected by this ADR

## 1. Context

Describe the current repository reality that makes this decision necessary.

Prefer concrete evidence over aspiration:

- current code paths
- existing runtime behavior
- migration or compatibility pressure
- operational or security risks

If the decision is still only a proposal, say so explicitly.

## 2. Decision

State the decision in direct, testable language.

Use flat bullets when needed:

- what is now the accepted rule
- what is explicitly not part of the rule
- what remains compatibility-only

## 3. Code Evidence

List the repository locations that justify or enforce this ADR.

- `path/to/file.py`
- `path/to/file.ts`
- `backend/tests/unit/test_example.py`

If no code exists yet, the ADR must not claim the decision is already accepted.

## 4. Consequences

### Positive

- What this ADR clarifies
- What drift or risk it reduces

### Tradeoffs

- Migration cost
- Compatibility cost
- Follow-up work still required

## 5. Follow-up

Record the next actions needed to keep the ADR honest.

- migrations still required
- tests or gates still missing
- future ADRs that would supersede this one

## 6. Source-of-Truth Rule

Repository truth is ordered as follows:

1. implementation and exported code contracts
2. tests and enforcement gates
3. ADR text and design notes

If ADR text conflicts with code, fix the code-backed contract first or downgrade the ADR status.
