"""作者：Wangyuan；日期：2026-10-29；职责：独立离线JSON命令行入口。"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from ledger import Ledger, candidates, coverage, confirmed_reason, extract_text
def run(payload: dict, output: Path) -> Any:
    """执行账本动作或文本/覆盖校验；持久化使用--output。"""
    action = payload["action"]; args = payload.get("args", {})
    if action in {"candidates", "coverage", "confirmed_reason"}:
        return {"candidates": candidates, "coverage": coverage, "confirmed_reason": confirmed_reason}[action](**args)
    if action == "extract_text":
        source = Path(args["path"])
        return {"text": extract_text(source.name, source.read_bytes())}
    ledger = Ledger(output / "ledger.sqlite")
    methods = {"create": ledger.create, "change": ledger.change, "get": ledger.get, "draft": ledger.draft, "report": ledger.report, "import_candidates": ledger.import_candidates}
    if action not in methods: raise ValueError("未知动作")
    return methods[action](**args)

def demo(output: Path) -> dict:
    """从零构造收文、证据、确认、完成以及期限候选。"""
    ledger = Ledger(output / "demo.sqlite")
    record = ledger.create("receipt", "synthetic-receipt-1", "示例材料核对", received_on="2030-01-01", due_at="2030-01-03T10:00:00+08:00", actor="reviewer-1", operation_key="create-1")
    record = ledger.change(record["id"], 1, action="evidence", actor="reviewer-1", operation_key="evidence-1", values={"reference": "synthetic-page-1", "text": "示例材料已核对"})
    record = ledger.change(record["id"], 2, action="confirm", actor="reviewer-1", operation_key="confirm-1", values={"confirmed": True})
    record = ledger.change(record["id"], 3, action="complete", actor="reviewer-1", operation_key="complete-1")
    return {"record": record, "text_candidates": candidates("请资料组于2030年1月3日前反馈核对结果。", "2030-01-01"), "report": ledger.report("2030-01-04T00:00:00+08:00")}

def main() -> int:
    """运行合成演示或显式输入；错误返回非零，不访问外部服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "run"])
    parser.add_argument("input", nargs="?")
    parser.add_argument("--output", help="显式输出目录；默认临时目录")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(args.output or temporary)
            output.mkdir(parents=True, exist_ok=True)
            if args.command == "demo":
                result = demo(output)
            else:
                if not args.input:
                    raise ValueError("run必须提供JSON文件")
                source = Path(args.input)
                if source.stat().st_size > 10_000_000:
                    raise ValueError("JSON文件超出预算")
                result = run(json.loads(source.read_text(encoding="utf-8")), output)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
if __name__ == "__main__":
    raise SystemExit(main())
