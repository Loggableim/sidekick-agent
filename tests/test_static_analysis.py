import subprocess
import sys


def test_key_python_routes_have_no_undefined_names():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/routes.py",
            "cli/gateway.py",
            "runtime/_compat/shim_logging.py",
            "tools/patch_parser.py",
            "web/api/config.py",
            "web/api/extensions.py",
            "web/api/models.py",
            "--select",
            "F821",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_focused_modules_have_no_unused_imports():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/auth.py",
            "cli/_subprocess_compat.py",
            "cli/checkpoints.py",
            "cli/gateway_windows.py",
            "cli/local_state_repair.py",
            "--select",
            "F401",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_focused_modules_have_no_unused_locals():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/gateway.py",
            "cli/kanban_db.py",
            "cli/main.py",
            "runtime/_compat/copy_dependent_modules.py",
            "runtime/_compat/copy_runtime_modules.py",
            "runtime/_compat/shim_cli.py",
            "runtime/cron/scheduler.py",
            "tests/test_nova_hub_stt.py",
            "tools/computer_use/cua_backend.py",
            "tools/environments/vercel_sandbox.py",
            "tools/skill_usage.py",
            "web/api/clarify.py",
            "web/api/config.py",
            "web/api/routes.py",
            "web/api/upload.py",
            "web/server.py",
            "--select",
            "F841",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cronjob_tool_handler_avoids_function_calls_in_defaults():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/cronjob_tools.py",
            "--select",
            "B008",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_focused_modules_have_no_import_shadowing():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/auth.py",
            "cli/cli.py",
            "cli/web_server.py",
            "runtime/_compat/shim_constants_v2.py",
            "tools/send_message_tool.py",
            "web/api/config.py",
            "web/api/routes.py",
            "--select",
            "F402,F811",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_focused_modules_have_explicit_zip_strictness():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/config.py",
            "runtime/think_scrubber.py",
            "tools/fuzzy_match.py",
            "tools/mcp_tool.py",
            "tools/session_search_tool.py",
            "--select",
            "B905",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_approval_input_thread_binds_outer_state_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/approval.py",
            "--select",
            "B023",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_model_metadata_has_no_duplicate_collection_items():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/model_metadata.py",
            "--select",
            "B033",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_memory_write_errors_preserve_original_exception_context():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/memory_tool.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_transcription_text_extraction_uses_direct_attribute_access():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/transcription_tools.py",
            "--select",
            "B009",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_transport_finish_reason_uses_direct_attribute_assignment():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/transports.py",
            "--select",
            "B010",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_web_server_loop_marker_uses_direct_attribute_assignment():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/web_server.py",
            "--select",
            "B010",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_focused_modules_use_direct_attribute_access_for_constant_names():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/rl_training_tool.py",
            "web/api/routes.py",
            "--select",
            "B009",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_profile_distribution_uses_keyword_re_split_arguments():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/profile_distribution.py",
            "--select",
            "B034",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_dump_skill_counter_marks_unused_loop_value_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/dump.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_doctor_provider_fallback_marks_unused_url_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/doctor.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_claw_state_file_listing_marks_unused_path_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/claw.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_gateway_marks_unused_loop_values_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/gateway.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_main_and_memory_setup_mark_unused_loop_values_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/main.py",
            "cli/memory_setup.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_plugin_configuration_marks_unused_loop_values_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/plugins.py",
            "cli/plugins_cmd.py",
            "cli/skills_config.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_tools_and_web_mark_unused_loop_values_explicitly():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/auxiliary_client.py",
            "runtime/credential_pool.py",
            "tools/url_safety.py",
            "tools/web_tools.py",
            "web/api/agents.py",
            "--select",
            "B007",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_optional_abstract_base_hooks_have_explicit_noop_bodies():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/context_engine.py",
            "runtime/memory_provider.py",
            "tools/environments/base.py",
            "--select",
            "B027",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_environment_backend_errors_preserve_original_exception_context():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/environments/docker.py",
            "tools/environments/singularity.py",
            "tools/environments/ssh.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_oauth_and_provider_setup_errors_preserve_original_exception_context():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/bedrock_adapter.py",
            "runtime/google_oauth.py",
            "tools/mcp_oauth.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_helper_errors_use_explicit_exception_chaining():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/auth_commands.py",
            "cli/config.py",
            "cli/kanban_db.py",
            "cli/plugins_cmd.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_web_api_helper_errors_use_explicit_exception_chaining():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/kanban_bridge.py",
            "web/api/profiles.py",
            "web/api/workspace.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_tool_runtime_errors_use_explicit_exception_chaining():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/image_generation_tool.py",
            "tools/terminal_tool.py",
            "tools/tts_tool.py",
            "tools/web_tools.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_auth_errors_use_explicit_exception_chaining():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/auth.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_gateway_and_route_errors_use_explicit_exception_chaining():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/cron/jobs.py",
            "runtime/gateway/run.py",
            "web/api/routes.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_web_server_errors_use_explicit_exception_chaining():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/web_server.py",
            "--select",
            "B904",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_remaining_bugbear_checks_are_clean_for_gateway_and_web_server():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/web_server.py",
            "runtime/gateway/run.py",
            "--select",
            "B006,B010",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_web_api_and_server_have_no_unused_imports_or_empty_fstrings():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/helpers.py",
            "web/api/models.py",
            "web/api/providers.py",
            "web/api/rollback.py",
            "web/api/session_ops.py",
            "web/api/space_engine.py",
            "web/api/streaming.py",
            "web/api/terminal.py",
            "web/api/upload.py",
            "web/api/workspace.py",
            "web/server.py",
            "--select",
            "F401,F541",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_high_signal_pyflakes_findings_are_clean():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/model_normalize.py",
            "cli/setup.py",
            "tools/web_tools.py",
            "--select",
            "F601,F823,F841",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_and_tool_modules_have_no_unused_imports_or_empty_fstrings():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/config.py",
            "runtime/credential_pool.py",
            "runtime/cron/scheduler.py",
            "runtime/curator_backup.py",
            "runtime/gateway/agent_pool.py",
            "runtime/gateway/platforms/telegram.py",
            "runtime/gateway/runtime_footer.py",
            "runtime/gateway/shutdown_forensics.py",
            "runtime/models.py",
            "runtime/transports.py",
            "shared/runtime.py",
            "tools/code_execution_tool.py",
            "tools/computer_use/cua_backend.py",
            "tools/skill_usage.py",
            "tools/tts_tool.py",
            "--select",
            "F401,F541",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_modules_have_no_empty_fstrings():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/auth_commands.py",
            "cli/backup.py",
            "cli/cli.py",
            "cli/config.py",
            "cli/debug.py",
            "cli/dingtalk_auth.py",
            "cli/gateway.py",
            "cli/kanban_diagnostics.py",
            "cli/logs.py",
            "cli/main.py",
            "cli/memory_setup.py",
            "cli/profiles.py",
            "cli/webhook.py",
            "--select",
            "F541",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_remaining_tool_modules_have_no_unused_imports_or_empty_fstrings():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "tools/checkpoint_manager.py",
            "tools/delegate_tool.py",
            "tools/environments/local.py",
            "tools/send_message_tool.py",
            "tools/sidekick_memory.py",
            "--select",
            "F401,F541",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_remaining_web_api_modules_have_no_unused_imports_or_empty_fstrings():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/appstore.py",
            "web/api/browser_runtime.py",
            "web/api/config.py",
            "web/api/discord_bot.py",
            "web/api/dispatcher.py",
            "web/api/error_logger.py",
            "web/api/evey_tools.py",
            "web/api/gateway_watcher.py",
            "web/api/routes.py",
            "web/api/workspace_isolation.py",
            "--select",
            "F401,F541",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_remaining_entrypoints_and_compat_modules_have_no_unused_imports():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/__init__.py",
            "cli/main.py",
            "cli/stdio.py",
            "gateway/platforms/base.py",
            "gateway/platforms/discord.py",
            "runtime/_compat/shim_auth.py",
            "runtime/_compat/shim_cli.py",
            "runtime/gateway/run.py",
            "sidekick_app/__init__.py",
            "tests/smoke_all.py",
            "tests/smoke_dashboard.py",
            "tests/test_nova_lifecycle.py",
            "tests/test_space_memory_path.py",
            "--select",
            "F401",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_error_logger_queries_do_not_use_string_built_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/error_logger.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_discord_bot_has_no_hardcoded_token_paths():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/discord_bot.py",
            "--select",
            "S105",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_local_state_repair_does_not_interpolate_table_names():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/local_state_repair.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_kanban_sql_uses_parameterized_list_filters():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/kanban.py",
            "cli/kanban_db.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_kanban_bridge_patch_queries_use_static_update_statements():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/kanban_bridge.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_agents_update_queries_use_static_update_statements():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/agents.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_web_models_session_chain_query_uses_static_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/models.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_session_recovery_state_db_queries_use_static_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/session_recovery.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_agent_session_queries_use_static_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/agent_sessions.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_insights_session_queries_use_static_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/insights.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_nova_lifecycle_ltm_merge_uses_static_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/nova_lifecycle.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_routes_insights_state_db_query_uses_static_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "web/api/routes.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_shim_state_schema_adaptive_sql_is_reviewed():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "runtime/_compat/shim_state.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_main_model_menu_title_is_not_sql():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "cli/main.py",
            "--select",
            "S608",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
