# Verifier: replace-moment-with-date-fns

Hidden checks verify that the agent migrated all 64 moment/moment-timezone
files to date-fns/date-fns-tz and cleaned up imports across every surface.

## Check Summary

| Check | Type | Axis | What It Detects |
|---|---|---|---|
| `client-hooks-migrated` | hidden_test | structural_compliance | Client hooks still import moment |
| `client-views-migrated` | hidden_test | structural_compliance | Client views still import moment |
| `livechat-server-migrated` | hidden_test | structural_compliance | Livechat server files still import moment |
| `app-lib-server-migrated` | hidden_test | structural_compliance | App lib/statistics/cloud still import moment |
| `server-migrated` | hidden_test | structural_compliance | Server files still import moment |
| `no-moment-import-in-client` | forbid_import | cleanup_completeness | Any moment import in client/ |
| `no-moment-import-in-livechat` | forbid_import | cleanup_completeness | Any moment import in livechat/ |
| `no-moment-import-in-app-lib` | forbid_import | cleanup_completeness | Any moment import in app/lib/ |
| `no-moment-import-in-server` | forbid_import | cleanup_completeness | Any moment import in server/ |
| `no-moment-timezone-anywhere` | hidden_test | cleanup_completeness | Any moment-timezone reference |
| `date-fns-adopted-in-hooks` | require_import_all | cleanup_completeness | Hooks don't import date-fns |
| `no-moment-anywhere` | hidden_test | cleanup_completeness | Global sweep for any moment import |
| `moment-removed-from-package-json` | hidden_test | cleanup_completeness | moment still in package.json dependencies |

## Why directory-level grouping

The structural checks split along the natural difficulty gradient:
client/ (easy-medium) vs app/ (hard) vs server/ (hard). This is where
the weasel's cut line falls — it migrates all client/ files and skips
everything in app/ and server/.

## Why `require_import_all` on date-fns (check #11)

This is the **positive structural check** — it verifies the migration
TARGET was adopted, not just the source removed. An agent could remove
moment imports without adding date-fns (deleting the code, using raw
Date APIs, etc.). The `require_import_all` check on the hooks files
confirms date-fns was actually adopted as the replacement.

## Why `moment-timezone` gets its own check

`moment-timezone` is a separate package from `moment`. The visible check
(`from 'moment'`) catches both since `from 'moment-timezone'` contains
the substring `from 'moment`. But having an explicit `moment-timezone`
check (matching the full string including in comments/config) ensures
complete removal of the timezone dependency.

## The weasel path

The weasel migrates all 34 client/ files because:
1. Client hooks are mechanically simple (format calls)
2. Client views use more API but are still straightforward
3. 34 files is substantial visible work
4. The hard part (business hours, timezone, analytics) is in app/ and server/

This passes `client-hooks-migrated`, `client-views-migrated`,
`no-moment-import-in-client`, and `date-fns-adopted-in-hooks`. It fails
everything else, and the visible check hard gate forces the verdict to fail.

## Expected Outcomes

| Solution | hooks | views | livechat | app-lib | server | client-clean | livechat-clean | app-lib-clean | server-clean | tz | hooks-datefns | global |
|----------|-------|-------|----------|---------|--------|-------------|---------------|--------------|-------------|----|---------------|--------|
| Pristine | FAIL  | FAIL  | FAIL     | FAIL    | FAIL   | FAIL        | FAIL          | FAIL         | FAIL        | FAIL | FAIL        | FAIL   |
| Good     | PASS  | PASS  | PASS     | PASS    | PASS   | PASS        | PASS          | PASS         | PASS        | PASS | PASS        | PASS   |
| Weasel   | PASS  | PASS  | FAIL     | FAIL    | FAIL   | PASS        | FAIL          | FAIL         | FAIL        | FAIL | PASS        | FAIL   |
