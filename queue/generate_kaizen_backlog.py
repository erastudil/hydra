from pathlib import Path
import json
import datetime

QUEUE_DIR = Path(r"C:\Users\jpm05\Documents\hydra\queue")
QUEUE_DIR.mkdir(parents=True, exist_ok=True)
Q_FILE = QUEUE_DIR / "kaizen_queue.jsonl"
S_FILE = QUEUE_DIR / "kaizen_state.json"

DOMAINS = [
    ("context_compaction", "hydra_cli/agent.py", "ctx", [
        "progen_reanchor", "sliding_window", "decay_weighting", "semantic_dedup",
        "utf8_boundary", "lru_pruning", "checkpoint_index", "delta_encoding",
        "budget_governor", "buffer_isolation", "state_quantization", "fingerprinting",
        "spillover_journal", "selective_recall", "message_dedup", "null_payload_strip",
        "token_metering", "preamble_masking", "trailing_tail", "branch_pruning",
        "turn_squashing", "invariant_lock", "in_place_compact", "overflow_failsafe",
        "diff_engine", "temporal_filter", "sparse_attention", "topic_splitting",
        "result_summary", "cyclic_cleanup", "snapshot_export", "snapshot_import",
        "entropy_prune", "decay_telemetry", "rehydration_gate", "ceiling_validator",
        "scratchpad_pass", "head_locking", "call_folding", "trace_condense",
        "canonicalizer", "entity_registry", "eviction_event", "footprint_bench",
        "gc_trigger", "turn_priority", "latency_meter", "vector_persist",
        "slice_indexer", "hash_chaining", "whitespace_strip", "json_stripping",
        "code_dedup", "aging_formula", "working_capsule", "history_linter",
        "recall_evaluator", "undo_logger", "shrink_heuristic", "serialization",
        "zero_copy_view", "pressure_signal", "emergency_prune", "audit_trail",
        "long_run_stability"
    ]),
    ("token_cache_efficiency", "hydra_cli/providers.py", "cache", [
        "prefix_isolation", "kv_fingerprint", "timestamp_strip", "hit_telemetry",
        "template_hash", "boundary_align", "preamble_freeze", "turn_separation",
        "expiration_monitor", "kv_reuse", "savings_audit", "prefix_normalize",
        "prompt_pooling", "cache_warmup", "tool_caching", "breakage_detect",
        "efficiency_report", "minification", "zone_delimiter", "kv_checkpoint",
        "savings_calc", "schema_format", "header_pinning", "multi_model_sync",
        "eviction_alert", "hit_rate_bench", "zero_drift_builder", "suffix_isolate",
        "rule_partition", "digest_verify", "ttl_tracking", "param_stripping",
        "test_harness", "cache_router", "speculative_align", "system_dedup",
        "warmth_metric", "template_compile", "invalidation_hook", "cost_telemetry",
        "length_optimizer", "prefix_stability", "miss_diff_log", "provider_audit",
        "local_kv_cache", "json_order_persist", "header_inject", "invariant_verify",
        "status_widget", "concurrency_test", "fuzz_generator", "alphabetical_tools",
        "schema_digest", "cold_start_mitigate", "regression_suite", "swarm_prefix_reuse",
        "ratio_monitor", "hex_audit", "fast_assembly", "tail_injection",
        "ttft_timer", "quota_guard", "session_affinity", "token_alignment",
        "end_to_end_proof"
    ]),
    ("playwright_browser_tests", "hydra_cli/sandbox.py", "playwright", [
        "dom_idle_latch", "har_recording", "selector_cascade", "screenshot_gate",
        "cookie_persist", "network_intercept", "console_capture", "a11y_inspection",
        "viewport_matrix", "form_autofill", "mutation_waiter", "network_idle",
        "context_isolate", "crash_recovery", "download_verify", "dialog_handler",
        "iframe_traversal", "geo_emulation", "offline_simulate", "clipboard_verify",
        "media_abort", "visibility_check", "keyboard_events", "touch_gestures",
        "dark_mode_test", "status_assert", "worker_telemetry", "service_worker",
        "canvas_assert", "svg_interaction", "infinite_scroll", "drag_and_drop",
        "file_upload", "tab_coordinator", "storage_dump", "font_rendering",
        "ws_interceptor", "vitals_metrics", "cdp_heap_snapshot", "css_transition",
        "media_playback", "pdf_export", "security_headers", "custom_headers",
        "trace_recording", "selector_engine", "read_only_assert", "shadow_dom",
        "content_linter", "process_reaper", "artifact_archive", "rate_limit_test",
        "promise_rejection", "resize_listener", "focus_trapping", "aria_live",
        "csp_violation", "lazy_load_check", "lcp_threshold", "dom_serializer",
        "multi_browser", "worker_pool", "retry_backoff", "cli_runner",
        "test_suite_proof"
    ]),
    ("mcp_tool_integrations", "hydra_cli/mcp.py", "mcp", [
        "schema_validator", "heartbeat_keepalive", "multi_namespace", "transport_failover",
        "timeout_guard", "type_coercion", "lazy_spawning", "error_channel",
        "sandbox_env", "registry_discovery", "manifest_cache", "concurrent_dispatch",
        "result_sanitizer", "lifecycle_fsm", "stdio_drain", "proto_negotiator",
        "rate_limiter", "log_bridge", "resource_reader", "prompt_provider",
        "restart_backoff", "signature_hasher", "secret_isolation", "binary_encoder",
        "call_audit", "config_loader", "schema_normalize", "zero_call_pass",
        "cancel_signal", "health_probe", "dynamic_register", "output_truncator",
        "subagent_delegate", "schema_diff_log", "unix_socket", "named_pipe",
        "mock_server", "call_memoize", "conformance_test", "profiler_bench",
        "enum_validator", "stateful_loop", "call_dag", "shutdown_notice",
        "reap_orphans", "prompt_injector", "json_rpc_parser", "dry_run_mode",
        "output_compress", "permission_gate", "argument_fuzzer", "stderr_redirect",
        "manifest_export", "capability_probe", "retry_loop", "diagnostic_cmd",
        "type_negotiate", "metrics_export", "stream_results", "header_propagate",
        "schema_linter", "mount_whitelist", "event_bus", "handler_linter",
        "end_to_end_proof"
    ]),
    ("streaming_throughput", "hydra_cli/providers.py", "stream", [
        "sse_zero_copy", "ring_buffer", "jitter_smoothing", "utf8_chunking",
        "ttft_metrics", "async_generator", "raw_stdout_write", "backpressure_pause",
        "delta_emission", "reconnect_backoff", "partial_json", "frozen_detector",
        "fanout_stream", "cancel_propagate", "speedometer_ui", "decompression",
        "buffer_pool", "sse_comment_strip", "chunk_coalesce", "ansi_highlight",
        "markdown_parser", "throughput_bench", "telemetry_collector", "stdin_cancel",
        "boundary_validate", "schema_unifier", "loss_detector", "speculative_merge",
        "error_recovery", "tcp_nodelay", "event_emitter", "cursor_restore",
        "zero_alloc_reader", "abort_cleanup", "latency_histogram", "diff_renderer",
        "newline_flush", "http2_multiplex", "integrity_hasher", "spinner_overlay",
        "tee_utility", "bandwidth_meter", "stage_profiler", "memory_throttle",
        "stream_linter", "connection_pool", "tool_preview", "finish_audit",
        "stress_test", "encoding_fallback", "resize_adapter", "cost_accumulator",
        "pause_on_scroll", "fast_stdout_impl", "chunk_validator", "repl_stream_sync",
        "token_counter", "error_payload_parse", "jitter_filter", "verbosity_levels",
        "benchmark_cli", "buffer_guard", "event_dedup", "pipeline_linter",
        "end_to_end_proof"
    ]),
    ("repl_completions", "hydra_cli/repl.py", "repl", [
        "fuzzy_alias", "bracket_matcher", "atomic_history", "tab_heuristics",
        "parameter_hints", "syntax_lexer", "undo_stack", "shortcut_registry",
        "fuzzy_search", "smart_indent", "theme_loader", "continuation_glyph",
        "bracketed_paste", "alias_expansion", "duration_tracker", "history_dedup",
        "auto_close_delims", "bell_suppressor", "menu_pager", "exit_confirm",
        "token_preview", "history_path", "history_governor", "model_quick_switch",
        "mcp_inspector", "editor_spawn", "syntax_validator", "progen_toggle",
        "markdown_export", "timestamp_tagging", "title_updater", "contrast_checker",
        "context_indicator", "startup_timer", "slash_dispatcher", "migration_tool",
        "kill_ring", "word_navigation", "tooltip_renderer", "ansi_sanitize",
        "cursor_shape", "error_underlining", "macro_recorder", "crash_dump",
        "pause_toggle", "asciicast_record", "shift_tab_cycle", "prompt_customizer",
        "status_line_refresh", "fuzz_input_test", "search_highlight", "auto_trim_spaces",
        "env_var_expand", "clear_screen_keep", "idle_handler", "execution_timer",
        "desktop_notify", "privacy_sanitize", "conformance_suite", "comment_highlight",
        "rainbow_brackets", "stats_summary", "ghost_suggestions", "repl_linter",
        "end_to_end_proof"
    ]),
    ("ast_refactoring_tools", "hydra_cli/native_tools.py", "ast", [
        "p018_linter", "dead_code_elim", "import_sorter", "progen_rewriter",
        "p001_detector", "p013_detector", "complexity_meter", "type_annotations",
        "unused_var_cleaner", "docstring_linter", "constant_folding", "ban_mock_tests",
        "ponytail_analyzer", "p014_metaphor_ban", "state_vector_linter", "function_length",
        "arg_count_guard", "structural_dedup", "narrow_exceptions", "fstring_modernizer",
        "walrus_simplify", "with_statement", "generator_expr", "dataclass_transform",
        "immutable_dict", "enum_validator", "lambda_to_def", "return_consistency",
        "regex_precompile", "deprecated_api", "blank_line_linter", "exit_code_checker",
        "unreachable_code", "global_statement", "loop_flattener", "bool_simplify",
        "ternary_simplify", "magic_numbers", "shadowed_builtins", "utf8_enforcer",
        "pathlib_enforcer", "dict_comprehension", "set_membership", "truth_gate_linter",
        "pure_functions", "json_loads_guard", "subprocess_timeout", "hash_modernizer",
        "docstring_strip", "poka_yoke_check", "refactor_reporter", "atomic_rollback",
        "format_preserve", "tree_diff_viewer", "circular_imports", "dead_import_prune",
        "var_name_clarity", "pure_function_cache", "argparse_linter", "atomic_save_tmp",
        "cli_interface", "regression_pytest", "genome_compliance", "self_verification",
        "end_to_end_proof"
    ]),
    ("terminal_ui_polish", "hydra_cli/ui.py", "ui", [
        "truecolor_fallback", "win32_vt100", "box_drawing", "spinner_bench",
        "status_bar_scaling", "scrollback_preserve", "high_dpi_scaling", "wcag_contrast",
        "cursor_management", "progress_bar", "diff_colorizer", "table_auto_wrap",
        "markdown_render", "multi_column_grid", "terminal_bell", "status_badges",
        "double_buffer", "tree_renderer", "interactive_prompt", "truncate_ellipsis",
        "code_block_frame", "gutter_line", "emoji_width", "theme_palettes",
        "raw_keyboard_poll", "help_formatter", "error_banner", "success_banner",
        "spinner_stepper", "live_metrics", "column_alignment", "no_color_flag",
        "osc8_hyperlinks", "swarm_fanout_view", "ascii_logo", "sigwinch_handler",
        "progen_highlight", "warning_banner", "memory_sparkline", "speedometer_gauge",
        "completion_ui", "log_viewport", "status_bar_clock", "prompt_symbol",
        "clean_line_erase", "counter_animation", "capabilities_probe", "conhost_compat",
        "win_term_features", "ansi_strip_pipe", "colored_json", "task_stepper",
        "toast_overlay", "menu_selector", "compact_kv_print", "stress_test_60fps",
        "text_wrapper", "screen_clear_exit", "separator_lines", "theme_toggle_cmd",
        "ui_profiler", "circular_spinner", "a11y_high_contrast", "ui_progen_linter",
        "end_to_end_proof"
    ])
]

created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
tasks = []

for domain_name, subsystem, short, topic_slugs in DOMAINS:
    assert len(topic_slugs) == 65, f"Domain {domain_name} has {len(topic_slugs)} topics, expected 65"
    for idx, slug in enumerate(topic_slugs, start=1):
        clean_title = slug.replace('_', ' ').title()
        task_id = f"kaizen_{short}_{idx:03d}_{slug[:30]}"
        priority = 1 if idx <= 20 else (2 if idx <= 45 else 3)
        prompt_user = (
            f"Execute discrete Kaizen improvement {task_id} on subsystem '{subsystem}'.\n\n"
            f"Domain: {domain_name}\n"
            f"Title: {clean_title} Implementation\n"
            f"Task ID: {task_id}\n\n"
            "Requirements:\n"
            "1. Architect Head (GLM 5.3 Flash): Analyze failure modes, invariants, and minimal architectural diff.\n"
            "2. Coder Head (DeepSeek 4.1 Flash): Provide exact, runnable Python code implementation adhering to zero-copula P018.\n"
            "3. Auditor Head (Mimo 2.6 Flash): Audit edge cases, security fences, memory leaks, and verification assertions.\n"
            "4. Synthesizer: Synthesize final unified consensus roadmap and runnable verification gate asserting exit code 0.\n\n"
            "Constraints: Zero stubs P013. Zero parentheticals P001 in running prose. Pure Python standard library or existing dependencies."
        )
        task_obj = {
            "task_id": task_id,
            "domain": domain_name,
            "title": clean_title,
            "description": f"Discrete Kaizen improvement: {clean_title} in {subsystem}",
            "target_subsystem": subsystem,
            "status": "PENDING",
            "priority": priority,
            "swarm": "open swarm",
            "models": {
                "architect": "glm 5.3 flash",
                "coder": "deepseek 4.1 flash",
                "auditor": "mimo 2.6 flash"
            },
            "provider": "cheaperinference",
            "prompt": {
                "system": (
                    "dialect : progen instruct.\n"
                    "role : hydra open swarm kaizen engineer.\n"
                    "standard : zero copula P018; zero parentheticals P001; exit code 0 on tests.\n"
                    "heads : GLM 5.3 Flash (Architect), DeepSeek 4.1 Flash (Coder), Mimo 2.6 Flash (Auditor).\n"
                    "provider : cheaperinference.\n"
                    "format : topic : comment with blank line delimiters."
                ),
                "user": prompt_user
            },
            "attempts": 0,
            "max_attempts": 3,
            "created_at": created_at,
            "completed_at": None,
            "result": None,
            "error": None
        }
        tasks.append(task_obj)

print(f"Total tasks constructed: {len(tasks)}")
lines = [json.dumps(t, ensure_ascii=False) for t in tasks]
Q_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Wrote kaizen_queue.jsonl: {Q_FILE.stat().st_size} bytes")

state_doc = {
    "schema_version": "1.0.0",
    "timestamp": created_at,
    "total_tasks": len(tasks),
    "pending_tasks": len(tasks),
    "in_progress_tasks": 0,
    "completed_tasks": 0,
    "failed_tasks": 0,
    "domains": {d[0]: 65 for d in DOMAINS},
    "tasks": tasks
}
S_FILE.write_text(json.dumps(state_doc, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote kaizen_state.json: {S_FILE.stat().st_size} bytes")
