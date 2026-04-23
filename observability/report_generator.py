from __future__ import annotations

import html
import os

from observability import chart_renderer
from observability import metrics


DEFAULT_OUTPUT_PATH = "reports/backtest_dashboard.html"


def _format_value(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _table_html(title, rows, columns):
    header_html = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body_rows = []
    for row in rows:
        cells = "".join(
            f"<td>{html.escape(_format_value(row.get(column, '')))}</td>" for column in columns
        )
        body_rows.append(f"<tr>{cells}</tr>")

    body_html = "".join(body_rows) if body_rows else f"<tr><td colspan='{len(columns)}'>No data</td></tr>"
    return f"""
    <section class="card">
      <h2>{html.escape(title)}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr>{header_html}</tr></thead>
          <tbody>{body_html}</tbody>
        </table>
      </div>
    </section>
    """


def _metrics_panel(summary):
    items = [
        ("Total Trades", f"{summary['total_trades']}"),
        ("Win Rate", f"{summary['win_rate']:.2f}%"),
        ("Average PnL", f"{summary['average_pnl']:.2f}"),
        ("Profit/Loss Ratio", f"{summary['profit_loss_ratio']:.2f}"),
        ("Expectancy", f"{summary['expectancy']:.2f}"),
        ("Max Drawdown", f"{summary['max_drawdown']:.2f}%"),
    ]
    cards = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in items
    )
    return f"<section class='card metrics'>{cards}</section>"


def generate_report(
    df,
    events,
    summary=None,
    output_path: str = DEFAULT_OUTPUT_PATH,
    initial_capital: float = 10000.0,
):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    summary = summary or metrics.calculate_metrics(events, initial_capital=initial_capital)
    chart_html = chart_renderer.build_chart_html(df, events)

    trade_rows = [event for event in events if event.get("event_type") in {"entry", "exit"}]
    signal_rows = [
        event for event in events if event.get("event_type") in {"signal_trigger", "rejected_signal"}
    ]

    trade_table = _table_html(
        "Trade Events",
        trade_rows,
        [
            "timestamp",
            "bar_index",
            "event_type",
            "symbol",
            "price",
            "trend_status",
            "reason_code",
            "exit_type",
            "exit_action",
            "sell_pct",
            "current_return",
            "mfe",
            "entry_price",
            "exit_price",
            "pnl",
            "holding_bars",
            "exit_reason",
        ],
    )
    signal_table = _table_html(
        "Signal Events",
        signal_rows,
        [
            "timestamp",
            "bar_index",
            "event_type",
            "symbol",
            "price",
            "ema44_at_signal",
            "ema144_4h_at_signal",
            "macd_line_at_signal",
            "macd_signal_at_signal",
            "macd_hist_at_signal",
            "rsi_at_signal",
            "trend_status",
            "reason_code",
            "signal_rejected_reason",
        ],
    )

    page_html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Backtest Dashboard</title>
      <style>
        body {{
          margin: 0;
          padding: 24px;
          font-family: Helvetica, Arial, sans-serif;
          background: #f6f7fb;
          color: #1f2937;
        }}
        h1 {{ margin-top: 0; }}
        .layout {{ display: grid; gap: 24px; }}
        .card {{
          background: #ffffff;
          border-radius: 16px;
          padding: 20px;
          box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
        }}
        .metrics {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
          gap: 16px;
        }}
        .metric {{
          background: linear-gradient(135deg, #f8fafc, #eef2ff);
          border-radius: 12px;
          padding: 16px;
        }}
        .metric span {{
          display: block;
          font-size: 12px;
          color: #64748b;
          margin-bottom: 6px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }}
        .metric strong {{
          font-size: 22px;
        }}
        .table-wrap {{
          overflow-x: auto;
        }}
        table {{
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }}
        th, td {{
          padding: 10px 12px;
          border-bottom: 1px solid #e5e7eb;
          text-align: left;
          white-space: nowrap;
        }}
        th {{
          background: #f8fafc;
        }}
      </style>
    </head>
    <body>
      <div class="layout">
        <section class="card">
          <h1>Backtest Dashboard</h1>
        </section>
        {_metrics_panel(summary)}
        <section class="card">
          <h2>Chart</h2>
          {chart_html}
        </section>
        {trade_table}
        {signal_table}
      </div>
    </body>
    </html>
    """

    with open(output_path, "w", encoding="utf-8") as file:
        file.write(page_html)
    return output_path
