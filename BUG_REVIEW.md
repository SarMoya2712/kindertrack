# Logic and bug review — 2026-10-13

Reviewed authentication, attendance transactions, face matching and enrollment,
photo storage, child editing, reports, request security, and browser scripts.
The four issues below were reproduced with regression tests and fixed.
Locations refer to the updated source files.

| Priority | Location | Bug and impact | Fix |
| --- | --- | --- | --- |
| High | `services/admin_auth.py:76` | Concurrent successful logins from the same address could delete the login-attempt row while another request waited to read it. The waiting request dereferenced `None` and returned HTTP 500. The reproduction produced seven failures in eight simultaneous logins. | Lock the attempt row atomically with an upsert before reading it; refresh the timestamp after waiting for the lock. Existing lockout behavior and successful-login cleanup remain covered. |
| High | `services/scanner.py:35` | A reference-photo replacement could commit after face comparison but before the scanner locked the child. The scan then created attendance using the obsolete identity match. | Compare again while holding the child lock used by photo replacements. Reject a changed candidate without disclosing its identity or creating attendance. |
| Medium | `services/face_recognition.py:113` | A truncated stored embedding crashed comparison; NaN/infinite embeddings disrupted ranking. Non-unit vectors could inflate dot-product similarity. One corrupt reference could affect matching for other children. | Require 128 finite float32 values with unit norm, skip invalid references, return `not_found` when none are usable, and bound similarity to [-1, 1]. Corrupt database rows are preserved for manual replacement. |
| Medium | `routes/children.py:94` | `getattr(child, key) or ''` converted a valid zero tariff into an empty string in edit audit records. It also falsely reported a tariff change when zero remained zero. | Treat only `None` as empty and preserve the string representation of zero. |

Regression coverage is in `tests/test_review_regressions.py`: simultaneous
successful logins, changed/unchanged zero tariffs, five corrupt-vector cases,
and reference replacement between matching and attendance registration.

Validation:

- Original suite before fixes: 85 passed.
- Final full suite: **94 passed in 51.84 seconds**, including PostgreSQL,
  browser workflows, migrations, and real-model face recognition.
- Ruff checks for unused imports, unused variables, and undefined names passed.
- Python compilation checks passed.

This review does not establish biometric accuracy on a representative child
population. The existing unavailable liveness provider remains an explicitly
documented product limitation; these fixes do not implement anti-spoofing.
