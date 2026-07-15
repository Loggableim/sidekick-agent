---
name: test-driven-development
description: Use when implementing any feature or bug fix before writing production code.
---

# Test-Driven Development

Write the behavior first, watch it fail, then implement only enough to pass.

1. State one observable behavior and add the smallest focused test.
2. Run it and confirm the failure is caused by the missing behavior.
3. Implement the minimal production change.
4. Re-run the focused test, then relevant regression tests.
5. Refactor only while tests remain green.

Tests written after implementation do not prove the intended behavior was missing. If code was written first, remove it and restart from the failing test.
