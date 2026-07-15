---
name: systematic-debugging
description: Use when a test fails, a runtime behavior is unexpected, or a regression needs a root-cause diagnosis.
---

# Systematic Debugging

Find the cause before proposing a fix.

1. Capture the exact failure, environment, input, and expected behavior.
2. Reproduce on the named surface: CLI, TUI, WebUI, gateway, or live browser.
3. Trace the narrowest path from symptom to responsible state or code.
4. Separate existing baseline failures, expected noise, and the new regression.
5. Write a regression test, apply the smallest cause-directed fix, then verify.

Do not patch around an error message or weaken a test until the cause is known.
