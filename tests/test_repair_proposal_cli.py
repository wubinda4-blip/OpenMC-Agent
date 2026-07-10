from scripts.run_workflow_benchmark import build_parser


def test_repair_cli_flags_parse():
    args = build_parser().parse_args(["--cases","cases.json","--enable-llm-repair","--llm-repair-mode","validate-only","--llm-repair-max-proposals","1"])
    assert args.enable_llm_repair is True
    assert args.llm_repair_mode == "validate-only"
    assert args.llm_repair_max_proposals == 1
