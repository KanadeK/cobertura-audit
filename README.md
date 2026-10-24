# cobertura-audit

**Recompute Cobertura line coverage before trusting a CI threshold.** Detect
inflated totals, stale class/package rates, malformed observations and duplicated
source lines. Offline, read only, Python 3.11+, no runtime dependencies.

If a report declares 100% but its detailed lines show 2 covered out of 3, this
tool fails the report and computes the threshold from **2/3**.

## Install and try

In an activated virtual environment:

```console
git clone https://github.com/KanadeK/cobertura-audit.git
cd cobertura-audit
python -m pip install .
cobertura-audit examples/valid.xml --min-line-rate 0.6
cobertura-audit examples/inflated.xml --min-line-rate 0.6 --json
cobertura-audit examples/valid.xml --min-line-rate 0.8 --json
```

The first command exits **0** with `PASS: 2/3 lines covered; 0 findings`.
The second exits **1** because the declared root counters/rate disagree with
the details. The third exits **1** because 2/3 is below 80%.
`--min-line-rate` is a ratio from 0 to 1, not a percentage from 0 to 100.

Use `python -m cobertura_audit` if the console script is outside PATH. Release
wheels install with `python -m pip install PATH_TO_WHEEL`; this is not currently
published on PyPI. Uninstall with `python -m pip uninstall cobertura-audit`.
The tool creates no database, service, report file or cache; stdout redirection
is controlled by your shell.

## Accounting contract

Only `<class><lines><line number="…" hits="…"/></lines></class>` observations
count. Method-level copies are ignored. A line is covered if its integer hit
count is greater than zero. Line numbers are positive; hits and summary counts
must be nonnegative decimal integers of at most 12 digits.

- Class, package and root rates are recomputed from observations.
- Root `lines-valid`, `lines-covered`, and `line-rate` are required and checked.
- Class/package `line-rate` is required. Their line counters are checked if present.
- Declared rates tolerate an absolute difference of **0.0001**, allowing common
  four-decimal producer rounding. The user threshold uses unrounded counts.
- Duplicate line numbers within one class fail. Overlapping `(filename, line)`
  pairs across classes/packages fail even if both have the same hit status.
- Multiple classes in one source file are allowed when their line sets are disjoint.
- An entirely empty report fails with `no_measurable_lines`; 0/0 cannot pass a gate.
  Empty classes/packages may declare any finite rate from 0 to 1 because producers
  use different conventions, but their counters must still be zero if present.

Source identities are case-sensitive filename strings after replacing `\\` with
`/` and normalizing `.` / `..` segments. No source file is opened and `<sources>`
prefixes are not resolved. Use reports for a single coherent source namespace.
Combining multiple target frameworks or separate checkouts under the same
filename can be ambiguous; audit those reports separately.

Computed totals count each normalized filename/line once, combining hit status
with logical OR for diagnosis. **Any duplicate finding still fails**, so this
diagnostic union cannot silently turn an ambiguous report into a passing gate.
When malformed observations are found, computed numbers are provisional.

## JSON and CI

```console
cobertura-audit coverage.xml --min-line-rate 0.85 --json > audit.json
```

Preserve the exit code when using shell pipelines. Exit **0** means arithmetic
and threshold passed, **1** means findings or no evidence, **2** means input,
format, resource-limit or operational error.

The version-1 JSON report includes computed totals, full finding counts grouped
by stable codes, bounded finding receipts, and file summaries. Defaults are
100 findings and 50 files. `--finding-limit 0 --file-limit 0` omits details without
skipping any validation. Filenames and line positions can be sensitive; review
reports before sharing. Source text is never read or emitted.

UTF-8 XML is required (BOM allowed). The root is an unnamespaced `coverage` with
one `packages` container; each package has one `classes`, and each class one
`lines`. A simple external DOCTYPE is allowed for conventional Cobertura output;
no external DTD is fetched. Entities and internal DTD subsets are rejected.
Defaults: 32 MiB XML and 250,000 element nodes. Adjust `--max-bytes` and
`--max-nodes` deliberately. XML trees and line identities are held in memory;
large reports may need more memory than their file size.

## What this does and does not establish

It checks the **internal line accounting** of one report. It cannot prove that
the producer instrumented every executable line, included every source file,
ran meaningful tests or used the intended exclusions. Branch coverage,
condition coverage, method metrics and cross-report merging are outside v0.1.0.
This is a strict accounting profile, not a complete Cobertura DTD validator.
Some valid Java or multi-target reports reuse source lines across classes;
these deliberately fail this profile and should be audited per target.

[pycobertura](https://github.com/aconrad/pycobertura) provides coverage diffs and
rendering. [actions-validate-coverage](https://github.com/vln-devsecops/actions-validate-coverage)
offers multi-format threshold gates. cobertura-audit adds an independent check
of the arithmetic underneath the declared summary. Producer/consumer issues
such as [Slather's duplicate-data fixes](https://github.com/SlatherOrg/slather/blob/master/CHANGELOG.md)
and [Jenkins coverage issue 658](https://github.com/jenkinsci/coverage-plugin/issues/658)
motivate checking report structure; this utility does not repair those tools.

## Develop / 中文说明

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests include malformed XML, entity
rejection, duplicated lines, rounding boundaries, thresholds, source-preservation
checks and an integration test that generates real XML with coverage.py.
Clean-wheel acceptance runs all three examples outside the source tree.
CI covers Linux and Windows with Python 3.11/3.13, and macOS with Python 3.13.

本工具从 Cobertura XML 的逐行记录重算行覆盖率，校验 class、package 和根节点
的汇总数字，发现重复行、重叠源文件记录和虚高汇总时返回失败。
门槛使用未舍入的真实计数；`--min-line-rate 0.8` 表示 80%。它不会读源码，
不会证明覆盖工具采集完整，也不检查分支覆盖率。多目标框架的报告应分别检查。

MIT © KanadeK. No third-party runtime code is bundled.
