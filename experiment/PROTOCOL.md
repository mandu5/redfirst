# Replay study protocol

Written before the main run. Changes after the main run starts are listed
under "Deviations" with the reason and the jobs they affected.

## Question

Coding agents now open a large share of pull requests, and most bug-fix pull
requests come with new tests. Do those tests actually test the fix?

For one pull request the check is mechanical: keep its test changes, undo
everything else, and run the tests it added or edited. If all of them still
pass, none of them observed the behaviour the pull request changed.

Primary outcome: the share of merged agent bug-fix pull requests whose added
or edited tests all pass with the non-test changes undone
(`passes_without_fix`), among pull requests whose tests pass with the change.
The same share for human bug-fix pull requests in the same repositories is
the comparison.

## Sampling frame

- AIDev v5 (hao-li/AIDev on Hugging Face, cutoff 2026-03-31, CC BY 4.0),
  AIDev-pop subset: repositories with more than 100 stars. Agents: OpenAI
  Codex, GitHub Copilot coding agent, Claude Code, Cursor, Google Jules, Devin,
  identified by the dataset's own mining rules.
- Repository language Python, not a fork.
- Pull request merged, created 2025-01-01 to 2026-03-31.
- Bug-fix title: matches `fix|fixes|fixed|fixing|bug|bugfix|hotfix` as a word
  and does not start with a non-fix Conventional Commit type
  (`feat`, `docs`, `test`, `chore`, `refactor`, `ci`, `build`, `style`,
  `perf`, `revert`). Regex in `sample.py`.

## Draw

1. Sort the frame by pull request id and shuffle it with
   `numpy.random.default_rng(20261116)`.
2. Walk the shuffled list, at most one pull request per repository.
3. Keep a pull request when, per the GitHub files API:
   - it changes at most 50 files;
   - it adds or modifies at least one non-test `.py` file;
   - it adds or modifies at least one test module (`test_*.py` or `*_test.py`);
   - it changes no compiled source (C, C++, Cython, Rust, Go, Fortran, CUDA,
     Objective-C, Zig), since undoing those needs a rebuild.
   Test side means a path under a `test`, `tests` or `testing` directory, a
   test module, or `conftest.py`. Everything else is the code side.
4. Stop at 120 kept agent pull requests.

Human match, one per kept agent pull request: in the same repository, merged
within 180 days of the agent pull request, bug-fix title by the same rule,
author is a GitHub user account (not a bot), no agent branch prefix (`codex/`,
`copilot/`, `cursor/`, `claude/`, `devin/`, `jules/` and similar), no agent
marker in the body, not in AIDev's agent list, and the same file rules. Among
those, the one merged closest in time; up to ten candidates are checked.

## Procedure per pull request

1. Base is the merge base of the pull request's `base.sha` and `head.sha`;
   head is `head.sha`.
2. Targets: test functions and methods (pytest naming) added or edited in the
   changed test modules. Edited means the function's syntax tree changed,
   decorators included (a new parametrize case counts; comments do not).
   No targets: `no_new_tests`, not judged.
3. Environment: a Linux container (Debian bookworm, arm64) in a dedicated VM
   that sees only the study folder. Python version from `requires-python`
   (3.12 first, then 3.11, 3.13, 3.10, 3.9, 3.8 as allowed). The project is
   installed editable at head with its test extras, test requirement files,
   dependency groups or tool-specific dev dependencies, resolved with
   `uv --exclude-newer` set to the day after the merge, so dependencies are the
   versions that existed then. Setup has network access.
4. Run the target tests by id at head, network off. If pytest cannot find
   one of the ids it runs nothing; then the whole changed test modules are
   run instead, and the same selection is used in step 5.
5. Undo every code-side change (restore base content, delete files the pull
   request added), keep the test side at head, and run the same selection
   again, network off.
6. Fold each target's cases (parametrized ids) into one outcome, then judge
   only targets that passed at head:
   - `proves_fix`: at least one fails without the change;
   - `fails_by_error`: none fails, at least one errors (import, fixture,
     collection);
   - `passes_without_fix`: all pass without the change;
   - `inconclusive`: otherwise (skipped, xfailed, or the run did not report).
   No target passes at head: `head_not_green`, not judged.
   Setup failure: `env_failed`, not judged. Harness exception: `harness_error`.

Judged = `proves_fix`, `fails_by_error`, `passes_without_fix`, `inconclusive`.

## Analysis

- Primary: `passes_without_fix` / judged, agent pull requests, Wilson 95%
  interval. The same for human pull requests.
- Paired: repositories where both the agent and the human pull request are
  judged; counts of the four combinations and the difference in shares.
- Secondary, descriptive only: by agent; per test (share of judged target
  tests that pass without the change); `fails_by_error` share.
- All 120 agent jobs and all human matches are run. No re-draws based on
  outcomes, no early stop.
- Infrastructure reruns: a job whose setup or test run hit the time limit, or
  that ended in `harness_error`, is rerun once (the host can sleep or lose
  network mid-run). The second result stands.

## Manual review

Every `passes_without_fix` pull request, agent and human, is read (diff and
tests) and labelled:

- `gap`: the new tests never exercise the behaviour the change altered;
- `unobservable`: the change cannot be observed by a unit test (log or message
  text, type hints, performance, a refactor under a fix title);
- `environment`: the bug only shows on another platform, version or service;
- `test_side_fix`: the fix itself lives in a file counted as test side;
- `other`, with a note.

Reported: the raw rate and the rate counting only `gap`.

## Pre-gate for publishing

From the startup ledger (data/startup/weeks.json, 2026-W47): publish the tool
and the write-up only if `passes_without_fix` is at least 10% of judged agent
pull requests. Below that, the week's verdict is a fail on the spot.

## Known limits

- Human authors may use AI tools without saying so; that would shrink any
  difference.
- Bug fixes are found by title, not by reading each pull request.
- Environment heuristics lose samples, in both groups alike. Losses are
  reported by reason.
- One platform (Linux arm64) and one Python per pull request.
- Only tests in the changed test modules are run; other tests that might have
  caught the bug are not.

## Pilot

The first four agent draws and their human matches were run twice to debug
the harness before this protocol was fixed. Round one ran whole test
modules and put temporary files on the shared folder, where pytest's capture
files could not be read back (one job crashed). Round two ran target ids
and kept temporary files inside the container; the six judged verdicts of
round one did not change and the crashed job ran. Pilot results are
discarded; these jobs are rerun in the main run with the frozen harness.

## Deviations

None to the procedure. Added after the main run started, analysis only:
`review.py` (one packet per `passes_without_fix` pull request for the
manual labels) and a descriptive breakdown of `head_not_green` by head
outcome in `analyze.py`. Neither changes a verdict.
