"""Initialize isolated local state; never inherit upstream account approvals."""
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".local"


def initialize(root=ROOT):
    root = Path(root)
    if root.exists():
        raise ValueError("Local workspace already exists; initialization never overwrites it")
    root.mkdir(mode=0o700)
    for name in ("03-定时任务-routines", "04-运行状态-state", "05-交易记录-data", "09-API密钥-仅本地"):
        (root / name).mkdir(mode=0o700)
    for name in ("journal", "evidence", "decisions", "backups"):
        (root / "05-交易记录-data" / name).mkdir(mode=0o700)
    def write(relative, value):
        path = root / relative
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
    readiness = {
        "paper_trading_only": True, "live_trading_enabled": False,
        "human_confirmation_required_for_live_orders": True, "orders_allowed": False,
        "broker_connected": False, "binance_stock_trading_eligibility_verified": False,
        "binance_stock_api_read_access_verified": False,
        "local_paper_ledger_initialized": True, "autonomous_paper_execution_enabled": False,
        "max_position_size_percent": 10, "max_daily_loss_percent": 2,
        "max_single_trade_loss_percent": 0.5,
    }
    state = {"starting_capital_usdt": 10000, "cash_usdt": 10000, "equity_usdt": 10000,
             "realized_pnl_usdt": 0, "unrealized_pnl_usdt": 0,
             "daily_realized_pnl_usdt": 0, "daily_open_risk_usdt": 0,
             "positions": [], "open_orders": [], "watchlist": [], "planned_trading_dates": []}
    write("04-运行状态-state/readiness.json", readiness)
    write("05-交易记录-data/current-state.json", state)
    write("05-交易记录-data/paper-ledger.json", {"version": 1, "next_sequence": 1, "events": []})
    config = json.loads((PROJECT / "04-运行状态-state/paper-config.json").read_text(encoding="utf-8"))
    write("04-运行状态-state/paper-config.json", config)
    schedule = json.loads((PROJECT / "03-定时任务-routines/schedule.json").read_text(encoding="utf-8"))
    schedule.update(activation_status="disabled", planned_trading_dates=[])
    write("03-定时任务-routines/schedule.json", schedule)
    credentials = root / "09-API密钥-仅本地/binance-api.env"
    credentials.write_text("BINANCE_API_KEY=\nBINANCE_API_SECRET=\nBINANCE_ENV=production\n", encoding="utf-8")
    if os.name != "nt":
        credentials.chmod(0o600)
    return root


if __name__ == "__main__":
    try:
        initialize()
        print("本機工作區 .local 已建立；密鑰空白、交易與排程尚未啟用。")
    except (ValueError, OSError):
        print("初始化未完成；請檢查 .local 是否已存在。既有工作區不會被覆蓋。")
        raise SystemExit(2)
