# Debugging notes

Findings worth keeping, with enough detail to reconstruct the reasoning later.

---

## Float tolerance in the budget guard

**Symptom.** `test_unbundle_keeps_expensive_card_that_proves_its_price` failed: a $40 synergy card was dropped from a deck that was nominally within budget. Phase 2's unbundle audit had correctly ruled the card kept its slot (`stats["unbundles"] == 0`), so something downstream removed it.

**How it was found.** Reading the code didn't reveal it — the logic looks correct. Instrumenting the actual runtime values did:

```
budget      = 40.0 + 0.20*2   = 40.4
deck total  = 40.0 + 0.20 + 0.20 = 40.400000000000006
budget_left = -7.105427357601002e-15
stats: {'upgrades': 0, 'unbundles': 0, 'downgrades': 1, 'final_total': 12.4}
```

**Root cause.** In binary floating point `0.20 * 2` is exact, but `40.0 + 0.2 + 0.2` accumulates one ULP high. The Phase 3 downgrade safety net guarded on a bare comparison with no tolerance:

```python
while total_price(deck) > budget:      # greedy_optimizer.py:746 (pre-fix)
```

A 7-femtodollar overage satisfied that condition, so the safety net fired and shed the $40 engine to buy back $28 of headroom that was never actually needed.

**Why it mattered beyond the test.** This is production logic, not a test artifact. Any deck whose card prices happen to sum one ULP above the requested budget triggers a full downgrade cascade. The test author had already used tolerance in the *assertions* (`<= budget + 1e-9`, `tests/test_rebalance.py:189`) but the guard itself had none — the invariant was stated with tolerance and enforced without it.

**Fix.** A documented module constant, and tolerance on the guard:

```python
_PRICE_EPSILON = 1e-6                                  # greedy_optimizer.py:40

while total_price(deck) > budget + _PRICE_EPSILON:     # greedy_optimizer.py:746
```

`test_downgrade_safety_net_fixes_overrun` (`tests/test_rebalance.py:231`) still passes unchanged, confirming the mechanism is intact for genuine overruns — this suppresses the rounding artifact without disabling the safety net.

**Note on history.** `git log -S` shows the test and the Phase 3 guard landed in the same commit (`f428131`, "Make price a constraint, not a quality signal"). Checking out that commit and running it confirms the test failed there too. It had never passed. This is the case for CI in a sentence: a test that has been red since birth is invisible without it.

---

## Test isolation: production database leaking into synthetic fixtures

**Symptom.** A clean clone reported `2 failed, 749 passed, 19 skipped`. Running the identical suite a second time in the same tree reported `15 failed, 748 passed, 1 skipped, 6 errors`. The suite degraded across runs.

**Root cause.** Two layers, and the first diagnosis was incomplete.

*First layer.* `test_health_monitor_init` (`tests/test_ingestion.py:187`) pointed at the production database path directly rather than using `_make_edhrec_db(tmp_path)` (`tests/test_ingestion.py:60`, whose DDL creates `source_health` at line 78) like every other test in the file. `sqlite3.connect()` **creates** a file on open, so the test left behind a 0-byte `data/sabermetrics.db`.

*Second layer.* Removing that alone did not stop the file reappearing. `test_generators.py` also created it. It passes the production path at 23 call sites, and 16 of those hand it to a generator that `sqlite3.connect()`s it inside a try/except fallback — ramp (6, `ramp.py:234`), protection (6, `protection.py:224`), removal (4, `removal.py:256`). The connect succeeds and creates the file; the subsequent query fails on the missing table; the generator silently falls back to the caller-supplied `role_tag_pool`. The tests pass either way, so nothing ever surfaced. The other 7 sites are Draw and Land, which store `db_path` and never open it.

*The cascade.* DB-dependent tests are gated four different ways — a module-level `HAS_DB = DB_PATH.exists()`, a direct `skipif(not DB.exists())`, an inline `pytest.skip("no local DB")`, and a bare early `return` — and all four test for existence, not validity. Once a 0-byte file existed, 18 tests that should have skipped ran against an empty database instead: 12 failed and 6 errored.

The fourth style deserves its own note. `test_analytics.py::test_apply_hard_filters_integration` guards with `if not db_path.exists(): return` (`tests/test_analytics.py:117-119`) rather than a skip, so with no database it reports as *passed* — a test that silently does nothing and calls it success. It was the nineteenth casualty and the only one that had been green: 13 failures and 6 errors in total, and the reason the passed count moves 749 to 748.

**The more serious finding.** On a machine with a populated database, the 16 ramp, protection and removal tests were not falling back at all — they were silently merging real database rows into what was supposed to be a synthetic in-memory pool. They passed, but for the wrong reason, and their behavior depended on the state of local data that is gitignored and therefore differs per machine. (The Draw and Land tests were always hermetic; they never open the path they are handed.) CI would never have caught this, because CI has no database. It only surfaces when someone looks.

**Fix.** All 23 sites now point at a shared sentinel whose parent directory does not exist:

```python
_NO_DB = Path(__file__).parent / "_nonexistent" / "sabermetrics.db"   # tests/test_generators.py:30
```

`sqlite3` raises `OperationalError` rather than creating anything — which is the already-documented fallback path — so the tests are hermetic and deterministic on populated and empty machines alike. `test_health_monitor_init` now uses `_make_edhrec_db(tmp_path)`, which guarantees an empty `source_health` table and lets the assertion be exact (`report == []`) rather than a tautology (`isinstance(report, list)`).

**Verification.** Three consecutive full runs, since the failure mode was degradation across runs rather than a single red result. Stable at `751 passed, 19 skipped`.

---

## Known open issue

`greedy_optimizer.py:768`, Phase 3 swap selection:

```python
if loss < worst_loss and saving >= min(over, saving):
```

`saving >= min(over, saving)` is a tautology — true for every input. The intended constraint (presumably "this swap must actually cover the overage") is dead code, so Phase 3 selects the minimum-objective-loss swap without checking whether it sheds enough, or far too much.

The epsilon fix above stops spurious entry into Phase 3, so this is dormant rather than resolved. Fixing it requires deciding what the constraint was meant to enforce, which is a design decision rather than a mechanical repair. Tracked in [issue #17](https://github.com/DylnMtthws/bristly_billy_beane/issues/17).
