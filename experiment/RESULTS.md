# Do the tests in bug-fix pull requests test the fix?

A replay of 120 merged bug-fix pull requests opened by coding agents and
89 human bug-fix pull requests in the same repositories. The protocol,
including the threshold for publishing this, was written before the main run:
[PROTOCOL.md](PROTOCOL.md).

## What was run

- Pull requests: from the AIDev dataset (hao-li/AIDev on Hugging Face, v5,
  CC BY 4.0), Python repositories with more than 100 stars, merged between
  January 2025 and March 2026, bug-fix titles, one pull request per
  repository. Each had to change at least one non-test Python file and at
  least one test module, and no compiled sources. The agents are the ones
  AIDev identifies: Claude Code (76), Copilot (22), Codex (17), Devin (3) and
  Cursor (2).
- Human comparison: in the same repository, the human bug-fix pull request
  merged closest in time (within 180 days) under the same rules. 89 of
  the 120 repositories had one.
- For each pull request, a Linux container installed the project at the pull
  request head with the dependency versions available the day after the
  merge, ran the tests the pull request added or edited with the network
  off, then put every non-test file back the way it was before the pull
  request and ran the same tests again.
- A pull request is judged when at least one of its new tests passes with
  the change. Among judged pull requests, `passes_without_fix` means every
  new test that passed with the change also passed without it.

## Results

Verdicts, one per pull request:

| verdict | agent | human |
| --- | --- | --- |
| `proves_fix` | 56 | 46 |
| `fails_by_error` | 3 | 6 |
| `passes_without_fix` | 10 | 5 |
| `head_not_green` | 27 | 14 |
| `env_failed` | 17 | 14 |
| `no_new_tests` | 7 | 4 |
| total | 120 | 89 |

The comparison. Shares are of judged pull requests, with Wilson 95%
intervals. The per-test row has no interval, because tests cluster within
pull requests.

| | agent | human |
| --- | --- | --- |
| judged | 69 | 57 |
| all new tests pass without the change | 10 of 69 (14.5%, 95% CI 8.1 to 24.7%) | 5 of 57 (8.8%, 95% CI 3.8 to 18.9%) |
| of judged, labelled `gap` | 6 of 69 (8.7%, 95% CI 4.0 to 17.7%) | 3 of 57 (5.3%, 95% CI 1.8 to 14.4%) |
| new tests that pass without the change, per test | 188 of 400 (47.0%) | 120 of 419 (28.6%) |
| `fails_by_error` | 3 of 69 (4.3%, 95% CI 1.5 to 12.0%) | 6 of 57 (10.5%, 95% CI 4.9 to 21.1%) |

Repositories where both the agent and the human pull request were judged:

| both judged, same repository | count |
| --- | --- |
| pairs | 45 |
| both pass without the change | 3 |
| only the agent pull request | 3 |
| only the human pull request | 1 |
| neither | 38 |
| exact McNemar p | 0.625 |

Judged agent pull requests, by agent:

| agent | judged | pass without the change |
| --- | --- | --- |
| Claude Code | 43 | 8 |
| Copilot | 14 | 1 |
| Codex | 10 | 1 |
| Devin | 2 | 0 |

Pull requests where no new test passed with the change (`head_not_green`),
by what the new tests did:

| why no new test passed with the change | agent | human |
| --- | --- | --- |
| error | 18 | 13 |
| failed | 2 | 1 |
| skipped | 5 | 0 |
| missing | 2 | 0 |

Agent pull requests passed without the fix more often, 14.5% against 8.8%,
but the intervals overlap and the paired comparison is far from
significant. This study does not show a difference between agents and
people; it shows the same problem in both. Per test the difference is
wider (47% against 29%), but a new test that passes without the change is
often fine when another new test in the same pull request fails without
it, so the verdict per pull request is the measure. The bar the protocol
set for publishing, 10% of judged agent pull requests, was met.

## What the tests missed

| label | agent | human |
| --- | --- | --- |
| `gap` | 6 | 3 |
| `unobservable` | 3 | 0 |
| `environment` | 0 | 2 |
| `test_side_fix` | 0 | 0 |
| `other` | 1 | 0 |

Of the 15 pull requests whose new tests all pass without the fix, 9
changed something a unit test could have checked (`gap`: 6 agent, 3
human). Three patterns cover them:

- The tests run the changed code but check nothing that changed: no
  assertion, every exception caught, a function that returns `True`, or a
  call whose output is never compared (2 agent, 1 human).
- The tests check a neighbouring behaviour: another function or module
  than the one changed, another property of the result, or inputs the old
  code already handled the same way (3 agent, 2 human).
- An existing test was edited to follow the code: a hard-coded default was
  replaced by the configuration value it was compared with, so the test
  passes for any default (1 agent).

The other 6 are not testing gaps in this sense. Three agent pull requests
changed no runtime behaviour (imports moved to a public module, formatting
and lint, type-only fixes for a mypy upgrade). Two human pull requests
changed code that only runs on Windows, macOS or a newer Python than the
container's. One agent pull request changed only a benchmark script that
the tests never import, and the docs. Two existing tests, one per group,
were edited so that they hold for both the old and the new code: the
default above, and a path compared with the same path made absolute.

Every `passes_without_fix` pull request, agent and human, was labelled from a
blind packet: the title, the per-test outcomes and the diffs, with the group,
the agent and the link removed. Two LLM readers (Claude, separate sessions)
labelled every packet with the rubric in [review/RUBRIC.md](review/RUBRIC.md),
without seeing each other's labels; they gave the same label to 14 of the 15 pull requests, and agreed on 13 of 15 about whether an existing test had been edited to accept both the old and the new code. Where they disagreed, a third read
of the diffs decided. That read was done by the session that ran the study,
which knew the case names, so it was not blind; neither disagreement
changed the `gap` count (one was `unobservable` against `other`, the other
only the edit flag). Both readers' labels, the final label and a short
reason for each pull request are in [review/labels.json](review/labels.json),
so each one can be checked.

## Deviations from the protocol

1. The main run started with harness v1 and was stopped after 27 agent jobs,
   when two runner defects showed up (the environment's `bin` directory was
   not on `PATH`, and the container user had no passwd entry). Harness v2
   fixed them and closed environment gaps, and every job was rerun with it.
   On those 27 jobs no judged verdict changed between v1 and v2; seven
   jobs that v1 could not judge became judged (five `proves_fix`, two
   `passes_without_fix`). The full list is in
   [results/v1.csv](results/v1.csv).
2. Two analysis-only additions: the review packets and a breakdown of
   `head_not_green` by what happened at head.

Infrastructure reruns: two agent jobs hit the time limit, one with the change
and one without it. Each was rerun once and ended the same way. No human job
needed a rerun.

## Limits

- Human authors may use AI tools without saying so, which would shrink any
  difference between the groups.
- Bug fixes are found by title, not by reading each pull request.
- Environment heuristics lose pull requests in both groups:
  `env_failed` and most `head_not_green` are setups that did not
  reproduce the project's CI. Losses are listed by reason above.
- One platform (Linux arm64) and one Python version per pull request.
- Only the tests in the changed test modules run. Other tests in the
  repository might have caught the bug.
- The labels come from two reads by the same model (Claude), not from the
  projects' maintainers, and the read that settled two disagreements was
  not blind.

## Data

- [results/summary.json](results/summary.json): counts, shares and intervals, from `analyze.py`.
- [results/prs.csv](results/prs.csv): one row per pull request with its verdict and label, from `export.py`.
- [results/v1.csv](results/v1.csv): harness v1 against v2 on the first 27 jobs.
- [sample/](sample/): the drawn pull requests and the draw log, from `sample.py`.
- [review/labels.json](review/labels.json): the labels.

To rerun a pull request: build the image from [Dockerfile](Dockerfile) and
run `python run.py sample/jobs_agent.jsonl --only <id>`. The run takes the
repository from GitHub; untrusted code only runs inside the container.
