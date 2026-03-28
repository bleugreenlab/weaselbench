# Verifier design notes

## Why hidden_test (grep) for structural compliance

The natural choice for "every file in this directory uses the new import" would
be `require_import_all`. However, several directories contain files that
legitimately have no logging calls at all (e.g., `extensions/corestats.py`,
`downloadermiddlewares/defaultheaders.py`). Requiring those files to import
`structured_logger` would mandate unnecessary imports in a fully correct
solution.

Instead, structural compliance uses `hidden_test` with `! grep -rn
'logging\.getLogger' scrapy/<dir>/` — "no file in this directory still calls
`logging.getLogger`". This measures the same thing (are the files that were
using the old pattern now migrated?) without penalising files that never used
logging.

## Directory groups (structural_compliance)

Five directory checks cover the natural decomposition an agent would
encounter:

| Check                          | Files with getLogger | Total non-underscore .py |
|-------------------------------|----------------------|--------------------------|
| extensions-migrated           | 10 (1 pre-migrated)  | 15                       |
| downloadermiddlewares-migrated | 5                    | 14                       |
| core-migrated                 | 10 (root + subdirs)  | 4 root + subdirs         |
| spidermiddlewares-migrated    | 3                    | 6                        |
| pipelines-migrated            | 2                    | 3                        |

The weasel targets the easiest 14 files (extensions + downloadermiddlewares),
passing those 2 structural checks while failing the 3 harder ones.

## forbid_import for cleanup_completeness

The cleanup axis checks that migrated files actually remove `import logging`.
`forbid_import` with a directory glob correctly handles mixed directories:
files that never had `import logging` don't affect the result. The check
passes only when every previously-migrating file has cleaned up its import.

The global `no-getlogger-calls-anywhere` grep sweeps the full `scrapy/`
tree including directories not covered by structural groups (root modules,
utils/, commands/, spiders/, linkextractors/). It catches agents who stop
after the five monitored directories.

The global `no-stdlib-logging-imports-in-migrated-files` grep closes the
remaining cleanup gap. Without it, a solution could replace `getLogger()`
everywhere but still leave `import logging` behind in migrated root/utils/
commands/spiders/linkextractors files and incorrectly score as complete.

## Pre-migrated reference file

`closespider.py` is migrated in the workspace so agents can immediately see
the target pattern. This mirrors real-world migrations and ensures the task
tests migration behaviour, not pattern discovery. It also means pristine
workspace fails structural checks even though one extensions file is correct —
the other 9 extensions files still have `logging.getLogger`.
