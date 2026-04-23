from __future__ import annotations


def _exit_events(events):
    return [event for event in events if event.get("event_type") == "exit"]


def calculate_metrics(events, initial_capital: float = 10000.0):
    exits = _exit_events(events)
    pnl_values = [
        float(event["pnl"]) for event in exits if event.get("pnl") is not None
    ]

    total_trades = len(pnl_values)
    winning_pnls = [pnl for pnl in pnl_values if pnl > 0]
    losing_pnls = [pnl for pnl in pnl_values if pnl < 0]

    win_rate = (len(winning_pnls) / total_trades * 100.0) if total_trades else 0.0
    average_pnl = (sum(pnl_values) / total_trades) if total_trades else 0.0

    avg_win = (sum(winning_pnls) / len(winning_pnls)) if winning_pnls else 0.0
    avg_loss = (sum(losing_pnls) / len(losing_pnls)) if losing_pnls else 0.0
    profit_loss_ratio = (
        avg_win / abs(avg_loss) if avg_win > 0 and avg_loss < 0 else 0.0
    )

    win_probability = len(winning_pnls) / total_trades if total_trades else 0.0
    loss_probability = len(losing_pnls) / total_trades if total_trades else 0.0
    expectancy = (win_probability * avg_win) - (loss_probability * abs(avg_loss))

    equity_curve = [initial_capital]
    for pnl in pnl_values:
        equity_curve.append(equity_curve[-1] + pnl)

    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "average_pnl": average_pnl,
        "profit_loss_ratio": profit_loss_ratio,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown * 100.0,
    }


def print_metrics(summary):
    print(f"Total trades: {summary['total_trades']}")
    print(f"Win rate: {summary['win_rate']:.2f}%")
    print(f"Average PnL: {summary['average_pnl']:.2f}")
    print(f"Profit/Loss ratio: {summary['profit_loss_ratio']:.2f}")
    print(f"Expectancy: {summary['expectancy']:.2f}")
    print(f"Max drawdown: {summary['max_drawdown']:.2f}%")
