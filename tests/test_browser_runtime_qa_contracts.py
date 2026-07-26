from pathlib import Path


def test_browser_runtime_qa_collects_visual_layout_accessibility_console_network_evidence():
    runtime_py = Path("web/api/browser_runtime.py").read_text(encoding="utf-8")

    assert "screenshot_analysis = _analyze_png_frame(self._frame_bytes)" in runtime_py
    assert "visual_findings = [" in runtime_py
    assert "if visual_findings:" in runtime_py
    assert "findings.extend(visual_findings)" in runtime_py

    assert "horizontal_overflow = int(layout.get(\"horizontalOverflowPx\") or 0)" in runtime_py
    assert "Page has horizontal overflow of {horizontal_overflow}px beyond the viewport." in runtime_py
    assert "fixedOverlays" in runtime_py
    assert "Large fixed/sticky overlay detected" in runtime_py
    assert "offscreenInteractive" in runtime_py
    assert "visible interactive control(s) are outside the viewport bounds" in runtime_py

    assert "unlabeledInteractive" in runtime_py
    assert "visible enabled interactive control(s) have no accessible label" in runtime_py
    assert "imagesMissingAlt" in runtime_py
    assert "visible image(s) are missing alt text" in runtime_py
    assert "No visible H1 heading was detected." in runtime_py

    assert "console_findings = [" in runtime_py
    assert "pageerror" in runtime_py
    assert "console/page error or warning event(s) captured" in runtime_py
    assert "network_findings = [" in runtime_py
    assert "int(ev.get(\"status\") or 0) >= 400" in runtime_py
    assert "failed or non-2xx/3xx network event(s) captured" in runtime_py

    assert "status = \"pass\" if not findings else \"needs_review\"" in runtime_py
    assert "approval_mode = _current_approval_mode()" in runtime_py
    assert "active_goal = _active_goal_context(self.session_id)" in runtime_py
    assert "\"approval_mode\": approval_mode" in runtime_py
    assert "\"active_goal\": active_goal" in runtime_py
    assert "\"Approval mode: \" + str(approval_mode or \"manual\")" in runtime_py
    assert "\"Active goal: \" + (active_goal_text or \"none\")" in runtime_py
    assert "f\"- Approval mode: {approval_mode or 'manual'}\"" in runtime_py
    assert "f\"- Active goal: {active_goal_text or 'none'}\"" in runtime_py
    assert "\"visual_findings\": visual_findings" in runtime_py
    assert "\"layout_findings\": layout_findings" in runtime_py
    assert "\"accessibility_findings\": accessibility_findings" in runtime_py
    assert "\"console_events\": console_recent" in runtime_py
    assert "\"network_events\": network_recent" in runtime_py


def test_browser_runtime_qa_requires_current_screenshot_frame_for_visual_proof():
    runtime_py = Path("web/api/browser_runtime.py").read_text(encoding="utf-8")

    assert "if not self._frame_bytes or self._snapshot.frame_rev <= 0:" in runtime_py
    assert "No current browser screenshot frame was captured." in runtime_py
    assert "\"available\": bool(self._frame_bytes and self._snapshot.frame_rev > 0)" in runtime_py
    assert "\"frame_rev\": self._snapshot.frame_rev" in runtime_py
    assert "\"viewport\": {" in runtime_py
    assert "\"analysis\": screenshot_analysis" in runtime_py
    assert "Screenshot: " in runtime_py
    assert "rev {report['screenshot']['frame_rev']}" in runtime_py
