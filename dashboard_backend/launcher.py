from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .models import AIConfig, HighFanoutConfig, PblockConfig
from .settings import Settings


def _append_flag(args: List[str], flag: str, enabled: bool) -> None:
    if enabled:
        args.append(flag)


def _append_value(args: List[str], flag: str, value: object, default: object) -> None:
    if value != default:
        args.extend([flag, str(value)])


def _append_optional(args: List[str], flag: str, value: Optional[int]) -> None:
    if value is not None:
        args.extend([flag, str(value)])


def build_pblock_command(
    settings: Settings,
    *,
    input_dcp: Path,
    output_root: Path,
    config: Optional[PblockConfig] = None,
    max_attempts_override: Optional[int] = None,
) -> List[str]:
    cfg = config or PblockConfig()
    defaults = PblockConfig()
    args: List[str] = [
        settings.python_executable,
        str(settings.pblock_script),
        "--input-dcp",
        str(input_dcp),
        "--output-root",
        str(output_root),
    ]

    _append_value(args, "--pblock-name", cfg.pblock_name, defaults.pblock_name)
    _append_value(args, "--apply-to", cfg.apply_to, defaults.apply_to)
    _append_flag(args, "--use-clock-regions", cfg.use_clock_regions)
    _append_flag(args, "--hard-pblock", cfg.hard_pblock)
    _append_value(args, "--place-directive", cfg.place_directive, defaults.place_directive)
    _append_value(args, "--route-directive", cfg.route_directive, defaults.route_directive)
    _append_optional(args, "--target-lut", cfg.target_lut)
    _append_optional(args, "--target-ff", cfg.target_ff)
    _append_optional(args, "--target-dsp", cfg.target_dsp)
    _append_optional(args, "--target-bram", cfg.target_bram)
    _append_flag(args, "--keep-placement", cfg.keep_placement)
    _append_flag(args, "--keep-routing", cfg.keep_routing)

    effective_max_attempts = cfg.max_attempts if max_attempts_override is None else max_attempts_override
    _append_value(args, "--max-attempts", effective_max_attempts, defaults.max_attempts)
    _append_flag(args, "--continue-after-improvement", cfg.continue_after_improvement)
    _append_value(args, "--seed-count", cfg.seed_count, defaults.seed_count)
    _append_value(args, "--elite-count", cfg.elite_count, defaults.elite_count)
    _append_value(args, "--random-seed", cfg.random_seed, defaults.random_seed)

    return args


def build_ai_command(
    settings: Settings,
    *,
    input_dcp: Path,
    output_dcp: Path,
    run_dir: Path,
    config: Optional[AIConfig] = None,
    test_mode: bool = False,
    max_nets: Optional[int] = None,
) -> List[str]:
    cfg = config or AIConfig()
    args: List[str] = [
        settings.python_executable,
        str(settings.optimizer_script),
        str(input_dcp),
        "--output",
        str(output_dcp),
        "--run-dir",
        str(run_dir),
        "--model",
        cfg.model,
    ]
    if test_mode:
        args.append("--test")
    if max_nets is not None:
        args.extend(["--max-nets", str(max_nets)])
    if cfg.debug:
        args.append("--debug")
    if cfg.continue_when_timing_met:
        args.append("--continue-when-timing-met")
    return args


def build_high_fanout_command(
    settings: Settings,
    *,
    input_dcp: Path,
    output_dcp: Path,
    run_dir: Path,
    config: Optional[HighFanoutConfig] = None,
    use_test_mode: bool,
) -> List[str]:
    cfg = config or HighFanoutConfig()
    return build_ai_command(
        settings,
        input_dcp=input_dcp,
        output_dcp=output_dcp,
        run_dir=run_dir,
        config=AIConfig(
            model=cfg.model,
            debug=cfg.debug,
            continue_when_timing_met=cfg.continue_when_timing_met,
        ),
        test_mode=use_test_mode,
        max_nets=cfg.max_nets if use_test_mode else None,
    )
